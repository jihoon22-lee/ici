//! Historical `ici lint` Ruff view (frozen v0.13.0 `engines/lint.py` with
//! `analysis/_ruff_output.py`).
//!
//! Only plans built with `NativeOptions::legacy` reach these observers; the modern Ruff grammar
//! (`python::ruff`/`python::format`) is untouched. Frozen named check diagnostics `Ruff:<code>`
//! (FAIL), files `ruff format --check --output-format=json` would change `Format:Style` (WARN,
//! no column), read Ruff's stderr `warning:` blocks as tool warnings (an ESTIMATED WARN) and any
//! other stderr, exit code or unreadable answer as a tool error.
use super::finding;
use crate::plan::{ProviderKind, ProviderPlan, relative_location};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskState};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::{Value, json};

/// Frozen `parse_stderr_warnings`: `warning:` blocks with indented continuations, else unreadable.
fn stderr_warnings(text: &str) -> Result<Vec<String>, String> {
    if text.trim().is_empty() {
        return Ok(vec![]);
    }
    let warning = Regex::new(r"^warning:\s+\S.*$").expect("constant regex");
    let mut warnings: Vec<Vec<&str>> = vec![];
    for line in text.lines() {
        if warning.is_match(line) {
            warnings.push(vec![line]);
        } else if let Some(block) = warnings.last_mut()
            && line.starts_with(char::is_whitespace)
        {
            block.push(line);
        } else {
            return Err(format!("unrecognized stderr line: {line:?}"));
        }
    }
    Ok(warnings.into_iter().map(|block| block.join("\n")).collect())
}

fn coordinate(value: &Value, label: &str, minimum: u64) -> Result<u32, String> {
    value
        .as_u64()
        .filter(|value| *value >= minimum && *value <= u64::from(u32::MAX))
        .map(|value| value as u32)
        .ok_or_else(|| format!("{label} is invalid"))
}

fn required_text<'a>(item: &'a Value, key: &str) -> Result<&'a str, String> {
    item.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("{key} is missing"))
}

/// Frozen `parse_check_json` entries as `Ruff:<code>` targets (inclusive end column).
fn check_targets(stdout: &str, plan: &ProviderPlan) -> Result<Vec<InspectionTarget>, String> {
    let payload: Value = serde_json::from_str(stdout.trim())
        .map_err(|error| format!("ruff output was not JSON: {error}"))?;
    let items = payload
        .as_array()
        .ok_or_else(|| "ruff output was not a list".to_owned())?;
    let mut targets = vec![];
    for (index, item) in items.iter().enumerate() {
        let entry = || -> Result<InspectionTarget, String> {
            let filename = required_text(item, "filename")?;
            let code = required_text(item, "code")?;
            let message = required_text(item, "message")?;
            let location = item
                .get("location")
                .filter(|value| value.is_object())
                .ok_or("location is not an object")?;
            let row = coordinate(&location["row"], "row", 1)?;
            let column = match &location["column"] {
                Value::Null => None,
                value => Some(coordinate(value, "column", 1)?),
            };
            let mut target = InspectionTarget::at(
                relative_location(filename, &plan.context).unwrap_or_else(|_| filename.to_owned()),
                row,
            );
            if let Some(end) = item.get("end_location").filter(|value| !value.is_null()) {
                if !end.is_object() {
                    return Err("end_location is not an object".into());
                }
                let end_row = coordinate(&end["row"], "end row", u64::from(row))?;
                let end_column = coordinate(&end["column"], "end column", 1)?
                    .saturating_sub(1)
                    .max(1);
                if end_row == row && column.is_some_and(|column| end_column < column) {
                    return Err("source range is invalid".into());
                }
                target.end_line = Some(end_row);
                target.end_column = Some(end_column);
            }
            target.start_column = column;
            target.target_name = format!("Ruff:{code}");
            target.status = "FAIL".into();
            target.message = message.into();
            Ok(target)
        };
        targets.push(
            entry().map_err(|error| format!("ruff entry {index} was not readable: {error}"))?,
        );
    }
    Ok(targets)
}

/// Frozen `_parse_ruff_format_diagnostics`: one `Format:Style` WARN per JSON item.
fn format_targets(items: &[Value], plan: &ProviderPlan) -> Result<Vec<InspectionTarget>, String> {
    let mut targets = vec![];
    for item in items {
        if !item.is_object() {
            return Err("Ruff format JSON item is not an object".into());
        }
        let filename = item
            .get("filename")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or("Ruff format JSON filename is missing")?;
        if item.get("code").and_then(Value::as_str) != Some("unformatted") {
            return Err("Ruff format JSON code is not unformatted".into());
        }
        let location = item
            .get("location")
            .filter(|value| value.is_object())
            .ok_or("Ruff format JSON location is not an object")?;
        let row = coordinate(&location["row"], "row", 1)
            .map_err(|_| "Ruff format JSON row is invalid".to_owned())?;
        if !location["column"].is_null() {
            coordinate(&location["column"], "column", 1)
                .map_err(|_| "Ruff format JSON column is invalid".to_owned())?;
        }
        let message = match item.get("message") {
            None => "File requires reformatting (PEP 8 style mismatch)",
            Some(value) => value
                .as_str()
                .filter(|value| !value.trim().is_empty())
                .ok_or("Ruff format JSON message is invalid")?,
        };
        let mut target = InspectionTarget::at(
            relative_location(filename, &plan.context).unwrap_or_else(|_| filename.to_owned()),
            row,
        );
        target.target_name = "Format:Style".into();
        target.status = "WARN".into();
        target.message = message.into();
        targets.push(target);
    }
    Ok(targets)
}

/// Frozen `_evaluate_ruff_check` / `_evaluate_ruff_format(json_output=True)`.
fn evaluate(
    plan: &ProviderPlan,
    outcome: &TaskOutcome,
    warnings: &mut Vec<String>,
) -> Result<Vec<InspectionTarget>, String> {
    let check = plan.kind == ProviderKind::RuffLint;
    let (name, output) = if check {
        ("Ruff check", "Ruff check output")
    } else {
        ("Ruff format check", "Ruff format output")
    };
    if outcome.timed_out {
        return Err(format!("{name} timed out"));
    }
    if outcome.truncated {
        return Err(format!("{output} was truncated"));
    }
    if outcome.task_id != plan.task.id
        || outcome.cancelled
        || outcome.state != TaskState::Succeeded
        || outcome.exit_code.is_none_or(|code| code < 0)
    {
        return Err(format!("{name} terminated before producing a result"));
    }
    let code = outcome.exit_code.unwrap_or_default();
    if !matches!(code, 0 | 1) {
        return Err(format!("{name} failed with exit code {code}"));
    }
    let stdout = String::from_utf8_lossy(&outcome.stdout);
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    let emitted = if check {
        "Ruff check emitted unexpected stderr"
    } else {
        "Ruff format emitted unexpected stderr"
    };
    warnings.extend(stderr_warnings(&stderr).map_err(|error| format!("{emitted}: {error}"))?);
    if check {
        if stdout.trim().is_empty() {
            let state = if code == 1 { "violations" } else { "succeeded" };
            return Err(format!("Ruff check {state} without parseable JSON"));
        }
        let targets = check_targets(&stdout, plan)
            .map_err(|error| format!("Ruff check output was not valid JSON: {error}"))?;
        return match (code, targets.is_empty()) {
            (0, false) => Err("Ruff check returned success with diagnostic findings".into()),
            (1, true) => Err("Ruff reported violations without any JSON findings".into()),
            _ => Ok(targets),
        };
    }
    let items: Vec<Value> = serde_json::from_str::<Value>(&stdout)
        .map_err(|error| error.to_string())
        .and_then(|value| match value {
            Value::Array(items) => Ok(items),
            _ => Err("Ruff format JSON output is not a list".into()),
        })
        .map_err(|error| format!("Ruff format output was not parseable as JSON: {error}"))?;
    match (code, items.is_empty()) {
        (0, true) => Ok(vec![]),
        (0, false) => Err("Ruff format returned success with diagnostic findings".into()),
        (_, true) => Err("Ruff format reported violations without any JSON findings".into()),
        _ => format_targets(&items, plan)
            .map_err(|error| format!("Ruff format output was not parseable: {error}")),
    }
}

pub(super) fn ruff(plan: &ProviderPlan, outcome: &TaskOutcome) -> AnalysisOutput {
    let mut result = AnalysisOutput::new(&plan.task.id, plan.kind.name());
    let observation = &mut result.observation;
    observation.exit_code = outcome.exit_code;
    observation.signal = outcome.signal.map(|signal| signal.to_string());
    observation.timed_out = outcome.timed_out;
    observation.truncated = outcome.truncated;
    observation.duration_seconds = Some(outcome.duration_seconds);
    observation.limitations.clone_from(&plan.limitations);
    let mut warnings = vec![];
    let targets = match evaluate(plan, outcome, &mut warnings) {
        Ok(targets) => targets,
        Err(error) => {
            observation.state = if outcome.cancelled {
                TaskState::Cancelled
            } else {
                TaskState::Failed
            };
            observation.limitations.insert(0, error.clone());
            result.extra = json!({"legacy_ruff": {"warnings": warnings}});
            result.status = "ERROR".into();
            result.summary = error;
            return result;
        }
    };
    let check = plan.kind == ProviderKind::RuffLint;
    for target in &targets {
        let rule = target.target_name.strip_prefix("Ruff:").unwrap_or("format");
        match finding(
            plan,
            &target.file_path,
            target.start_line,
            target.start_column,
            rule,
            &target.message,
            if check { "high" } else { "medium" },
        ) {
            Ok(mut item) => {
                item.category = if check { "lint" } else { "style" }.into();
                item.primary_location.end_line = target.end_line;
                item.primary_location.end_column = target.end_column;
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
                result.summary = format!("Ruff output was not parseable: {error}");
                observation.limitations.insert(0, result.summary.clone());
                return result;
            }
        }
    }
    observation.limitations.extend(warnings.iter().cloned());
    result.status = if targets.iter().any(|target| target.status == "FAIL") {
        "FAIL"
    } else if !targets.is_empty() || !warnings.is_empty() {
        "WARN"
    } else {
        "PASS"
    }
    .into();
    result.summary = format!(
        "{} {} target(s), {} tool warning(s)",
        if check { "Ruff check" } else { "Ruff format" },
        targets.len(),
        warnings.len()
    );
    result.extra = json!({"legacy_ruff": {"warnings": warnings}});
    result.targets = targets;
    result
}
