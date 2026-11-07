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
    let legacy = plan.context.options.legacy_pytest_result;
    // Frozen reads every process's output with `errors="replace"`; native refuses bad bytes.
    let (stdout, stderr) = if legacy || !planning::completed(outcome) {
        (
            String::from_utf8_lossy(&outcome.stdout).into_owned(),
            String::from_utf8_lossy(&outcome.stderr).into_owned(),
        )
    } else {
        (
            String::from_utf8(outcome.stdout.clone()).map_err(|_| {
                ValidationError("Python ResourceWarning stdout is not UTF-8".into())
            })?,
            String::from_utf8(outcome.stderr.clone()).map_err(|_| {
                ValidationError("Python ResourceWarning stderr is not UTF-8".into())
            })?,
        )
    };
    let text = format!("{stdout}\n{stderr}");
    let executed = if !planning::completed(outcome) {
        0
    } else if legacy {
        executed_counts(&text)
    } else {
        python::parse_pytest(&stdout, &plan.context)?
            .cases
            .values()
            .filter(|case| {
                matches!(
                    case.verdict.as_str(),
                    "PASSED" | "FAILED" | "XFAIL" | "XPASS"
                )
            })
            .count() as u64
    };
    match refusal(outcome, &stdout, &stderr, executed) {
        Some(Refusal::Failed(message)) => return scope_error(output, message),
        Some(Refusal::Missing(message)) => {
            return missing_scope(
                output,
                &message,
                plan.context.options.sanitize_scope_optional,
            );
        }
        None => {}
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
            Regex::new(r"(.*?\.py):([1-9]\d*):[^\n]*ResourceWarning").expect("constant regex");
        let mut seen = BTreeSet::new();
        // Frozen `_resource_warning_targets`: the first match of each `splitlines()` line, every
        // matching line its own target.
        for capture in crate::make_check::lines(&text)
            .into_iter()
            .filter_map(|line| location.captures(line))
        {
            let Ok(path) = planning::relative(
                ici_analysis::unicode::python_strip(&capture[1]),
                &plan.context.root,
                &plan.context.cwd,
            )
            .or_else(|error| {
                output.observation.limitations.push("external ResourceWarning source location could not be attributed inside this workspace".into());
                // Frozen counts this line in its FAIL (decisions.md "프로젝트 밖 ResourceWarning
                // 위치"); the path stays out of the report.
                if legacy {
                    Ok("[external]".to_owned())
                } else {
                    Err(error)
                }
            }) else {
                continue;
            };
            let line = capture[2]
                .parse::<u32>()
                .map_err(|_| ValidationError("ResourceWarning line exceeds bounds".into()))?;
            if legacy || seen.insert((path.clone(), line)) {
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
/// Frozen `_PYTEST_EXECUTED_RE` (`\\b<n>\\s+(passed|failed|xfailed|xpassed)\\b`, any case): what
/// the output's summary says ran.
fn executed_counts(text: &str) -> u64 {
    let counts = Regex::new(r"(?i)\b(\d+)[\s\x1c-\x1f]+(?:passed|failed|xfailed|xpassed)\b")
        .expect("constant regex");
    counts
        .captures_iter(text)
        .map(|capture| {
            capture[1]
                .chars()
                .filter_map(ici_analysis::unicode::decimal_digit)
                .fold(0u64, |value, digit| {
                    value.saturating_mul(10).saturating_add(u64::from(digit))
                })
        })
        .fold(0u64, u64::saturating_add)
}
/// Why a finished or unfinished suite measured nothing.
enum Refusal {
    /// Frozen `_append_scope_error`: always an ERROR.
    Failed(String),
    /// Frozen `_missing_python_scope`: an ERROR, or a SKIP when the engine is not required.
    Missing(String),
}
/// Frozen `_check_python_resource_warnings`, in its order. `executed` is what ran by the reading
/// in force.
fn refusal(outcome: &TaskOutcome, stdout: &str, stderr: &str, executed: u64) -> Option<Refusal> {
    // `_process_incomplete` / `_incomplete_message`, snipping stderr (else stdout).
    if !planning::completed(outcome) {
        let tail = snippet(if stderr.is_empty() { stdout } else { stderr });
        return Some(Refusal::Failed(
            if outcome.state != ici_domain::TaskState::Succeeded
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
            },
        ));
    }
    let text = format!("{stdout}\n{stderr}");
    // An interpreter without pytest, a suite that collected nothing, or one without a parseable
    // result is one located `PythonResourceWarnings` target at `tests` naming why. Frozen keys
    // this on its executed count for every exit code (a collection error has none either).
    if outcome.exit_code != Some(0)
        && Regex::new(r#"No module named ['"]pytest['"]|No module named pytest"#)
            .expect("constant regex")
            .is_match(&text)
    {
        return Some(Refusal::Missing(format!(
            "Pytest is unavailable: {}",
            snippet(&text)
        )));
    }
    if outcome.exit_code == Some(5) {
        return Some(Refusal::Missing("Pytest collected 0 tests".to_owned()));
    }
    if executed == 0 {
        return Some(Refusal::Missing(
            "Pytest returned success without parseable test results".to_owned(),
        ));
    }
    // `_tool_failure_message`: a non-zero run without ResourceWarning evidence is a tool failure,
    // never a measured resource violation.
    let exit_code = outcome.exit_code.unwrap_or_default();
    if exit_code != 0 && !text.contains("ResourceWarning") {
        let tail = snippet(if stderr.is_empty() { stdout } else { stderr });
        return Some(Refusal::Failed(format!(
            "Pytest ResourceWarning check failed with exit code {exit_code}: {tail}"
        )));
    }
    None
}
/// Frozen `evidence.error` of the legacy `pytest resource warnings` record: why the suite measured
/// nothing, empty for a measured one.
pub fn legacy_evidence_error(outcome: &TaskOutcome) -> String {
    let stdout = String::from_utf8_lossy(&outcome.stdout);
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    let executed = executed_counts(&format!("{stdout}\n{stderr}"));
    match refusal(outcome, &stdout, &stderr, executed) {
        Some(Refusal::Failed(message) | Refusal::Missing(message)) => message,
        None => String::new(),
    }
}
/// Frozen SanitizeEngine `_snippet`: whitespace-collapsed output, first 300 characters.
fn snippet(text: &str) -> String {
    ici_analysis::unicode::python_split(text)
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
