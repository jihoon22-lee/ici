//! Frozen compiler-backed C++ function boundaries of a legacy `ici verify`
//! (`analysis/_cpp_function_boundaries.py::run_cpp_function_boundaries`).
//!
//! Only a verify run has frozen's analysis context: a standalone `ici complexity` /
//! `ici cognitive` never probes boundaries, and `cpp_boundaries = "off"` never calls the probe.
//! Otherwise an approved clang-tidy (a resolvable executable outside the project whose capability
//! probe passes) and an exact compilation database select the run; each recorded unit in frozen
//! order replays its syntax command through `clang-tidy --checks=-*,readability-function-size`
//! with the selected GCC's projected standard-library roots, and the frozen parser
//! (`ici_cpp::legacy_boundaries`) maps every diagnostic back to the source snapshot. Every unit
//! error is kept and stops the boundary mode (frozen collects them all), and the processes run
//! are the engine's tool evidence. The metric rows themselves are the frozen engines'
//! (`ici_cpp` heuristic metrics with the `legacy_boundaries` option).
use super::{CppPlans, legacy_syntax, legacy_tooling, run_cpp_tasks, unit_id};
use ici_application::plan::Work;
use ici_config::{Component, ResolvedProject};
use ici_cpp::{
    CompilationContext, ReplayPurpose,
    legacy_boundaries::{
        LegacyBoundaryOutcome, MAX_RUN_SOURCE_BYTES, MAX_SOURCE_BYTES, MAX_SOURCE_CACHE_BYTES,
        MappedSource, mapped_source, merge_boundaries, parse_function_boundaries_until,
    },
};
use ici_domain::{AnalysisOutput, AnalysisRequest, TaskSpec};
use ici_providers::resolver::ToolRequest;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    time::{Duration, Instant},
};

/// Frozen `_MAX_SELECTED_UNITS`.
const MAX_SELECTED_UNITS: usize = 2_048;
/// Frozen `_GLOBAL_TIMEOUT_SECONDS`.
const GLOBAL_BUDGET: Duration = Duration::from_secs(600);
/// Frozen `_UNIT_TIMEOUT_SECONDS`.
const UNIT_BUDGET: Duration = Duration::from_secs(120);

/// Frozen `_collect_boundary_rows`: each unit gets `min(_UNIT_TIMEOUT_SECONDS, remaining)` of
/// the run's global budget.
fn unit_budget(remaining: Duration) -> Duration {
    remaining.min(UNIT_BUDGET)
}

/// Frozen `_executable_identity`: `st_dev, st_ino, st_mode, st_size, st_mtime_ns, st_ctime_ns`.
pub(super) fn executable_identity(path: &Path) -> std::io::Result<[i128; 6]> {
    use std::os::unix::fs::MetadataExt;
    let details = std::fs::metadata(path)?;
    let nanos = |seconds: i64, nanos: i64| i128::from(seconds) * 1_000_000_000 + i128::from(nanos);
    Ok([
        i128::from(details.dev()),
        i128::from(details.ino()),
        i128::from(details.mode()),
        i128::from(details.size()),
        nanos(details.mtime(), details.mtime_nsec()),
        nanos(details.ctime(), details.ctime_nsec()),
    ])
}

/// Frozen `_run_unit` source check: the current file is still the analysed snapshot.
fn source_unchanged(root: &Path, source: &str, snapshot: Option<&String>) -> bool {
    std::fs::read(root.join(source))
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .is_some_and(|text| Some(&text) == snapshot)
}

/// Frozen `_resolve_compiler`'s `compiler-unavailable` replay error (cpp_replay.py:166-182) for
/// a recorded compiler that is neither on PATH nor resolvable; other failures stay native.
pub(super) fn unavailable_compiler(
    root: &Path,
    unit: &ici_cpp::CompilationUnit,
    environment: &BTreeMap<String, String>,
) -> Option<&'static str> {
    let value = unit
        .argv
        .first()
        .map(String::as_str)
        .unwrap_or(&unit.compiler);
    let lexical = Path::new(value);
    if lexical.is_absolute() || lexical.components().count() > 1 {
        let candidate = if lexical.is_absolute() {
            lexical.to_path_buf()
        } else {
            root.join(&unit.directory).join(lexical)
        };
        return candidate
            .canonicalize()
            .is_err()
            .then_some("The translation unit compiler could not be resolved.");
    }
    let found = environment
        .get("PATH")
        .into_iter()
        .flat_map(std::env::split_paths)
        .any(|directory| directory.join(value).is_file());
    (!found).then_some("The translation unit compiler is not available on PATH.")
}
/// Frozen `_metrics_config()` (sorted keys, compact separators).
const METRICS_CONFIG: &str = r#"{"CheckOptions":{"readability-function-size.CountMemberInitAsStmt":"true","readability-function-size.IgnoreMacros":"true","readability-function-size.LineThreshold":"0","readability-function-size.ParameterThreshold":"0","readability-function-size.StatementThreshold":"0"}}"#;

/// One selected unit in frozen order: its source, frozen configuration digest and either its
/// clang-tidy task or the frozen error that replaced the run.
#[derive(Clone, Debug)]
pub struct LegacyBoundaryUnit {
    pub source: String,
    pub configuration: String,
    pub step: Result<TaskSpec, String>,
}

/// The planned frozen boundary run of one complexity/cognitive check.
#[derive(Clone, Debug, Default)]
pub struct LegacyBoundaryPlan {
    /// Frozen `_prepare_boundary_run` errors recorded before any source is mapped.
    pub errors: Vec<String>,
    /// Whether those errors return before the source snapshots are mapped.
    pub stopped: bool,
    pub units: Vec<LegacyBoundaryUnit>,
    /// The frozen clang-tidy capability version.
    pub version: String,
    /// The resolved clang-tidy executable and its frozen `_executable_identity` at preparation.
    pub executable: String,
    pub identity: Option<[i128; 6]>,
}

/// Python `re.escape`.
fn python_re_escape(text: &str) -> String {
    let mut out = String::new();
    for character in text.chars() {
        if "()[]{}?*+-|^$\\.&~# \t\n\r\u{b}\u{c}".contains(character) {
            out.push('\\');
        }
        out.push(character);
    }
    out
}

impl CppPlans {
    /// Plan the frozen boundary run of a legacy complexity/cognitive check (`None`: frozen
    /// `unavailable`, no run, or `off`).
    pub(super) fn bind_legacy_boundaries(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        id: &str,
        environment: &BTreeMap<String, String>,
        policy: &str,
    ) -> Option<Work> {
        if self.legacy_standalone || policy == "off" {
            return None;
        }
        // Frozen `_prepare_boundary_run` resolves the project root first.
        let Ok(root) = project.root.canonicalize() else {
            let plan = LegacyBoundaryPlan {
                errors: vec!["function boundary project root is unavailable".into()],
                stopped: true,
                ..Default::default()
            };
            self.legacy_boundaries.insert(id.into(), plan);
            return Some(Work::Internal);
        };
        let tool = ToolRequest {
            id: "clang-tidy".into(),
            explicit: project
                .tools
                .get("clang-tidy")
                .and_then(|tool| tool.path.as_deref())
                .map(|path| crate::files::under(&project.root, path)),
            project_root: project.root.clone(),
            pack_root: None,
            path_env: environment.get("PATH").cloned(),
        }
        .resolve()
        .ok()?;
        // Frozen `regular_executable` of a complete capability outside the project.
        let version = legacy_tooling::legacy_version_banner(
            &tool.path,
            &project.root,
            environment,
            "clang-tidy",
        )?;
        let executable = legacy_tooling::resolved_executable(&tool.path);
        if Path::new(&executable).starts_with(&root) || context.database_path.is_none() {
            return None;
        }
        self.tools
            .insert(format!("boundaries:{}", tool.id), tool.clone());
        let mut plan = LegacyBoundaryPlan {
            version,
            executable: executable.clone(),
            ..Default::default()
        };
        let finish = |plans: &mut Self, plan: LegacyBoundaryPlan, tasks: Vec<TaskSpec>| {
            plans.legacy_boundaries.insert(id.into(), plan);
            Some(if tasks.is_empty() {
                Work::Internal
            } else {
                Work::steps(tasks, false)
            })
        };
        if context
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.level == "error")
        {
            plan.errors
                .push("function boundary compilation context has ingestion errors".into());
            plan.stopped = true;
            return finish(self, plan, vec![]);
        }
        match executable_identity(Path::new(&executable)) {
            Ok(identity) => plan.identity = Some(identity),
            Err(_) => {
                plan.errors
                    .push("function boundary tool identity is unavailable".into());
                plan.stopped = true;
                return finish(self, plan, vec![]);
            }
        }
        // Frozen `selected_units` over the production sources, in context order.
        let mut units: Vec<(usize, ici_cpp::CompilationUnit)> = context
            .units
            .iter()
            .cloned()
            .enumerate()
            .filter(|(_, unit)| {
                !context.unity_build || matches!(unit.language.as_str(), "c" | "c++")
            })
            .collect();
        units.sort_by_cached_key(|(_, unit)| {
            (
                unit.source.clone(),
                unit.directory.clone(),
                super::legacy_configuration_key(unit),
            )
        });
        if !context.unity_build && !context.uncovered_sources.is_empty() {
            plan.errors.push(format!(
                "function boundary context misses {} production source command(s)",
                context.uncovered_sources.len()
            ));
        }
        if units.is_empty() {
            plan.errors
                .push("function boundary context has no replayable production units".into());
        }
        if units.len() > MAX_SELECTED_UNITS {
            plan.errors
                .push("function boundary translation-unit count exceeds the bounded limit".into());
            plan.stopped = true;
            return finish(self, plan, vec![]);
        }
        let mut tasks = vec![];
        for (index, unit) in &units {
            let source = unit.source.clone();
            let configuration = super::legacy_configuration_key(unit);
            let error = |message: String| LegacyBoundaryUnit {
                source: source.clone(),
                configuration: configuration.clone(),
                step: Err(message),
            };
            if legacy_syntax::unit_targets(unit).1.is_some() {
                plan.units.push(error(format!(
                    "function boundary context has errors: {source}"
                )));
                continue;
            }
            // Frozen `build_replay_command`: the recorded compiler must be a probed one.
            if let Some(refused) = self.legacy_compiler_refusal(project, unit, environment) {
                plan.units.push(error(format!(
                    "function boundary replay {}: {source}: {}",
                    refused.code, refused.message
                )));
                continue;
            }
            let compiler = match self.compiler(project, unit, environment) {
                Ok(tool) => tool,
                Err(reason) => match unavailable_compiler(&project.root, unit, environment) {
                    // Frozen `build_replay_command` fails this unit only (`_run_unit`).
                    Some(message) => {
                        plan.units.push(error(format!(
                            "function boundary replay compiler-unavailable: {source}: {message}"
                        )));
                        continue;
                    }
                    None => return Some(super::blocked(reason)),
                },
            };
            // Frozen probe evidence carries the g++ capability version.
            let path = compiler.path.to_string_lossy().into_owned();
            self.compiler_versions.entry(path).or_insert_with(|| {
                legacy_tooling::legacy_version_banner(
                    &compiler.path,
                    &project.root,
                    environment,
                    "g++",
                )
                .unwrap_or_default()
            });
            let replay = match ici_cpp::replay_command(
                &project.root,
                unit,
                &compiler.path,
                ReplayPurpose::Diagnostics,
            ) {
                Ok(replay) => replay,
                Err(failure) => {
                    let (code, message) = legacy_syntax::frozen_replay(unit, &failure);
                    plan.units.push(error(format!(
                        "function boundary replay {code}: {source}: {message}"
                    )));
                    continue;
                }
            };
            let mut task = match ici_cpp::clang_tidy_boundary_task(
                &project.root,
                unit,
                &compiler.path,
                &tool.path,
                &ici_cpp::GccStdlibProjection::default(),
            ) {
                Ok(task) => task,
                Err(failure) => return Some(super::blocked(failure.to_string())),
            };
            let source_path = replay.source.display().to_string();
            // Frozen `_command`: `tooling_arguments` (the syntax replay without its program,
            // analysis suffix and source), the GCC stdlib projection, then `-w`.
            let mut argv = vec![
                executable.clone(),
                "--use-color=false".into(),
                "--checks=-*,readability-function-size".into(),
                format!("--config={METRICS_CONFIG}"),
                format!(
                    "--header-filter=^{}(?:/|$)",
                    python_re_escape(&root.display().to_string())
                ),
                source_path.clone(),
                "--".into(),
            ];
            argv.extend(
                replay.argv[1..replay.argv.len() - 4]
                    .iter()
                    .filter(|argument| **argument != source_path)
                    .cloned(),
            );
            argv.push("-fdiagnostics-color=never".into());
            let task_id = format!("{id}.boundaries{index}");
            let mut searches =
                match ici_cpp::gcc_include_search_tasks(&project.root, unit, &compiler.path) {
                    Ok(searches) => searches,
                    Err(failure) => return Some(super::blocked(failure.to_string())),
                };
            if !searches.is_empty() {
                argv.push("{observed-gcc-stdlib}".into());
            }
            argv.push("-w".into());
            task.id.clone_from(&task_id);
            task.argv = argv;
            task.analysis_unit_ids = vec![unit_id(component, unit)];
            // Frozen `_MAX_OUTPUT_CHARS` / `_UNIT_TIMEOUT_SECONDS`.
            task.output_limit_bytes = Some(1_000_000);
            task.timeout_seconds = Some(120.0);
            super::bind_inputs(project, component, context, unit, &mut task);
            for (search_index, search) in searches.iter_mut().enumerate() {
                search.id = format!("{task_id}.search{search_index}");
                search.analysis_unit_ids = vec![unit_id(component, unit)];
            }
            if !searches.is_empty() {
                tasks.extend(searches.clone());
                self.tooling_searches.insert(task_id.clone(), searches);
            }
            tasks.push(task.clone());
            plan.units.push(LegacyBoundaryUnit {
                source,
                configuration,
                step: Ok(task),
            });
        }
        finish(self, plan, tasks)
    }

    /// Run the planned frozen boundary units and the engine rows over their outcome.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn run_legacy_boundaries(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        plan: &LegacyBoundaryPlan,
        scheduler: &ici_execution::Scheduler,
        cancellation: &ici_execution::Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        let kind = if request.check_id == "cpp.complexity" {
            "complexity"
        } else {
            "cognitive"
        };
        // Frozen engines stop on a source they cannot read before any boundary run.
        let Some(texts) = ici_cpp::legacy_metric_texts(request, kind) else {
            return self
                .source_session
                .analyze_cancellable(request, &cancellation.flag());
        };
        let mut outcome = LegacyBoundaryOutcome {
            errors: plan.errors.clone(),
            ..Default::default()
        };
        let mut evidence: Vec<Value> = vec![];
        let mut mapped: BTreeMap<String, MappedSource> = BTreeMap::new();
        if !plan.stopped {
            // Frozen `_prepare_boundary_run` source snapshots, once per source.
            let mut bytes = 0usize;
            for unit in &plan.units {
                if mapped.contains_key(&unit.source) {
                    continue;
                }
                let text = request
                    .inputs
                    .iter()
                    .find(|input| input.path == unit.source)
                    .filter(|input| input.bytes.len() <= MAX_SOURCE_BYTES)
                    .and_then(|input| String::from_utf8(input.bytes.clone()).ok());
                let Some(text) = text else {
                    outcome.errors.push(format!(
                        "function boundary source is unavailable: {}",
                        unit.source
                    ));
                    continue;
                };
                if texts.get(&unit.source) != Some(&text) {
                    outcome.errors.push(format!(
                        "function boundary source snapshot changed: {}",
                        unit.source
                    ));
                    continue;
                }
                bytes += text.len();
                if bytes > MAX_RUN_SOURCE_BYTES {
                    outcome
                        .errors
                        .push("function boundary source snapshot budget exceeded".into());
                    break;
                }
                if bytes > MAX_SOURCE_CACHE_BYTES {
                    outcome
                        .errors
                        .push("function boundary source cache exceeds the bounded limit".into());
                    break;
                }
                match mapped_source(&text) {
                    Ok(source) => {
                        outcome.lambdas_excluded += source.lambda_count as u64;
                        mapped.insert(unit.source.clone(), source);
                    }
                    Err(error) => outcome.errors.push(format!(
                        "function boundary source scope is invalid: {}: {error}",
                        unit.source
                    )),
                }
            }
        }
        if outcome.errors.is_empty() {
            match self.collect_legacy_boundaries(
                project,
                component,
                plan,
                (&mapped, &texts),
                &mut outcome,
                &mut evidence,
                (scheduler, cancellation, events),
            ) {
                Ok(()) => {}
                Err(reason) => {
                    return super::failed(
                        AnalysisOutput::new(&request.task_id, "ici.metrics"),
                        &reason,
                    );
                }
            }
        } else {
            outcome.mode = "error".into();
        }
        let mut request = request.clone();
        request.options["legacy_boundaries"] =
            serde_json::to_value(&outcome).expect("serializable boundary outcome");
        let mut output = self
            .source_session
            .analyze_cancellable(&request, &cancellation.flag());
        output.extra["tool_evidence"] = json!(evidence);
        output
    }

    /// Frozen `_collect_boundary_rows` and the final merge.
    #[allow(clippy::too_many_arguments)]
    fn collect_legacy_boundaries(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        plan: &LegacyBoundaryPlan,
        (mapped, texts): (&BTreeMap<String, MappedSource>, &BTreeMap<String, String>),
        outcome: &mut LegacyBoundaryOutcome,
        evidence: &mut Vec<Value>,
        (scheduler, cancellation, events): (
            &ici_execution::Scheduler,
            &ici_execution::Cancellation,
            &crate::events::EventLog,
        ),
    ) -> Result<(), String> {
        let started = Instant::now();
        let mut collected = vec![];
        let mut excluded = BTreeSet::new();
        let mut checked = BTreeSet::new();
        let mut successful: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        let mut recorded = BTreeSet::new();
        let global = started + GLOBAL_BUDGET;
        for (index, unit) in plan.units.iter().enumerate() {
            let remaining = global.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                outcome.errors.push(format!(
                    "function boundary global budget left {} unit(s) unexamined",
                    plan.units.len() - index
                ));
                break;
            }
            let deadline = Instant::now() + unit_budget(remaining);
            let mut task = match &unit.step {
                Ok(task) => task.clone(),
                Err(message) => {
                    outcome.errors.push(message.clone());
                    continue;
                }
            };
            if !source_unchanged(&project.root, &unit.source, texts.get(&unit.source)) {
                outcome.errors.push(format!(
                    "function boundary source changed before analysis: {}",
                    unit.source
                ));
                continue;
            }
            if let Some(searches) = self.tooling_searches.get(&task.id).cloned() {
                // Frozen `gcc_standard_library_projection`: a runner exception is that unit's
                // `gcc-include-probe-exception` projection error and the run goes on with the
                // next unit (frozen names the exception type; native has the scheduler's error).
                let outcomes: BTreeMap<String, ici_execution::TaskOutcome> = match run_cpp_tasks(
                    &searches,
                    scheduler,
                    cancellation,
                    events,
                    &component.id,
                    "selected compiler library search",
                ) {
                    Ok(outcomes) => outcomes
                        .into_iter()
                        .map(|outcome| (outcome.task_id.clone(), outcome))
                        .collect(),
                    Err(failure) => {
                        outcome.errors.push(format!(
                            "function boundary GCC stdlib replay gcc-include-probe-exception: {}: \
                             GCC include search could not execute: {failure}",
                            unit.source
                        ));
                        continue;
                    }
                };
                let version = self
                    .compiler_versions
                    .get(&searches[0].argv[0])
                    .cloned()
                    .unwrap_or_default();
                // Frozen `_record_projection`: each probe once per run; a projection error is
                // written on its last probe when that record is new.
                let records = super::legacy_probe_evidence(&searches, &outcomes, &version);
                let count = records.len();
                let mut last_fresh = None;
                for (position, record) in records.into_iter().enumerate() {
                    if recorded.insert(record["argv"].to_string()) {
                        evidence.push(record);
                        if position + 1 == count {
                            last_fresh = Some(evidence.len() - 1);
                        }
                    }
                }
                match ici_cpp::gcc_standard_library_projection(
                    &outcomes[&searches[0].id],
                    &outcomes[&searches[1].id],
                    Path::new(&task.cwd),
                ) {
                    Ok(projection) => {
                        let position = task
                            .argv
                            .iter()
                            .position(|argument| argument == "{observed-gcc-stdlib}")
                            .expect("planned projection placeholder");
                        task.argv
                            .splice(position..=position, projection.arguments.clone());
                    }
                    Err(error) => {
                        let (code, message) = legacy_tooling::legacy_gcc_projection_error(
                            &outcomes[&searches[0].id],
                            &outcomes[&searches[1].id],
                            &error,
                        );
                        if let Some(last) = last_fresh {
                            evidence[last]["error"] = json!(message);
                        }
                        outcome.errors.push(format!(
                            "function boundary GCC stdlib replay {code}: {}: {message}",
                            unit.source
                        ));
                        continue;
                    }
                }
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                outcome.errors.push(format!(
                    "function boundary unit budget expired: {}",
                    unit.source
                ));
                continue;
            }
            task.timeout_seconds = Some(remaining.as_secs_f64());
            // Frozen records the command that did not run with its reason and no exit.
            let unrun = |message: &str| {
                json!({"name":"clang-tidy function boundaries","path":plan.executable,
                    "version":plan.version,"argv":task.argv,"returncode":null,
                    "timed_out":false,"truncated":false,"error":message})
            };
            if executable_identity(Path::new(&plan.executable)).ok() != plan.identity {
                let message = format!(
                    "function boundary tool identity changed before execution: {}",
                    unit.source
                );
                evidence.push(unrun(&message));
                outcome.errors.push(message);
                continue;
            }
            let ran = match run_cpp_tasks(
                std::slice::from_ref(&task),
                scheduler,
                cancellation,
                events,
                &component.id,
                "compiler function boundaries",
            ) {
                Ok(outcomes) => outcomes.into_iter().next().expect("one boundary outcome"),
                Err(failure) => {
                    let message = format!(
                        "function boundary tool could not execute: {}: {failure}",
                        unit.source
                    );
                    evidence.push(unrun(&message));
                    outcome.errors.push(message);
                    continue;
                }
            };
            let failed =
                !ran.completed() || ran.timed_out || ran.truncated || ran.exit_code != Some(0);
            let error = if failed {
                Some(format!("function boundary tool failed for {}", unit.source))
            } else if !source_unchanged(&project.root, &unit.source, texts.get(&unit.source)) {
                Some(format!(
                    "function boundary source changed during analysis: {}",
                    unit.source
                ))
            } else {
                let stdout = String::from_utf8_lossy(&ran.stdout);
                let stderr = String::from_utf8_lossy(&ran.stderr);
                match parse_function_boundaries_until(
                    &project.root,
                    Path::new(&task.cwd),
                    &stdout,
                    &stderr,
                    &unit.configuration,
                    &unit.source,
                    &mapped[&unit.source],
                    deadline,
                ) {
                    Ok((boundaries, macros)) => {
                        collected.extend(boundaries);
                        excluded.extend(macros);
                        checked.insert(unit.source.clone());
                        successful
                            .entry(unit.source.clone())
                            .or_default()
                            .insert(unit.configuration.clone());
                        outcome.configurations_checked += 1;
                        None
                    }
                    Err(message) => Some(format!(
                        "function boundary output rejected for {}: {message}",
                        unit.source
                    )),
                }
            };
            evidence.push(legacy_tooling::process_evidence(
                "clang-tidy function boundaries",
                &plan.version,
                &task.argv,
                &ran,
                error.as_deref().unwrap_or(""),
            ));
            outcome.errors.extend(error);
            if collected.len() > ici_cpp::legacy_boundaries::MAX_BOUNDARIES {
                outcome
                    .errors
                    .push("function boundary count exceeds the bounded limit".into());
                break;
            }
        }
        outcome.macro_functions_excluded = excluded.len() as u64;
        outcome.sources_checked = checked.len() as u64;
        if outcome.errors.is_empty() {
            let (boundaries, warnings) = merge_boundaries(collected, &successful);
            outcome.mode = if warnings.is_empty() {
                "exact"
            } else {
                "partial"
            }
            .into();
            outcome.boundaries = boundaries;
            outcome.warnings = warnings;
        } else {
            outcome.mode = "error".into();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Frozen `_UNIT_TIMEOUT_SECONDS = 120.0`, `_GLOBAL_TIMEOUT_SECONDS = 600.0` and
    /// `min(_UNIT_TIMEOUT_SECONDS, remaining)` (_cpp_function_boundaries.py:73-74, 487).
    #[test]
    fn a_unit_gets_at_most_120_seconds_of_the_600_second_run_budget() {
        assert_eq!(UNIT_BUDGET, Duration::from_secs(120));
        assert_eq!(GLOBAL_BUDGET, Duration::from_secs(600));
        assert_eq!(unit_budget(GLOBAL_BUDGET), Duration::from_secs(120));
        assert_eq!(
            unit_budget(Duration::from_secs(30)),
            Duration::from_secs(30)
        );
    }

    /// Frozen `_executable_identity` changes with the file's metadata, not only its path.
    #[test]
    fn a_touched_executable_has_another_identity() {
        let directory =
            std::env::temp_dir().join(format!("ici-boundary-identity-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let tool = directory.join("clang-tidy");
        std::fs::write(&tool, "#!/bin/sh\n").unwrap();
        let before = executable_identity(&tool).unwrap();
        std::fs::write(&tool, "#!/bin/sh\nexit 0\n").unwrap();
        assert_ne!(executable_identity(&tool).unwrap(), before);
        std::fs::remove_dir_all(&directory).unwrap();
        assert!(executable_identity(&tool).is_err());
    }
}
