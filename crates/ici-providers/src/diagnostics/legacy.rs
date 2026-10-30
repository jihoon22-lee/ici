//! Historical `ici type` mypy view (frozen v0.13.0 `engines/type_check.py`).
//!
//! Only plans built with `NativeOptions::legacy` reach this observer; the modern mypy grammar
//! (`python::mypy`) is untouched. Frozen read mypy's own text, so the targets keep its message
//! with the trailing `[code]`, the column only when the project configuration printed one, and
//! fold repeated identical notes of one file into the first (`(xN)`).
use super::finding;
use crate::plan::{ProviderPlan, relative_location};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskState};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::json;

struct Diagnostic {
    path: String,
    line: u32,
    column: Option<u32>,
    error: bool,
    message: String,
}

/// Frozen `parse_mypy_line(..., severities=("error", "note"))`: a `warning:` line is not one.
fn parse(line: &str) -> Option<Diagnostic> {
    let pattern = Regex::new(
        r"^(.+?):([1-9]\d*)(?::([1-9]\d*))?:\s*(error|warning|note):\s*(\S.*?)(?:\s+\[([A-Za-z0-9_-]+)\])?$",
    )
    .expect("constant regex");
    let captures = pattern.captures(line)?;
    if &captures[4] == "warning" {
        return None;
    }
    let message = match captures.get(6) {
        Some(code) => format!("{} [{}]", &captures[5], code.as_str()),
        None => captures[5].to_owned(),
    };
    Some(Diagnostic {
        path: captures[1].to_owned(),
        line: captures[2].parse().ok()?,
        column: captures
            .get(3)
            .and_then(|value| value.as_str().parse().ok()),
        error: &captures[4] == "error",
        message,
    })
}

/// Frozen `_append_mypy_target` / `_parse_mypy_diagnostics` target plus `_merge_note_target`.
fn push(targets: &mut Vec<(InspectionTarget, String, u32)>, plan: &ProviderPlan, item: Diagnostic) {
    let path = relative_location(&item.path, &plan.context).unwrap_or(item.path);
    if !item.error
        && let Some((target, base, count)) = targets.iter_mut().find(|(target, base, _)| {
            target.target_name == "MypyNote" && target.file_path == path && *base == item.message
        })
    {
        *count += 1;
        target.message = format!("{base} (x{count})");
        return;
    }
    let mut target = InspectionTarget::at(&path, item.line);
    target.start_column = item.column;
    target.target_name = if item.error { "MypyError" } else { "MypyNote" }.into();
    target.status = if item.error { "FAIL" } else { "WARN" }.into();
    target.message.clone_from(&item.message);
    targets.push((target, item.message, 1));
}

/// Frozen `_parse_mypy_diagnostics`: true only when an error was read and no line was unknown.
fn parse_failure_stream(
    text: &str,
    plan: &ProviderPlan,
    targets: &mut Vec<(InspectionTarget, String, u32)>,
) -> bool {
    let tally = Regex::new(r"^Found \d+ errors? in \d+ files? \(checked \d+ source files?\)$")
        .expect("constant regex");
    let mut has_error = false;
    let mut malformed = false;
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if let Some(item) = parse(line) {
            has_error |= item.error;
            push(targets, plan, item);
        } else if !tally.is_match(line) {
            malformed = true;
        }
    }
    has_error && !malformed
}

/// Frozen `_validated_mypy_success`: one exact success tally as the last line; only notes before
/// it. Diagnostics read before a non-note line are still reported.
fn parse_success(
    stdout: &str,
    plan: &ProviderPlan,
    targets: &mut Vec<(InspectionTarget, String, u32)>,
) -> bool {
    let success =
        Regex::new(r"^Success: no issues found in (\d+) source files?$").expect("constant regex");
    let lines: Vec<&str> = stdout.lines().collect();
    let tallies: Vec<_> = lines
        .iter()
        .enumerate()
        .filter(|(_, line)| success.is_match(line))
        .map(|(index, _)| index)
        .collect();
    if tallies != [lines.len().saturating_sub(1)] || lines.is_empty() {
        return false;
    }
    let count: u64 = success.captures(lines[lines.len() - 1]).expect("matched")[1]
        .parse()
        .unwrap_or(0);
    let noun = if count == 1 { "file" } else { "files" };
    if count < 1
        || lines[lines.len() - 1] != format!("Success: no issues found in {count} source {noun}")
    {
        return false;
    }
    for line in &lines[..lines.len() - 1] {
        let Some(item) = parse(line) else {
            return false;
        };
        let error = item.error;
        push(targets, plan, item);
        if error {
            return false;
        }
    }
    true
}

pub(super) fn mypy(plan: &ProviderPlan, outcome: &TaskOutcome) -> AnalysisOutput {
    let mut result = AnalysisOutput::new(&plan.task.id, plan.kind.name());
    let observation = &mut result.observation;
    observation.exit_code = outcome.exit_code;
    observation.signal = outcome.signal.map(|signal| signal.to_string());
    observation.timed_out = outcome.timed_out;
    observation.truncated = outcome.truncated;
    observation.duration_seconds = Some(outcome.duration_seconds);
    observation.limitations.clone_from(&plan.limitations);
    let stdout = String::from_utf8_lossy(&outcome.stdout);
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    let mut targets = vec![];
    let mut clean = false;
    // Frozen `_DID_NOT_RUN` and the mypy exit contract (0 success, 1 findings, else failed).
    let error = if outcome.task_id != plan.task.id {
        Some("Mypy did not produce a result".to_owned())
    } else if outcome.cancelled {
        Some("Mypy was stopped before producing a result".into())
    } else if outcome.timed_out {
        Some("Mypy timed out".into())
    } else if outcome.truncated {
        Some("Mypy output was truncated".into())
    } else if outcome.state != TaskState::Succeeded {
        Some(if outcome.signal.is_some() {
            "Mypy terminated before producing a result".into()
        } else {
            "Mypy could not be started".into()
        })
    } else {
        match outcome.exit_code {
            Some(0) if !stderr.trim().is_empty() => {
                Some("Mypy emitted unexpected stderr on success".into())
            }
            Some(0) => {
                clean = parse_success(&stdout, plan, &mut targets);
                (!clean).then(|| "Mypy success output was not parseable".into())
            }
            Some(1) => (!parse_failure_stream(&format!("{stdout}\n{stderr}"), plan, &mut targets))
                .then(|| "Mypy diagnostics were not parseable".into()),
            Some(code) => {
                parse_failure_stream(&format!("{stdout}\n{stderr}"), plan, &mut targets);
                Some(format!("Mypy tool failed with exit code {code}"))
            }
            None => Some("Mypy terminated before producing a result".into()),
        }
    };
    let targets: Vec<_> = targets.into_iter().map(|(target, _, _)| target).collect();
    let fail = targets
        .iter()
        .filter(|target| target.status == "FAIL")
        .count();
    let warn = targets.len() - fail;
    result.extra = json!({"legacy_mypy": {"clean": clean}});
    if let Some(error) = error {
        observation.state = if outcome.cancelled {
            TaskState::Cancelled
        } else {
            TaskState::Failed
        };
        observation.limitations.insert(0, error.clone());
        result.status = "ERROR".into();
        result.summary = error;
        result.targets = targets;
        return result;
    }
    for target in &targets {
        match finding(
            plan,
            &target.file_path,
            target.start_line,
            target.start_column,
            "",
            &target.message,
            if target.status == "FAIL" {
                "high"
            } else {
                "low"
            },
        ) {
            Ok(mut item) => {
                item.task_id = Some(plan.task.id.clone());
                item.component_id.clone_from(&plan.context.component_id);
                item.variant.clone_from(&plan.context.variant);
                if plan.context.analysis_unit_ids.len() == 1 {
                    item.analysis_unit_id = plan.context.analysis_unit_ids.first().cloned();
                }
                observation.findings.push(item);
            }
            Err(error) => {
                observation.state = TaskState::Failed;
                observation.findings.clear();
                result.status = "ERROR".into();
                result.summary = format!("Mypy diagnostics were not parseable: {error}");
                observation.limitations.insert(0, result.summary.clone());
                result.targets = targets;
                return result;
            }
        }
    }
    result.status = if fail > 0 {
        "FAIL"
    } else if warn > 0 {
        "WARN"
    } else {
        "PASS"
    }
    .into();
    result.summary = if fail + warn == 0 {
        "Static Type Check Passed".into()
    } else {
        format!("{fail} Type Findings, {warn} Warnings")
    };
    result.targets = targets;
    result
}
