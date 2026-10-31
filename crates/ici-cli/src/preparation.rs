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
    if !prepared.legacy_projection || prepared.plan.profile == ici_domain::Profile::Fast {
        return Ok(());
    }
    for component in &prepared.project.components {
        if !contexts
            .iter()
            .any(|context| context.plan.component_id == component.id)
        {
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
    let initial_project = prepared.project.clone();
    let initial_pins = prepared.tool_pins();
    let initial_tools = prepared.tools.clone();
    let original = snapshot.clone();
    let original_limits = crate::inputs::binary_limits(&prepared);
    let mut contexts: Vec<crate::export_context::PreparedContext> = vec![];
    let mut limitations = vec![];
    let mut failures = vec![];
    let mut outputs = BTreeMap::new();
    let mut observed_failures = BTreeSet::new();
    if intake_ok && !runtime.cancellation.is_cancelled() {
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
            let result = if let Some(context) = matching {
                crate::export_context::for_consumer(context, plan)
                    .map_err(crate::export_context::PreparationFailure::Incomplete)
            } else {
                crate::export_context::execute_preparation_typed(
                    plan,
                    runtime.scheduler,
                    runtime.cancellation,
                    runtime.events,
                )
            };
            match result {
                Ok(context) => contexts.push(context),
                Err(failure) => {
                    let reason = failure.to_string();
                    if let Some(check) = prepared.plan.checks.iter().find(|check| {
                        check.component_id == plan.component_id && check.check.id == "cpp.artifact"
                    }) && let Some(mut observed) = failure.observed_for(&check.task_id)
                    {
                        if prepared.legacy_projection
                            && plan.build.variant == "release"
                            && plan.build.system == "direct-gcc"
                        {
                            // Frozen BuildEngine summary for a failed direct g++ build.
                            observed.summary =
                                "Build did not produce a complete artifact set".into();
                        }
                        if let Err(redaction) = crate::execute::redact(&mut observed) {
                            limitations
                                .push(format!("direct compiler evidence redaction: {redaction}"));
                        } else {
                            outputs.insert(check.task_id.clone(), observed);
                            observed_failures.insert(check.task_id.clone());
                        }
                    }
                    limitations.push(format!(
                        "{} {} preparation: {reason}",
                        plan.component_id, plan.build.variant
                    ));
                    failures.push((
                        plan.component_id.clone(),
                        plan.build.variant.clone(),
                        reason,
                    ));
                }
            }
        }
        if !contexts.is_empty() {
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
    if intake_ok {
        for mut check in native {
            if runtime.cancellation.is_cancelled() {
                break;
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
                &format!("{:?}", output.observation.state),
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
    for (component, variant, reason) in failures {
        for check in &prepared.plan.checks {
            if check.component_id != component || check.check.language != "cpp" {
                continue;
            }
            let affected = crate::cpp::preparation_dependent(
                &check.check.id,
                &variant,
                !check.tasks().is_empty(),
            );
            if affected {
                if observed_failures.contains(&check.task_id) {
                    prepared.graph.blocked.remove(&check.task_id);
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
