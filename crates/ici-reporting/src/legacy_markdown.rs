//! Frozen v0.13.0 `reporters/markdown.generate_markdown_report` for a historical suite: the
//! `ici verify --github-summary` step summary (status/TEM header, engine table, tool capability
//! snapshot, baseline delta, canonical issue groups and per-engine target/related rows, each
//! bounded at 100), and the bounded append payload of `write_github_step_summary`.
use crate::{Result, StoredResult, overview, view::View};
use serde_json::Value;

const MAX_ROWS: usize = 100;
const BASELINE_DETAIL_LIMIT: usize = 20;
const BASELINE_UNCHANGED_LIMIT: usize = 3;
/// Frozen `MAX_GITHUB_SUMMARY_BYTES`.
pub const MAX_SUMMARY_BYTES: usize = 900_000;
const TRUNCATION_NOTICE: &str = "\n\n> ⚠️ GitHub step summary truncated at its bounded byte limit. Full JSON and HTML reports retain all details.\n";

/// Python `str()` of a saved JSON value, redacted like frozen `redact_suite`.
fn text(value: &Value) -> String {
    let raw = match value {
        Value::String(text) => text.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    };
    ici_execution::redaction::redact_text(&raw)
}
/// `value or fallback` for a saved string field.
fn or(value: &Value, fallback: &str) -> String {
    match value {
        Value::String(text) if !text.is_empty() => text_of(text),
        Value::Null | Value::String(_) => fallback.into(),
        other => text(other),
    }
}
fn text_of(raw: &str) -> String {
    ici_execution::redaction::redact_text(raw)
}
/// Frozen `_escape_html_content`.
fn escape_with(value: &str, backticks: bool) -> String {
    let mut escaped = value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('|', "&#124;")
        .replace('[', "&#91;")
        .replace(']', "&#93;");
    if backticks {
        escaped = escaped.replace('`', "&#96;");
    }
    escaped
        .replace("\r\n", "<br>")
        .replace(['\r', '\n'], "<br>")
}
fn escape(value: &str) -> String {
    escape_with(value, true)
}
fn code(value: &str) -> String {
    format!("<code>{}</code>", escape_with(value, false))
}
fn number(value: &Value) -> Option<u64> {
    value.as_u64()
}
/// Frozen `_make_gh_link` without a repository URL (the CLI never passes one): the code-styled
/// `path#L<start>[:C<col>][-L<end>[:C<col>]|-C<col>]` display.
fn location(
    path: &str,
    start: u64,
    end: Option<u64>,
    columns: (Option<u64>, Option<u64>),
) -> String {
    let mut anchor = format!("L{start}");
    if let Some(column) = columns.0 {
        anchor.push_str(&format!(":C{column}"));
    }
    match end.filter(|end| *end > start) {
        Some(end) => {
            anchor.push_str(&format!("-L{end}"));
            if let Some(column) = columns.1 {
                anchor.push_str(&format!(":C{column}"));
            }
        }
        None => {
            if let Some(column) = columns.1.filter(|column| Some(*column) != columns.0) {
                anchor.push_str(&format!("-C{column}"));
            }
        }
    }
    code(&format!("{path}#{anchor}"))
}
fn span(location_value: &Value, columns: bool) -> String {
    location(
        &text(&location_value["path"]),
        number(&location_value["start_line"]).unwrap_or(0),
        number(&location_value["end_line"]),
        if columns {
            (
                number(&location_value["start_column"]),
                number(&location_value["end_column"]),
            )
        } else {
            (None, None)
        },
    )
}
fn array(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}
fn status_rank(status: &str) -> u8 {
    match status {
        "ERROR" | "FAIL" => 0,
        "WARN" => 1,
        "SKIP" => 2,
        _ => 3,
    }
}
fn capability_lines(inventory: &Value, lines: &mut Vec<String>) {
    let tools = array(&inventory["tools"]);
    let ready = |tool: &Value| tool["available"] == true && tool["complete"] == true;
    let count = |keep: &dyn Fn(&Value) -> bool| tools.iter().filter(|tool| keep(tool)).count();
    let health = if inventory["healthy"] == true {
        "✅ READY"
    } else {
        "⚠️ ATTENTION"
    };
    lines.push("### Tool capability snapshot\n".into());
    lines.push(format!("> **Health**: {health}  "));
    lines.push(format!(
        "> **Inventory**: {} total · {} ready · {} incomplete · {} unavailable  ",
        tools.len(),
        count(&ready),
        count(&|tool| tool["available"] == true && tool["complete"] != true),
        count(&|tool| tool["available"] != true)
    ));
    for (key, label) in [
        ("missing_required", "Missing required"),
        ("incomplete_required", "Incomplete required"),
    ] {
        let names: Vec<String> = array(&inventory[key]).iter().map(text).collect();
        if !names.is_empty() {
            lines.push(format!("> **{label}**: {}  ", code(&names.join(", "))));
        }
    }
    lines.push("\n<details>".into());
    lines.push(format!(
        "<summary><b>Complete tool inventory ({})</b></summary>\n",
        tools.len()
    ));
    lines.push("| Tool | State | Policy | Version | Details |".into());
    lines.push("|---|:---:|---|---|---|".into());
    for tool in tools {
        let state = if ready(tool) {
            "ready"
        } else if tool["available"] == true {
            "incomplete"
        } else {
            "unavailable"
        };
        let names = |key: &str| {
            array(&tool[key])
                .iter()
                .map(text)
                .collect::<Vec<_>>()
                .join(", ")
        };
        let policy = if tool["required"] == true {
            format!("required by {}", names("required_by"))
        } else if tool["optional"] == true {
            format!("optional for {}", names("optional_by"))
        } else {
            "registry".into()
        };
        let details: Vec<String> = tool["details"]
            .as_object()
            .map(|details| {
                frozen_detail_order(details)
                    .into_iter()
                    .map(|(key, value)| format!("{}={}", text_of(key), text(value)))
                    .collect()
            })
            .unwrap_or_default();
        let details = if details.is_empty() {
            "—".into()
        } else {
            details.join(", ")
        };
        lines.push(format!(
            "| {} | {} | {} | {} | {} |",
            code(&text(&tool["name"])),
            code(state),
            escape(&policy),
            escape(&or(&tool["version"], "—")),
            escape(&details)
        ));
    }
    lines.push("</details>\n".into());
}
fn delta_count(baseline: &Value, state: &str) -> usize {
    array(&baseline["entries"])
        .iter()
        .filter(|entry| {
            entry["state"]
                .as_str()
                .is_some_and(|value| value.eq_ignore_ascii_case(state))
        })
        .count()
}
fn baseline_location(value: &Value) -> String {
    if value.is_object() {
        span(value, false)
    } else {
        code("—")
    }
}
fn baseline_lines(document: &Value, engines: &[Value], lines: &mut Vec<String>) {
    let baseline = &document["baseline_comparison"];
    let gate = if baseline["gate_failed"] == true {
        "❌ FAILED"
    } else if baseline["fail_on_new"] == true {
        "✅ PASSED"
    } else {
        "INFO: NOT ENFORCED"
    };
    lines.push("### Baseline finding delta\n".into());
    lines.push(format!(
        "> **Source**: {}  ",
        code(&text(&baseline["source_path"]))
    ));
    lines.push(format!("> **Fail-on-new gate**: {gate}  "));
    lines.push(format!(
        "> **Gate reason**: {}\n",
        escape(&text_of(&overview::gate_reason(document, engines)))
    ));
    lines.push("| Delta | Count |".into());
    lines.push("|---|---:|".into());
    for (label, state) in [
        ("New", "new"),
        ("Unchanged", "unchanged"),
        ("Moved", "moved"),
        ("Resolved", "resolved"),
    ] {
        lines.push(format!(
            "| {label} | **{}** |",
            delta_count(baseline, state)
        ));
    }
    lines.push(format!(
        "| Regressed | **{}** |",
        text(&baseline["regressed_count"])
    ));
    lines.push(format!(
        "| Gated | **{}** |",
        text(&baseline["gated_count"])
    ));
    let warnings = array(&baseline["warnings"]);
    if !warnings.is_empty() {
        lines.push("\n**Compatibility warnings:**".into());
        lines.extend(
            warnings
                .iter()
                .map(|warning| format!("> ⚠️ {}", escape(&text(warning)))),
        );
    }
    let mut entries: Vec<&Value> = array(&baseline["entries"]).iter().collect();
    entries.sort_by_key(|entry| overview::delta_order(entry));
    let is_unchanged = |entry: &&Value| {
        entry["state"]
            .as_str()
            .is_some_and(|state| state.eq_ignore_ascii_case("unchanged"))
    };
    let changed: Vec<&Value> = entries
        .iter()
        .copied()
        .filter(|e| !is_unchanged(e))
        .collect();
    let unchanged: Vec<&Value> = entries.iter().copied().filter(is_unchanged).collect();
    let shown_changed = changed.len().min(BASELINE_DETAIL_LIMIT);
    let shown_unchanged = unchanged
        .len()
        .min(BASELINE_UNCHANGED_LIMIT.min(BASELINE_DETAIL_LIMIT - shown_changed));
    let visible: Vec<&Value> = changed[..shown_changed]
        .iter()
        .chain(&unchanged[..shown_unchanged])
        .copied()
        .collect();
    if !visible.is_empty() {
        lines.push("\n<details>".into());
        lines.push(format!(
            "<summary><b>Issues-first delta details ({} shown)</b></summary>\n",
            visible.len()
        ));
        lines.push("| Delta | Engine / Rule | Current location | Baseline location | Severity transition | Gate | Message |".into());
        lines.push("|---|---|---|---|:---:|:---:|---|".into());
        for entry in visible {
            let severity = |key: &str| {
                if entry[key].is_null() {
                    "—".into()
                } else {
                    text(&entry[key])
                }
            };
            lines.push(format!(
                "| `{}` | {} | {} | {} | {} | {} | {} |",
                escape(&text(&entry["state"]).to_uppercase()),
                code(&format!(
                    "{} / {}",
                    text(&entry["engine_name"]),
                    text(&entry["rule_id"])
                )),
                baseline_location(&entry["current_location"]),
                baseline_location(&entry["baseline_location"]),
                code(&format!(
                    "{} → {}",
                    severity("baseline_severity"),
                    severity("current_severity")
                )),
                if entry["gated"] == true { "❌" } else { "—" },
                escape(&or(&entry["message"], "—"))
            ));
        }
        lines.push("</details>\n".into());
    } else if !entries.is_empty() {
        lines.push("\n<details><summary>Delta details omitted</summary></details>\n".into());
    }
    let mut omitted = vec![];
    if changed.len() > shown_changed {
        omitted.push(format!(
            "{} additional delta row(s) omitted from this view",
            changed.len() - shown_changed
        ));
    }
    if unchanged.len() > shown_unchanged {
        omitted.push(format!(
            "{} unchanged row(s) omitted",
            unchanged.len() - shown_unchanged
        ));
    }
    if !omitted.is_empty() {
        lines.push(format!(
            "> Note: {}. The JSON report retains the full inventory.\n",
            omitted.join("; ")
        ));
    }
}
fn issue_lines(stored: &StoredResult, lines: &mut Vec<String>) -> Result<()> {
    let projection = crate::issues::project_issue_groups(stored)?;
    if projection.groups.is_empty() {
        return Ok(());
    }
    lines.push("### Canonical issue groups\n".into());
    lines.push(format!(
        "> **Display projection**: {} original finding(s) shown as {} group(s). JSON and baseline inventories retain every producer record.\n",
        projection.total_findings,
        projection.groups.len()
    ));
    lines.push("| Severity | Producers / Canonical rule | Location | Findings | Message |".into());
    lines.push("|:---:|---|---|---:|---|".into());
    for group in projection.groups.iter().take(MAX_ROWS) {
        let view = &group.view;
        let primary = &view["primary_location"];
        let place = if primary.is_object() && !primary["path"].is_null() {
            span(primary, true)
        } else if let Some(first) = array(&view["locations"]).first() {
            span(first, false)
        } else {
            code("—")
        };
        let provenance: Vec<String> = array(&view["provenance"]).iter().map(text).collect();
        let sources = if provenance.is_empty() {
            text(&view["engine_name"])
        } else {
            provenance.join(", ")
        };
        lines.push(format!(
            "| {} | {} | {} | **{}** | {} |",
            code(&text(&view["severity"]).to_uppercase()),
            code(&format!("{sources} → {}", text(&view["rule_id"]))),
            place,
            text(&view["original_finding_count"]),
            escape(&or(&view["message"], "—"))
        ));
    }
    if projection.groups.len() > MAX_ROWS {
        lines.push(format!(
            "\n> {} canonical issue group(s) omitted from this bounded Markdown view. The HTML report retains the full display projection.\n",
            projection.groups.len() - MAX_ROWS
        ));
    }
    Ok(())
}
/// Frozen `_related_rows_for_result`: related locations of actionable, unsuppressed findings in
/// `findings_for_result` order (fingerprint, path, line, message; target adapters carry none).
fn related_rows(engine: &Value) -> Vec<(String, &Value)> {
    let mut findings: Vec<&Value> = array(&engine["findings"])
        .iter()
        .filter(|finding| {
            !finding["severity"]
                .as_str()
                .is_some_and(|severity| severity.eq_ignore_ascii_case("info"))
                && finding["suppression"]["suppressed"] != true
        })
        .collect();
    findings.sort_by_key(|finding| {
        let primary = &finding["primary_location"];
        (
            text(&finding["fingerprint"]),
            text(&primary["path"]),
            number(&primary["start_line"]).unwrap_or(0),
            text(&finding["message"]),
        )
    });
    findings
        .into_iter()
        .flat_map(|finding| {
            let rule = or(&finding["tool_rule_id"], "");
            let rule = if rule.is_empty() {
                text(&finding["rule_id"])
            } else {
                rule
            };
            array(&finding["related_locations"])
                .iter()
                .map(move |location| (rule.clone(), location))
        })
        .collect()
}
fn fenced(path: &str, line: &Value, snippet: &str) -> String {
    let mut longest = 0;
    let mut run = 0;
    for ch in snippet.chars() {
        run = if ch == '`' { run + 1 } else { 0 };
        longest = longest.max(run);
    }
    let fence = "`".repeat((longest + 1).max(3));
    format!(
        "{fence}diff\n# {}:{}\n{snippet}\n{fence}\n",
        escape(path),
        text(line)
    )
}
/// Frozen `ToolCapability.details` in its insertion order (`_registered_details`: static
/// details, vendor/compiler family, resolved alias, Python-module provider, then the metadata
/// probe's values) without native's probe-evidence keys.
fn frozen_detail_order(details: &serde_json::Map<String, Value>) -> Vec<(&String, &Value)> {
    const NATIVE: [&str; 3] = ["analysis_executed", "evidence", "limitation"];
    let mut order: Vec<&str> = vec![];
    if details.get("provider").and_then(Value::as_str) == Some("pkg-config") {
        order.extend(["provider", "module", "qt_major"]);
    }
    order.extend([
        "vendor",
        "compiler_family",
        "resolved_alias",
        "provider",
        "module",
    ]);
    order.extend([
        "target_triple",
        "qt_version",
        "qt_major",
        "generator",
        "qt_prefix",
        "features",
        "generators",
        "server_mode",
    ]);
    let mut seen = std::collections::BTreeSet::new();
    let mut ordered = vec![];
    for key in order {
        if let Some((key, value)) = details.get_key_value(key)
            && seen.insert(key.as_str())
        {
            ordered.push((key, value));
        }
    }
    ordered.extend(
        details
            .iter()
            .filter(|(key, _)| !seen.contains(key.as_str()) && !NATIVE.contains(&key.as_str())),
    );
    ordered
}

fn engine_details(engine: &Value, lines: &mut Vec<String>) {
    let targets = array(&engine["targets"]);
    let related = related_rows(engine);
    if targets.is_empty() && related.is_empty() {
        return;
    }
    let issues = targets
        .iter()
        .filter(|target| target["status"] != "PASS")
        .count();
    let badge = if issues > 0 {
        format!(" ({issues} issues)")
    } else if !targets.is_empty() {
        format!(" ({} targets)", targets.len())
    } else {
        format!(" ({} related locations)", related.len())
    };
    lines.push("<details>".into());
    lines.push(format!(
        "<summary><b>🔍 <code>{}</code> Detailed Targets & Locations{badge}</b></summary>\n",
        escape(&text(&engine["engine_name"]))
    ));
    if !targets.is_empty() {
        lines.push("| Location | Symbol / Rule | Status | Details |".into());
        lines.push("|---|---|:---:|---|".into());
        let mut ordered: Vec<(usize, &Value)> = targets.iter().enumerate().collect();
        ordered.sort_by_key(|(index, target)| {
            (status_rank(target["status"].as_str().unwrap_or("")), *index)
        });
        for (_, target) in ordered.iter().take(MAX_ROWS) {
            lines.push(format!(
                "| {} | {} | `{}` | {} |",
                location(
                    &text(&target["file_path"]),
                    number(&target["start_line"]).unwrap_or(0),
                    number(&target["end_line"]),
                    (None, None)
                ),
                code(&or(&target["target_name"], "-")),
                text(&target["status"]),
                escape(&or(&target["message"], "-"))
            ));
        }
        if targets.len() > MAX_ROWS {
            lines.push(format!(
                "\n> {} target row(s) omitted from this bounded GitHub view. The JSON and HTML reports retain the full inventory.\n",
                targets.len() - MAX_ROWS
            ));
        }
    }
    if !related.is_empty() {
        lines.push("\n**Related diagnostic locations:**\n".into());
        lines.push("| Primary rule | Related location | Details |".into());
        lines.push("|---|---|---|".into());
        for (rule, place) in related.iter().take(MAX_ROWS) {
            lines.push(format!(
                "| {} | {} | {} |",
                code(rule),
                span(place, true),
                escape(&or(&place["label"], "Related diagnostic location"))
            ));
        }
        if related.len() > MAX_ROWS {
            lines.push(format!(
                "\n> {} related diagnostic row(s) omitted from this bounded GitHub view. The JSON and HTML reports retain the full inventory.\n",
                related.len() - MAX_ROWS
            ));
        }
    }
    let failed: Vec<&Value> = targets
        .iter()
        .filter(|target| {
            matches!(target["status"].as_str(), Some("FAIL" | "ERROR"))
                && target["snippet"].as_str().is_some_and(|s| !s.is_empty())
        })
        .collect();
    if !failed.is_empty() {
        lines.push("\n**Failure Snippets:**\n".into());
        for target in failed.iter().take(5) {
            lines.push(fenced(
                &text(&target["file_path"]),
                &target["start_line"],
                &text(&target["snippet"]),
            ));
        }
    }
    lines.push("</details>\n".into());
}
/// The frozen step-summary Markdown of a historical suite; `None` for a native run result.
pub fn legacy_summary_markdown(stored: &StoredResult) -> Result<Option<String>> {
    if !matches!(stored, StoredResult::Legacy(_)) {
        return Ok(None);
    }
    let view = View::new(stored)?;
    let document = &view.document;
    let status = document["suite_status"].as_str().unwrap_or("ERROR");
    let emoji = match status {
        "PASS" => "✅",
        "WARN" => "⚠️",
        "FAIL" => "❌",
        "SKIP" => "⏭️",
        _ => "🛑",
    };
    let tem = document["tem_score"]
        .as_f64()
        .map_or_else(String::new, |tem| {
            format!(
                " (TEM: **`{tem:.2} / {:.1}`**)",
                document["max_tem_score"].as_f64().unwrap_or(5.0)
            )
        });
    let count = |key: &str| document[key].as_u64().unwrap_or(0);
    let engines = array(&document["results"]);
    let mut lines = vec![
        format!("## {emoji} `ici` Verification Report — **`{status}`**{tem}\n"),
        format!(
            "> **Summary**: Total {} engines executed in {:.2}s. (**{} Passed**, **{} Warnings**, **{} Failed**, **{} Errors**, **{} Skipped**, **{} Cache Hits**)\n",
            count("total_count"),
            document["duration"].as_f64().unwrap_or(0.0),
            count("passed_count"),
            count("warned_count"),
            count("failed_count").saturating_sub(count("error_count")),
            count("error_count"),
            count("skipped_count"),
            engines
                .iter()
                .filter(|engine| engine["cache_hit"] == true)
                .count()
        ),
        "| Engine | Status | Summary | Score / Metrics | Cache | Duration |".into(),
        "|---|:---:|---|---|:---:|:---:|".into(),
    ];
    for engine in engines {
        let badge = match engine["status"].as_str().unwrap_or("") {
            "PASS" => "🟢 `PASS`",
            "WARN" => "🟡 `WARN`",
            "FAIL" => "🔴 `FAIL`",
            "SKIP" => "⏭️ `SKIP`",
            _ => "🛑 `ERROR`",
        };
        let score = text_of(&overview::score_display(engine));
        let score = if score == "-" {
            score
        } else {
            format!("<strong>{}</strong>", code(&score))
        };
        let duration = match engine["duration"].as_f64() {
            Some(seconds) if seconds > 0.0 => format!("{seconds:.2}s"),
            _ => "-".into(),
        };
        lines.push(format!(
            "| <strong>{}</strong> | {badge} | {} | {score} | {} | {duration} |",
            code(&text(&engine["engine_name"])),
            escape(&text(&engine["summary"])),
            if engine["cache_hit"] == true {
                "HIT"
            } else {
                "—"
            }
        ));
    }
    if document["capability_inventory"].is_object() {
        capability_lines(&document["capability_inventory"], &mut lines);
    }
    if document["baseline_comparison"].is_object() {
        baseline_lines(document, engines, &mut lines);
    }
    issue_lines(stored, &mut lines)?;
    lines.push("\n---\n".into());
    for engine in engines {
        engine_details(engine, &mut lines);
    }
    Ok(Some(lines.join("\n")))
}
/// Frozen `_bounded_summary_payload`: one UTF-8 append of at most `maximum` bytes.
pub fn bounded_summary_payload(markdown: &str, maximum: usize) -> String {
    if maximum == 0 {
        return String::new();
    }
    let payload = format!("\n{markdown}\n");
    if payload.len() <= maximum {
        return payload;
    }
    let cut = |text: &str, bytes: usize| {
        let mut end = bytes.min(text.len());
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text[..end].to_owned()
    };
    if maximum <= TRUNCATION_NOTICE.len() {
        return cut(TRUNCATION_NOTICE, maximum);
    }
    cut(&payload, maximum - TRUNCATION_NOTICE.len())
        .trim_end()
        .to_owned()
        + TRUNCATION_NOTICE
}
