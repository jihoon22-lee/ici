use crate::{CapturedArtifacts, ValidationError, ValidationPlan, planning, python::finding};
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement, TaskKind};
use ici_execution::TaskOutcome;
use serde_json::json;
use std::{collections::BTreeMap, path::Path};
pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    if request.options.argv.is_empty() || Path::new(&request.options.argv[0]) != request.tool.path {
        return Err(ValidationError(
            "integration argv must name its selected executable identity".into(),
        ));
    }
    if !(0..=255).contains(&request.options.expected_exit) {
        return Err(ValidationError(
            "integration expected exit must be 0..255".into(),
        ));
    }
    let mut task = planning::task(request, "case", request.options.argv[1..].to_vec());
    task.kind = TaskKind::Test;
    task.requires = request.options.requires.clone();
    for output in &request.options.outputs {
        let path = if output.path.is_absolute() {
            output.path.clone()
        } else {
            request.cwd.join(&output.path)
        };
        if !path.starts_with(&request.root)
            || path.components().any(|part| {
                !matches!(
                    part,
                    std::path::Component::RootDir | std::path::Component::Normal(_)
                )
            })
        {
            return Err(ValidationError(
                "integration output must be normalized inside workspace".into(),
            ));
        }
        task.output_specs.push(path.to_string_lossy().into_owned());
    }
    plan.tasks.push(task);
    if !request.options.requires.is_empty() {
        plan.limitations.push(format!(
            "case depended on declared services/resources: {}",
            request.options.requires.join(", ")
        ));
    }
    Ok(())
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.case", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("integration case did not run".into()))?;
    if !planning::completed(outcome) {
        return Err(ValidationError(
            "integration case did not complete; process contract is unverified".into(),
        ));
    }
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("integration stdout is not UTF-8".into()))?;
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("integration stderr is not UTF-8".into()))?;
    let anchor = plan
        .context
        .targets
        .first()
        .cloned()
        .unwrap_or_else(|| "ici.toml".into());
    let mut checks = vec![(
        "exit".to_string(),
        outcome.exit_code == Some(plan.context.options.expected_exit),
        format!(
            "expected exit {}, got {:?}",
            plan.context.options.expected_exit, outcome.exit_code
        ),
    )];
    for (name, text, must, must_not) in [
        (
            "stdout",
            stdout,
            &plan.context.options.stdout_contains,
            &plan.context.options.stdout_not_contains,
        ),
        (
            "stderr",
            stderr,
            &plan.context.options.stderr_contains,
            &plan.context.options.stderr_not_contains,
        ),
    ] {
        for needle in must {
            checks.push((
                format!("{name}-contains"),
                text.contains(needle),
                format!("{name} must contain {needle:?}"),
            ));
        }
        for needle in must_not {
            checks.push((
                format!("{name}-not-contains"),
                !text.contains(needle),
                format!("{name} must not contain {needle:?}"),
            ));
        }
    }
    for (index, assertion) in plan.context.options.outputs.iter().enumerate() {
        let key = format!("integration-output:{index}");
        let witness = artifacts.outputs.get(&key);
        let passed = witness.is_some_and(|witness| {
            witness.fresh && witness.size >= assertion.min_size && witness.producer_task_id == id
        });
        checks.push((
            "output".into(),
            passed,
            format!(
                "output {} must be a fresh regular file of at least {} bytes{}",
                assertion.path.display(),
                assertion.min_size,
                artifacts
                    .errors
                    .get(&key)
                    .map(|error| format!(": {error}"))
                    .unwrap_or_default()
            ),
        ));
    }
    for (aspect, passed, detail) in &checks {
        let mut target = InspectionTarget::at(&anchor, 1);
        target.target_name = format!("Process contract: {aspect}");
        target.status = if *passed { "PASS" } else { "FAIL" }.into();
        target.message = detail.clone();
        output.targets.push(target);
        if !passed {
            output.observation.findings.push(finding(
                plan,
                &format!("integration.{aspect}"),
                detail,
                &anchor,
                1,
            ));
        }
    }
    let mut metric = Measurement::measured("integration.assertions", checks.len() as f64);
    metric.numerator = Some(checks.iter().filter(|(_, passed, _)| *passed).count() as u64);
    metric.denominator = Some(checks.len() as u64);
    output.observation.measurements.push(metric);
    output.extra["assertions"] = json!(checks);
    output.summary = format!(
        "{} process contract assertions evaluated",
        output.targets.len()
    );
    Ok(())
}
