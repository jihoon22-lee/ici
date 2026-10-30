//! Execution lifecycle. Immutable analysis, process evidence, policy and saved artifacts stay distinct.
use crate::{
    commands::{CliError, CommandOutput, Result, under},
    events::{EventLog, SignalGuard},
    inputs,
    planning::{self, PreparedPlan},
    registry::Invocation,
};
use ici_application::{
    assemble::{self, RunContext},
    identity,
    plan::Work,
    suppressions::SuppressionPolicy,
    verification,
};
use ici_domain::{AnalysisOutput, GateVerdict, Observation, RunResult, TaskState};
use ici_execution::{
    Cancellation, Runner, Scheduler,
    storage::{self, WriteMode},
};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::Instant,
};

#[derive(Debug)]
pub struct ExecutedRun {
    pub result: RunResult,
    /// Retain actual inspected PASS/FAIL locations and descriptor extras for report projection.
    pub outputs: BTreeMap<String, AnalysisOutput>,
    pub result_path: PathBuf,
    pub raw_fingerprints: BTreeMap<String, String>,
    pub required_checks: BTreeMap<String, bool>,
    pub engine_names: BTreeMap<String, String>,
    pub project_name: String,
    pub duration_seconds: f64,
    /// Frozen-shaped legacy `baseline_comparison` of a comparable baseline (severity regressions
    /// and fail-on-new gate entries); `None` without a comparable baseline.
    pub legacy_baseline: Option<serde_json::Value>,
    /// Gated new findings and gated severity regressions against a comparable baseline.
    pub baseline_gate_failures: Vec<String>,
}
/// The exact command that writes a baseline this command can compare against.
pub fn regenerate_baseline_command(command: &str, baseline: &str) -> String {
    let quote = |path: &str| {
        if !path.is_empty()
            && path
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._/-+@%=:,".contains(&b))
        {
            path.to_owned()
        } else {
            format!("'{}'", path.replace('\'', "'\\''"))
        }
    };
    if command == "verify" {
        format!("ici verify --write-baseline {}", quote(baseline))
    } else {
        let fresh = baseline.strip_suffix(".json").map_or_else(
            || format!("{baseline}.next.json"),
            |stem| format!("{stem}.next.json"),
        );
        format!(
            "ici analyze --result {} (then pass --baseline {})",
            quote(&fresh),
            quote(&fresh)
        )
    }
}
fn error(error: impl std::fmt::Display) -> CliError {
    CliError(error.to_string())
}

pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
) -> Result<CommandOutput> {
    let cancellation = Cancellation::default();
    let _signals = SignalGuard::install(&cancellation).map_err(error)?;
    let run = execute(invocation, cwd, environment, static_only, &cancellation)?;
    let stdout = if invocation.flag("json_mode") {
        run.result.to_json().map_err(error)? + "\n"
    } else {
        let stored = crate::report::load(&run.result_path)?;
        let root = crate::commands::artifact_root(cwd);
        let options = ici_reporting::RenderOptions {
            source_root: Some(root.to_string_lossy().into_owned()),
            ..Default::default()
        };
        let mut text = ici_reporting::render_console(&stored, &options).map_err(error)?;
        let page = run.result_path.with_extension("html");
        let html = ici_reporting::render_html(&stored, &options).map_err(error)?;
        crate::report::write_artifact(
            &page,
            html.as_bytes(),
            &run.result_path,
            crate::report::ArtifactKind::Html,
        )
        .map_err(|failure| {
            CliError(format!(
                "analysis result saved at {}; HTML could not be written: {failure}",
                run.result_path.display()
            ))
        })?;
        text.push_str(&format!(
            "Result: {}\nHTML: {}\n",
            run.result_path.display(),
            page.display()
        ));
        text
    };
    Ok(CommandOutput {
        stdout,
        stderr: String::new(),
        exit_code: run.result.exit_code(),
    })
}

pub fn execute(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    cancellation: &Cancellation,
) -> Result<ExecutedRun> {
    let started = Instant::now();
    let run_id = format!(
        "{}-{}",
        chrono::Utc::now().format("%Y%m%dT%H%M%S%.9fZ"),
        std::process::id()
    );
    let mut prepared =
        planning::prepare_for_run(invocation, cwd, environment, static_only, &run_id)?;
    let result_path = under(
        &prepared.project.root,
        invocation.value("result").unwrap_or(crate::DEFAULT_RESULT),
    );
    let events_path = invocation
        .value("events")
        .map(|path| under(&prepared.project.root, path));
    let baseline_path = invocation
        .value("baseline")
        .map(|path| under(&prepared.project.root, path));
    protect_outputs(
        &prepared,
        &result_path,
        events_path.as_deref(),
        baseline_path.as_deref(),
    )?;
    // Invalid baselines and artifact destinations fail before launching project tools.
    let baseline_document = baseline_path
        .as_ref()
        .map(|path| crate::report::load(path))
        .transpose()?;
    let baseline = baseline_document.as_ref().and_then(|stored| match stored {
        ici_reporting::StoredResult::Run(run) => Some(run.as_ref().clone()),
        ici_reporting::StoredResult::Legacy(legacy) => legacy
            .as_value()
            .get("native_run")
            .cloned()
            .and_then(|value| RunResult::from_value(value).ok()),
    });
    validate_destination(&result_path, false)?;
    validate_destination(&crate::report::details_path(&result_path, &run_id)?, true)?;
    if result_path.exists() {
        storage::read_result(&result_path).map_err(|failure| CliError(format!("existing result is not a native run artifact; choose a new --result path: {failure}")))?;
    }
    if let Some(path) = &events_path {
        if previous_event_stream(path)? {
            validate_destination(path, false)?;
            std::fs::remove_file(path).map_err(error)?;
        }
        validate_destination(path, true)?;
    }
    let runner = Runner::with_environment(environment.clone());
    // Observe the user's tree before our result/event artifacts can make it dirty.
    let vcs = crate::vcs::inspect(&prepared.source_identity, &runner, cancellation);
    let events = EventLog::open(events_path.as_deref(), &run_id).map_err(error)?;
    events.emit("run.started", None, None, "capturing selected inputs");
    let mut scheduler = Scheduler::new(runner.clone(), 2);
    scheduler.lock_directory = Some(prepared.project.root.join(".ici/rust-v1/locks/tasks"));
    let mut limitations = vec![];
    limitations.extend(vcs.limitation);
    let mut input_limits = inputs::binary_limits(&prepared);
    let mut code_input_paths = inputs::code_evidence_paths(&prepared);
    let (snapshot, intake_ok) = match inputs::capture(&prepared) {
        Ok(snapshot) => (snapshot, true),
        Err(failure) => {
            limitations.push(failure.to_string());
            (inputs::empty_snapshot(), false)
        }
    };
    events.emit(
        "plan.ready",
        None,
        None,
        &format!("{} selected checks", prepared.plan.checks.len()),
    );
    let preparation = crate::preparation::execute(
        prepared,
        snapshot,
        intake_ok,
        &crate::preparation::Runtime {
            invocation,
            environment,
            run_id: &run_id,
            static_only,
            scheduler: &scheduler,
            cancellation,
            events: &events,
        },
    );
    let crate::preparation::PreparedExecution {
        prepared: resolved,
        snapshot,
        intake_ok,
        outputs: pre_outputs,
        contexts: context_guards,
        limitations: preparation_limits,
    } = preparation;
    prepared = resolved;
    input_limits.extend(inputs::binary_limits(&prepared));
    code_input_paths.extend(inputs::code_evidence_paths(&prepared));
    limitations.extend(preparation_limits);
    let core_digest =
        ici_execution::process::executable_digest(Path::new("/proc/self/exe")).map_err(error);
    let core_known = core_digest.is_ok();
    if let Err(failure) = &core_digest {
        limitations.push(format!("core identity unavailable: {failure}"));
    }
    let policy_digest = identity::policy_digest(&prepared.project).map_err(error)?;
    let cache = crate::reuse::RunCache::new(
        &prepared.project.root,
        core_digest.as_ref().ok().map(String::as_str),
        &policy_digest,
        invocation.flag("no_cache"),
    );
    let mut cache_keys = BTreeMap::new();
    let mut reused = vec![];
    let toolchain_digest=identity::digest(&json!({"namespace":"ici.rust.toolchain.v1","core":core_digest.ok(),"tools":prepared.toolchain_roles()})).map_err(error)?;
    let coordinates =
        ici_providers::source_coordinates(snapshot.files.values().map(|file| &file.input));
    prepared.cpp.attach_sources(coordinates.clone());
    for provider in prepared.providers.values_mut() {
        ici_providers::attach_sources(provider, coordinates.clone());
    }
    let mut python = ici_analysis::AnalysisSession::new();
    let mut cpp = ici_cpp::CppAnalysisSession::new();
    let mut outputs = pre_outputs;
    let mut step_outcomes = BTreeMap::new();
    let mut owned_runs = BTreeMap::new();
    let mut enrichment_incomplete = BTreeMap::new();
    for plans in prepared.validations.values_mut() {
        for plan in plans {
            ici_validation::attach_sources(plan, coordinates.clone());
        }
    }
    if intake_ok && !cancellation.is_cancelled() {
        for planned in &prepared.plan.checks {
            if planned.check.language != "python"
                || !prepared.validations.contains_key(&planned.task_id)
            {
                continue;
            }
            let mut request =
                inputs::analysis_request(&prepared, planned, &snapshot, prepared.legacy_projection);
            let component = inputs::component(&prepared, planned);
            for (path, file) in &snapshot.files {
                if path.ends_with(".py")
                    && (component.root == "." || path.starts_with(&format!("{}/", component.root)))
                    && !request.inputs.iter().any(|input| input.path == *path)
                {
                    request.inputs.push(file.input.clone());
                }
            }
            let (facts, facts_complete, fact_limitation) = match python
                .source_facts_cancellable(&request, &cancellation.flag())
            {
                Ok(facts) => (
                    facts
                        .functions
                        .into_iter()
                        .map(|fact| ici_validation::FunctionFact {
                            path: fact.path,
                            name: fact.name,
                            qualified_name: fact.qualified_name,
                            start_line: fact.start_line,
                            entry_line: Some(fact.entry_line),
                            body_start_line: fact.body_start_line,
                            end_line: fact.end_line,
                        })
                        .collect::<Vec<_>>(),
                    true,
                    None,
                ),
                Err(output) => (
                    vec![],
                    false,
                    Some(format!(
                        "core source geometry unavailable; project runtime evidence is independent: {}",
                        output
                            .observation
                            .limitations
                            .last()
                            .cloned()
                            .unwrap_or(output.summary)
                    )),
                ),
            };
            if cancellation.is_cancelled() {
                break;
            }
            let requirement = if planned.check.id == "python.compat-runtime" {
                match ici_analysis::requires_python(&request) {
                    Ok(requirement) => requirement.filter(|requirement| !requirement.is_empty()),
                    Err(output) => {
                        enrichment_incomplete.insert(
                            planned.task_id.clone(),
                            format!(
                                "declared runtime compatibility cannot be evaluated: {}",
                                output
                                    .observation
                                    .limitations
                                    .last()
                                    .cloned()
                                    .unwrap_or(output.summary)
                            ),
                        );
                        None
                    }
                }
            } else {
                None
            };
            for plan in prepared
                .validations
                .get_mut(&planned.task_id)
                .expect("selected validation")
            {
                if let Some(limitation) = &fact_limitation {
                    plan.limitations.push(limitation.clone());
                }
                plan.context.functions = facts
                    .iter()
                    .filter(|fact| {
                        plan.kind != ici_validation::ValidationKind::PythonCoverage
                            || plan.context.source_files.contains(&fact.path)
                    })
                    .cloned()
                    .collect();
                plan.context.options.function_facts_complete = facts_complete;
                if plan.context.options.requires_python.is_none() {
                    plan.context.options.requires_python = requirement.clone();
                }
            }
        }
    }
    for planned in &prepared.plan.checks {
        if let Some(reason) = prepared.graph.blocked.get(&planned.task_id) {
            outputs.insert(
                planned.task_id.clone(),
                unavailable(&planned.task_id, TaskState::Blocked, reason),
            );
        }
    }
    for layer in prepared.graph.layers.clone() {
        let mut tasks = vec![];
        for id in &layer {
            let planned = prepared
                .plan
                .checks
                .iter()
                .find(|planned| &planned.task_id == id)
                .expect("graph owns selected checks");
            if outputs.contains_key(id) {
                continue;
            }
            let reason = if cancellation.is_cancelled() {
                Some((TaskState::Cancelled, "run cancelled".into()))
            } else if !intake_ok {
                Some((TaskState::Blocked, "input snapshot failed".into()))
            } else {
                prepared
                    .graph
                    .edges
                    .get(id)
                    .and_then(|dependencies| {
                        dependencies.iter().find(|dependency| {
                            outputs
                                .get(*dependency)
                                .is_none_or(|output| !output.observation.evidence_is_complete())
                                && !prepared.cpp.partial_input_available(dependency, id)
                        })
                    })
                    .map(|dependency| {
                        (
                            TaskState::Blocked,
                            format!("prerequisite {dependency} has no complete evidence"),
                        )
                    })
            };
            if let Some((state, reason)) = reason {
                outputs.insert(id.clone(), unavailable(id, state, &reason));
                continue;
            }
            match &planned.work {
                Work::NativePrepare { tasks } => {
                    let Some(build) = prepared.artifact_builds.get(id).cloned() else {
                        outputs.insert(
                            id.clone(),
                            unavailable(
                                id,
                                TaskState::Blocked,
                                "native preparation handler is not bound",
                            ),
                        );
                        continue;
                    };
                    let (mut output, resolved) = execute_artifact_prepare(
                        &build,
                        tasks,
                        &snapshot,
                        &scheduler,
                        cancellation,
                        &events,
                    );
                    if let Some(build) = resolved {
                        prepared.artifact_builds.insert(id.clone(), build);
                    }
                    redact(&mut output)?;
                    events.emit(
                        "task.completed",
                        Some(id),
                        Some(&planned.component_id),
                        &format!("{:?}", output.observation.state),
                    );
                    outputs.insert(id.clone(), output);
                }
                Work::Internal => {
                    let start = Instant::now();
                    let request = inputs::analysis_request(
                        &prepared,
                        planned,
                        &snapshot,
                        prepared.legacy_projection,
                    );
                    if let Some(cache) = &cache {
                        match cache.internal_key(&request, &prepared.project.root, &snapshot) {
                            Ok(Some(key)) => {
                                cache_keys.insert(id.clone(), key.clone());
                                match cache.store.get_output(&key, id) {
                                    Ok(Some(mut output)) => {
                                        redact(&mut output)?;
                                        events.emit(
                                            "task.progress",
                                            Some(id),
                                            Some(&planned.component_id),
                                            "reused unchanged native analysis",
                                        );
                                        reused.push(id.clone());
                                        outputs.insert(id.clone(), output);
                                        continue;
                                    }
                                    Ok(None) => {}
                                    Err(error) => {
                                        limitations.push(format!("cache ignored for {id}: {error}"))
                                    }
                                }
                            }
                            Ok(None) => {}
                            Err(error) => {
                                limitations.push(format!("cache identity for {id}: {error}"))
                            }
                        }
                    }

                    events.emit(
                        "task.started",
                        Some(id),
                        Some(&planned.component_id),
                        "native analysis",
                    );
                    let mut output = if let Some(output) = crate::validation::internal(
                        &prepared.project,
                        inputs::component(&prepared, planned),
                        &planned.check.id,
                        id,
                    ) {
                        output
                    } else if prepared.cpp.handles(id)
                        || matches!(planned.check.id.as_str(), "cpp.compile" | "cpp.artifact")
                    {
                        let component = inputs::component(&prepared, planned).clone();
                        prepared.cpp.run(
                            &prepared.project,
                            &component,
                            &request,
                            &scheduler,
                            cancellation,
                            &events,
                        )
                    } else if planned.check.language == "python" {
                        python.analyze_cancellable(&request, &cancellation.flag())
                    } else {
                        cpp.analyze_cancellable(&request, &cancellation.flag())
                    };
                    output.observation.duration_seconds = Some(start.elapsed().as_secs_f64());
                    redact(&mut output)?;
                    events.emit(
                        "task.completed",
                        Some(id),
                        Some(&planned.component_id),
                        &format!("{:?}", output.observation.state),
                    );
                    outputs.insert(id.clone(), output);
                }
                Work::Process { task, .. } => tasks.push(task.as_ref().clone()),
                Work::Steps { .. } => {
                    let mut output = if planned.check.language == "cpp" && prepared.cpp.handles(id)
                    {
                        let request = inputs::analysis_request(
                            &prepared,
                            planned,
                            &snapshot,
                            prepared.legacy_projection,
                        );
                        let component = inputs::component(&prepared, planned).clone();
                        let mut output = prepared.cpp.run(
                            &prepared.project,
                            &component,
                            &request,
                            &scheduler,
                            cancellation,
                            &events,
                        );
                        if prepared.legacy_projection
                            && planned.check.id == "cpp.diagnostics"
                            && prepared.cpp.is_fallback(id)
                        {
                            let tooling = prepared
                                .plan
                                .checks
                                .iter()
                                .find(|check| {
                                    check.component_id == planned.component_id
                                        && check.check.id == "cpp.tidy"
                                })
                                .and_then(|check| prepared.cpp.planned(&check.task_id));
                            crate::cpp::legacy_fallback_lint(&mut output, tooling);
                        }
                        output
                    } else if let Some(plans) = prepared.validations.get(id) {
                        let mut output = execute_validation(
                            id,
                            plans,
                            &scheduler,
                            cancellation,
                            &events,
                            &mut step_outcomes,
                            &mut owned_runs,
                        );
                        if prepared.legacy_projection && planned.check.id == "cpp.binary-compat" {
                            let shadow = plans
                                .first()
                                .and_then(|plan| plan.context.options.build_roots.get(1))
                                .and_then(|directory| {
                                    directory.strip_prefix(&prepared.project.root).ok()
                                })
                                .map(|directory| directory.to_string_lossy().into_owned())
                                .unwrap_or_default();
                            crate::cpp::legacy_binary_compat(&mut output, &shadow, "release");
                        }
                        if prepared.legacy_projection
                            && matches!(planned.check.id.as_str(), "cpp.sanitize" | "cpp.tsan")
                            && prepared
                                .project
                                .components
                                .iter()
                                .find(|component| component.id == planned.component_id)
                                .and_then(|component| {
                                    crate::cpp::linked_build(&prepared.project, component)
                                })
                                .is_some_and(|build| build.system.as_deref() == Some("direct-gcc"))
                        {
                            crate::cpp::legacy_direct_sanitizer(&mut output, &planned.check.id);
                        }
                        output
                    } else {
                        unavailable(
                            id,
                            TaskState::Blocked,
                            "ordered validation adapter has not supplied an observer",
                        )
                    };
                    redact(&mut output)?;
                    outputs.insert(id.clone(), output);
                }
                Work::Blocked { reason } => {
                    outputs.insert(id.clone(), unavailable(id, TaskState::Blocked, reason));
                }
            }
        }
        // Ruff's actual inspected scope is proven by a separate visible probe, never inferred from requested targets.
        let probes: Vec<_> = tasks
            .iter()
            .filter_map(|task| prepared.probes.get(&task.id).cloned())
            .collect();
        let proof = scheduler
            .run_with_start(&probes, cancellation, |task| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    None,
                    "native scope inventory",
                )
            })
            .map_err(error)?;
        for outcome in proof.outcomes {
            events.emit(
                "task.completed",
                Some(&outcome.task_id),
                None,
                &format!("{:?}", outcome.state),
            );
            if let Some(id) = outcome.task_id.strip_suffix(".files") {
                let provider = prepared
                    .providers
                    .get_mut(id)
                    .expect("probe belongs to provider");
                let evidence = &prepared.native_configuration[id];
                if let Err(failure) =
                    ici_providers::plan::apply_ruff_inventory(provider, &outcome, evidence)
                {
                    provider.scope_complete = false;
                    provider.limitations.push(failure.to_string());
                }
            }
        }
        let mut runnable = vec![];
        for task in tasks {
            let mut hit = false;
            if let (Some(cache), Some(provider)) = (&cache, prepared.providers.get(&task.id)) {
                match cache.provider_key(provider, &prepared.project.root, &snapshot, &runner) {
                    Ok(Some(key)) => {
                        cache_keys.insert(task.id.clone(), key.clone());
                        match cache.store.get_output(&key, &task.id) {
                            Ok(Some(mut output)) => {
                                redact(&mut output)?;
                                events.emit(
                                    "task.progress",
                                    Some(&task.id),
                                    None,
                                    "reused verified native tool evidence",
                                );
                                reused.push(task.id.clone());
                                outputs.insert(task.id.clone(), output);
                                hit = true;
                            }
                            Ok(None) => {}
                            Err(error) => {
                                limitations.push(format!("cache ignored for {}: {error}", task.id))
                            }
                        }
                    }
                    Ok(None) => {}
                    Err(error) => {
                        limitations.push(format!("cache identity for {}: {error}", task.id))
                    }
                }
            }
            if !hit {
                runnable.push(task);
            }
        }
        let schedule = scheduler
            .run_with_start(&runnable, cancellation, |task| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    None,
                    "native tool execution",
                )
            })
            .map_err(error)?;
        for outcome in schedule.outcomes {
            let id = outcome.task_id.clone();
            let mut output = if let Some(provider) = prepared.providers.get(&id) {
                ici_providers::observe_full(provider, &outcome)
            } else {
                unavailable(&id, TaskState::Failed, "process observer unavailable")
            };
            redact(&mut output)?;
            events.emit(
                "task.completed",
                Some(&id),
                None,
                &format!("{:?}", output.observation.state),
            );
            outputs.insert(id, output);
        }
    }
    for planned in &prepared.plan.checks {
        outputs.entry(planned.task_id.clone()).or_insert_with(|| {
            unavailable(
                &planned.task_id,
                if cancellation.is_cancelled() {
                    TaskState::Cancelled
                } else {
                    TaskState::Blocked
                },
                "selected check did not execute",
            )
        });
    }
    for (id, reason) in enrichment_incomplete {
        if let Some(output) = outputs.get_mut(&id) {
            output.observation.state = TaskState::Failed;
            output.observation.limitations.push(reason.clone());
            output.status = "ERROR".into();
            output.summary = reason;
        }
    }
    for output in outputs.values_mut() {
        redact(output)?;
    }
    let mut generated_outputs = BTreeSet::new();
    let mut preparation_verified = true;
    for (id, build) in &prepared.artifact_builds {
        if outputs
            .get(id)
            .is_some_and(|output| output.observation.evidence_is_complete())
        {
            match crate::artifact_build::verify_output(build) {
                Ok(paths) => generated_outputs.extend(
                    paths
                        .into_keys()
                        .filter(|path| !snapshot.files.contains_key(path)),
                ),
                Err(reason) => {
                    preparation_verified = false;
                    limitations.push(format!("prepared artifact integrity: {reason}"));
                }
            }
        }
    }
    let inputs_stable = match planning::prepare_with_contexts(
        invocation,
        cwd,
        environment,
        static_only,
        &run_id,
        &generated_outputs,
        &context_guards,
    )
    .and_then(|mut after| {
        for context in context_guards
            .iter()
            .filter(|context| context.plan.direct.is_some())
        {
            for (name, tool) in &context.plan.tools {
                let current =
                    ici_execution::process::executable_digest(&tool.path).map_err(error)?;
                if current != tool.digest {
                    return Ok(false);
                }
                after
                    .tools
                    .entry(format!(
                        "prepare:{}:{}:{name}",
                        context.plan.component_id, context.plan.build.variant
                    ))
                    .or_insert_with(|| tool.clone());
            }
        }
        inputs::capture_preserving(&after, &snapshot, &input_limits, &[])
            .map(|new| !inputs::changed(&prepared, &after, &snapshot, &new))
    }) {
        Ok(stable) => intake_ok && preparation_verified && stable,
        Err(failure) => {
            limitations.push(format!("final input verification: {failure}"));
            false
        }
    };
    if inputs_stable
        && !cancellation.is_cancelled()
        && let Some(cache) = &cache
    {
        for (id, key) in cache_keys {
            if !reused.contains(&id)
                && let Some(output) = outputs.get(&id)
                && let Err(error) = cache.store.put_output(&key, output)
            {
                limitations.push(format!("cache write for {id}: {error}"));
            }
        }
    }
    crate::legacy::annotation_fallback(&prepared, &mut outputs);
    crate::legacy::lint_syntax(&prepared, &mut outputs);
    crate::legacy::apply_gate_policy(&prepared, &mut outputs);
    let suppressions = SuppressionPolicy::new(&prepared.project.suppressions).map_err(error)?;
    let observations: Vec<Observation> = prepared
        .plan
        .checks
        .iter()
        .map(|planned| outputs[&planned.task_id].observation.clone())
        .collect();
    let verification = verification::evaluate(
        &prepared.plan,
        observations,
        cancellation.is_cancelled(),
        |finding| suppressions.apply(finding),
    )
    .map_err(error)?;
    let mut raw_fingerprints = verification.raw_fingerprints.clone();
    let checks = inputs::check_records(
        &prepared,
        &verification.observations,
        &snapshot,
        &outputs,
        &runner,
        &inputs::InputVerification {
            stable: inputs_stable,
            code_paths: code_input_paths,
        },
        core_known,
    )?;
    if let Some(failure) = events.failure() {
        limitations.push(failure);
    }
    let mut source = snapshot.source(vcs.commit, !vcs.clean);
    let selected: BTreeSet<_> = prepared
        .project
        .components
        .iter()
        .flat_map(|component| component.files.iter().cloned())
        .collect();
    let generated: BTreeSet<_> = prepared
        .cpp
        .contexts
        .values()
        .flat_map(|context| context.units.iter().map(|unit| unit.source.clone()))
        .filter(|path| !selected.contains(path))
        .collect();
    source.files.retain(|path| !generated.contains(path));
    source.generated = generated.into_iter().collect();
    let mut result = assemble::assemble(
        &prepared.project,
        &prepared.request,
        &prepared.plan,
        verification,
        RunContext {
            run_id,
            ici_version: crate::VERSION.into(),
            source,
            policy_digest,
            toolchain_digest,
            bundle_digest: None,
            cancelled: cancellation.is_cancelled(),
            inputs_stable,
            checks,
            reused_task_ids: reused,
            limitations,
            baseline: None,
        },
    )
    .map_err(error)?;
    let given_baseline = invocation.value("baseline").unwrap_or_default().to_owned();
    let regenerate = regenerate_baseline_command(&invocation.command(), &given_baseline);
    let mut legacy_baseline = None;
    let mut baseline_gate_failures = vec![];
    if let Some(baseline) = baseline {
        let comparison = ici_application::baseline::compare(
            &result.findings,
            &result.identity,
            result.checks.as_deref(),
            &baseline,
            baseline_path.expect("baseline path").to_string_lossy(),
        );
        let mut comparison = comparison;
        if comparison.state == ici_domain::BaselineState::Incompatible {
            comparison.reason = format!("{}; regenerate it with: {regenerate}", comparison.reason);
            result.gate.reasons.push(format!(
                "baseline comparison unavailable: {}",
                comparison.reason
            ));
        }
        result.baseline = Some(comparison);
        if let Some(document) = &baseline_document {
            raw_fingerprints.extend(crate::coverage_regression::apply(
                &mut result,
                &mut outputs,
                &prepared.plan,
                &baseline,
                document,
                &suppressions,
                &crate::coverage_regression::configured_checks(&prepared),
            ));
        }
        // After coverage-regression findings joined the comparison: one delta for every view.
        if let Some(comparison) = &result.baseline {
            legacy_baseline = ici_reporting::legacy_baseline_comparison(
                &result,
                &baseline,
                comparison,
                &engine_names(&prepared),
                invocation.flag("fail_on_new"),
            );
            baseline_gate_failures =
                ici_reporting::baseline_gate_failures(&result, &baseline, comparison);
        }
    } else if let Some(ici_reporting::StoredResult::Legacy(legacy)) = baseline_document {
        let previous = legacy
            .engines()
            .iter()
            .flat_map(|engine| engine["findings"].as_array().into_iter().flatten())
            .filter_map(|finding| finding["fingerprint"].as_str().map(str::to_owned))
            .collect::<BTreeSet<_>>()
            .len();
        result.baseline = Some(ici_domain::BaselineComparison {
            state: ici_domain::BaselineState::Incompatible,
            origin: baseline_path
                .expect("baseline path")
                .to_string_lossy()
                .into_owned(),
            reason: format!(
                "historical inventory of {previous} findings is readable, but native provider policy and completed check scope were not recorded; no finding delta is inferred and the original baseline is preserved; regenerate it with: {regenerate}"
            ),
            ..Default::default()
        });
    }
    if !core_known && result.gate.selected == GateVerdict::Pass {
        result.gate.selected = GateVerdict::Incomplete;
        result.gate.workspace = GateVerdict::NotEvaluated;
        result.execution.required_complete = false;
        result.gate.reasons.push("core identity unavailable".into());
    }
    result = RunResult::from_value(ici_execution::redaction::redact_value(
        &result.to_value().map_err(error)?,
    ))
    .map_err(error)?;
    storage::write_result(&result_path, &result, WriteMode::ReplaceRegular).map_err(error)?;
    events.emit(
        "run.completed",
        None,
        None,
        &format!("{:?}", result.gate.selected),
    );
    if let Some(failure) = events.failure()
        && !result.limitations.contains(&failure)
    {
        result.limitations.push(failure);
        storage::write_result(&result_path, &result, WriteMode::ReplaceRegular).map_err(error)?;
    }
    let executed = ExecutedRun {
        result,
        outputs,
        result_path,
        raw_fingerprints,
        required_checks: prepared
            .plan
            .checks
            .iter()
            .map(|planned| (planned.task_id.clone(), planned.check.required))
            .collect(),
        engine_names: engine_names(&prepared),
        project_name: prepared.project.workspace_name.clone(),
        duration_seconds: started.elapsed().as_secs_f64(),
        legacy_baseline,
        baseline_gate_failures,
    };
    crate::report::save_details(&executed)?;
    Ok(executed)
}
pub(crate) fn execute_artifact_prepare(
    initial: &crate::artifact_build::ArtifactBuildPlan,
    tasks: &[ici_domain::TaskSpec],
    snapshot: &ici_application::snapshot::InputSnapshot,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &EventLog,
) -> (
    AnalysisOutput,
    Option<crate::artifact_build::ArtifactBuildPlan>,
) {
    let started = Instant::now();
    let id = initial.task.id.as_str();
    let mut build = initial.clone();
    let probes = &tasks[..tasks.len().saturating_sub(1)];
    if !probes.is_empty() {
        let schedule = match scheduler.run_with_start(probes, cancellation, |task| {
            events.emit(
                "task.started",
                Some(&task.id),
                Some(&initial.component_id),
                "project version probe",
            )
        }) {
            Ok(schedule) => schedule,
            Err(reason) => {
                return (
                    unavailable(id, TaskState::Blocked, &reason.to_string()),
                    None,
                );
            }
        };
        for outcome in schedule.outcomes {
            events.emit(
                "task.completed",
                Some(&outcome.task_id),
                Some(&initial.component_id),
                &format!("{:?}", outcome.state),
            );
            if build
                .version_probe
                .as_ref()
                .is_some_and(|probe| probe.id == outcome.task_id)
            {
                build = match crate::artifact_build::apply_version_probe(&build, &outcome) {
                    Ok(build) => build,
                    Err(reason) => {
                        return (
                            unavailable(
                                id,
                                if cancellation.is_cancelled() {
                                    TaskState::Cancelled
                                } else {
                                    TaskState::Failed
                                },
                                &reason,
                            ),
                            None,
                        );
                    }
                };
            } else {
                return (
                    unavailable(
                        id,
                        TaskState::Failed,
                        "native preparation has an unrecognized probe observer",
                    ),
                    None,
                );
            }
        }
    }
    let leases = ici_execution::scheduler::ResourceLocks::acquire(
        &build.root.join(".ici/rust-v1/locks/tasks"),
        &build.task.resource_keys,
        cancellation,
        std::time::Duration::from_secs_f64(build.task.timeout_seconds.unwrap_or(300.0)),
    );
    let mut output = match leases {
        Ok(_leases) => {
            events.emit(
                "task.started",
                Some(id),
                Some(&build.component_id),
                "packaging declared project artifacts",
            );
            crate::artifact_build::execute(&build, snapshot, cancellation)
        }
        Err(reason) => unavailable(
            id,
            if cancellation.is_cancelled() {
                TaskState::Cancelled
            } else {
                TaskState::Blocked
            },
            &reason.to_string(),
        ),
    };
    output.observation.duration_seconds = Some(started.elapsed().as_secs_f64());
    (output, Some(build))
}

fn unavailable(id: &str, state: TaskState, reason: &str) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici");
    output.observation.state = state;
    output.observation.limitations.push(reason.into());
    output.status = "SKIP".into();
    output.summary = reason.into();
    output
}
pub(crate) fn redact(output: &mut AnalysisOutput) -> Result<()> {
    let value = serde_json::to_value(&*output).map_err(error)?;
    *output =
        serde_json::from_value(ici_execution::redaction::redact_value(&value)).map_err(error)?;
    Ok(())
}
fn lexical(path: &Path) -> PathBuf {
    let mut output = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                output.pop();
            }
            other => output.push(other.as_os_str()),
        }
    }
    output
}
fn protect_outputs(
    prepared: &PreparedPlan,
    result: &Path,
    events: Option<&Path>,
    baseline: Option<&Path>,
) -> Result<()> {
    let outputs: Vec<_> = std::iter::once(result).chain(events).collect();
    if events.is_some_and(|events| lexical(events) == lexical(result)) {
        return Err(CliError("result and event output paths must differ".into()));
    }
    for output in outputs {
        let output = lexical(output);
        if baseline.is_some_and(|baseline| {
            lexical(baseline) == output
                || baseline
                    .canonicalize()
                    .ok()
                    .zip(output.canonicalize().ok())
                    .is_some_and(|(baseline, output)| baseline == output)
        }) {
            return Err(CliError(
                "an output cannot replace the comparison baseline".into(),
            ));
        }
        let selected = prepared
            .project
            .components
            .iter()
            .flat_map(|component| component.files.iter())
            .map(|path| prepared.project.root.join(path));
        if selected
            .chain(
                prepared
                    .legacy_lines
                    .values()
                    .flat_map(|scope| scope.inputs.iter())
                    .map(|path| prepared.project.root.join(path)),
            )
            .chain(prepared.project.configuration_files.iter().cloned())
            .any(|path| lexical(&path) == output)
        {
            return Err(CliError(
                "an output cannot replace source or configuration inputs".into(),
            ));
        }
    }
    Ok(())
}
/// A rerun may replace an earlier `ici.next.event` stream at the same path. Any other
/// existing file is user data and stays untouched.
fn previous_event_stream(path: &Path) -> Result<bool> {
    const MAX_STREAM: u64 = 64 * 1024 * 1024;
    let metadata = match std::fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(failure) if failure.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(failure) => return Err(error(failure)),
    };
    if !metadata.file_type().is_file() || metadata.len() > MAX_STREAM {
        return Ok(false);
    }
    let Ok(text) = String::from_utf8(std::fs::read(path).map_err(error)?) else {
        return Ok(false);
    };
    if text.is_empty() {
        return Ok(true);
    }
    let last = text.lines().count();
    Ok(match ici_domain::read_events(&text) {
        // Only a truncated final line (a killed writer) may be unparseable.
        Ok((events, notes)) => {
            !events.is_empty()
                && notes
                    .iter()
                    .all(|note| note.starts_with(&format!("line {last}: not valid JSON")))
        }
        Err(_) => false,
    })
}
pub(crate) fn validate_destination(path: &Path, create_new: bool) -> Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) => {
            if create_new || !metadata.file_type().is_file() {
                return Err(CliError(format!(
                    "{} is not an available regular output",
                    path.display()
                )));
            }
        }
        Err(failure) if failure.kind() == std::io::ErrorKind::NotFound => {}
        Err(failure) => return Err(error(failure)),
    }
    let parent = path
        .parent()
        .ok_or_else(|| CliError("output has no parent directory".into()))?;
    storage::prepare_directory(parent).map_err(error)?;
    Ok(())
}

fn execute_validation(
    id: &str,
    plans: &[ici_validation::ValidationPlan],
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &EventLog,
    outcomes: &mut BTreeMap<String, ici_execution::TaskOutcome>,
    owners: &mut BTreeMap<PathBuf, ici_validation::OwnedRun>,
) -> AnalysisOutput {
    let mut combined = AnalysisOutput::new(id, "ici-validation");
    for original in plans {
        let mut captured = original.clone();
        if let Err(error) = ici_validation::capture_binary_headers(&mut captured) {
            return unavailable(
                id,
                TaskState::Failed,
                &format!("binary artifact header capture: {error}"),
            );
        }
        let plan = &captured;
        let directory = &plan.context.owned_run_dir;
        if !owners.contains_key(directory) {
            let prepare = directory
                .parent()
                .ok_or_else(|| "validation run has no parent".into())
                .and_then(|parent| {
                    storage::prepare_directory(parent).map_err(|error| error.to_string())
                })
                .and_then(|()| {
                    ici_validation::OwnedRun::create(directory).map_err(|error| error.to_string())
                });
            match prepare {
                Ok(owner) => {
                    owners.insert(directory.clone(), owner);
                }
                Err(reason) => {
                    return unavailable(
                        id,
                        TaskState::Failed,
                        &format!("fresh validation directory: {reason}"),
                    );
                }
            }
        }
        let mut failure = None;
        loop {
            let ready = match ici_validation::ready_steps(plan, outcomes) {
                Ok(ready) => ready,
                Err(error) => {
                    failure = Some(error.to_string());
                    break;
                }
            };
            if ready.is_empty() {
                break;
            }
            let tasks: Vec<_> = plan
                .tasks
                .iter()
                .filter(|task| ready.contains(&task.id))
                .cloned()
                .collect();
            match scheduler.run_with_start(&tasks, cancellation, |task| {
                owners[directory].mark_started(plan, &task.id);
                events.emit(
                    "task.started",
                    Some(&task.id),
                    Some(&plan.context.component_id),
                    "project validation",
                )
            }) {
                Ok(schedule) => {
                    for outcome in schedule.outcomes {
                        events.emit(
                            "task.completed",
                            Some(&outcome.task_id),
                            Some(&plan.context.component_id),
                            &format!("{:?}", outcome.state),
                        );
                        outcomes.insert(outcome.task_id.clone(), outcome);
                    }
                }
                Err(error) => {
                    failure = Some(error.to_string());
                    break;
                }
            }
        }
        let artifacts = match ici_validation::capture_artifacts(plan, &owners[directory], outcomes)
        {
            Ok(artifacts) => artifacts,
            Err(error) => {
                return unavailable(
                    id,
                    TaskState::Failed,
                    &format!("validation artifact ownership: {error}"),
                );
            }
        };
        let mut output = ici_validation::observe(plan, outcomes, &artifacts);
        if !output.observation.evidence_is_complete() {
            output.extra["step_diagnostics"]=json!(plan.tasks.iter().filter_map(|task|outcomes.get(&task.id)).map(|outcome|json!({"task_id":outcome.task_id,"exit_code":outcome.exit_code,"stdout":String::from_utf8_lossy(&outcome.stdout).chars().take(4096).collect::<String>(),"stderr":String::from_utf8_lossy(&outcome.stderr).chars().take(4096).collect::<String>(),"error":outcome.error})).collect::<Vec<_>>());
        }
        if let Some(reason) = failure {
            output.observation.state = if cancellation.is_cancelled() {
                TaskState::Cancelled
            } else {
                TaskState::Blocked
            };
            output.observation.limitations.push(reason);
        }
        let complete = output.observation.evidence_is_complete();
        combined.observation.timed_out |= output.observation.timed_out;
        combined.observation.truncated |= output.observation.truncated;
        combined.observation.duration_seconds = Some(
            combined.observation.duration_seconds.unwrap_or(0.0)
                + output.observation.duration_seconds.unwrap_or(0.0),
        );
        combined
            .observation
            .tool_versions
            .extend(output.observation.tool_versions);
        // A later required FAIL must not be hidden by an earlier advisory WARN.
        // Other states keep the original first-non-PASS rule; incompleteness is handled below.
        let rank = |status: &str| match status {
            "PASS" => Some(0),
            "WARN" => Some(1),
            "FAIL" => Some(2),
            _ => None,
        };
        match (rank(&combined.status), rank(&output.status)) {
            (Some(current), Some(next)) if next > current => {
                combined.status = output.status.clone();
            }
            (Some(0), None) => combined.status = output.status.clone(),
            _ => {}
        }
        if !complete {
            combined.observation.state = output.observation.state;
            combined.status = "SKIP".into();
        }
        combined
            .observation
            .findings
            .extend(output.observation.findings);
        combined
            .observation
            .measurements
            .extend(output.observation.measurements);
        combined
            .observation
            .limitations
            .extend(output.observation.limitations);
        combined.targets.extend(output.targets);
        if !combined.summary.is_empty() {
            combined.summary.push_str("; ");
        }
        combined.summary.push_str(&output.summary);
        if combined.extra.is_null() {
            combined.extra = json!({});
        }
        if let Some(details) = output
            .extra
            .get("finding_details")
            .and_then(serde_json::Value::as_object)
        {
            if !combined
                .extra
                .get("finding_details")
                .is_some_and(serde_json::Value::is_object)
            {
                combined.extra["finding_details"] = json!({});
            }
            combined.extra["finding_details"]
                .as_object_mut()
                .expect("details map")
                .extend(details.clone());
        }
        combined.extra[&plan.context.task_id] = output.extra;
    }
    aggregate_integration_cases(plans, &mut combined);
    combined
}
/// Frozen IntegrationEngine reports one case table and one summary for every declared case.
fn aggregate_integration_cases(
    plans: &[ici_validation::ValidationPlan],
    combined: &mut AnalysisOutput,
) {
    if plans.is_empty()
        || !combined.observation.evidence_is_complete()
        || !plans.iter().all(|plan| {
            plan.kind == ici_validation::ValidationKind::Integration
                && plan.context.options.integration_case_name.is_some()
        })
    {
        return;
    }
    let cases = plans
        .iter()
        .filter_map(|plan| {
            combined.extra[&plan.context.task_id]
                .pointer("/integration/cases/0")
                .cloned()
        })
        .collect::<Vec<_>>();
    if cases.len() != plans.len() {
        return;
    }
    let violations = cases.iter().filter(|case| case["status"] != "PASS").count();
    combined.summary = format!(
        "Integration contracts: {} run, {violations} violation(s)",
        cases.len()
    );
    combined.extra["integration"] = json!({"state":"MEASURED","cases":cases});
    combined.extra["tool_evidence"] = json!(
        plans
            .iter()
            .filter_map(|plan| {
                combined.extra[&plan.context.task_id]
                    .pointer("/tool_evidence/0")
                    .cloned()
            })
            .collect::<Vec<_>>()
    );
}

fn engine_names(prepared: &crate::planning::PreparedPlan) -> BTreeMap<String, String> {
    prepared
        .plan
        .checks
        .iter()
        .filter_map(|planned| {
            ici_application::plan::engine_name(&planned.check.id)
                .map(|name| (planned.task_id.clone(), name.into()))
        })
        .collect()
}
