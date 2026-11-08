//! Explicit preparation has two bounded stages: native build context, then owned artifact copies.
use crate::{
    commands::{CliError, Result},
    events::EventLog,
    planning::{self, PreparedPlan},
    registry::Invocation,
};
use ici_application::{
    plan::{PlannedCheck, Work},
    snapshot::{InputLimits, InputSnapshot},
};
use ici_domain::{AnalysisOutput, TaskState};
use ici_execution::{Cancellation, Scheduler};
use std::collections::{BTreeMap, BTreeSet};

pub struct Runtime<'a> {
    pub invocation: &'a Invocation,
    pub environment: &'a BTreeMap<String, String>,
    pub run_id: &'a str,
    pub static_only: bool,
    pub scheduler: &'a Scheduler,
    pub cancellation: &'a Cancellation,
    pub events: &'a EventLog,
}
pub struct PreparedExecution {
    pub prepared: PreparedPlan,
    pub snapshot: InputSnapshot,
    pub intake_ok: bool,
    pub outputs: BTreeMap<String, AnalysisOutput>,
    /// Holding these guards protects every observed shadow until final input verification.
    pub contexts: Vec<crate::export_context::PreparedContext>,
    pub limitations: Vec<String>,
}
/// Bind measured compiler outputs to the same release writer used by Python components.
pub fn bind_compiled_artifacts(
    prepared: &mut PreparedPlan,
    contexts: &[crate::export_context::PreparedContext],
    environment: &BTreeMap<String, String>,
) -> Result<()> {
    if contexts.is_empty() || prepared.plan.profile == ici_domain::Profile::Fast {
        return Ok(());
    }
    for component in &prepared.project.components {
        // The isolated link-reachability build answers the linker scope and is never an output.
        if !contexts.iter().any(|context| {
            context.plan.component_id == component.id
                && context.plan.build.variant != "link-reachability"
        }) {
            continue;
        }
        let packaging = prepared
            .plan
            .checks
            .iter()
            .position(|check| {
                check.component_id == component.id && check.check.id == "python.artifact"
            })
            .or_else(|| {
                prepared.plan.checks.iter().position(|check| {
                    check.component_id == component.id && check.check.id == "cpp.artifact"
                })
            });
        let Some(index) = packaging else { continue };
        let check = &mut prepared.plan.checks[index];
        // Frozen adapter builds (CMake, qmake, Make) publish the release tree and loaders only;
        // the direct g++ build publishes its binary into bin/. Next-schema projects (which the
        // frozen pyz rejects) keep publishing their measured binaries.
        let adapter = contexts
            .iter()
            .find(|context| {
                prepared.project.schema_family == ici_config::SchemaFamily::Legacy
                    && context.plan.component_id == component.id
                    && matches!(
                        context.plan.build.system.as_str(),
                        "cmake" | "qmake" | "make"
                    )
            })
            .map(|context| context.plan.clone());
        let build =
            crate::export_context::compiled_artifacts(&prepared.project, component, contexts)
                .and_then(|artifacts| {
                    if let Some(adapter) = &adapter {
                        crate::artifact_build::describe_adapter(
                            &prepared.project,
                            component,
                            &check.task_id,
                            environment,
                            artifacts.len(),
                        )
                        .map(|mut build| {
                            build.lead_targets.push(crate::cpp::legacy_adapter_success(
                                &prepared.project.root,
                                &component.root,
                                &adapter.build.system,
                                &adapter.descriptor,
                                crate::cpp::legacy_shadow_linked_outputs(&crate::files::under(
                                    &prepared.project.root,
                                    &adapter.build.shadow,
                                ))
                                .unwrap_or(artifacts.len()),
                            ));
                            build
                        })
                    } else {
                        crate::artifact_build::describe_compiled(
                            &prepared.project,
                            component,
                            &check.task_id,
                            environment,
                            &artifacts,
                        )
                        .map(|mut build| {
                            build.legacy_binary_message = legacy_direct_binary_message(
                                prepared.project.schema_family,
                                component,
                                contexts,
                            );
                            build
                        })
                    }
                });
        match build {
            Ok(build) => {
                check.work = Work::native_prepare_after(
                    build.version_probe.iter().cloned().collect(),
                    build.task.clone(),
                );
                prepared
                    .artifact_builds
                    .insert(check.task_id.clone(), build);
            }
            Err(reason)
                if prepared.legacy_projection
                    && check.check.id == "cpp.artifact"
                    && reason.starts_with("declared artifact") =>
            {
                // Frozen `_publish_declared_artifacts`: a broken [build.artifacts] declaration is
                // a BuildError (ERROR, exit 1), never an incomplete or passing build.
                let message = format!("Declared artifacts could not be recorded: {reason}");
                prepared.cpp.fail_preparation(
                    &check.task_id,
                    crate::cpp::build_failure(
                        &check.task_id,
                        ".",
                        "ici.toml",
                        true,
                        &message,
                        &message,
                    ),
                );
                check.check.needs.clear();
                check.work = Work::Internal;
                prepared.artifact_builds.remove(&check.task_id);
            }
            Err(reason) => {
                check.work = Work::Blocked {
                    reason: format!("compiled artifact preparation: {reason}"),
                };
                prepared.artifact_builds.remove(&check.task_id);
            }
        }
    }
    prepared.graph = ici_application::graph::TaskGraph::build(&prepared.plan.checks)
        .map_err(|error| CliError(error.to_string()))?;
    Ok(())
}
/// Frozen BuildEngine direct g++ success: "Compiled C++17 binary (<seconds>s)", here the summed
/// wall time of the observed compile and link tasks.
fn legacy_direct_binary_message(
    family: ici_config::SchemaFamily,
    component: &ici_config::Component,
    contexts: &[crate::export_context::PreparedContext],
) -> Option<String> {
    if family != ici_config::SchemaFamily::Legacy {
        return None;
    }
    let context = contexts.iter().find(|context| {
        context.plan.component_id == component.id && context.plan.build.system == "direct-gcc"
    })?;
    let seconds: f64 = context
        .evidence
        .as_ref()
        .and_then(|evidence| evidence.get("outcomes"))
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|outcome| {
            outcome
                .get("duration_seconds")
                .and_then(serde_json::Value::as_f64)
        })
        .sum();
    Some(format!("Compiled C++17 binary ({seconds:.2}s)"))
}
/// Frozen `prepare_qmake_compilation_context` / `prepare_cmake_compilation_context` (core/
/// qmake_context.py, core/cmake_context.py): a legacy `verify` whose canonical release analysis
/// database could not be prepared keeps running without a database and records why. `None`
/// when frozen would have produced a database (a failed build after a configured CMake cache,
/// or a captured database whose contents are incomplete): those stay native failures.
pub(crate) fn legacy_context_fallback(
    plan: &crate::export_context::ContextPreparationPlan,
    failure: &crate::export_context::PreparationFailure,
) -> Option<ici_cpp::ContextDiagnostic> {
    // The project's own database is the analysis context: a failed BuildEngine release build
    // is no preparation diagnostic of it.
    if plan.direct.is_some() || plan.build.variant != "release" || plan.existing_database {
        return None;
    }
    legacy_release_fallback(&plan.build.system, failure)
}

/// The native preparation could not start its processes at all (the scheduler refused the
/// preparation task set): frozen's `configure` raising before any session result.
pub(crate) const PREPARATION_NOT_STARTED: &str = "native preparation could not start";

/// [`legacy_context_fallback`]'s diagnostic for a release analysis database of `system`.
fn legacy_release_fallback(
    system: &str,
    failure: &crate::export_context::PreparationFailure,
) -> Option<ici_cpp::ContextDiagnostic> {
    use crate::export_context::PreparationFailure;
    let diagnostic = |code: &str, message: &str, level: &str| ici_cpp::ContextDiagnostic {
        code: code.into(),
        message: message.into(),
        level: level.into(),
        entry_index: None,
        source: String::new(),
    };
    let unavailable = || {
        diagnostic(
            "qmake-capture-unavailable",
            "Exact qmake compiler capture could not be produced safely; analysis remains lower confidence.",
            "warning",
        )
    };
    let loaded = matches!(failure, PreparationFailure::Incomplete(reason)
        if reason.starts_with("prepared compilation evidence is incomplete"));
    match (system, failure) {
        (_, _) if loaded => None,
        ("qmake", PreparationFailure::Observed { phase, .. }) => Some(match phase.as_str() {
            // The canonical probe configure (frozen `probe.configured` false).
            "qmake probe" => diagnostic(
                "qmake-configure-failed",
                "Canonical qmake configure did not complete.",
                "error",
            ),
            // The recorder-wrapped configure: the helper's own refusal (frozen
            // `_qmake_compilers` cannot resolve one compiler program) is an unavailable capture.
            "qmake configure" if failure.to_string().contains("ici qmake configure capture:") => {
                unavailable()
            }
            "qmake configure" => diagnostic(
                "qmake-capture-configure-failed",
                "Canonical qmake capture configure did not complete.",
                "error",
            ),
            "qmake compile" => diagnostic(
                "qmake-capture-build-failed",
                "The canonical qmake capture build did not complete.",
                "error",
            ),
            _ => unavailable(),
        }),
        // Frozen catches every capture/journal error as an unavailable capture.
        ("qmake", PreparationFailure::Incomplete(_)) => Some(unavailable()),
        ("cmake", PreparationFailure::Observed { phase, .. }) if phase == "cmake configure" => {
            Some(diagnostic(
                "cmake-configure-failed",
                "Canonical CMake configure did not complete.",
                "error",
            ))
        }
        // Frozen cmake_context.py:197-202: `configure` raising OSError/RuntimeError/ValueError.
        ("cmake", PreparationFailure::Incomplete(reason))
            if reason.starts_with(PREPARATION_NOT_STARTED) =>
        {
            Some(diagnostic(
                "cmake-configure-error",
                "Canonical CMake configure could not start.",
                "error",
            ))
        }
        ("cmake", PreparationFailure::Incomplete(_)) => Some(diagnostic(
            "cmake-configure-failed",
            "Canonical CMake configure did not complete.",
            "error",
        )),
        _ => None,
    }
}

/// Frozen `BuildEngine.run` (build.py:47-150) when a legacy adapter release session fails
/// before it builds: the result keeps the session's backend selection and process records as
/// tool evidence and the project metadata extras (`project_name`, `project_version`,
/// `target_path`, "Built into <version>/x86_64"), and its one finding is the adapter target's
/// (the native finding text is trimmed; the frozen target keeps stderr's trailing whitespace).
fn legacy_adapter_build_failure(
    observed: &mut AnalysisOutput,
    prepared: &PreparedPlan,
    plan: &crate::export_context::ContextPreparationPlan,
    task_id: &str,
    runtime: &Runtime<'_>,
) {
    if let Some(processes) = observed
        .extra
        .get("process_evidence")
        .and_then(serde_json::Value::as_array)
        .cloned()
        && let Some(component) = prepared
            .project
            .components
            .iter()
            .find(|component| component.id == plan.component_id)
    {
        observed.extra["tool_evidence"] = crate::cpp::legacy_build_session_evidence(
            &prepared.project.root,
            &component.root,
            &plan.build.system,
            &plan.descriptor,
            &processes,
        );
    }
    // The session records are the tool evidence now. The release plan was never bound (no
    // context), so its metadata is described here; frozen `project_version` runs the same
    // `git describe --tags --always` with the v1.0.0 default.
    if let Some(extra) = observed.extra.as_object_mut() {
        extra.remove("process_evidence");
    }
    let build = prepared
        .project
        .components
        .iter()
        .find(|component| component.id == plan.component_id)
        .and_then(|component| {
            crate::artifact_build::describe_adapter(
                &prepared.project,
                component,
                task_id,
                runtime.environment,
                0,
            )
            .ok()
        })
        .and_then(|build| match build.version_probe.clone() {
            None => Some(build),
            Some(probe) => {
                let schedule = runtime
                    .scheduler
                    .run_with_start(std::slice::from_ref(&probe), runtime.cancellation, |_| {})
                    .ok()?;
                let outcome = schedule
                    .outcomes
                    .iter()
                    .find(|outcome| outcome.task_id == probe.id)?;
                crate::artifact_build::apply_version_probe(&build, outcome).ok()
            }
        });
    if let Some(build) = build {
        observed.extra["project_name"] = serde_json::json!(build.metadata.name);
        observed.extra["project_version"] = serde_json::json!(build.metadata.version);
        observed.extra["target_path"] = serde_json::json!(build.root.join(&build.output_root));
        observed.extra["metrics_summary"] =
            serde_json::json!(format!("Built into {}", build.output_root));
    }
    let details: serde_json::Map<String, serde_json::Value> = observed
        .observation
        .findings
        .iter()
        .filter_map(|finding| {
            let target = observed.targets.iter().find(|target| {
                target.target_name == finding.native_rule_id
                    && target.message.trim() == finding.message
            })?;
            Some((
                finding.fingerprint.clone(),
                serde_json::json!({"legacy_path": target.file_path, "legacy_message": target.message}),
            ))
        })
        .collect();
    if !details.is_empty() {
        observed.extra["finding_details"] = serde_json::Value::Object(details);
    }
}

/// Frozen `BinaryCompatibilityEngine.run` (binary_compat.py:96-117) after the legacy release
/// build failed: no manifest was published, so with no `artifacts` selection the engine is the
/// not-applicable SKIP (or, when required, the "no published executable or shared-library
/// artifacts" ERROR). Frozen publishes manifests only from the build, test and sanitizer
/// sessions, so this holds once every preparation of the run has failed or built nothing
/// (`contexts`: the preparations that succeeded). `None` when a prepared session built
/// executables or artifacts, or a declaration could still publish one (Python packaging,
/// `[build.artifacts]`); those keep the blocked check.
fn legacy_binary_compat_without_manifests(
    prepared: &PreparedPlan,
    plan: &crate::export_context::ContextPreparationPlan,
    contexts: &[crate::export_context::PreparedContext],
) -> Option<AnalysisOutput> {
    if contexts.iter().any(|context| {
        !context.artifacts.is_empty()
            || context.test_executable.is_some()
            || !context.test_executables.is_empty()
    }) || prepared
        .plan
        .checks
        .iter()
        .any(|check| check.check.id == "python.artifact")
        || prepared
            .project
            .effective
            .pointer("/build/artifacts")
            .is_some()
    {
        return None;
    }
    let check = prepared.plan.checks.iter().find(|check| {
        check.component_id == plan.component_id && check.check.id == "cpp.binary-compat"
    })?;
    let component = prepared
        .project
        .components
        .iter()
        .find(|component| component.id == plan.component_id)?;
    let settings = component.settings.pointer("/engines/binary_compat");
    if settings
        .and_then(|settings| settings.get("artifacts"))
        .and_then(serde_json::Value::as_array)
        .is_some_and(|artifacts| !artifacts.is_empty())
    {
        return None;
    }
    let required = settings
        .and_then(|settings| settings.get("required"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    Some(crate::cpp::legacy_binary_compat_unavailable(
        &check.task_id,
        required.then_some("no published executable or shared-library artifacts"),
    ))
}
fn rebind(
    runtime: &Runtime<'_>,
    project: ici_config::ResolvedProject,
    original: &InputSnapshot,
    original_limits: &BTreeMap<String, usize>,
    contexts: &[crate::export_context::PreparedContext],
) -> Result<(PreparedPlan, InputSnapshot, bool)> {
    let overlay = crate::export_context::overlay(&project, contexts);
    let mut prepared = planning::prepare_resolved(
        runtime.invocation,
        overlay,
        runtime.environment,
        runtime.static_only,
        runtime.run_id,
    )?;
    prepared.cpp.attach_prepared(contexts);
    bind_compiled_artifacts(&mut prepared, contexts, runtime.environment)?;
    let mut roots: Vec<_> = contexts
        .iter()
        .flat_map(|context| context.plan.build.generated_roots.iter().cloned())
        .collect();
    // Packages ici published during preparation (verified ownership manifest) are
    // preparation outputs that later checks consume, not user edits made mid-run.
    // Unverifiable ownership adds nothing, so such files still count as drift.
    if let Ok(owned) = crate::artifact_build::owned_outputs(&prepared.project.root) {
        roots.extend(owned);
    }
    let after = crate::inputs::capture_preserving(&prepared, original, original_limits, &roots)?;
    let mut limits = crate::inputs::binary_limits(&prepared);
    limits.extend(original_limits.clone());
    let (snapshot, drift) = original
        .after_preparation_with_path_limits(&after, &roots, InputLimits::default(), &limits)
        .map_err(|error| CliError(error.to_string()))?;
    Ok((prepared, snapshot, drift.stable()))
}
pub fn execute(
    mut prepared: PreparedPlan,
    mut snapshot: InputSnapshot,
    mut intake_ok: bool,
    runtime: &Runtime<'_>,
) -> PreparedExecution {
    let mut initial_project = prepared.project.clone();
    let initial_pins = prepared.tool_pins();
    let initial_tools = prepared.tools.clone();
    let original = snapshot.clone();
    let original_limits = crate::inputs::binary_limits(&prepared);
    let mut contexts: Vec<crate::export_context::PreparedContext> = vec![];
    let mut limitations = vec![];
    let mut failures = vec![];
    let mut outputs = BTreeMap::new();
    let mut observed_failures = BTreeSet::new();
    // `cpp.artifact` checks whose configured legacy release build failed: their BuildEngine
    // result is that failure, so their own artifact preparation never runs.
    let mut adapter_failures = BTreeSet::new();
    // Legacy dead checks whose GNU ELF link-reachability preparation failed: they run and
    // report the frozen linker-scope error instead of being blocked.
    let mut legacy_linker_failures: Vec<(String, bool, Vec<serde_json::Value>)> = vec![];
    // Legacy adapter release preparations that failed before building: their binary_compat
    // check is decided once every other preparation has run.
    let mut compat_without_release: Vec<crate::export_context::ContextPreparationPlan> = vec![];
    // Legacy release analysis databases that could not be prepared: the source checks run
    // without a database and report the frozen preparation diagnostic.
    // Legacy direct coverage checks rebound to gcov over the notes a stopped compile left.
    let mut legacy_notes_only = BTreeSet::new();
    let mut legacy_fallbacks: Vec<(String, ici_cpp::ContextDiagnostic)> =
        if prepared.legacy_projection && !prepared.cpp.legacy_standalone {
            prepared.cpp.legacy_planned_fallbacks.clone()
        } else {
            vec![]
        };
    // Incomplete input is never a basis for preparing or building anything.
    let prepare = intake_ok && snapshot.uncaptured.is_empty();
    if prepare && !runtime.cancellation.is_cancelled() {
        for plan in &prepared.context_preparations {
            if runtime.cancellation.is_cancelled() {
                break;
            }
            let matching = contexts.iter().find(|context| {
                context.plan.root == plan.root
                    && context.plan.build.id == plan.build.id
                    && context.plan.build.variant == plan.build.variant
                    && context.plan.build.owner_digest == plan.build.owner_digest
            });
            let fresh = matching.is_none();
            let result = if let Some(context) = matching {
                crate::export_context::for_consumer(context, plan)
                    .map_err(crate::export_context::PreparationFailure::Incomplete)
            } else {
                crate::export_context::execute_preparation_shared(
                    plan,
                    runtime.scheduler,
                    runtime.cancellation,
                    runtime.events,
                    contexts
                        .iter()
                        .find_map(|context| context.shared_make_lease(plan)),
                )
            };
            match result {
                Ok(context) => {
                    if let Some(failure) = &context.build_failure {
                        limitations.push(format!(
                            "{} {} build failed; its configured compilation database is analysed like frozen, which never builds this shadow: {failure}",
                            plan.component_id, plan.build.variant
                        ));
                    }
                    // Frozen BuildEngine `_compile_cpp`: the failed adapter build is its own
                    // `BuildAdapter` FAIL, beside the source checks that keep the database;
                    // only the checks that consume release artifacts stop.
                    if let Some(failed) = &context.build_failure_output {
                        let failure = crate::export_context::PreparationFailure::Observed {
                            phase: format!("{} compile", plan.build.system),
                            output: failed.clone(),
                        };
                        if let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id
                                && check.check.id == "cpp.artifact"
                        }) && let Some(mut observed) = failure.observed_for(&check.task_id)
                        {
                            // Frozen BuildEngine's own release session failed at its build: the
                            // same adapter result (session evidence, project metadata) as a
                            // session that failed earlier, and binary_compat has no manifest.
                            if prepared.legacy_projection
                                && prepared.project.schema_family
                                    == ici_config::SchemaFamily::Legacy
                                && plan.build.variant == "release"
                                && plan.build.system == "cmake"
                            {
                                legacy_adapter_build_failure(
                                    &mut observed,
                                    &prepared,
                                    plan,
                                    &check.task_id,
                                    runtime,
                                );
                                compat_without_release.push(plan.clone());
                            }
                            if let Err(redaction) = crate::execute::redact(&mut observed) {
                                limitations
                                    .push(format!("build adapter evidence redaction: {redaction}"));
                            } else {
                                outputs.insert(check.task_id.clone(), observed);
                                observed_failures.insert(check.task_id.clone());
                                adapter_failures.insert(check.task_id.clone());
                            }
                        }
                        failures.push((
                            plan.component_id.clone(),
                            plan.build.variant.clone(),
                            failure.to_string(),
                            true,
                        ));
                    }
                    // Frozen `prepare_cmake_compilation_context` adds the cache metadata,
                    // generation and unity diagnostics to the canonical release context.
                    if fresh
                        && prepared.legacy_projection
                        && !prepared.cpp.legacy_standalone
                        && let Some(analysis) = &context.analysis
                    {
                        legacy_fallbacks.extend(
                            analysis
                                .diagnostics
                                .iter()
                                .map(|diagnostic| (plan.component_id.clone(), diagnostic.clone())),
                        );
                    }
                    contexts.push(context);
                }
                Err(failure) => {
                    let reason = failure.to_string();
                    // Frozen BuildEngine runs only the release session: a legacy test or
                    // sanitizer variant that fails is its own engine's result, not the build's.
                    if !(prepared.legacy_projection && plan.build.variant != "release")
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id
                                && check.check.id == "cpp.artifact"
                        })
                        && let Some(mut observed) = failure.observed_for(&check.task_id)
                    {
                        if prepared.legacy_projection
                            && plan.build.variant == "release"
                            && plan.build.system == "direct-gcc"
                        {
                            // Frozen BuildEngine summary for a failed direct g++ build.
                            observed.summary =
                                "Build did not produce a complete artifact set".into();
                        }
                        let adapter = prepared.legacy_projection
                            && prepared.project.schema_family == ici_config::SchemaFamily::Legacy
                            && plan.build.variant == "release"
                            && matches!(plan.build.system.as_str(), "cmake" | "qmake" | "make");
                        if adapter {
                            legacy_adapter_build_failure(
                                &mut observed,
                                &prepared,
                                plan,
                                &check.task_id,
                                runtime,
                            );
                            // Decided after every preparation ran: a later session may still
                            // publish a manifest.
                            compat_without_release.push(plan.clone());
                        }
                        if let Err(redaction) = crate::execute::redact(&mut observed) {
                            limitations
                                .push(format!("direct compiler evidence redaction: {redaction}"));
                        } else {
                            outputs.insert(check.task_id.clone(), observed);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen TestEngine `_run_cpp_tests_via_adapter`: a build of the test
                    // variant that fails is the test command's measured FAIL at the descriptor.
                    if matches!(plan.build.variant.as_str(), "coverage" | "test")
                        && plan.build.system != "direct-gcc"
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == "cpp.test"
                        })
                        && let Some(observed) = failure.observed_for(&check.task_id)
                        && let Some(mut output) = crate::cpp::test_build_failure(
                            &check.task_id,
                            &observed,
                            prepared.legacy_projection,
                        )
                    {
                        // Frozen `_run_cpp_tests_via_adapter` keeps the failed session's
                        // evidence: the backend selection and the processes it ran.
                        if prepared.legacy_projection
                            && let Some(processes) = observed
                                .extra
                                .get("process_evidence")
                                .and_then(serde_json::Value::as_array)
                            && let Some(component) = prepared
                                .project
                                .components
                                .iter()
                                .find(|component| component.id == plan.component_id)
                        {
                            output.extra["tool_evidence"] =
                                crate::cpp::legacy_build_session_evidence(
                                    &prepared.project.root,
                                    &component.root,
                                    &plan.build.system,
                                    &plan.descriptor,
                                    processes,
                                );
                        }
                        if let Err(redaction) = crate::execute::redact(&mut output) {
                            limitations.push(format!("test build evidence redaction: {redaction}"));
                        } else {
                            outputs.insert(check.task_id.clone(), output);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen TestEngine `_run_cpp_tests_via_adapter`: a coverage session that was
                    // not configured records the session's errors as tool errors (ERROR,
                    // NOT_RUN, no targets, TEM 0.00 / 5.0) and keeps its evidence: the backend
                    // selection and the processes it ran.
                    if prepared.legacy_projection
                        && matches!(plan.build.variant.as_str(), "coverage" | "test")
                        && matches!(plan.build.system.as_str(), "cmake" | "qmake" | "make")
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == "cpp.test"
                        })
                        && !outputs.contains_key(&check.task_id)
                        && let Some(observed) = failure.observed_for(&check.task_id)
                        && let Some(failed) = observed
                            .targets
                            .iter()
                            .find(|target| target.target_name == "BuildError")
                    {
                        let mut output = crate::cpp::legacy_test_configuration_error(
                            &check.task_id,
                            &failed.message,
                        );
                        if let Some(processes) = observed
                            .extra
                            .get("process_evidence")
                            .and_then(serde_json::Value::as_array)
                            && let Some(component) = prepared
                                .project
                                .components
                                .iter()
                                .find(|component| component.id == plan.component_id)
                        {
                            output.extra["tool_evidence"] =
                                crate::cpp::legacy_build_session_evidence(
                                    &prepared.project.root,
                                    &component.root,
                                    &plan.build.system,
                                    &plan.descriptor,
                                    processes,
                                );
                        }
                        if let Err(redaction) = crate::execute::redact(&mut output) {
                            limitations
                                .push(format!("test configure evidence redaction: {redaction}"));
                        } else {
                            outputs.insert(check.task_id.clone(), output);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen TestEngine compiles the library with every test: a library source
                    // that does not compile is a `Compilation Error` FAIL for each test.
                    if matches!(plan.build.variant.as_str(), "coverage" | "test")
                        && plan.build.system == "direct-gcc"
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == "cpp.test"
                        })
                        && let Some(observed) = failure.observed_for(&check.task_id)
                        && let Some(stderr) = observed
                            .extra
                            .get("compile_failure_stderr")
                            .and_then(serde_json::Value::as_str)
                        && let Some(direct) = plan.direct.as_ref()
                        && !direct.test_sources().is_empty()
                    {
                        let failures: crate::cpp::TestCompileFailures = direct
                            .test_sources()
                            .into_iter()
                            .map(|source| (source, stderr.to_owned()))
                            .collect::<std::collections::BTreeMap<_, _>>()
                            .into();
                        let mut output =
                            crate::cpp::test_compile_failures_only(&check.task_id, &failures);
                        if prepared.legacy_projection {
                            crate::cpp::legacy_direct_tests(&mut output, &failures);
                            // The compiles the failed phase ran (frozen records each test's).
                            if let Some(records) =
                                crate::execute::legacy_direct_compile_records(&observed.extra)
                                    .filter(|records| !records.is_empty())
                            {
                                output.extra["tool_evidence"] = serde_json::json!(records);
                            }
                        }
                        if crate::execute::redact(&mut output).is_ok() {
                            outputs.insert(check.task_id.clone(), output);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen `_collect_cpp_coverage` after `_compile_cpp_objects` stopped at a
                    // later production source: gcov still reads the notes the earlier sources
                    // left, then refuses the missing source (`incomplete_source_coverage`). The
                    // coverage check is rebound to run gcov over those notes without a suite.
                    if prepared.legacy_projection
                        && plan.build.system == "direct-gcc"
                        && let crate::export_context::PreparationFailure::Observed { phase, output } =
                            &failure
                        && phase == "compile"
                        && let Some(direct) = plan.direct.as_ref()
                        && let Some((notes, sources)) = direct.legacy_notes_before_failure(
                            &plan.build.id,
                            &plan.build.variant,
                            &output.extra["outcomes"],
                        )
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id
                                && check.check.id == "cpp.coverage"
                        })
                        && let Some(component) = initial_project
                            .components
                            .iter_mut()
                            .find(|component| component.id == plan.component_id)
                    {
                        component.settings["legacy_direct_coverage_notes"] = serde_json::json!({
                            "directory": plan.build.shadow,
                            "notes": notes,
                            "sources": sources,
                        });
                        legacy_notes_only.insert(check.task_id.clone());
                        if let Some(test) = prepared.plan.checks.iter().find(|test| {
                            test.component_id == plan.component_id && test.check.id == "cpp.test"
                        }) && let Some(output) = outputs.get_mut(&test.task_id)
                        {
                            crate::cpp::legacy_stopped_compile_suite(output, &test.task_id);
                        }
                    }
                    // Frozen SanitizeEngine/ThreadSanitizeEngine direct g++ run: a production
                    // source that does not compile fails every test's instrumented compile.
                    if prepared.legacy_projection
                        && plan.build.system == "direct-gcc"
                        && let Some(check_id) = match plan.build.variant.as_str() {
                            "sanitize" => Some("cpp.sanitize"),
                            "thread-sanitize" => Some("cpp.tsan"),
                            _ => None,
                        }
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == check_id
                        })
                        && let Some(observed) = failure.observed_for(&check.task_id)
                        && let Some(stderr) = observed
                            .extra
                            .get("compile_failure_stderr")
                            .and_then(serde_json::Value::as_str)
                        && let Some(direct) = plan.direct.as_ref()
                        && !direct.test_sources().is_empty()
                    {
                        let mut output = crate::cpp::legacy_sanitizer_compile_failures(
                            &check.task_id,
                            &direct.test_sources(),
                            stderr,
                        );
                        // Frozen records each test's failed `sanitizer compile` process (with
                        // its failure message) and runs nothing after it: here the failed
                        // native object compile shared by every test executable.
                        let failed = observed.extra["outcomes"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .find(|outcome| outcome["exit_code"] == 1)
                            .and_then(|outcome| {
                                observed.extra["compiler_tasks"]
                                    .as_array()?
                                    .iter()
                                    .find(|task| task["id"] == outcome["task_id"])
                            });
                        if let Some(task) = failed {
                            let records: Vec<serde_json::Value> = output
                                .targets
                                .iter()
                                .map(|target| {
                                    serde_json::json!({"name":"sanitizer compile",
                                        "path":task["argv"][0],"version":"","argv":task["argv"],
                                        "returncode":1,"timed_out":false,"truncated":false,
                                        "error":target.message})
                                })
                                .collect();
                            output.extra["tool_evidence"] = serde_json::json!(records);
                        }
                        if crate::execute::redact(&mut output).is_ok() {
                            outputs.insert(check.task_id.clone(), output);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen SanitizeEngine/ThreadSanitizeEngine `_prepare_sanitizer_adapter`: an
                    // adapter configure or build that fails is one `C++Sanitizer` ERROR scope at
                    // the descriptor with the session error (`_fail_adapter_scope`); nothing runs.
                    if prepared.legacy_projection
                        && plan.build.system != "direct-gcc"
                        && let Some(check_id) = match plan.build.variant.as_str() {
                            "sanitize" => Some("cpp.sanitize"),
                            "thread-sanitize" => Some("cpp.tsan"),
                            _ => None,
                        }
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == check_id
                        })
                        && let Some(observed) = failure.observed_for(&check.task_id)
                        && let Some(failed) = observed.targets.iter().find(|target| {
                            matches!(target.target_name.as_str(), "BuildAdapter" | "BuildError")
                        })
                    {
                        let mut output = crate::cpp::legacy_sanitizer_configuration_error(
                            &check.task_id,
                            &failed.file_path,
                            &failed.message,
                        );
                        output.observation.limitations = observed.observation.limitations.clone();
                        output.observation.limitations.push(failed.message.clone());
                        // The failed session's evidence: its backend selection and processes.
                        if let Some(processes) = observed
                            .extra
                            .get("process_evidence")
                            .and_then(serde_json::Value::as_array)
                            && let Some(component) = prepared
                                .project
                                .components
                                .iter()
                                .find(|component| component.id == plan.component_id)
                        {
                            output.extra["tool_evidence"] =
                                crate::cpp::legacy_build_session_evidence(
                                    &prepared.project.root,
                                    &component.root,
                                    &plan.build.system,
                                    &plan.descriptor,
                                    processes,
                                );
                        }
                        if crate::execute::redact(&mut output).is_ok() {
                            outputs.insert(check.task_id.clone(), output);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    // Frozen DeadCodeEngine GNU ELF linker scope (`_configure_shadow`): a CMake
                    // link-reachability configure or build that did not complete is the
                    // linker scope's own ERROR; the dead check reports it.
                    if prepared.legacy_projection
                        && plan.build.variant == "link-reachability"
                        && plan.build.system == "cmake"
                        && let Some(check) = prepared.plan.checks.iter().find(|check| {
                            check.component_id == plan.component_id && check.check.id == "cpp.dead"
                        })
                        && let Some(observed) = failure.observed_for(&check.task_id)
                    {
                        let configure = reason.starts_with("cmake configure");
                        let steps = if configure { 1 } else { 2 };
                        let version = plan
                            .build
                            .tasks
                            .first()
                            .and_then(|task| task.argv.first())
                            .and_then(|cmake| {
                                crate::cpp::legacy_tooling::legacy_version_banner(
                                    std::path::Path::new(cmake),
                                    &plan.root,
                                    runtime.environment,
                                    "cmake",
                                )
                            })
                            .unwrap_or_default();
                        let records = plan
                            .build
                            .tasks
                            .iter()
                            .take(steps)
                            .enumerate()
                            .map(|(index, task)| {
                                let failed = index + 1 == steps;
                                let code = if failed {
                                    observed.observation.exit_code
                                } else {
                                    Some(0)
                                };
                                serde_json::json!({"name": if index == 0 {
                                        "cmake linker-reachability configure"
                                    } else {
                                        "cmake linker-reachability build"
                                    },
                                    "path":task.argv.first(),"version":version,"argv":task.argv,
                                    "returncode":code,
                                    "timed_out":failed && observed.observation.timed_out,
                                    "truncated":failed && observed.observation.truncated,
                                    "error":""})
                            })
                            .collect();
                        legacy_linker_failures.push((check.task_id.clone(), configure, records));
                    }
                    limitations.push(format!(
                        "{} {} preparation: {reason}",
                        plan.component_id, plan.build.variant
                    ));
                    if prepared.legacy_projection
                        && !prepared.cpp.legacy_standalone
                        && let Some(diagnostic) = legacy_context_fallback(plan, &failure)
                    {
                        legacy_fallbacks.push((plan.component_id.clone(), diagnostic));
                    }
                    failures.push((
                        plan.component_id.clone(),
                        plan.build.variant.clone(),
                        reason,
                        // The project's own database keeps the source checks analysing.
                        plan.direct.is_some() || plan.existing_database,
                    ));
                }
            }
        }
        for plan in &compat_without_release {
            if let Some(mut compat) =
                legacy_binary_compat_without_manifests(&prepared, plan, &contexts)
                && crate::execute::redact(&mut compat).is_ok()
            {
                observed_failures.insert(compat.observation.task_id.clone());
                outputs.insert(compat.observation.task_id.clone(), compat);
            }
        }
        // Frozen `_with_capture_coverage`: a qmake capture that missed a production translation
        // unit is an error diagnostic of the captured context.
        if prepared.legacy_projection && !prepared.cpp.legacy_standalone {
            for held in contexts.iter().filter(|held| {
                held.plan.build.system == "qmake"
                    && held.plan.build.variant == "release"
                    && held.plan.direct.is_none()
                    && held.context.database_path.is_some()
            }) {
                let captured: BTreeSet<&String> =
                    held.context.units.iter().map(|unit| &unit.source).collect();
                let missing = held
                    .plan
                    .selected_sources
                    .iter()
                    .filter(|source| ici_cpp::is_translation_unit(source))
                    .filter(|source| !captured.contains(source))
                    .count();
                if missing > 0 {
                    legacy_fallbacks.push((
                        held.plan.component_id.clone(),
                        ici_cpp::ContextDiagnostic::error(
                            "qmake-capture-incomplete",
                            format!(
                                "The qmake capture missed {missing} production translation unit(s)."
                            ),
                        ),
                    ));
                }
            }
        }
        // Every C++ check binds with the preparation diagnostics in its component's context
        // (frozen consumers stop on an error diagnostic before any replay).
        for (component, diagnostic) in &legacy_fallbacks {
            if let Some(component) = initial_project
                .components
                .iter_mut()
                .find(|item| item.id == *component)
            {
                let entry = &mut component.settings["legacy_preparation_diagnostics"];
                if !entry.is_array() {
                    *entry = serde_json::json!([]);
                }
                entry
                    .as_array_mut()
                    .expect("diagnostic list")
                    .push(serde_json::json!(diagnostic));
            }
        }
        if !contexts.is_empty() || !legacy_fallbacks.is_empty() || !legacy_notes_only.is_empty() {
            match rebind(
                runtime,
                initial_project.clone(),
                &original,
                &original_limits,
                &contexts,
            ) {
                Ok((mut after, captured, stable)) => {
                    for (role, tool) in &initial_tools {
                        after
                            .tools
                            .entry(role.clone())
                            .or_insert_with(|| tool.clone());
                    }
                    prepared = after;
                    snapshot = captured;
                    intake_ok &= stable;
                    if !stable {
                        limitations.push("user inputs changed during native preparation; analysis retains the original captured bytes".into());
                    }
                }
                Err(reason) => {
                    intake_ok = false;
                    limitations.push(format!("prepared input capture: {reason}"));
                }
            }
        }
    }
    let mut executed_checks: Vec<PlannedCheck> = vec![];
    let native: Vec<_> = prepared
        .plan
        .checks
        .iter()
        .filter(|check| matches!(check.work, Work::NativePrepare { .. }))
        .cloned()
        .collect();
    if intake_ok && prepare {
        for mut check in native {
            if runtime.cancellation.is_cancelled() {
                break;
            }
            if adapter_failures.contains(&check.task_id) {
                continue;
            }
            let Some(build) = prepared.artifact_builds.get(&check.task_id).cloned() else {
                continue;
            };
            let (mut output, resolved) = crate::execute::execute_artifact_prepare(
                &build,
                check.tasks(),
                &snapshot,
                runtime.scheduler,
                runtime.cancellation,
                runtime.events,
            );
            for context in contexts
                .iter()
                .filter(|context| context.plan.component_id == check.component_id)
            {
                if let Some(evidence) = &context.evidence {
                    output.extra["preparation_evidence"][&context.plan.build.variant] =
                        evidence.clone();
                }
            }
            // Frozen BuildEngine `tool_evidence`: the adapter session's backend selection and the
            // configure/clean/build processes its release build ran.
            if prepared.legacy_projection
                && check.check.id == "cpp.artifact"
                && prepared.project.schema_family == ici_config::SchemaFamily::Legacy
                && let Some(session) = contexts.iter().find(|context| {
                    context.plan.component_id == check.component_id
                        && matches!(
                            context.plan.build.system.as_str(),
                            "cmake" | "qmake" | "make"
                        )
                        && context.plan.build.variant == "release"
                })
                && let Some(component) = prepared
                    .project
                    .components
                    .iter()
                    .find(|component| component.id == check.component_id)
            {
                output.extra["tool_evidence"] = crate::cpp::legacy_build_session_evidence(
                    &prepared.project.root,
                    &component.root,
                    &session.plan.build.system,
                    &session.plan.descriptor,
                    &session.process_evidence,
                );
            }
            // Frozen deep BuildEngine release session of an adapter backend (`ici verify`
            // only) publishes its linked shadow outputs into the analysis context, like the
            // test/sanitize sessions do.
            if prepared.legacy_projection
                && !prepared.cpp.legacy_standalone
                && check.check.id == "cpp.artifact"
                && let Some(session) = contexts.iter().find(|context| {
                    context.plan.component_id == check.component_id
                        && matches!(
                            context.plan.build.system.as_str(),
                            "cmake" | "qmake" | "make"
                        )
                        && context.plan.build.variant == "release"
                })
                && let Some(build) = session.plan.build.tasks.iter().find(|task| {
                    task.argv.get(1).map(String::as_str) == Some("--build")
                        || (session.plan.build.system != "cmake" && task.id.ends_with(":compile"))
                })
                && let Some(manifest) = crate::cpp::legacy_cmake_artifact_manifest(
                    &prepared.project.root,
                    &prepared.project.root.join(&session.plan.build.shadow),
                    "release",
                    &build.argv,
                    &format!("{}.build", session.plan.build.system),
                )
            {
                output.extra["legacy_artifact_manifests"] = serde_json::json!([manifest]);
            }
            if let Some(resolved) = resolved {
                check.work = Work::native_prepare_after(
                    resolved.version_probe.iter().cloned().collect(),
                    resolved.task.clone(),
                );
                prepared
                    .artifact_builds
                    .insert(check.task_id.clone(), resolved);
            }
            // Provider bytes have already been parsed; redact the complete structured output now.
            if let Err(reason) = crate::execute::redact(&mut output) {
                output = AnalysisOutput::new(&check.task_id, "ici.native-preparation");
                output.observation.state = TaskState::Failed;
                output
                    .observation
                    .limitations
                    .push(format!("preparation output serialization: {reason}"));
                output.status = "ERROR".into();
            }
            runtime.events.emit(
                "task.completed",
                Some(&check.task_id),
                Some(&check.component_id),
                &crate::execute::state_label(output.observation.state),
            );
            outputs.insert(check.task_id.clone(), output);
            executed_checks.push(check);
        }
        if !executed_checks.is_empty() {
            let resolved_builds = prepared.artifact_builds.clone();
            match rebind(
                runtime,
                initial_project,
                &original,
                &original_limits,
                &contexts,
            ) {
                Ok((mut after, captured, stable)) => {
                    for (role, tool) in &initial_tools {
                        after
                            .tools
                            .entry(role.clone())
                            .or_insert_with(|| tool.clone());
                    }
                    prepared = after;
                    snapshot = captured;
                    intake_ok &= stable;
                    if !stable {
                        limitations.push("user inputs changed while ici built artifacts; analysis retains the original captured bytes".into());
                    }
                }
                Err(reason) => {
                    intake_ok = false;
                    limitations.push(format!("artifact input capture: {reason}"));
                }
            }
            prepared.artifact_builds = resolved_builds;
            for executed in executed_checks {
                if let Some(check) = prepared
                    .plan
                    .checks
                    .iter_mut()
                    .find(|check| check.task_id == executed.task_id)
                {
                    *check = executed;
                }
            }
            match ici_application::graph::TaskGraph::build(&prepared.plan.checks) {
                Ok(graph) => prepared.graph = graph,
                Err(reason) => {
                    intake_ok = false;
                    limitations.push(format!("prepared output/dependency conflict: {reason}"));
                }
            }
        }
    }
    for check in &mut prepared.plan.checks {
        if observed_failures.contains(&check.task_id) {
            check.work = Work::Internal;
            prepared.graph.blocked.remove(&check.task_id);
        }
    }
    // A notes-only coverage check was graphed as blocked on the suite that never ran (now the
    // observed compile failures): schedule it after them, as frozen runs gcov after every test.
    if !legacy_notes_only.is_empty() {
        let blocked = std::mem::take(&mut prepared.graph.blocked);
        match ici_application::graph::TaskGraph::build(&prepared.plan.checks) {
            Ok(graph) => prepared.graph = graph,
            Err(reason) => {
                intake_ok = false;
                limitations.push(format!("prepared output/dependency conflict: {reason}"));
            }
        }
        for (id, reason) in blocked {
            if !legacy_notes_only.contains(&id) && !observed_failures.contains(&id) {
                prepared.graph.blocked.entry(id).or_insert(reason);
            }
        }
    }
    // Nothing was published for a failed adapter build, so no output ownership is verified.
    for task_id in &adapter_failures {
        prepared.artifact_builds.remove(task_id);
    }
    let mut legacy_linker_ids = BTreeSet::new();
    for (id, configure, records) in legacy_linker_failures {
        prepared
            .cpp
            .fail_legacy_linker_preparation(&id, configure, records);
        prepared.graph.blocked.remove(&id);
        legacy_linker_ids.insert(id);
    }
    if !legacy_linker_ids.is_empty() {
        for check in &mut prepared.plan.checks {
            if legacy_linker_ids.contains(&check.task_id) {
                check.work = Work::Internal;
            }
        }
        // The check was unbound (blocked) while its preparation was pending: schedule it.
        let blocked = std::mem::take(&mut prepared.graph.blocked);
        match ici_application::graph::TaskGraph::build(&prepared.plan.checks) {
            Ok(graph) => prepared.graph = graph,
            Err(reason) => {
                intake_ok = false;
                limitations.push(format!("prepared output/dependency conflict: {reason}"));
            }
        }
        for (id, reason) in blocked {
            if !legacy_linker_ids.contains(&id) {
                prepared.graph.blocked.entry(id).or_insert(reason);
            }
        }
    }
    for (component, variant, reason, direct) in failures {
        let fallback = variant == "release"
            && legacy_fallbacks
                .iter()
                .any(|(fallback, _)| *fallback == component);
        for check in &prepared.plan.checks {
            if check.component_id != component || check.check.language != "cpp" {
                continue;
            }
            // A failed legacy direct release compile, or the failed build of a configured legacy
            // CMake release context, is the BuildEngine's own failure; the source checks keep
            // their analysis like frozen. So does a legacy release analysis database that could
            // not be prepared: the source checks analyse without one.
            let affected = if (direct || fallback) && variant == "release" {
                matches!(
                    check.check.id.as_str(),
                    "cpp.artifact" | "cpp.binary-compat"
                )
            } else {
                crate::cpp::preparation_dependent(
                    &check.check.id,
                    &variant,
                    !check.tasks().is_empty(),
                )
            };
            if affected {
                if legacy_linker_ids.contains(&check.task_id) {
                    continue;
                }
                if observed_failures.contains(&check.task_id) {
                    prepared.graph.blocked.remove(&check.task_id);
                    continue;
                }
                if legacy_notes_only.contains(&check.task_id)
                    && !matches!(check.work, Work::Blocked { .. })
                {
                    continue;
                }
                prepared.graph.blocked.insert(
                    check.task_id.clone(),
                    format!("explicit {variant} preparation failed: {reason}"),
                );
            }
        }
    }
    let after_pins = prepared.tool_pins();
    if initial_pins.iter().any(|(path, digest)| {
        after_pins.get(path) != Some(digest)
            || ici_execution::process::executable_digest(std::path::Path::new(path))
                .ok()
                .as_ref()
                != Some(digest)
    }) {
        intake_ok = false;
        limitations.push("a selected tool changed during preparation; completed preparation remains partial evidence".into());
    }
    // Preserve completed preparation evidence even when later capture or graph validation fails.
    if !intake_ok {
        for output in outputs.values_mut() {
            output
                .observation
                .limitations
                .push("later input verification is incomplete".into());
        }
    }
    PreparedExecution {
        prepared,
        snapshot,
        intake_ok,
        outputs,
        contexts,
        limitations,
    }
}

#[cfg(test)]
mod tests {
    use super::{PREPARATION_NOT_STARTED, legacy_release_fallback};
    use crate::export_context::PreparationFailure;

    /// Frozen core/cmake_context.py:197-202, which no fixture reaches (the frozen `configure`
    /// raising before a session result; native refuses the equivalent inputs at planning):
    ///     except (OSError, RuntimeError, ValueError):
    ///         return _with_diagnostics(
    ///             existing,
    ///             _diagnostic("cmake-configure-error", "Canonical CMake configure could not start."),
    ///         )
    /// and its neighbour :203-207 for a configure that ran and did not complete.
    #[test]
    fn cmake_preparation_that_could_not_start_is_frozen_configure_error() {
        let started = |reason: &str| {
            legacy_release_fallback("cmake", &PreparationFailure::Incomplete(reason.into()))
                .map(|diagnostic| (diagnostic.code, diagnostic.message, diagnostic.level))
        };
        assert_eq!(
            started(&format!("{PREPARATION_NOT_STARTED}: invalid task graph")),
            Some((
                "cmake-configure-error".into(),
                "Canonical CMake configure could not start.".into(),
                "error".into()
            ))
        );
        assert_eq!(
            started("build lease is held by another run"),
            Some((
                "cmake-configure-failed".into(),
                "Canonical CMake configure did not complete.".into(),
                "error".into()
            ))
        );
        // A configured database whose contents are incomplete stays a native failure.
        assert_eq!(
            started("prepared compilation evidence is incomplete: []"),
            None
        );
    }
}
