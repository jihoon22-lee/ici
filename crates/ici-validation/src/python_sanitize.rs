//! The project's pytest suite promotes ResourceWarning; unrelated test failures stay incomplete.
use crate::{ValidationError, ValidationPlan, planning, python};
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.suite", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("Python ResourceWarning suite did not run".into()))?;
    if !planning::completed(outcome) {
        return Err(ValidationError(
            "Python ResourceWarning suite did not complete".into(),
        ));
    }
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("Python ResourceWarning stdout is not UTF-8".into()))?;
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("Python ResourceWarning stderr is not UTF-8".into()))?;
    let evidence = python::parse_pytest(stdout, &plan.context)?;
    let executed = evidence
        .cases
        .values()
        .filter(|case| {
            matches!(
                case.verdict.as_str(),
                "PASSED" | "FAILED" | "XFAIL" | "XPASS"
            )
        })
        .count();
    if executed == 0 || !matches!(outcome.exit_code, Some(0 | 1)) {
        return Err(ValidationError(
            "Python ResourceWarning check has no completed executed test scope".into(),
        ));
    }
    let anchor = plan
        .context
        .targets
        .first()
        .map(String::as_str)
        .unwrap_or("tests");
    if outcome.exit_code == Some(0) {
        let mut target = InspectionTarget::at(anchor, 1);
        target.target_name = "PythonResourceWarnings".into();
        target.message = "pytest completed with ResourceWarning promoted to errors".into();
        output.targets.push(target);
    } else {
        let text = format!("{stdout}\n{stderr}");
        if !text.contains("ResourceWarning") {
            return Err(ValidationError("Pytest failed without ResourceWarning evidence; this does not measure a resource violation".into()));
        }
        let location =
            Regex::new(r"(?m)(.*?\.py):([1-9]\d*):[^\n]*ResourceWarning").expect("constant regex");
        let mut seen = BTreeSet::new();
        for capture in location.captures_iter(&text) {
            let Ok(path) =
                planning::relative(capture[1].trim(), &plan.context.root, &plan.context.cwd)
            else {
                output.observation.limitations.push("external ResourceWarning source location could not be attributed inside this workspace".into());
                continue;
            };
            let line = capture[2]
                .parse::<u32>()
                .map_err(|_| ValidationError("ResourceWarning line exceeds bounds".into()))?;
            if seen.insert((path.clone(), line)) {
                add_issue(
                    plan,
                    output,
                    &path,
                    line,
                    "ResourceWarning was promoted to an exception by the sanitizer",
                );
            }
        }
        if output.observation.findings.is_empty() {
            let snippet = text
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .chars()
                .take(300)
                .collect::<String>();
            add_issue(plan, output, anchor, 1, &snippet);
        }
    }
    output.extra["python_resource_warnings"] =
        json!({"state":"MEASURED","executed":executed,"issues":output.observation.findings.len()});
    output.observation.measurements.push(Measurement::measured(
        "sanitize.python_tests",
        executed as f64,
    ));
    output.summary = if output.observation.findings.is_empty() {
        "Python ResourceWarning check completed without defects".into()
    } else {
        format!(
            "{} Python ResourceWarning defect(s)",
            output.observation.findings.len()
        )
    };
    Ok(())
}
fn add_issue(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
    path: &str,
    line: u32,
    message: &str,
) {
    let mut target = InspectionTarget::at(path, line);
    target.status = "FAIL".into();
    target.target_name = "ResourceWarning".into();
    target.message = message.into();
    let mut finding = python::finding(
        plan,
        "sanitize.python-resource-warning",
        message,
        path,
        line,
    );
    finding.category = "resource".into();
    finding.confidence = "exact".into();
    output.observation.findings.push(finding);
    output.targets.push(target);
}
