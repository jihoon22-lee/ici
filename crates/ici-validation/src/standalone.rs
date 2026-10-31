//! A plain C++ test executable exposes one process verdict, not assertion counts.
use crate::{
    ValidationError, ValidationPlan, planning,
    python::{finding, pair},
};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskKind};
use ici_execution::TaskOutcome;
use serde_json::json;
use std::{collections::BTreeMap, path::Path};

pub(crate) fn source(plan: &ValidationPlan) -> Result<&str, ValidationError> {
    let source = plan
        .context
        .options
        .executable_test_source
        .as_deref()
        .filter(|source| plan.context.source_files.iter().any(|path| path == source))
        .ok_or_else(|| {
            ValidationError(
                "plain C++ test requires one declared source in its captured source scope".into(),
            )
        })?;
    ici_domain::validation::relative_path(source, false)
        .map_err(|error| ValidationError(error.to_string()))?;
    Ok(source)
}
pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    source(plan)?;
    let request = &plan.context;
    if !request.tool.path.starts_with(&request.root) {
        return Err(ValidationError(
            "plain C++ test executable must lie inside the workspace".into(),
        ));
    }
    let args = if request.options.argv.is_empty() {
        vec![]
    } else {
        if Path::new(&request.options.argv[0]) != request.tool.path {
            return Err(ValidationError(
                "plain C++ suite argv must match the selected executable identity".into(),
            ));
        }
        request.options.argv[1..].to_vec()
    };
    let mut task = planning::task(request, "suite", args);
    task.kind = TaskKind::Test;
    task.input_refs
        .push(request.tool.path.display().to_string());
    plan.tasks.push(task);
    plan.limitations.push("one case represents the declared C++ executable's exit status; no internal assertion count was observed".into());
    Ok(())
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let path = source(plan)?;
    let id = format!("{}.suite", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .filter(|outcome| outcome.task_id == id)
        .ok_or_else(|| {
            ValidationError("plain C++ suite outcome is absent or has a different identity".into())
        })?;
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = path.into();
    if !planning::completed(outcome) || !matches!(outcome.exit_code, Some(0..=255)) {
        target.status = "ERROR".into();
        target.message =
            "plain C++ executable did not return a complete exit-status verdict".into();
        output.targets.push(target);
        return Err(ValidationError(
            "plain C++ executable did not complete; no test count was inferred".into(),
        ));
    }
    let passed = outcome.exit_code == Some(0);
    let verdict = if passed { "PASSED" } else { "FAILED" };
    target.status = if passed { "PASS" } else { "FAIL" }.into();
    target.message = format!(
        "executable exited {}",
        outcome.exit_code.expect("complete exit")
    );
    if !passed {
        output.observation.findings.push(finding(
            plan,
            "ici.cpp.executable-test.failed",
            &target.message,
            path,
            1,
        ));
    }
    output.targets.push(target);
    output
        .observation
        .measurements
        .push(pair("cpp-executable.cases", u64::from(passed), 1));
    output.extra["test_outcomes"] = json!({format!("cpp-executable:{path}"):verdict});
    output.extra["executable_test"] = json!({"source":path,"executable":plan.context.tool.path,"exit_code":outcome.exit_code,"stderr":String::from_utf8_lossy(&outcome.stderr).chars().take(4096).collect::<String>()});
    output.summary = format!("{}/1 C++ executables passed", u8::from(passed));
    Ok(())
}
