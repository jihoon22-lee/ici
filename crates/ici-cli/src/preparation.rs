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
use std::collections::BTreeMap;

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
        let build =
            crate::export_context::compiled_artifacts(&prepared.project, component, contexts)
                .and_then(|artifacts| {
                    crate::artifact_build::describe_compiled(
                        &prepared.project,
                        component,
                        &check.task_id,
                        environment,
                        &artifacts,
                    )
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
fn rebind(
    runtime: &Runtime<'_>,
    project: ici_config::ResolvedProject,
    original: &InputSnapshot,
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
    bind_compiled_artifacts(&mut prepared, contexts, runtime.environment)?;
    let after = crate::inputs::capture(&prepared)?;
    let roots: Vec<_> = contexts
        .iter()
        .flat_map(|context| context.plan.build.generated_roots.iter().cloned())
        .collect();
    let (snapshot, drift) = original
        .after_preparation_with_path_limits(
            &after,
            &roots,
            InputLimits::default(),
            &crate::inputs::binary_limits(&prepared),
        )
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
    let original = snapshot.clone();
    let mut contexts: Vec<crate::export_context::PreparedContext> = vec![];
    let mut limitations = vec![];
    let mut failures = vec![];
    let mut outputs = BTreeMap::new();
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
            } else {
                crate::export_context::execute_preparation(
                    plan,
                    runtime.scheduler,
                    runtime.cancellation,
                    runtime.events,
                )
            };
            match result {
                Ok(context) => contexts.push(context),
                Err(reason) => {
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
            match rebind(runtime, initial_project.clone(), &original, &contexts) {
                Ok((after, captured, stable)) => {
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
            match rebind(runtime, initial_project, &original, &contexts) {
                Ok((after, captured, stable)) => {
                    prepared = after;
                    snapshot = captured;
                    intake_ok &= stable;
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
    for (component, variant, reason) in failures {
        for check in &prepared.plan.checks {
            if check.component_id != component || check.check.language != "cpp" {
                continue;
            }
            let affected = match variant.as_str() {
                "coverage" => check.check.id == "cpp.coverage",
                "sanitize" => check.check.id == "cpp.sanitize",
                "tsan" => check.check.id == "cpp.tsan",
                _ => {
                    matches!(
                        check.check.id.as_str(),
                        "cpp.compile"
                            | "cpp.diagnostics"
                            | "cpp.tidy"
                            | "cpp.artifact"
                            | "cpp.binary-compat"
                    ) || (!check.tasks().is_empty()
                        && !matches!(
                            check.check.id.as_str(),
                            "cpp.test" | "cpp.coverage" | "cpp.sanitize" | "cpp.tsan"
                        ))
                }
            };
            if affected {
                prepared.graph.blocked.insert(
                    check.task_id.clone(),
                    format!("explicit {variant} preparation failed: {reason}"),
                );
            }
        }
    }
    let after_pins = prepared.tool_pins();
    if initial_pins
        .iter()
        .any(|(path, digest)| after_pins.get(path) != Some(digest))
    {
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
