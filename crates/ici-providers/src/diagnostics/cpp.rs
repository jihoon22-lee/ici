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
    // Frozen clang-tidy parsing is text-only; a clang-tidy transcript may open with a
    // `[n/N] Processing file` progress line, which is not a JSON document.
    let mut parsed = if plan.kind != ProviderKind::ClangTidy && joined.starts_with(['[', '{']) {
        parse_json(&joined, plan)?
    } else {
        parse_text(&joined, plan)?
    };
    if plan.context.next_tidy {
        next_tidy_summaries(&joined)?;
    }
    if plan.kind == ProviderKind::ClangTidy {
        clang_tidy_accounting(&joined, &parsed)?;
    }
    cpp_details::finish(&mut parsed, plan);
    if plan.kind == ProviderKind::Compiler {
        parsed
            .inspected
            .extend(plan.context.targets.iter().cloned());
    }
    Ok(parsed)
}
/// Frozen `_clang_tidy_accounting_error`: a "N warnings generated." summary (or a header-filter
/// hint without any summary) that no diagnostic or suppression trailer accounts for is not a
/// clean run.
fn clang_tidy_accounting(output: &str, parsed: &Parsed) -> Result<(), ProviderError> {
    let generated = Regex::new(r"^[1-9]\d* warnings? generated\.$").expect("constant regex");
    let suppressed = Regex::new(r"^Suppressed [1-9]\d* warnings? \([^\r\n]{1,512}\)\.$")
        .expect("constant regex");
    let hint = Regex::new(r"^Use -header-filter=.{1,512}(?: or leave it as default)? to display errors from all non-system headers\.(?: Use -system-headers to display errors from system headers as well\.)?$").expect("constant regex");
    let lines: Vec<&str> = output.lines().map(str::trim).collect();
    let any = |pattern: &Regex| lines.iter().any(|line| pattern.is_match(line));
    let accounted = !parsed.findings.is_empty()
        || any(&suppressed)
        || parsed
            .limitations
            .iter()
            .any(|item| item == "external compiler diagnostic omitted from project locations");
    if accounted {
        return Ok(());
    }
    if any(&generated) {
        return Err(ProviderError(
            "clang-tidy generated-warning summary has no diagnostic accounting".into(),
        ));
    }
    if any(&hint) {
        return Err(ProviderError(
            "clang-tidy header-filter hint has no diagnostic summary".into(),
        ));
    }
    Ok(())
}
/// Frozen `_consume_clang_tidy_summary` refuses a second "N warnings generated." and a second
/// "Suppressed N warnings (...)." line. R3 correction (cpp28, real clang-tidy 21.1.8 batches,
/// `tests/rust-parity/cpp/tidy-batch-transcripts.json`): a `-p` batch over several sources
/// prints `[k/N] Processing file <source>.` before each compile command and, after each command
/// once the running total of the whole process is above zero, that total ("1 warning
/// generated." then "2 warnings generated."; a command adding none repeats it). Exactly that
/// shape is accepted: no summary before the first progress line, at most one per command,
/// never decreasing, none missing once the total is above zero, and the suppression trailer at
/// most once and after the last command. Without progress lines (one source) frozen's single
/// summary rule stands. Everything else still fails the parse.
///
/// The shared parser skips any line that only begins like a summary. A next batch answers on
/// exit 0 (cpp28 real clang-tidy 21.1.8 captures), where clang-tidy's own summary lines are
/// exactly "N warning(s) generated." with N above zero, then once "Suppressed N warnings (...)."
/// and, after it, once the header-filter hint; a compile error makes it exit 1 and is never
/// read. Any other line that begins like one of the shared summary or context prefixes (a
/// trailing remainder, a zero count, an error summary, "Error while processing", a summary
/// after the trailer, a trailer without a total) is output nobody accounted for.
fn next_tidy_summaries(output: &str) -> Result<(), ProviderError> {
    let progress =
        Regex::new(r"^\[[1-9]\d*/[1-9]\d*\] Processing file /.+\.$").expect("constant regex");
    let generated = Regex::new(r"^([1-9]\d*) warnings? generated\.$").expect("constant regex");
    let suppressed = Regex::new(r"^Suppressed [1-9]\d* warnings? \([^\r\n]{1,512}\)\.$")
        .expect("constant regex");
    let hint = Regex::new(r"^Use -header-filter=.{1,512}(?: or leave it as default)? to display errors from all non-system headers\.(?: Use -system-headers to display errors from system headers as well\.)?$").expect("constant regex");
    // The summary alternatives of the shared parser's open-ended context pattern.
    let summary_like = Regex::new(r"^(?:\d+ warnings? |\d+ errors? |Suppressed \d+ warnings?|Use -header-filter=|Error while processing |compilation terminated\.|Found compiler error|Error while trying to load|Running without flags)").expect("constant regex");
    let refuse = |reason: &str| Err(ProviderError(reason.into()));
    let mut commands = 0usize;
    let mut summarized = false;
    let mut total: Option<u64> = None;
    let mut trailer = false;
    let mut hinted = false;
    for line in output.lines().map(str::trim) {
        if progress.is_match(line) {
            if commands == 0 && total.is_some() {
                return refuse(
                    "clang-tidy generated-warning summary precedes the first processed file",
                );
            }
            if total.is_some() && !summarized {
                return refuse(
                    "clang-tidy command after a warning has no generated-warning summary",
                );
            }
            if trailer || hinted {
                return refuse("clang-tidy suppression summary precedes a processed file");
            }
            commands += 1;
            summarized = false;
        } else if let Some(captures) = generated.captures(line) {
            let count: u64 = captures[1].parse().map_err(|_| {
                ProviderError("clang-tidy generated-warning count is out of range".into())
            })?;
            if trailer || hinted {
                return refuse(
                    "clang-tidy generated-warning summary follows the suppression summary",
                );
            }
            if summarized || (commands == 0 && total.is_some()) {
                return refuse("clang-tidy emitted duplicate generated-warning summaries");
            }
            if total.is_some_and(|previous| count < previous) {
                return refuse("clang-tidy generated-warning total decreased");
            }
            total = Some(count);
            summarized = true;
        } else if suppressed.is_match(line) {
            if trailer || hinted {
                return refuse("clang-tidy emitted duplicate suppression summaries");
            }
            if total.is_none() {
                return refuse("clang-tidy suppression summary has no generated-warning summary");
            }
            trailer = true;
        } else if hint.is_match(line) {
            if hinted || !trailer {
                return refuse(
                    "clang-tidy header-filter hint is not after one suppression summary",
                );
            }
            hinted = true;
        } else if summary_like.is_match(line) {
            return Err(ProviderError(format!(
                "unrecognized clang-tidy summary line: {line}"
            )));
        }
    }
    if commands > 0 && total.is_some() && !summarized {
        return refuse("clang-tidy command after a warning has no generated-warning summary");
    }
    Ok(())
}
fn parse_text(output: &str, plan: &ProviderPlan) -> Result<Parsed, ProviderError> {
    // Frozen _TEXT_DIAGNOSTIC_RE: the column is optional (GCC -fno-show-column, old drivers).
    let diagnostic = Regex::new(r"^(.+?):([1-9]\d*)(?::([1-9]\d*))?:\s*(fatal error|error|warning|note|remark):\s*(\S.*?)(?:\s+\[([^\]]+)\])?$").expect("constant regex");
    let fixit = Regex::new(r#"^fix-it:("(?:[^"\\]|\\.)*"):\{([1-9]\d*):([1-9]\d*)-([1-9]\d*):([1-9]\d*)\}:("(?:[^"\\]|\\.)*")$"#).expect("constant regex");
    // Frozen _TEXT_CONTEXT_HEADER_RE / _TEXT_REQUIRED_FROM_RE ancillary GCC context lines.
    let frozen_context = Regex::new(r"^(?:.+:\s+(?:At global scope|In (?:function|member function|constructor|destructor|lambda function|instantiation of)(?: .*)?):|.+:[1-9]\d*(?::[1-9]\d*)?:\s+required (?:from|by)\s+\S.*)$").expect("constant regex");
    let context = Regex::new(r"^(?:In file included from |\s+from |.*: In (?:function|member function|instantiation)|\d+ warnings? generated\.|\d+ errors? generated\.|Suppressed \d+ warnings?|Use -header-filter=|Error while processing |compilation terminated\.)").expect("constant regex");
    // clang-tidy 18+ prints `[n/N] Processing file <absolute source>.` on stderr before each
    // compile command when it is given several sources. The counter rises by one per command
    // (it can exceed N when the database holds several commands for one file); N is the number
    // of sources on the command line and the path is one of them.
    let progress =
        Regex::new(r"^\[([1-9]\d*)/([1-9]\d*)\] Processing file (/.+)\.$").expect("constant regex");
    let mut last_progress: Option<u32> = None;
    let mut parsed = Parsed {
        merge_identical: plan.context.next_tidy,
        ..Default::default()
    };
    let mut saw_diagnostic = false;
    let mut fixit_owner = false;
    let mut count = 0;
    for line in output.lines().filter(|line| !line.trim().is_empty()) {
        if plan.kind == ProviderKind::ClangTidy
            && let Some(captures) = progress.captures(line)
        {
            let index = number(&captures[1])?;
            let total = number(&captures[2])? as usize;
            let source = relative_location(&captures[3], &plan.context).ok();
            if total < 2
                || total != plan.context.targets.len()
                || index != last_progress.map_or(1, |last| last + 1)
                || !source.is_some_and(|source| plan.context.targets.contains(&source))
            {
                return Err(ProviderError(format!(
                    "unexpected clang-tidy progress line: {line}"
                )));
            }
            last_progress = Some(index);
            continue;
        }
        if let Some(captures) = diagnostic.captures(line) {
            count += 1;
            if count > MAX_DIAGNOSTICS {
                return Err(ProviderError("C++ diagnostic count exceeds bounds".into()));
            }
            if plan.kind == ProviderKind::ClangTidy {
                clang_tidy_structure(&parsed, saw_diagnostic, &captures[4], captures.get(6))?;
            }
            saw_diagnostic = true;
            fixit_owner = true;
            let row = number(&captures[2])?;
            let column = captures
                .get(3)
                .map(|value| number(value.as_str()))
                .transpose()?;
            let located = relative_location(&captures[1], &plan.context);
            // A batch spelling that names no file or several is unattributable, never external.
            if let Err(error) = &located
                && error.0.starts_with("relative clang-tidy location ")
            {
                return Err(error.clone());
            }
            if located.is_err() {
                if plan.context.legacy {
                    // Frozen `_diagnostic_path`: an external location is the `[external]`
                    // sentinel with the tool's own line and column.
                    let mut current = finding(
                        plan,
                        &external_path(plan),
                        row,
                        column,
                        captures.get(6).map_or("diagnostic", |value| value.as_str()),
                        &captures[5],
                        if captures[4].contains("error") {
                            "high"
                        } else {
                            "medium"
                        },
                    )?;
                    current.primary_location.start_column = column;
                    append(&mut parsed, plan, current, &captures[4])?;
                    fixit_owner = false;
                    continue;
                }
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
            // Frozen `ici next` ClangTidyProvider keeps the shared parser's `<kind>: <message>`
            // target message as the finding message, and its fingerprint covers that text.
            // Frozen `ici next` CompilerDiagnosticsProvider does the same (`warning: …`).
            let message = if (plan.kind == ProviderKind::ClangTidy && !plan.context.legacy)
                || plan.context.next_compiler
            {
                format!("{}: {}", &captures[4], &captures[5])
            } else {
                captures[5].to_owned()
            };
            let current = finding(
                plan,
                &captures[1],
                row,
                column,
                rule,
                &message,
                if captures[4].contains("error") {
                    "high"
                } else {
                    "medium"
                },
            )?;
            append_raw(&mut parsed, current, &captures[4], &captures[5])?;
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
            || frozen_context.is_match(line.trim())
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
/// Frozen `_normalize_clang_tidy` (_cpp_diagnostics.py:990-1037): every clang-tidy primary
/// diagnostic names its check, a note follows a primary diagnostic, and a note that names a check
/// names its primary's. A primary omitted as external still counts as the note's predecessor
/// (frozen keeps it as the `[external]` sentinel), so only a note before any diagnostic is
/// orphaned.
fn clang_tidy_structure(
    parsed: &Parsed,
    saw_diagnostic: bool,
    kind: &str,
    rule: Option<regex::Match<'_>>,
) -> Result<(), ProviderError> {
    if kind != "note" {
        return match rule {
            Some(_) => Ok(()),
            None => Err(ProviderError(
                "clang-tidy diagnostic has no check identifier".into(),
            )),
        };
    }
    if !saw_diagnostic {
        return Err(ProviderError(
            "clang-tidy note has no preceding primary diagnostic".into(),
        ));
    }
    let parent = parsed
        .attach
        .or_else(|| parsed.findings.len().checked_sub(1))
        .and_then(|index| parsed.findings.get(index));
    if let (Some(rule), Some(parent)) = (rule, parent)
        && rule.as_str() != parent.native_rule_id
    {
        return Err(ProviderError(
            "clang-tidy note check does not match its primary diagnostic".into(),
        ));
    }
    Ok(())
}

fn number(value: &str) -> Result<u32, ProviderError> {
    value
        .parse::<u32>()
        .ok()
        .filter(|value| *value > 0 && *value <= i32::MAX as u32)
        .ok_or_else(|| ProviderError("invalid C++ coordinate".into()))
}
/// The synthetic absolute path whose project-relative form is the frozen `[external]`.
fn external_path(plan: &ProviderPlan) -> String {
    plan.context
        .workspace_root
        .join("[external]")
        .to_string_lossy()
        .into_owned()
}

/// Child diagnostics of a legacy `[external]` diagnostic (frozen keeps external fix-its out of
/// project locations; notes attach to their parent).
fn legacy_external_children(
    value: &Value,
    plan: &ProviderPlan,
    sink: &mut Parsed,
    depth: usize,
    count: &mut usize,
) -> Result<(), ProviderError> {
    if let Some(children) = value.get("children") {
        for child in children
            .as_array()
            .filter(|children| children.len() <= MAX_DIAGNOSTICS)
            .ok_or_else(|| ProviderError("unbounded C++ child diagnostics".into()))?
        {
            json_entry(child, plan, sink, depth + 1, count)?;
        }
    }
    Ok(())
}

fn append(
    parsed: &mut Parsed,
    _plan: &ProviderPlan,
    current: ici_domain::Finding,
    kind: &str,
) -> Result<(), ProviderError> {
    let raw = current.message.clone();
    append_raw(parsed, current, kind, &raw)
}
/// Appends a primary diagnostic or attaches a note; `raw` is the tool's message without the
/// severity kind (the legacy message and the note label add it once).
fn append_raw(
    parsed: &mut Parsed,
    current: ici_domain::Finding,
    kind: &str,
    raw: &str,
) -> Result<(), ProviderError> {
    if current.message.chars().count() > 8192 || parsed.findings.len() >= MAX_DIAGNOSTICS {
        return Err(ProviderError(
            "C++ diagnostic count/message exceeds bounds".into(),
        ));
    }
    if kind == "note" && !parsed.findings.is_empty() {
        let mut location = current.primary_location;
        location.label = format!("note: {raw}");
        let index = parsed.attach.unwrap_or(parsed.findings.len() - 1);
        let related = &mut parsed.findings[index].related_locations;
        // A repeated primary keeps its first notes and adds only the ones it did not have.
        if parsed.attach.is_none() || !related.contains(&location) {
            related.push(location);
        }
        return Ok(());
    }
    if parsed.merge_identical
        && let Some(index) = parsed.findings.iter().position(|earlier| {
            earlier.fingerprint == current.fingerprint
                && earlier.rule_id == current.rule_id
                && earlier.message == current.message
                && earlier.primary_location == current.primary_location
                && earlier.provider == current.provider
        })
    {
        parsed.attach = Some(index);
        return Ok(());
    }
    parsed.attach = None;
    parsed.details.insert(
        current.fingerprint.clone(),
        json!({"legacy_message":format!("{kind}: {raw}")}),
    );
    parsed.findings.push(current);
    Ok(())
}
fn add_edit(parsed: &mut Parsed, edit: Value) -> Result<(), ProviderError> {
    let finding = match parsed.attach {
        Some(index) => parsed.findings.get(index),
        None => parsed.findings.last(),
    }
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
    if parsed.attach.is_some() && edits.contains(&edit) {
        return Ok(());
    }
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
    // Frozen `_diagnostic_path` (legacy view): an external location is kept as the
    // `[external]` sentinel with the tool's own line and columns.
    let legacy_external = external && plan.context.legacy;
    if external && kind.contains("error") && !legacy_external {
        return Err(ProviderError("unattributed external compiler error".into()));
    }
    // Validate external branches atomically, including fixes/children, without allowing a
    // child note or fix to attach to an unrelated preceding project diagnostic.
    let mut discarded = Parsed::default();
    let sink = if external && !legacy_external {
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
    if legacy_external {
        current.primary_location.start_column = column;
        current.primary_location.end_line = Some(finish_line);
        current.primary_location.end_column = finish_column;
        append(sink, plan, current, kind)?;
        return legacy_external_children(value, plan, sink, depth, count);
    }
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
