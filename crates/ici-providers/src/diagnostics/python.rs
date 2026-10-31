use super::{Parsed, finding, positive, text};
use crate::{
    ProviderError,
    plan::{ProviderPlan, relative_location},
};
use regex::Regex;
use serde_json::Value;

pub(super) fn ruff(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    let mut parsed = Parsed::default();
    warnings(stderr, &mut parsed)?;
    if stdout.trim().is_empty() {
        return Ok(parsed);
    }
    let document: Value = serde_json::from_str(stdout).map_err(|e| ProviderError(e.to_string()))?;
    let items = document
        .as_array()
        .ok_or_else(|| ProviderError("Ruff output is not an array".into()))?;
    for item in items {
        let path = text(item, "filename")?;
        let rule = text(item, "code")?;
        let message = text(item, "message")?;
        let row = positive(&item["location"]["row"], "row")?;
        let column = optional_positive(&item["location"]["column"], "column")?;
        let mut result = finding(plan, path, row, column, rule, message, "warning")?;
        result.category = "lint".into();
        if !item["end_location"].is_null() {
            let end_line = positive(&item["end_location"]["row"], "end row")?;
            let end_column = positive(&item["end_location"]["column"], "exclusive end column")?
                .saturating_sub(1)
                .max(1);
            if end_line < row || (end_line == row && column.is_some_and(|col| end_column < col)) {
                return Err(ProviderError("Ruff source range is reversed".into()));
            }
            result.primary_location.end_line = Some(end_line);
            result.primary_location.end_column = Some(end_column);
        }
        parsed.findings.push(result);
    }
    Ok(parsed)
}
fn warnings(stderr: &str, parsed: &mut Parsed) -> Result<(), ProviderError> {
    let mut warning = false;
    for line in stderr.lines().filter(|line| !line.trim().is_empty()) {
        if line.starts_with("warning:") {
            warning = true;
            parsed.limitations.push(line.into());
        } else if warning && (line.starts_with(' ') || line.starts_with("  - ")) {
            parsed.limitations.push(line.into());
        } else {
            return Err(ProviderError(format!("unrecognized Ruff stderr: {line}")));
        }
    }
    Ok(())
}
pub(super) fn format(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    let mut parsed = Parsed::default();
    warnings(stderr, &mut parsed)?;
    let location =
        Regex::new(r"^\s*-->\s*(.+?):([1-9]\d*):([1-9]\d*)\s*$").expect("constant regex");
    let count = Regex::new(r"^(\d+) files? (?:would be reformatted|already formatted)(?:, \d+ files? already formatted)?$").expect("constant regex");
    let body = Regex::new(r"^\s*(?:[|+-]|\d+\s*[-+|])").expect("constant regex");
    let mut header = false;
    let mut seen = std::collections::BTreeSet::new();
    let mut expected_count = None;
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        if let Some(path) = line.strip_prefix("Would reformat: ") {
            if seen.insert(path.to_string()) {
                parsed.findings.push(finding(
                    plan,
                    path,
                    1,
                    None,
                    "format",
                    "file would be reformatted",
                    "low",
                )?);
            }
        } else if line.starts_with("unformatted:") {
            if header {
                return Err(ProviderError(
                    "format header without source location".into(),
                ));
            }
            header = true;
        } else if let Some(captures) = location.captures(line) {
            if !header {
                return Err(ProviderError("format source span without header".into()));
            }
            header = false;
            if seen.insert(captures[1].to_string()) {
                parsed.findings.push(finding(
                    plan,
                    &captures[1],
                    number(&captures[2])?,
                    Some(number(&captures[3])?),
                    "format",
                    "file would be reformatted",
                    "low",
                )?);
            }
        } else if let Some(captures) = count.captures(line.trim()) {
            if line.contains("would be reformatted") {
                expected_count = Some(
                    captures[1]
                        .parse::<usize>()
                        .map_err(|e| ProviderError(e.to_string()))?,
                );
            }
        } else if body.is_match(line) && !parsed.findings.is_empty() {
            // Current Ruff emits both indented gutters and unindented line-number diffs.
        } else {
            return Err(ProviderError(format!("unknown format output: {line}")));
        }
    }
    if header || expected_count.is_some_and(|count| count != parsed.findings.len()) {
        return Err(ProviderError(
            "format summary/location count mismatch".into(),
        ));
    }
    Ok(parsed)
}
pub(super) fn ty(stdout: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    let document: Value = serde_json::from_str(stdout).map_err(|e| ProviderError(e.to_string()))?;
    if document["version"] != "2.1.0" {
        return Err(ProviderError("ty output is not SARIF 2.1.0".into()));
    }
    let runs = document["runs"]
        .as_array()
        .ok_or_else(|| ProviderError("SARIF runs missing".into()))?;
    if runs.len() != 1 || runs[0]["tool"]["driver"]["name"] != "ty" {
        return Err(ProviderError(
            "unexpected SARIF producer or run count".into(),
        ));
    }
    let items = runs[0]["results"]
        .as_array()
        .ok_or_else(|| ProviderError("SARIF results missing".into()))?;
    let column_kind = Some(match runs[0].get("columnKind").and_then(Value::as_str) {
        Some("unicodeCodePoints") => crate::coordinates::ColumnKind::Unicode,
        Some("utf16CodeUnits") => crate::coordinates::ColumnKind::Utf16,
        Some(_) => return Err(ProviderError("unknown SARIF columnKind".into())),
        // Measured producer exception: pinned ty 0.0.93 omits columnKind but emits codepoints.
        None if runs[0]["tool"]["driver"]["version"] == "0.0.93" => {
            crate::coordinates::ColumnKind::Unicode
        }
        None => crate::coordinates::ColumnKind::Utf16,
    });
    let mut parsed = Parsed {
        column_kind,
        ..Default::default()
    };
    for item in items {
        let locations = item["locations"]
            .as_array()
            .filter(|v| !v.is_empty())
            .ok_or_else(|| ProviderError("ty diagnostic has no source location".into()))?;
        let location = &locations[0]["physicalLocation"];
        if location["artifactLocation"].get("uriBaseId").is_some() {
            return Err(ProviderError("ty URI base mapping is unresolved".into()));
        }
        let path = file_uri(text(&location["artifactLocation"], "uri")?)?;
        let row = positive(&location["region"]["startLine"], "start line")?;
        let column = optional_positive(&location["region"]["startColumn"], "start column")?;
        let rule = text(item, "ruleId")?;
        let message = text(&item["message"], "text")?;
        let level = text(item, "level")?;
        let severity = match level {
            "error" => "high",
            "warning" => "medium",
            "note" | "none" => "low",
            _ => return Err(ProviderError("unknown ty severity".into())),
        };
        let mut result = finding(plan, &path, row, column, rule, message, severity)?;
        result.tags.push(format!("native-severity:{level}"));
        // Preserve the old concise provider's start-only span/fingerprint contract.
        parsed.findings.push(result);
    }
    Ok(parsed)
}
pub(super) fn mypy(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    if !stderr.trim().is_empty() {
        return Err(ProviderError(format!("mypy stderr: {stderr}")));
    }
    let diagnostic = Regex::new(r"^(.+?):([1-9]\d*)(?::([1-9]\d*))?:\s*(error|warning|note):\s*(\S.*?)(?:\s+\[([A-Za-z0-9_-]+)\])?$").expect("constant regex");
    let summary = Regex::new(r"^(?:Success: no issues found in \d+ source files?|Found \d+ errors? in \d+ files? \(checked \d+ source files?\)|Checked \d+ source files?)$").expect("constant regex");
    let mut parsed = Parsed::default();
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        if let Some(captures) = diagnostic.captures(line) {
            let severity = match &captures[4] {
                "error" => "high",
                "warning" => "medium",
                _ => "low",
            };
            let code = captures.get(6).map_or("", |v| v.as_str());
            let column = captures
                .get(3)
                .map(|value| number(value.as_str()))
                .transpose()?;
            parsed.findings.push(finding(
                plan,
                &captures[1],
                number(&captures[2])?,
                column,
                code,
                &captures[5],
                severity,
            )?);
        } else if !summary.is_match(line) {
            return Err(ProviderError(format!("unrecognized mypy output: {line}")));
        }
    }
    Ok(parsed)
}
fn optional_positive(value: &Value, label: &str) -> Result<Option<u32>, ProviderError> {
    if value.is_null() {
        Ok(None)
    } else {
        positive(value, label).map(Some)
    }
}
fn number(value: &str) -> Result<u32, ProviderError> {
    value
        .parse()
        .map_err(|_| ProviderError("invalid source coordinate".into()))
}
fn file_uri(value: &str) -> Result<String, ProviderError> {
    let path = if let Some(path) = value.strip_prefix("file://") {
        if path.starts_with('/') {
            path
        } else {
            return Err(ProviderError("non-local file URI".into()));
        }
    } else if value.contains("://") {
        return Err(ProviderError("non-file source URI".into()));
    } else {
        value
    };
    let bytes = path.as_bytes();
    let mut decoded = vec![];
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            let hex = bytes
                .get(index + 1..index + 3)
                .ok_or_else(|| ProviderError("truncated URI escape".into()))?;
            let text =
                std::str::from_utf8(hex).map_err(|_| ProviderError("invalid URI escape".into()))?;
            decoded.push(
                u8::from_str_radix(text, 16)
                    .map_err(|_| ProviderError("invalid URI escape".into()))?,
            );
            index += 3;
        } else {
            decoded.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(decoded).map_err(|_| ProviderError("source URI is not UTF-8".into()))
}
/// Parse the separate, explicitly planned Ruff file inventory, never infer it from no findings.
pub fn ruff_inspected_files(
    stdout: &str,
    plan: &ProviderPlan,
) -> Result<Vec<String>, ProviderError> {
    stdout
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| relative_location(line.trim(), &plan.context))
        .collect()
}
