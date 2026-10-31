//! Explicit interpreter matrices; missing project runtimes never fall back to the core.
use crate::{RuntimePlan, ValidationError, ValidationKind, ValidationPlan, ValidationRequest};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskState};
use ici_execution::TaskOutcome;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
pub(crate) fn plan(
    request: &ValidationRequest,
    mut result: ValidationPlan,
) -> Result<ValidationPlan, ValidationError> {
    if !matches!(
        request.kind,
        ValidationKind::PythonRuntime | ValidationKind::PythonImports
    ) {
        return Err(ValidationError(
            "runtime matrices are only valid for compatibility/import checks".into(),
        ));
    }
    if request.options.runtimes.len() > 32 {
        return Err(ValidationError(
            "runtime matrix exceeds 32 interpreters".into(),
        ));
    }
    let mut names = BTreeSet::new();
    for runtime in &request.options.runtimes {
        if runtime.configured.is_empty() || !names.insert(runtime.configured.clone()) {
            return Err(ValidationError(
                "configured runtimes must have unique nonempty names".into(),
            ));
        }
    }
    let required = request
        .options
        .required_interpreters
        .iter()
        .collect::<BTreeSet<_>>();
    if required.len() != request.options.required_interpreters.len()
        || required
            .iter()
            .any(|runtime| !names.contains(runtime.as_str()))
    {
        return Err(ValidationError(
            "required_interpreters must be a unique subset of configured runtimes".into(),
        ));
    }
    for (index, runtime) in request.options.runtimes.iter().enumerate() {
        let required = runtime.required || required.contains(&runtime.configured);
        let child = if let Some(tool) = &runtime.tool {
            let mut request = request.clone();
            request.task_id = format!("{}.runtime{index}", request.task_id);
            request.tool = tool.clone();
            request.options.runtimes.clear();
            request.options.required_interpreters.clear();
            request.owned_run_dir = request.owned_run_dir.join(format!("runtime{index}"));
            let mut child = crate::planning::plan(&request)?;
            // Child bytecode directories are owned beneath the shared fresh run capability.
            for task in &mut child.tasks {
                if !task
                    .work_dirs
                    .contains(&child.context.owned_run_dir.to_string_lossy().into_owned())
                {
                    task.work_dirs
                        .push(child.context.owned_run_dir.to_string_lossy().into_owned());
                }
            }
            result.tasks.extend(child.tasks.iter().cloned());
            if !required {
                result
                    .optional_task_ids
                    .extend(child.tasks.iter().map(|task| task.id.clone()));
            }
            result.prerequisites.extend(child.prerequisites.clone());
            result.artifacts.extend(child.artifacts.iter().cloned());
            Some(Box::new(child))
        } else {
            result.limitations.push(format!(
                "configured {} runtime {:?} is unavailable",
                if required { "required" } else { "optional" },
                runtime.configured
            ));
            None
        };
        result.runtime_plans.push(RuntimePlan {
            configured: runtime.configured.clone(),
            required,
            plan: child,
        });
    }
    Ok(result)
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &crate::CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let mut states = vec![];
    let mut required_failed = false;
    let mut completed = 0;
    let anchor = plan
        .context
        .source_files
        .first()
        .map(String::as_str)
        .unwrap_or("ici.toml");
    for runtime in &plan.runtime_plans {
        let Some(child) = &runtime.plan else {
            let mut target = InspectionTarget::at(anchor, 1);
            target.target_name = format!("Runtime: {}", runtime.configured);
            target.status = if runtime.required { "ERROR" } else { "WARN" }.into();
            target.message = format!(
                "configured {} interpreter is unavailable",
                if runtime.required {
                    "required"
                } else {
                    "optional"
                }
            );
            target
                .metrics
                .insert("required".into(), json!(runtime.required));
            // The runtime matrix owns the one located unavailability target; the dependent
            // import matrix only carries its state (frozen reports one Unavailable target).
            if plan.kind == ValidationKind::PythonRuntime {
                output.targets.push(target);
            }
            required_failed |= runtime.required;
            states.push(json!({"configured":runtime.configured,"required":runtime.required,"state":"unavailable"}));
            continue;
        };
        let mut child = (**child).clone();
        child.source_coordinates = plan.source_coordinates.clone();
        if child.context.options.requires_python.is_none() {
            child.context.options.requires_python = plan.context.options.requires_python.clone();
        }
        let mut observed = crate::python::observe(&child, outcomes, artifacts);
        if observed.observation.evidence_is_complete() {
            completed += 1;
        } else if runtime.required {
            required_failed = true;
        }
        for finding in &mut observed.observation.findings {
            if !runtime.required && !finding.tags.iter().any(|tag| tag == "advisory") {
                finding.tags.push("advisory".into());
            }
            finding.tags.push(format!("runtime:{}", runtime.configured));
        }
        for target in &mut observed.targets {
            target.target_name = format!("[{}] {}", runtime.configured, target.target_name);
            target
                .metrics
                .insert("runtime".into(), json!(runtime.configured));
            target
                .metrics
                .insert("required".into(), json!(runtime.required));
        }
        states.push(json!({"configured":runtime.configured,"required":runtime.required,"state":observed.observation.state,"findings":observed.observation.findings.len(),"runtime":observed.extra.get("runtime"),"imports":observed.extra.get("imports")}));
        output
            .observation
            .findings
            .extend(observed.observation.findings);
        output
            .observation
            .measurements
            .extend(observed.observation.measurements);
        output
            .observation
            .tool_versions
            .extend(observed.observation.tool_versions);
        output.observation.limitations.extend(
            observed
                .observation
                .limitations
                .into_iter()
                .map(|message| format!("{}: {message}", runtime.configured)),
        );
        output.targets.extend(observed.targets);
    }
    output.extra["runtime_matrix"] = json!(states);
    output.summary = format!(
        "{completed}/{} configured runtimes produced complete evidence",
        plan.runtime_plans.len()
    );
    if required_failed {
        return Err(ValidationError(
            "one or more required project runtimes could not be verified".into(),
        ));
    }
    if completed == 0 {
        output.observation.state = TaskState::Failed;
        return Err(ValidationError(
            "no configured project runtime produced measured evidence".into(),
        ));
    }
    Ok(())
}
