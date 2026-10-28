use crate::{
    RenderOptions, ReportError, Result, StoredResult,
    safe::{self, html, plain},
    view::{View, informational, location, marks, suppressed},
};
use serde_json::Value;
use std::fmt::Write;

pub fn render_console(stored: &StoredResult, options: &RenderOptions) -> Result<String> {
    let view = View::new(stored)?;
    let mut out = format!("{}: {}\n", options.title, view.verdict());
    let _ = writeln!(
        out,
        "Scope, execution, gate and publication: {}",
        crate::canonical_json(&view.axes())?
    );
    let _ = writeln!(
        out,
        "{} findings; {} suppressed",
        view.findings.len(),
        view.findings
            .iter()
            .filter(|finding| suppressed(finding))
            .count()
    );
    let mut ordered: Vec<_> = view.findings.iter().collect();
    ordered.sort_by_key(|finding| (suppressed(finding), informational(finding)));
    for finding in ordered.iter().take(options.max_findings) {
        let _ = writeln!(
            out,
            "{} [{}] {}: {} ({})",
            location(finding),
            plain(&finding["severity"]),
            plain(&finding["rule_id"]),
            plain(&finding["message"]),
            marks(finding)
        );
    }
    if view.findings.len() > options.max_findings {
        let _ = writeln!(
            out,
            "{} additional findings in saved report",
            view.findings.len() - options.max_findings
        );
    }
    for engine in &view.engines {
        let _ = writeln!(
            out,
            "{} {}: {} ({} inspected targets)",
            plain(&engine["status"]),
            plain(&engine["engine_name"]),
            plain(&engine["summary"]),
            engine["targets"].as_array().map_or(0, Vec::len)
        );
    }
    if !view.baseline().is_null() {
        let _ = writeln!(out, "Baseline: {}", crate::canonical_json(view.baseline())?);
    }
    for auxiliary in &options.auxiliary {
        auxiliary.validate()?;
        let _ = writeln!(
            out,
            "Supporting {}: {} records, imported, NOT_EVALUATED",
            auxiliary.format,
            auxiliary.records.len()
        );
    }
    Ok(safe::terminal(&ici_execution::redaction::redact_text(&out)))
}
fn md(text: &str) -> String {
    html(text)
        .replace('\\', "\\\\")
        .replace('`', "\\`")
        .replace('|', "\\|")
        .replace('*', "\\*")
        .replace('_', "\\_")
        .replace('[', "\\[")
        .replace(']', "\\]")
        .replace('\r', "")
        .replace('\n', "<br>")
}
pub fn render_markdown(stored: &StoredResult, options: &RenderOptions) -> Result<String> {
    let view = View::new(stored)?;
    let mut out = format!(
        "# {}\n\n**{}**\n\n",
        md(&ici_execution::redaction::redact_text(&options.title)),
        view.verdict()
    );
    out.push_str("## Scope and execution\n\n");
    out.push_str(&format!(
        "<pre>{}</pre>\n\n",
        html(&crate::canonical_json(&view.axes())?)
    ));
    out.push_str("## Findings\n\n| Location | Rule | Severity | Message | Evidence and marks |\n| --- | --- | --- | --- | --- |\n");
    let mut ordered: Vec<_> = view.findings.iter().collect();
    ordered.sort_by_key(|finding| (suppressed(finding), informational(finding)));
    for finding in ordered {
        let _ = writeln!(
            out,
            "| {} | {} | {} | {} | {} |",
            md(&location(finding)),
            md(&plain(&finding["rule_id"])),
            md(&plain(&finding["severity"])),
            md(&plain(&finding["message"])),
            md(&marks(finding))
        );
        if !suppressed(finding) && !informational(finding) {
            for related in finding["related_locations"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default()
            {
                let _ = writeln!(
                    out,
                    "| Related: {}:{} | | | {} | |",
                    md(&plain(&related["path"])),
                    plain(&related["start_line"]),
                    md(&plain(&related["label"]))
                );
            }
        }
    }
    for finding in view
        .findings
        .iter()
        .filter(|finding| !suppressed(finding) && !informational(finding))
    {
        for key in ["explanation", "remediation", "fixes"] {
            if let Some(detail) = finding.get(key).filter(|value| {
                !value.is_null() && *value != "" && *value != &serde_json::json!([])
            }) {
                let _ = writeln!(
                    out,
                    "\n{} ({}): {}\n",
                    md(key),
                    md(&plain(&finding["rule_id"])),
                    md(&plain(detail))
                );
            }
        }
    }
    for engine in &view.engines {
        let _ = write!(
            out,
            "\n## {}\n\n{} — {}\n\n",
            md(&plain(&engine["engine_name"])),
            plain(&engine["status"]),
            md(&plain(&engine["summary"]))
        );
        out.push_str("| Target | Status | Message | Metrics |\n| --- | --- | --- | --- |\n");
        let targets = engine["targets"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default();
        let mut targets: Vec<_> = targets.iter().collect();
        targets.sort_by_key(|target| target["status"] == "PASS");
        for target in targets.iter().take(options.max_target_rows) {
            let _ = writeln!(
                out,
                "| {}:{} {} | {} | {} | {} |",
                md(&plain(&target["file_path"])),
                plain(&target["start_line"]),
                md(&plain(&target["target_name"])),
                plain(&target["status"]),
                md(&plain(&target["message"])),
                md(&plain(&target["metrics"]))
            );
        }
        if targets.len() > options.max_target_rows {
            let _ = writeln!(
                out,
                "\n{} additional targets retained in JSON/HTML.\n",
                targets.len() - options.max_target_rows
            );
        }
    }
    if !view.metrics().is_null() {
        let _ = write!(
            out,
            "\n## Saved measurements\n\n<pre>{}</pre>\n",
            html(&crate::canonical_json(view.metrics())?)
        );
    }
    if !view.baseline().is_null() {
        let _ = write!(
            out,
            "\n## Baseline\n\n<pre>{}</pre>\n",
            html(&crate::canonical_json(view.baseline())?)
        );
    }
    for document in &options.auxiliary {
        document.validate()?;
        let _ = write!(
            out,
            "\n## Supporting {}\n\n{} records; imported; NOT_EVALUATED. Source: {}. Scope: {}.\n",
            document.format,
            document.records.len(),
            md(&document.source.path),
            md(&document.scope)
        );
    }
    if out.len() > options.max_markdown_bytes {
        return Err(ReportError(format!(
            "Markdown exceeds {} byte output budget; use saved JSON/HTML",
            options.max_markdown_bytes
        )));
    }
    Ok(out)
}

/// GitHub workflow commands escape control separators before caller publication.
pub fn github_annotations(stored: &StoredResult, options: &RenderOptions) -> Result<Vec<String>> {
    fn escape(text: &str, property: bool) -> String {
        let text = text
            .replace('%', "%25")
            .replace('\r', "%0D")
            .replace('\n', "%0A");
        if property {
            text.replace(':', "%3A").replace(',', "%2C")
        } else {
            text
        }
    }
    let view = View::new(stored)?;
    Ok(view
        .findings
        .iter()
        .filter(|finding| !suppressed(finding) && !informational(finding))
        .take(options.max_annotations)
        .map(|finding| {
            let level = match finding["severity"].as_str() {
                Some("error" | "critical" | "high") => "error",
                Some("info" | "note" | "low") => "notice",
                _ => "warning",
            };
            let span = &finding["primary_location"];
            format!(
                "::{level} file={},line={},title={}::{}",
                escape(&plain(&span["path"]), true),
                span["start_line"].as_u64().unwrap_or(1),
                escape(&plain(&finding["rule_id"]), true),
                escape(
                    &format!("{} ({})", plain(&finding["message"]), marks(finding)),
                    false
                )
            )
        })
        .collect())
}

pub(crate) fn rich(value: &Value, out: &mut String) -> Result<()> {
    match value {
        Value::Object(items) => {
            out.push_str("<dl>");
            for (key, value) in items {
                let _ = write!(out, "<dt>{}</dt><dd>", html(key));
                rich(value, out)?;
                out.push_str("</dd>");
            }
            out.push_str("</dl>");
        }
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("<span class=muted>none recorded</span>");
            } else if items.iter().all(Value::is_object) {
                let keys: std::collections::BTreeSet<_> = items
                    .iter()
                    .filter_map(Value::as_object)
                    .flat_map(|item| item.keys())
                    .collect();
                out.push_str("<div class=scroll><table><thead><tr>");
                for key in &keys {
                    let _ = write!(out, "<th>{}</th>", html(key));
                }
                out.push_str("</tr></thead><tbody>");
                for item in items {
                    out.push_str("<tr data-search-row>");
                    for key in &keys {
                        out.push_str("<td>");
                        if ["file_path", "path"].contains(&key.as_str())
                            && item["start_line"].as_u64().is_some()
                        {
                            out.push_str(&copy_location(
                                item[*key].as_str().unwrap_or("unknown"),
                                item["start_line"].as_u64().unwrap_or(1),
                                item["start_column"].as_u64(),
                            ));
                        } else {
                            rich(&item[*key], out)?;
                        }
                        out.push_str("</td>");
                    }
                    out.push_str("</tr>");
                }
                out.push_str("</tbody></table></div>");
            } else {
                out.push_str("<ul>");
                for item in items {
                    out.push_str("<li>");
                    rich(item, out)?;
                    out.push_str("</li>");
                }
                out.push_str("</ul>");
            }
        }
        Value::String(text) if text.contains(['\n', '\r']) => {
            let _ = write!(out, "<pre>{}</pre>", html(text));
        }
        _ => out.push_str(&html(&plain(value))),
    }
    if out.len() > 128 * 1024 * 1024 {
        return Err(ReportError(
            "HTML expansion exceeds 128 MiB output bound".into(),
        ));
    }
    Ok(())
}

pub fn render_html(stored: &StoredResult, options: &RenderOptions) -> Result<String> {
    let view = View::new(stored)?;
    let title = ici_execution::redaction::redact_text(&options.title);
    let mut out = format!(
        "<!DOCTYPE html>\n<html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width, initial-scale=1\"><meta http-equiv=Content-Security-Policy content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'\"><title>{}</title><style>{}</style></head><body><main><h1>{}</h1><p class=verdict>{}</p>",
        html(&title),
        include_str!("../assets/native.css"),
        html(&title),
        html(&view.verdict())
    );
    out.push_str("<h2>Scope, execution and publication</h2>");
    rich(&view.axes(), &mut out)?;
    let suppressed_count = view
        .findings
        .iter()
        .filter(|finding| suppressed(finding))
        .count();
    let _ = write!(
        out,
        "<h2 id=issues>Issues ({})</h2><p>{suppressed_count} suppressed; every finding remains in saved JSON.</p><input id=finding-filter type=search placeholder=\"Filter findings and targets\" aria-label=\"Filter findings and targets\"><span id=filter-count aria-live=polite></span>",
        view.findings
            .iter()
            .filter(|finding| !suppressed(finding) && !informational(finding))
            .count()
    );
    for finding in view
        .findings
        .iter()
        .filter(|finding| !suppressed(finding) && !informational(finding))
    {
        finding_card(finding, &mut out)?;
    }
    let informational_count = view
        .findings
        .iter()
        .filter(|finding| !suppressed(finding) && informational(finding))
        .count();
    if informational_count > 0 {
        let _ = write!(
            out,
            "<details><summary>{informational_count} informational inventory records</summary>"
        );
        for finding in view
            .findings
            .iter()
            .filter(|finding| !suppressed(finding) && informational(finding))
        {
            finding_card(finding, &mut out)?;
        }
        out.push_str("</details>");
    }
    if suppressed_count > 0 {
        let _ = write!(
            out,
            "<details><summary>{suppressed_count} suppressed findings</summary>"
        );
        for finding in view.findings.iter().filter(|finding| suppressed(finding)) {
            finding_card(finding, &mut out)?;
        }
        out.push_str("</details>");
    }
    if view.findings.is_empty() {
        out.push_str("<p class=muted>No findings recorded.</p>");
    }
    out.push_str("<h2>Measurements</h2>");
    rich(view.metrics(), &mut out)?;
    if !view.baseline().is_null() {
        out.push_str("<h2>Baseline comparison</h2>");
        rich(view.baseline(), &mut out)?;
    }
    crate::facets::render(&view.engines, &mut out)?;
    for key in [
        "support_matrix",
        "capability_inventory",
        "analysis_context",
        "analysis_metadata",
    ] {
        if !view.document[key].is_null() {
            let _ = write!(out, "<h2>{}</h2>", html(key));
            rich(&view.document[key], &mut out)?;
        }
    }
    for document in &options.auxiliary {
        document.validate()?;
        out.push_str("<h2>Supporting external evidence — NOT_EVALUATED</h2>");
        rich(&safe::redact(&document.to_value()?), &mut out)?;
    }
    // Complete escaped payload survives the presentation, including unknown producer fields.
    let _ = writeln!(
        out,
        "<details><summary>Complete saved result</summary><pre id=raw-result></pre></details><script id=ici-result type=application/json>{}</script><script>{}</script></main></body></html>",
        crate::script_json(&view.document)?,
        include_str!("../assets/native.js")
    );
    if out.len() > 128 * 1024 * 1024 {
        return Err(ReportError(
            "HTML expansion exceeds 128 MiB output bound".into(),
        ));
    }
    Ok(out)
}
fn copy_location(path: &str, line: u64, column: Option<u64>) -> String {
    let text = format!(
        "{path}:{line}{}",
        column.map_or(String::new(), |column| format!(":{column}"))
    );
    format!(
        "<code>{}</code> <button type=button data-copy-location=\"{}\" aria-label=\"Copy location {}\">Copy</button>",
        html(&text),
        html(&text),
        html(&text)
    )
}
fn finding_card(finding: &Value, out: &mut String) -> Result<()> {
    let _ = write!(
        out,
        "<article class=finding data-search-row><h3>{} · {}</h3><p>{} {}</p><p>{}</p><p class=muted>{}</p>",
        html(&plain(&finding["rule_id"])),
        html(&plain(&finding["severity"])),
        copy_location(
            finding["primary_location"]["path"]
                .as_str()
                .unwrap_or("unknown"),
            finding["primary_location"]["start_line"]
                .as_u64()
                .unwrap_or(1),
            finding["primary_location"]["start_column"].as_u64()
        ),
        html(&plain(&finding["message"])),
        html(&plain(&finding["explanation"])),
        html(&marks(finding))
    );
    for key in [
        "remediation",
        "snippet",
        "metrics",
        "related_locations",
        "fixes",
        "limitations",
        "suppression",
    ] {
        if finding.get(key).is_some_and(|value| {
            !value.is_null()
                && value != ""
                && value != &serde_json::json!([])
                && value != &serde_json::json!({})
        }) {
            let _ = write!(out, "<details><summary>{}</summary>", html(key));
            rich(&finding[key], out)?;
            out.push_str("</details>");
        }
    }
    out.push_str("</article>");
    Ok(())
}
