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
        // Frozen's only reading of "executed" (`_pytest_has_executed_result`, legacy sanitize):
        // a node ran when pytest gave it a PASSED/FAILED/XFAIL/XPASS verdict. A test that passed
        // and whose teardown raised prints PASSED then ERROR, so it ran; a node whose only verdict
        // is ERROR is a setup error and never ran (an optional scope of only those is missing).
        python::parse_pytest(&stdout, &plan.context)?
            .legacy_lines
            .iter()
            .filter(|(_, verdict)| {
                matches!(verdict.as_str(), "PASSED" | "FAILED" | "XFAIL" | "XPASS")
            })
            .map(|(node, _)| node.as_str())
            .collect::<std::collections::BTreeSet<_>>()
            .len() as u64
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
        let reported = diagnostics(&stdout);
        let mut seen = BTreeSet::new();
        for (path, line) in &reported.located {
            let path = attributed(path, plan).unwrap_or_else(|| {
                if !output
                    .observation
                    .limitations
                    .iter()
                    .any(|known| known == UNATTRIBUTED)
                {
                    output.observation.limitations.push(UNATTRIBUTED.into());
                }
                UNATTRIBUTED_PATH.to_owned()
            });
            if legacy || seen.insert((path.clone(), *line)) {
                add_issue(
                    plan,
                    output,
                    &path,
                    *line,
                    "ResourceWarning was promoted to an exception by the sanitizer",
                );
            }
        }
        // `refusal` kept every run without a real diagnostic out of here: this one has no location.
        if output.observation.findings.is_empty() {
            output.observation.limitations.push(UNLOCATED.into());
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
/// Where an unattributable warning is reported: the location is withheld, not asserted external.
const UNATTRIBUTED_PATH: &str = "[external]";
const UNATTRIBUTED: &str =
    "ResourceWarning source location could not be attributed to a regular file in this workspace";
const UNLOCATED: &str = "ResourceWarning was reported without a source location";

/// The ResourceWarnings pytest itself reported in a failed run's output.
#[derive(Default)]
struct Diagnostics {
    /// Traceback footers, `<path>.py:<line>: <exception>`, in output order.
    located: Vec<(String, u32)>,
    /// The exception was reported without a footer (`--tb=short`, `native`, `no`).
    unlocated: bool,
}
impl Diagnostics {
    fn any(&self) -> bool {
        self.unlocated || !self.located.is_empty()
    }
}

/// Read pytest's report by its sections. A diagnostic is what pytest says about the exception a
/// test raised, so source pytest echoes, exception text and a test's captured output are not one
/// (decisions.md "pytest 출력을 ResourceWarning으로 잘못 읽은 판정"):
/// * a footer starts the line, its exception is what follows the first `.py:<n>: `;
/// * without a footer, the first line of an `E` block, or the line after a native traceback's
///   source, names the exception;
/// * with neither, a `FAILED`/`ERROR` summary line does, unless its node id is parametrized: an id
///   can spell ` - ResourceWarning: `, so that line cannot say what was raised.
fn diagnostics(text: &str) -> Diagnostics {
    #[derive(PartialEq)]
    enum Section {
        Report,
        Summary,
        Other,
    }
    let banner = Regex::new(r"^=+ (.+?) =+$").expect("constant regex");
    let test = Regex::new(r"^_+ .+ _+$").expect("constant regex");
    let captured = Regex::new(r"^-+ Captured .+ -+$").expect("constant regex");
    let location = Regex::new(r"\.py:([1-9]\d*): ").expect("constant regex");
    let exception = Regex::new(r"^[\w.]*ResourceWarning(:|$)").expect("constant regex");
    let mut found = Diagnostics::default();
    let mut section = Section::Other;
    let (mut in_captured, mut in_block, mut after_source) = (false, false, false);
    for line in crate::make_check::lines(text) {
        if let Some(title) = banner.captures(line) {
            section = match &title[1] {
                "FAILURES" | "ERRORS" => Section::Report,
                "short test summary info" => Section::Summary,
                _ => Section::Other,
            };
            (in_captured, in_block, after_source) = (false, false, false);
            continue;
        }
        match section {
            Section::Report => {
                if test.is_match(line) {
                    (in_captured, in_block, after_source) = (false, false, false);
                } else if captured.is_match(line) {
                    in_captured = true;
                } else if !in_captured {
                    let block = line.starts_with("E ");
                    let echoed = block || line.starts_with(' ') || line.starts_with("> ");
                    if let Some(footer) = location.captures(line).filter(|_| !echoed) {
                        let whole = footer.get(0).expect("whole match");
                        if exception.is_match(&line[whole.end()..])
                            && let Ok(number) = footer[1].parse()
                        {
                            found
                                .located
                                .push((line[..whole.start() + 3].to_owned(), number));
                        }
                    } else if (block && !in_block && exception.is_match(line[1..].trim_start()))
                        || (after_source && exception.is_match(line))
                    {
                        found.unlocated = true;
                    }
                    in_block = block;
                    after_source = line.starts_with(' ');
                }
            }
            Section::Summary => {
                if let Some((node, message)) = line
                    .strip_prefix("FAILED ")
                    .or_else(|| line.strip_prefix("ERROR "))
                    .and_then(|rest| rest.split_once(" - "))
                    && !node.contains('[')
                    && exception.is_match(message)
                {
                    found.unlocated = true;
                }
            }
            Section::Other => {}
        }
    }
    found
}

/// The project-relative regular file a located diagnostic names, resolved inside the root.
fn attributed(path: &str, plan: &ValidationPlan) -> Option<String> {
    let relative = planning::relative(path, &plan.context.root, &plan.context.cwd).ok()?;
    let root = std::fs::canonicalize(&plan.context.root).ok()?;
    let real = std::fs::canonicalize(plan.context.root.join(&relative)).ok()?;
    (real.starts_with(root) && real.is_file()).then_some(relative)
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
    // `_tool_failure_message`: a non-zero run without a real ResourceWarning diagnostic is a tool
    // failure, never a measured resource violation, whatever text its output quotes.
    let exit_code = outcome.exit_code.unwrap_or_default();
    if exit_code != 0 && !diagnostics(stdout).any() {
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

#[cfg(test)]
mod tests {
    use super::diagnostics;
    use serde_json::Value;

    const ORACLE: &str = include_str!(
        "../../../tests/rust-parity/validation/resource-warning-locations-oracle.json"
    );

    /// Pytest's raw output under frozen's command, case by case: where each real warning is, and
    /// whether pytest also reported one with no location.
    #[test]
    fn real_warnings_are_read_from_the_raw_pytest_captures() {
        let oracle: Value = serde_json::from_str(ORACLE).unwrap();
        let cases = oracle["cases"].as_object().unwrap();
        let calc = ["src/calc.py:5"].as_slice();
        let none = [].as_slice();
        let table: [(&str, &[&str], bool); 28] = [
            ("normal", calc, false),
            ("external", &["<outside>/ext.py:5"], false),
            ("echo-only", none, false),
            ("echo-plus-warning", calc, false),
            ("echo-in-message", none, false),
            ("summary-warning", none, false),
            ("captured-stdout-look-alike", none, false),
            ("captured-stderr-look-alike", none, false),
            ("quoted-without-location", none, false),
            (
                "file-name-with-space-and-quote",
                &["src/it's a.py:5"],
                false,
            ),
            ("two-warnings", &["src/calc.py:5", "src/calc.py:5"], false),
            ("raised-directly", &["tests/test_a.py:2"], false),
            ("chained", calc, false),
            ("fixture-teardown", calc, false),
            ("at-collection", none, true),
            ("file-gone", &["<root>/src/gone.py:5"], false),
            ("unclosed-file", none, false),
            ("traceback-short", none, true),
            ("traceback-line", &["<root>/src/calc.py:5"], true),
            ("traceback-native", none, true),
            ("traceback-no", none, true),
            ("parametrized-id", none, false),
            ("parametrized-id-no-traceback", none, false),
            ("parametrized-real-no-traceback", none, false),
            ("multiline-message", none, false),
            ("multiline-message-native", none, false),
            ("quoted-location-line-style", none, false),
            ("no-summary-section", none, true),
        ];
        assert_eq!(cases.len(), table.len());
        for (name, located, unlocated) in table {
            let found = diagnostics(cases[name]["pytest_stdout"].as_str().unwrap());
            let places: Vec<String> = found
                .located
                .iter()
                .map(|(path, line)| format!("{path}:{line}"))
                .collect();
            assert_eq!(places, located, "{name}");
            // A footer's own failure also has its `E` line, which adds nothing to the count.
            assert_eq!(found.unlocated, unlocated || !located.is_empty(), "{name}");
        }
    }

    /// The shape separated from a real diagnostic only by what pytest itself prints: a test that
    /// prints pytest's whole report around a footer, outside capture, is read as a warning. The
    /// decisions row records this limit.
    #[test]
    fn a_forged_report_section_is_the_recorded_limit() {
        let forged = "=== FAILURES ===\nsrc/calc.py:5: ResourceWarning\n";
        assert_eq!(diagnostics(forged).located.len(), 1);
    }
}
