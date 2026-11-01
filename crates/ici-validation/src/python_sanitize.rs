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
    // Frozen `_process_incomplete` / `_incomplete_message`: one `PythonResourceWarnings` ERROR
    // at `tests` naming how the run ended, snipping stderr (else stdout).
    if !planning::completed(outcome) {
        let tail = snippet(&String::from_utf8_lossy(if outcome.stderr.is_empty() {
            &outcome.stdout
        } else {
            &outcome.stderr
        }));
        let message = if outcome.state != ici_domain::TaskState::Succeeded
            && let Some(error) = &outcome.error
            && !outcome.timed_out
            && !outcome.cancelled
        {
            format!("Pytest could not execute: {error}")
        } else if outcome.timed_out {
            format!("Pytest ResourceWarning check timed out: {tail}")
        } else if outcome.truncated {
            format!("Pytest ResourceWarning check output was truncated: {tail}")
        } else {
            format!("Pytest ResourceWarning check terminated before producing a result: {tail}")
        };
        return scope_error(output, message);
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
    // Frozen `_missing_python_scope`: an interpreter without pytest, a suite that collected
    // nothing, or one that succeeded without a parseable result is one located
    // `PythonResourceWarnings` target at `tests` naming why (ERROR, or SKIP when not required).
    let text = format!("{stdout}\n{stderr}");
    let pytest_missing = outcome.exit_code != Some(0)
        && Regex::new(r#"No module named ['"]pytest['"]|No module named pytest"#)
            .expect("constant regex")
            .is_match(&text);
    let missing = if pytest_missing {
        Some(format!("Pytest is unavailable: {}", snippet(&text)))
    } else {
        // Frozen keys this on its executed-count regex for every exit code (a collection
        // error has no passed/failed count either).
        match outcome.exit_code {
            Some(5) => Some("Pytest collected 0 tests".to_owned()),
            _ if executed == 0 => {
                Some("Pytest returned success without parseable test results".to_owned())
            }
            _ => None,
        }
    };
    if let Some(message) = missing {
        return missing_scope(
            output,
            &message,
            plan.context.options.sanitize_scope_optional,
        );
    }
    // Frozen `_tool_failure_message`: a non-zero run without ResourceWarning evidence is a
    // tool failure, never a measured resource violation.
    let exit_code = outcome.exit_code.unwrap_or_default();
    if exit_code != 0 && !text.contains("ResourceWarning") {
        let tail = snippet(if stderr.is_empty() { stdout } else { stderr });
        return scope_error(
            output,
            format!("Pytest ResourceWarning check failed with exit code {exit_code}: {tail}"),
        );
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
            add_issue(plan, output, anchor, 1, &snippet(&text));
        }
    }
    output.extra["python_resource_warnings"] =
        json!({"state":"MEASURED","executed":executed,"issues":output.observation.findings.len()});
    output.observation.measurements.push(Measurement::measured(
        "sanitize.python_tests",
        executed as f64,
    ));
    // Frozen SanitizeEngine CLEAN_SUMMARY / "<n> Memory / Resource Defect(s) Detected".
    output.summary = if output.observation.findings.is_empty() {
        "Memory Safety & Sanitize Clean (0 Defects)".into()
    } else {
        format!(
            "{} Memory / Resource Defect(s) Detected",
            output.observation.findings.len()
        )
    };
    Ok(())
}
/// Frozen SanitizeEngine `_snippet`: whitespace-collapsed output, first 300 characters.
fn snippet(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(300)
        .collect()
}
/// Frozen `_append_scope_error`: an always-ERROR scope target; the check is incomplete.
fn scope_error(output: &mut AnalysisOutput, message: String) -> Result<(), ValidationError> {
    output.targets.push(missing_target(&message, false));
    Err(ValidationError(message))
}
fn missing_target(message: &str, optional: bool) -> InspectionTarget {
    let mut target = InspectionTarget::at("tests", 1);
    target.target_name = "PythonResourceWarnings".into();
    target.status = if optional { "SKIP" } else { "ERROR" }.into();
    target.message = message.into();
    target
}
/// A not-required missing scope is frozen's skipped-scope verdict: SKIP, ESTIMATED (a skipped
/// scope was counted), "Sanitize skipped: no applicable checks were executed".
fn skipped_scope(output: &mut AnalysisOutput, message: &str) {
    output.status = "SKIP".into();
    output.summary = "Sanitize skipped: no applicable checks were executed".into();
    output.observation.limitations.push(message.into());
    output.observation.measurements.push(Measurement {
        name: "applicability".into(),
        value: 0.0,
        evidence: ici_domain::EvidenceLevel::Estimated,
        unit: "checks".into(),
        numerator: None,
        denominator: None,
    });
}
fn missing_scope(
    output: &mut AnalysisOutput,
    message: &str,
    optional: bool,
) -> Result<(), ValidationError> {
    output.targets.push(missing_target(message, optional));
    if optional {
        skipped_scope(output, message);
        return Ok(());
    }
    Err(ValidationError(message.into()))
}
/// Frozen `_check_python_resource_warnings` refuses the scope before running pytest when the
/// project's `tests` directory is missing or holds no `test_*.py`/`*_test.py` file.
pub fn legacy_python_scope_missing(root: &std::path::Path) -> Option<&'static str> {
    // Frozen `tests_root.rglob("*")` matches any entry name; directory symlinks are not
    // descended (no loops).
    fn has_test(directory: &std::path::Path, depth: usize) -> bool {
        depth < 64
            && std::fs::read_dir(directory)
                .into_iter()
                .flatten()
                .flatten()
                .any(|entry| {
                    let name = entry.file_name().to_string_lossy().into_owned();
                    (name.ends_with(".py")
                        && (name.starts_with("test_") || name.ends_with("_test.py")))
                        || (entry.file_type().is_ok_and(|kind| kind.is_dir())
                            && has_test(&entry.path(), depth + 1))
                })
    }
    let tests = root.join("tests");
    if !tests.is_dir() {
        Some("Python ResourceWarning check skipped: tests directory is missing")
    } else if !has_test(&tests, 0) {
        Some("Python ResourceWarning check skipped: no Python test files were selected")
    } else {
        None
    }
}
/// The frozen missing-scope verdict without any process: ERROR (incomplete) when required,
/// the skipped-scope SKIP otherwise.
pub fn legacy_missing_scope(task_id: &str, message: &str, optional: bool) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(task_id, "ici-validation");
    output.targets.push(missing_target(message, optional));
    if optional {
        skipped_scope(&mut output, message);
    } else {
        output.observation.state = ici_domain::TaskState::Failed;
        output.observation.limitations.push(message.into());
        output.status = "INCOMPLETE".into();
        output.summary = message.into();
    }
    output
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
