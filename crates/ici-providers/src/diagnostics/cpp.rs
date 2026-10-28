use super::{Parsed, cpp_details, finding, fixits, positive, text};
use crate::{
    ProviderError,
    plan::{ProviderKind, ProviderPlan, relative_location},
};
use regex::Regex;
use serde_json::{Value, json};

const MAX_DIAGNOSTICS: usize = 10_000;

pub(super) fn parse(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    if stdout.len().saturating_add(stderr.len()) > 4_000_000
        || stdout.contains('\0')
        || stderr.contains('\0')
    {
        return Err(ProviderError(
            "C++ diagnostic output exceeds bounds or contains NUL".into(),
        ));
    }
    let joined = [stdout.trim(), stderr.trim()]
        .into_iter()
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    let mut parsed = if joined.starts_with(['[', '{']) {
        parse_json(&joined, plan)?
    } else {
        parse_text(&joined, plan)?
    };
    cpp_details::finish(&mut parsed, plan);
    if plan.kind == ProviderKind::Compiler {
        parsed
            .inspected
            .extend(plan.context.targets.iter().cloned());
    }
    Ok(parsed)
}
fn parse_text(output: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    let diagnostic = Regex::new(r"^(.+?):([1-9]\d*):([1-9]\d*):\s*(fatal error|error|warning|note|remark):\s*(\S.*?)(?:\s+\[([^\]]+)\])?$").expect("constant regex");
    let fixit = Regex::new(r#"^fix-it:("(?:[^"\\]|\\.)*"):\{([1-9]\d*):([1-9]\d*)-([1-9]\d*):([1-9]\d*)\}:("(?:[^"\\]|\\.)*")$"#).expect("constant regex");
    let context = Regex::new(r"^(?:In file included from |\s+from |.*: In (?:function|member function|instantiation)|\d+ warnings? generated\.|\d+ errors? generated\.|Suppressed \d+ warnings?|Use -header-filter=|Error while processing |compilation terminated\.)").expect("constant regex");
    let mut parsed = Parsed::default();
    let mut saw_diagnostic = false;
    let mut fixit_owner = false;
    let mut count = 0;
    for line in output.lines().filter(|line| !line.trim().is_empty()) {
        if let Some(captures) = diagnostic.captures(line) {
            count += 1;
            if count > MAX_DIAGNOSTICS {
                return Err(ProviderError("C++ diagnostic count exceeds bounds".into()));
            }
            saw_diagnostic = true;
            fixit_owner = true;
            let row = number(&captures[2])?;
            let column = Some(number(&captures[3])?);
            if relative_location(&captures[1], &plan.context).is_err() {
                if captures[4].contains("error") {
                    return Err(ProviderError("unattributed external compiler error".into()));
                }
                parsed
                    .limitations
                    .push("external compiler diagnostic omitted from project locations".into());
                fixit_owner = false;
                continue;
            }
            let rule = captures.get(6).map_or("diagnostic", |value| value.as_str());
            let current = finding(
                plan,
                &captures[1],
                row,
                column,
                rule,
                &captures[5],
                if captures[4].contains("error") {
                    "high"
                } else {
                    "medium"
                },
            )?;
            append(&mut parsed, plan, current, &captures[4])?;
        } else if let Some(captures) = fixit.captures(line.trim()) {
            if saw_diagnostic && !fixit_owner {
                parsed
                    .limitations
                    .push("external compiler fix-it omitted from project locations".into());
                continue;
            }
            let path: String = serde_json::from_str(&captures[1])
                .map_err(|_| ProviderError("invalid quoted fix-it path".into()))?;
            let replacement: String = serde_json::from_str(&captures[6])
                .map_err(|_| ProviderError("invalid quoted fix-it replacement".into()))?;
            let edit = fixits::replacement(
                plan,
                &path,
                (number(&captures[2])?, number(&captures[3])?),
                (number(&captures[4])?, number(&captures[5])?),
                &replacement,
                true,
            )?;
            add_edit(&mut parsed, edit)?;
        } else if context.is_match(line)
            || (saw_diagnostic && line.starts_with(char::is_whitespace))
            || line.trim_start().starts_with(['^', '~', '|'])
        {
            continue;
        } else {
            return Err(ProviderError(format!(
                "unrecognized C++ diagnostic output: {line}"
            )));
        }
    }
    Ok(parsed)
}
fn number(value: &str) -> Result<u32, ProviderError> {
    value
        .parse::<u32>()
        .ok()
        .filter(|value| *value > 0 && *value <= i32::MAX as u32)
        .ok_or_else(|| ProviderError("invalid C++ coordinate".into()))
}
fn append(
    parsed: &mut Parsed,
    _plan: &ProviderPlan,
    current: ici_domain::Finding,
    kind: &str,
) -> Result<(), ProviderError> {
    if current.message.chars().count() > 8192 || parsed.findings.len() >= MAX_DIAGNOSTICS {
        return Err(ProviderError(
            "C++ diagnostic count/message exceeds bounds".into(),
        ));
    }
    if kind == "note" && !parsed.findings.is_empty() {
        let mut location = current.primary_location;
        location.label = format!("note: {}", current.message);
        parsed
            .findings
            .last_mut()
            .expect("nonempty")
            .related_locations
            .push(location);
    } else {
        parsed.details.insert(
            current.fingerprint.clone(),
            json!({"legacy_message":format!("{kind}: {}",current.message)}),
        );
        parsed.findings.push(current);
    }
    Ok(())
}
fn add_edit(parsed: &mut Parsed, edit: Value) -> Result<(), ProviderError> {
    let finding = parsed
        .findings
        .last()
        .ok_or_else(|| ProviderError("C++ fix-it has no primary diagnostic".into()))?;
    let detail = parsed
        .details
        .get_mut(&finding.fingerprint)
        .expect("detail exists");
    if detail.get("replacements").is_none() {
        detail["replacements"] = json!([]);
    }
    let edits = detail["replacements"]
        .as_array_mut()
        .expect("replacement array");
    if edits.len() >= 128 {
        return Err(ProviderError("too many C++ fix-its".into()));
    }
    edits.push(edit);
    Ok(())
}
fn parse_json(output: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    let document: Value = serde_json::from_str(output)
        .map_err(|error| ProviderError(format!("invalid C++ JSON: {error}")))?;
    let entries = if let Some(entries) = document.as_array() {
        entries.clone()
    } else if let Some(entries) = document.get("diagnostics") {
        entries
            .as_array()
            .ok_or_else(|| ProviderError("C++ JSON diagnostics is not a list".into()))?
            .clone()
    } else if document.get("kind").is_some() {
        vec![document]
    } else {
        return Err(ProviderError("unknown C++ JSON envelope".into()));
    };
    let mut parsed = Parsed {
        column_kind: Some(crate::coordinates::ColumnKind::Unicode),
        ..Default::default()
    };
    let mut count = 0;
    for entry in &entries {
        json_entry(entry, plan, &mut parsed, 0, &mut count)?;
    }
    Ok(parsed)
}
fn position(value: &Value) -> Result<(&str, u32, Option<u32>), ProviderError> {
    let column = value.get("byte-column").or_else(|| value.get("column"));
    Ok((
        text(value, "file")?,
        positive(&value["line"], "C++ line")?,
        column
            .filter(|value| !value.is_null())
            .map(|value| positive(value, "C++ column"))
            .transpose()?,
    ))
}
fn json_entry(
    value: &Value,
    plan: &ProviderPlan,
    parsed: &mut Parsed,
    depth: usize,
    count: &mut usize,
) -> Result<(), ProviderError> {
    *count += 1;
    if *count > MAX_DIAGNOSTICS {
        return Err(ProviderError("C++ diagnostic count exceeds bounds".into()));
    }
    if depth > 32 {
        return Err(ProviderError(
            "C++ diagnostic nesting exceeds bounds".into(),
        ));
    }
    let kind = text(value, "kind")?;
    if value.get("column-origin").is_some_and(|origin| origin != 1) {
        return Err(ProviderError("C++ JSON column origin is not 1".into()));
    }
    if !["fatal error", "error", "warning", "note", "remark"].contains(&kind) {
        return Err(ProviderError("unknown C++ JSON severity".into()));
    }
    let location = if let Some(locations) = value.get("locations") {
        locations
            .as_array()
            .filter(|items| !items.is_empty() && items.len() <= 128)
            .and_then(|items| items.first())
            .ok_or_else(|| ProviderError("C++ JSON has no bounded source location".into()))?
    } else {
        value
            .get("location")
            .ok_or_else(|| ProviderError("C++ JSON location missing".into()))?
    };
    let caret = location.get("caret").unwrap_or(location);
    let (path, line, column) = position(caret)?;
    let rule = value
        .get("option")
        .or_else(|| value.get("rule"))
        .and_then(Value::as_str)
        .unwrap_or("diagnostic");
    if rule.len() > 256
        || !rule
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c))
    {
        return Err(ProviderError("unsafe C++ JSON rule".into()));
    }
    let message = text(value, "message")?;
    let external = relative_location(path, &plan.context).is_err();
    if external && kind.contains("error") {
        return Err(ProviderError("unattributed external compiler error".into()));
    }
    // Validate external branches atomically, including fixes/children, without allowing a
    // child note or fix to attach to an unrelated preceding project diagnostic.
    let mut discarded = Parsed::default();
    let sink = if external {
        &mut discarded
    } else {
        &mut *parsed
    };
    let synthetic = plan
        .context
        .workspace_root
        .join("[external]")
        .to_string_lossy()
        .into_owned();
    let attributed_path = if external { synthetic.as_str() } else { path };
    let mut current = finding(
        plan,
        attributed_path,
        line,
        column,
        rule,
        message,
        if kind.contains("error") {
            "high"
        } else {
            "medium"
        },
    )?;
    let finish = location.get("finish").unwrap_or(caret);
    let (finish_path, finish_line, finish_column) = position(finish)?;
    let start = location.get("start").unwrap_or(caret);
    let (start_path, start_line, start_column) = position(start)?;
    if finish_path != path
        || start_path != path
        || (finish_line, finish_column.unwrap_or(0)) < (line, column.unwrap_or(0))
        || (start_line, start_column.unwrap_or(0)) > (line, column.unwrap_or(0))
    {
        return Err(ProviderError(
            "C++ JSON diagnostic range is reversed or crosses files".into(),
        ));
    }
    let kind_columns = if plan.context.compiler_columns.is_some() {
        crate::coordinates::ColumnKind::Utf8
    } else {
        crate::coordinates::ColumnKind::Unknown
    };
    crate::coordinates::normalize(&mut current, plan, Some(kind_columns))?;
    current.primary_location.end_line = Some(finish_line);
    current.primary_location.end_column = finish_column
        .map(|column| {
            plan.context.source_coordinates.boundary(
                &current.primary_location.path,
                finish_line,
                column - 1,
                kind_columns,
                plan.context.compiler_columns.as_deref() == Some("clang-byte"),
            )
        })
        .transpose()?
        .flatten()
        .map(|column| column + 1);
    append(sink, plan, current, kind)?;
    if let Some(fixes) = value.get("fixits") {
        let fixes = fixes
            .as_array()
            .filter(|items| items.len() <= 128)
            .ok_or_else(|| ProviderError("unbounded C++ JSON fix-its".into()))?;
        for fix in fixes {
            let (start, end, exclusive) =
                if let (Some(start), Some(next)) = (fix.get("start"), fix.get("next")) {
                    (start, next, true)
                } else if let (Some(start), Some(end)) = (fix.get("start"), fix.get("end")) {
                    (start, end, false)
                } else if let Some(range) = fix.get("range") {
                    (&range["start"], &range["end"], false)
                } else {
                    return Err(ProviderError("C++ JSON fix-it range missing".into()));
                };
            let (path, line, column) = position(start)?;
            let (end_path, end_line, end_column) = position(end)?;
            if path != end_path {
                return Err(ProviderError("C++ fix-it crosses files".into()));
            }
            let replacement = fix
                .get("string")
                .or_else(|| fix.get("replacement"))
                .and_then(Value::as_str)
                .ok_or_else(|| ProviderError("C++ JSON fix-it replacement missing".into()))?;
            let edit = fixits::replacement(
                plan,
                path,
                (
                    line,
                    column.ok_or_else(|| ProviderError("fix-it start column missing".into()))?,
                ),
                (
                    end_line,
                    end_column.ok_or_else(|| ProviderError("fix-it end column missing".into()))?,
                ),
                replacement,
                exclusive,
            )?;
            add_edit(sink, edit)?;
        }
    }
    if let Some(children) = value.get("children") {
        for child in children
            .as_array()
            .filter(|children| children.len() <= MAX_DIAGNOSTICS)
            .ok_or_else(|| ProviderError("unbounded C++ child diagnostics".into()))?
        {
            json_entry(child, plan, sink, depth + 1, count)?;
        }
    }
    if external {
        parsed
            .limitations
            .push("external compiler diagnostic omitted from project locations".into());
        parsed.limitations.extend(discarded.limitations);
    }
    Ok(())
}
