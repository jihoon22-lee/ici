use crate::{
    RenderOptions, ReportError, Result, StoredResult,
    safe::{self, html, plain},
    view::{View, informational, location, marks, suppressed},
};
use serde_json::{Value, json};
use std::fmt::Write;
/// Output boundary; StoredResult::to_json instead preserves the raw archive for round trips.
pub fn render_json(stored: &StoredResult) -> Result<String> {
    let view = View::new(stored)?;
    let text = ici_domain::canonical_json(&view.document)?;
    if text.len() > crate::json::MAX_STORED_BYTES {
        return Err(ReportError(
            "Redacted JSON exceeds 64 MiB stored-result bound".into(),
        ));
    }
    Ok(text)
}

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
    let projection = crate::issues::project(&view)?;
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    let mut visible = 0;
    let mut visible_groups = 0;
    let mut current_bucket = String::new();
    for group in &projection.groups {
        let engine = plain(&group.view["engine_name"]);
        let count = counts.entry(engine).or_default();
        if !options.verbose && *count >= options.max_findings {
            continue;
        }
        *count += 1;
        visible += group.findings.len();
        visible_groups += 1;
        let bucket = group_bucket(&group.view, options.group_by);
        if bucket != current_bucket {
            let _ = writeln!(out, "{bucket}");
            current_bucket = bucket;
        }
        let _ = writeln!(
            out,
            "  [{}] {}: {}",
            plain(&group.view["severity"]),
            plain(&group.view["rule_id"]),
            plain(&group.view["message"])
        );
        for location in group.view["locations"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .take(options.max_locations)
        {
            let _ = writeln!(
                out,
                "    {}:{}-{}",
                plain(&location["path"]),
                plain(&location["start_line"]),
                plain(&location["end_line"])
            );
        }
        let _ = writeln!(
            out,
            "    {} original findings represented · {}",
            group.findings.len(),
            plain(&group.view["provenance"])
        );
    }
    let _ = writeln!(
        out,
        "{} actionable finding(s) shown as {} group(s); {} hidden finding(s), {} hidden group(s)",
        visible,
        visible_groups,
        projection.total_findings - visible,
        projection.groups.len() - visible_groups
    );
    for limitation in projection.limitations {
        let _ = writeln!(out, "Display limitation: {limitation}");
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
        let _ = writeln!(
            out,
            "Baseline: {}",
            crate::canonical_json(&baseline_preview(view.baseline()))?
        );
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
    Ok(wrap_console(
        &safe::terminal(&ici_execution::redaction::redact_text(&out)),
        options.console_width,
    ))
}
fn group_bucket(group: &Value, by: crate::ConsoleGroupBy) -> String {
    use crate::ConsoleGroupBy;
    let (label, value) = match by {
        ConsoleGroupBy::Engine => ("Engine", plain(&group["engine_name"])),
        ConsoleGroupBy::Severity => ("Severity", plain(&group["severity"])),
        ConsoleGroupBy::Category => ("Category", plain(&group["category"])),
        ConsoleGroupBy::Rule => ("Rule", plain(&group["rule_id"])),
        ConsoleGroupBy::File => {
            let paths: std::collections::BTreeSet<_> = group["locations"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default()
                .iter()
                .map(|location| plain(&location["path"]))
                .collect();
            (
                "File",
                if paths.len() == 1 {
                    paths.into_iter().next().expect("one path")
                } else {
                    "(multiple files)".into()
                },
            )
        }
    };
    format!("{label}: {value}")
}
fn wrap_console(text: &str, width: usize) -> String {
    use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
    let width = width.clamp(20, 500);
    let mut out = String::new();
    for line in text.lines() {
        let mut current = String::new();
        let mut cells = 0;
        for word in line.split_whitespace() {
            let word_cells = UnicodeWidthStr::width(word);
            if !current.is_empty() && cells + 1 + word_cells > width {
                out.push_str(&current);
                out.push('\n');
                current.clear();
                cells = 0;
            }
            if !current.is_empty() {
                current.push(' ');
                cells += 1;
            }
            for character in word.chars() {
                let next = UnicodeWidthChar::width(character).unwrap_or(0);
                if cells + next > width && !current.is_empty() {
                    out.push_str(&current);
                    out.push('\n');
                    current.clear();
                    cells = 0;
                }
                current.push(character);
                cells += next;
            }
        }
        out.push_str(&current);
        out.push('\n');
    }
    out
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
    crate::navigation::config(options)?;
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
    let projection = crate::issues::project(&view)?;
    let _ = write!(
        out,
        "## Canonical issue groups\n\n{} original finding(s) shown as {} group(s). Saved JSON and baseline identities are unchanged.\n\n| Rule | Message | Original findings | Provenance |\n| --- | --- | --- | --- |\n",
        projection.total_findings,
        projection.groups.len()
    );
    for group in &projection.groups {
        let _ = writeln!(
            out,
            "| {} | {} | {} | {} |",
            md(&plain(&group.view["rule_id"])),
            md(&plain(&group.view["message"])),
            group.findings.len(),
            md(&plain(&group.view["provenance"]))
        );
    }
    out.push('\n');
    out.push_str("## Findings\n\n| Location | Rule | Severity | Message | Evidence and marks |\n| --- | --- | --- | --- | --- |\n");
    let mut ordered: Vec<_> = view.findings.iter().collect();
    ordered.sort_by_key(|finding| (suppressed(finding), informational(finding)));
    for finding in ordered {
        let _ = writeln!(
            out,
            "| {} | {} | {} | {} | {} |",
            markdown_location(finding, options)?,
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
            html(&crate::canonical_json(&baseline_preview(view.baseline()))?)
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
        return bounded_summary(&out, options.max_markdown_bytes);
    }
    Ok(out)
}
pub fn bounded_summary(text: &str, maximum: usize) -> Result<String> {
    if text.len() <= maximum {
        return Ok(text.into());
    }
    let notice = "\n\n> Summary truncated at its bounded UTF-8 byte limit. Complete findings and targets remain in saved JSON/HTML.\n";
    if maximum < notice.len() {
        return Err(ReportError(
            "Markdown byte budget is smaller than the truncation notice".into(),
        ));
    }
    let mut end = maximum - notice.len();
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    let mut out = text[..end].to_string();
    out.push_str(notice);
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
    let mut candidates: Vec<_> = view
        .findings
        .iter()
        .filter(|finding| !suppressed(finding) && !informational(finding))
        .collect();
    let mut changed = std::collections::BTreeSet::<String>::new();
    if view.baseline()["state"] == "comparable" {
        for fp in view.baseline()["new"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            if let Some(fp) = fp.as_str() {
                changed.insert(fp.into());
            }
        }
    }
    for entry in view.baseline()["entries"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        if (entry["state"] == "new" || entry["regressed"] == true)
            && let Some(fp) = entry["fingerprint"].as_str()
        {
            changed.insert(fp.into());
        }
    }
    candidates.sort_by_key(|finding| {
        (
            !changed.contains(finding["fingerprint"].as_str().unwrap_or("")),
            match finding["severity"].as_str() {
                Some("critical" | "fatal") => 0,
                Some("high" | "error") => 1,
                Some("medium" | "warning") => 2,
                _ => 3,
            },
            location(finding),
            plain(&finding["rule_id"]),
            plain(&finding["fingerprint"]),
        )
    });
    Ok(candidates
        .into_iter()
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
                if keys.len() > 64 {
                    out.push_str("<ol>");
                    for item in items {
                        out.push_str("<li>");
                        rich(item, out)?;
                        out.push_str("</li>");
                    }
                    out.push_str("</ol>");
                    return Ok(());
                }
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
    let navigation = crate::navigation::config(options)?;
    let title = ici_execution::redaction::redact_text(&options.title);
    let mut out = format!(
        "<!DOCTYPE html>\n<html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width, initial-scale=1\"><meta http-equiv=Content-Security-Policy content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'\"><title>{}</title><style>{}</style></head><body><main><h1>{}</h1><p class=verdict>{}</p>",
        html(&title),
        include_str!("../assets/native.css"),
        html(&title),
        html(&view.verdict())
    );
    out.push_str("<h2>Scope, execution and publication</h2>");
    let mut axes = view.axes();
    let checks = axes.as_object_mut().and_then(|axes| axes.remove("checks"));
    rich(&axes, &mut out)?;
    if let (Some(Value::Array(checks)), Some(run)) = (checks, view.run_pointer()) {
        out.push_str("<h3>Selected checks</h3><div class=scroll><table><tr><th>check</th><th>component</th><th>state</th><th>evidence complete</th><th>variant</th></tr>");
        for check in &checks {
            out.push_str("<tr data-search-row>");
            for key in [
                "task_id",
                "component_id",
                "state",
                "evidence_complete",
                "variant",
            ] {
                let _ = write!(out, "<td>{}</td>", html(&plain(&check[key])));
            }
            out.push_str("</tr>");
        }
        out.push_str("</table></div>");
        lazy(
            &mut out,
            &format!("{run}/checks"),
            None,
            "Complete check records",
        );
    }
    if options.source_root.is_some() {
        out.push_str("<label>Open source with <select id=editor-select><option value=vscode>VS Code</option><option value=cursor>Cursor</option><option value=pycharm>PyCharm / IntelliJ</option><option value=sublime>Sublime Text</option><option value=file>Browser file</option><option value=copy>Copy location</option></select></label>");
    }
    let suppressed_count = view
        .findings
        .iter()
        .filter(|finding| suppressed(finding))
        .count();
    let projection = crate::issues::project(&view)?;
    let _ = write!(
        out,
        "<h2 id=issues>Issues ({})</h2><p>{} original findings in {} display groups; {suppressed_count} suppressed. Display grouping leaves JSON and baseline inventory unchanged.</p><input id=finding-filter type=search placeholder=\"Filter findings and targets\" aria-label=\"Filter findings and targets\"><span id=filter-count aria-live=polite></span>",
        projection.total_findings,
        projection.total_findings,
        projection.groups.len()
    );
    out.push_str("<div id=issue-groups>");
    let lazy = projection.total_findings > 2_000;
    for group in projection
        .groups
        .iter()
        .take(if lazy { 50 } else { usize::MAX })
    {
        group_card(group, &mut out)?;
    }
    out.push_str("</div>");
    let group_data = if lazy {
        out.push_str("<p><button type=button id=group-prev>Previous</button> <span id=group-page aria-live=polite></span> <button type=button id=group-next>Next</button></p>");
        let mut groups = vec![];
        for group in &projection.groups {
            let first = &view.findings[group.source_indices[0]];
            let mut data = serde_json::json!({"indices":group.source_indices});
            for (key, wire) in [
                ("rule_id", "rule"),
                ("message", "message"),
                ("snippet", "snippet"),
            ] {
                if group.view[key] != first[key] {
                    data[wire] = group.view[key].clone();
                }
            }
            if group.findings.len() > 1 {
                data["locations"] = group.view["locations"].clone();
            }
            if group.view["provenance"]
                .as_array()
                .is_some_and(|values| !values.is_empty())
            {
                data["provenance"] = group.view["provenance"].clone();
            }
            groups.push(data);
        }
        Some(crate::script_json(
            &serde_json::json!({"groups":groups,"page_size":50}),
        )?)
    } else {
        None
    };
    if !projection.limitations.is_empty() {
        rich(&serde_json::json!(projection.limitations), &mut out)?;
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
        cards(
            &view,
            |finding| !suppressed(finding) && informational(finding),
            &mut out,
        )?;
        out.push_str("</details>");
    }
    if suppressed_count > 0 {
        let _ = write!(
            out,
            "<details><summary>{suppressed_count} suppressed findings</summary>"
        );
        // Suppressed findings stay fully visible once their section is opened.
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
        rich(&baseline_preview(view.baseline()), &mut out)?;
    }
    crate::facets::render(&view.engines, &view.engine_pointers, &mut out)?;
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
    // Complete payload survives the presentation, including unknown producer fields. It is
    // stored once, losslessly compressed; closed sections are built from it on demand.
    let saved_json = ici_domain::canonical_json(&view.document)?;
    if let Some(config) = navigation {
        let _ = write!(
            out,
            "<script id=ici-source-navigation type=application/json>{}</script>",
            crate::script_json(&safe::redact(&config))?
        );
    }
    if saved_json.len() + group_data.as_ref().map_or(0, String::len) > crate::json::MAX_STORED_BYTES
    {
        return Err(ReportError(
            "HTML exceeds 64 MiB embedded JSON bound".into(),
        ));
    }
    if let Some(data) = group_data {
        let _ = write!(
            out,
            "<script id=ici-groups type=application/json>{data}</script>"
        );
    }
    let _ = writeln!(
        out,
        "<details><summary>Complete saved result</summary><pre id=raw-result></pre></details><script id=ici-result type=application/octet-stream data-encoding=deflate-raw+base64 data-bytes={}>{}</script><script>{}</script></main></body></html>",
        saved_json.len(),
        packed(saved_json.as_bytes())?,
        script()
    );
    if out.len() > 128 * 1024 * 1024 {
        return Err(ReportError(
            "HTML expansion exceeds 128 MiB output bound".into(),
        ));
    }
    Ok(out)
}
/// A closed section whose content the page builds from the embedded saved result.
pub(crate) fn lazy(out: &mut String, pointer: &str, indices: Option<&[usize]>, summary: &str) {
    let _ = write!(out, "<details data-lazy=\"{}\"", html(pointer));
    if let Some(indices) = indices {
        let indices: Vec<String> = indices.iter().map(usize::to_string).collect();
        let _ = write!(out, " data-indices=\"{}\"", indices.join(" "));
    }
    let _ = write!(out, "><summary>{}</summary></details>", html(summary));
}
/// Cards whose saved finding needs no display-time decoration are built on demand.
fn cards(view: &View, select: impl Fn(&Value) -> bool, out: &mut String) -> Result<()> {
    let mut deferred = vec![];
    for (finding, (pointer, engine)) in view.findings.iter().zip(&view.finding_pointers) {
        if !select(finding) {
            continue;
        }
        let mut saved = pointer_value(&view.document, pointer).clone();
        if let Some(engine) = engine {
            saved["evidence"] = pointer_value(&view.document, engine)["evidence"].clone();
        }
        if &saved == finding {
            deferred.push(json!([pointer, engine]));
        } else {
            finding_card(finding, out)?;
        }
    }
    if !deferred.is_empty() {
        let _ = write!(
            out,
            "<div data-lazy-findings=\"{}\"></div>",
            html(&ici_domain::canonical_json(&Value::Array(deferred))?)
        );
    }
    Ok(())
}
fn pointer_value<'a>(document: &'a Value, pointer: &str) -> &'a Value {
    document.pointer(pointer).unwrap_or(&Value::Null)
}
/// Raw DEFLATE (RFC 1951) in standard base64; browsers inflate it with DecompressionStream.
fn packed(bytes: &[u8]) -> Result<String> {
    use std::io::Write as _;
    let mut encoder =
        flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::default());
    encoder
        .write_all(bytes)
        .map_err(|error| ReportError(format!("HTML payload compression failed: {error}")))?;
    let compressed = encoder
        .finish()
        .map_err(|error| ReportError(format!("HTML payload compression failed: {error}")))?;
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut text = String::with_capacity(compressed.len().div_ceil(3) * 4);
    for chunk in compressed.chunks(3) {
        let value = chunk.iter().enumerate().fold(0u32, |value, (index, byte)| {
            value | u32::from(*byte) << (16 - 8 * index)
        });
        for index in 0..4 {
            text.push(if index <= chunk.len() {
                char::from(ALPHABET[(value >> (18 - 6 * index)) as usize & 63])
            } else {
                '='
            });
        }
    }
    Ok(text)
}
/// Page script with leading indentation removed; it contains no multi-line literals.
fn script() -> &'static str {
    static SCRIPT: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    SCRIPT.get_or_init(|| {
        include_str!("../assets/native.js")
            .lines()
            .map(str::trim_start)
            .collect::<Vec<_>>()
            .join("\n")
    })
}
fn group_card(group: &crate::IssueGroup, out: &mut String) -> Result<()> {
    let mut finding = group
        .findings
        .iter()
        .find(|finding| {
            finding["message"] == group.view["message"]
                && finding["primary_location"]["path"] == group.view["primary_location"]["path"]
                && finding["primary_location"]["start_line"]
                    == group.view["primary_location"]["start_line"]
        })
        .or_else(|| group.findings.first())
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    for key in [
        "rule_id",
        "message",
        "primary_location",
        "related_locations",
        "snippet",
        "severity",
        "confidence",
    ] {
        finding[key] = group.view[key].clone();
    }
    finding_card(&finding, out)?;
    if group.findings.len() > 1
        || group.view["provenance"]
            .as_array()
            .is_some_and(|items| !items.is_empty())
    {
        let _ = write!(
            out,
            "<details><summary>{} original findings represented · provenance and locations</summary>",
            group.findings.len()
        );
        rich(&group.view, out)?;
        out.push_str("<details><summary>Every original saved finding</summary>");
        rich(&serde_json::json!(group.findings), out)?;
        out.push_str("</details></details>");
    }
    Ok(())
}

fn copy_location(path: &str, line: u64, column: Option<u64>) -> String {
    let text = format!(
        "{path}:{line}{}",
        column.map_or(String::new(), |column| format!(":{column}"))
    );
    if path == "[external]" {
        return format!("<code>{}</code>", html(&text));
    }
    format!(
        "<code>{}</code> <button type=button data-path=\"{}\" data-line={line} data-copy-location=\"{}\" aria-label=\"Copy location {}\">Copy</button>",
        html(&text),
        html(path),
        html(&text),
        html(&text)
    )
}
fn markdown_location(finding: &Value, options: &RenderOptions) -> Result<String> {
    let span = &finding["primary_location"];
    let path = span["path"].as_str().unwrap_or("unknown");
    let line = span["start_line"]
        .as_u64()
        .and_then(|line| u32::try_from(line).ok())
        .unwrap_or(1);
    let end = span["end_line"]
        .as_u64()
        .and_then(|line| u32::try_from(line).ok());
    let label = md(&location(finding));
    Ok(
        if let Some(url) = crate::source_link(path, line, end, options)? {
            format!("[{label}]({url})")
        } else {
            label
        },
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

/// The saved result retains every delta. Presentation keeps changed rows first and a small
/// unchanged sample, matching the frozen issues-first baseline view's 20/3 detail bounds.
fn baseline_preview(baseline: &Value) -> Value {
    const DETAIL: usize = 20;
    const UNCHANGED: usize = 3;
    let mut changed = Vec::new();
    let mut unchanged = Vec::new();
    if let Some(entries) = baseline["entries"].as_array() {
        let mut entries = entries.clone();
        let order = |entry: &Value| {
            let state = entry["state"].as_str().unwrap_or("").to_ascii_lowercase();
            (
                !entry["gated"].as_bool().unwrap_or(false),
                match state.as_str() {
                    "new" => 0,
                    "moved" => 1,
                    "resolved" => 2,
                    "carried" => 3,
                    "unchanged" => 4,
                    _ => 5,
                },
                entry["engine_name"].as_str().unwrap_or("").to_owned(),
                entry["fingerprint"].as_str().unwrap_or("").to_owned(),
            )
        };
        entries.sort_by_key(order);
        for entry in entries {
            let message = entry["message"].as_str().unwrap_or("");
            let message = message.chars().take(512).collect::<String>();
            let row = json!({"state":entry["state"],"gated":entry["gated"],"engine_name":entry["engine_name"],"rule_id":entry["rule_id"],"fingerprint":entry["fingerprint"],"current_location":entry["current_location"],"baseline_location":entry["baseline_location"],"baseline_severity":entry["baseline_severity"],"current_severity":entry["current_severity"],"message":message});
            if entry["state"]
                .as_str()
                .is_some_and(|state| state.eq_ignore_ascii_case("unchanged"))
            {
                unchanged.push(row);
            } else {
                changed.push(row);
            }
        }
    } else {
        for key in ["new", "carried", "resolved", "unchanged"] {
            for fingerprint in baseline[key]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default()
            {
                let row = json!({"state":key,"fingerprint":fingerprint});
                if key == "unchanged" {
                    unchanged.push(row);
                } else {
                    changed.push(row);
                }
            }
        }
    }
    let changed_total = changed.len();
    let unchanged_total = unchanged.len();
    changed.truncate(DETAIL);
    unchanged.truncate(UNCHANGED.min(DETAIL - changed.len()));
    let omitted_changed = changed_total - changed.len();
    let omitted_unchanged = unchanged_total - unchanged.len();
    changed.extend(unchanged);
    let warnings = baseline["warnings"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default();
    let visible_warnings = warnings
        .iter()
        .take(10)
        .map(|warning| {
            warning
                .as_str()
                .unwrap_or("")
                .chars()
                .take(512)
                .collect::<String>()
        })
        .collect::<Vec<_>>();
    json!({"state":baseline["state"],"origin":baseline["origin"],"reason":baseline["reason"],"source_path":baseline["source_path"],"fail_on_new":baseline["fail_on_new"],"gate_failed":baseline["gate_failed"],"warnings":visible_warnings,"omitted_warnings":warnings.len().saturating_sub(10),"counts":{"changed":changed_total,"unchanged":unchanged_total},"details":changed,"omitted_changed":omitted_changed,"omitted_unchanged":omitted_unchanged,"full_inventory":"The saved JSON report retains every baseline delta"})
}

#[cfg(test)]
mod baseline_preview_tests {
    use super::baseline_preview;
    use serde_json::json;

    #[test]
    fn changed_rows_precede_bounded_unchanged_sample_without_losing_archive_counts() {
        let mut entries = vec![];
        for index in 0..30 {
            entries.push(json!({"state":if index<25 {"unchanged"}else{"new"},"gated":index==29,"fingerprint":format!("fp-{index}"),"engine_name":"lint","message":"x".repeat(2048)}));
        }
        let preview = baseline_preview(&json!({"state":"comparable","entries":entries}));
        let details = preview["details"].as_array().unwrap();
        assert_eq!(details.len(), 8);
        assert_eq!(details[0]["fingerprint"], "fp-29");
        assert_eq!(preview["counts"]["changed"], 5);
        assert_eq!(preview["counts"]["unchanged"], 25);
        assert_eq!(preview["omitted_unchanged"], 22);
        assert!(details[0]["message"].as_str().unwrap().len() <= 512);
    }
    #[test]
    fn twenty_changed_rows_hide_all_unchanged_details() {
        let baseline = json!({"state":"comparable","new":(0..24).map(|index|format!("new-{index}")).collect::<Vec<_>>(),"carried":[],"resolved":[],"unchanged":["same"]});
        let preview = baseline_preview(&baseline);
        assert_eq!(preview["details"].as_array().unwrap().len(), 20);
        assert_eq!(preview["omitted_changed"], 4);
        assert_eq!(preview["omitted_unchanged"], 1);
    }
}

#[cfg(test)]
mod on_demand_tests {
    use super::{View, finding_card, rich};
    use crate::{RenderOptions, StoredResult, render_html};
    use serde_json::Value;
    use std::path::Path;

    fn text(html: &str) -> String {
        let tags = regex::Regex::new("<[^>]*>").unwrap();
        tags.replace_all(html, "")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace("&amp;", "&")
    }
    /// Browsers keep the JSON number, not its spelling (`5.0` reads back as `5`).
    fn numbers(value: &Value) -> Value {
        match value {
            Value::Number(number) => match number.as_f64() {
                // Integral floats are spelled `1.0` by the server and `1` by the browser.
                Some(float) if float.fract() == 0.0 && float.abs() < 9.0e15 => {
                    serde_json::json!(float as i64)
                }
                float => serde_json::json!(float),
            },
            Value::Array(items) => Value::Array(items.iter().map(numbers).collect()),
            Value::Object(items) => Value::Object(
                items
                    .iter()
                    .map(|(key, item)| (key.clone(), numbers(item)))
                    .collect(),
            ),
            _ => value.clone(),
        }
    }
    fn hydrated(html: &str, query: &str) -> Value {
        let node = std::env::var("ICI_TEST_NODE")
            .expect("ICI_TEST_NODE must name the Node.js used for the DOM harness");
        let directory = tempfile::tempdir().unwrap();
        let page = directory.path().join("report.html");
        std::fs::write(&page, html).unwrap();
        let harness = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/rust-parity/reporting/lazy-dom-check.cjs");
        let output = std::process::Command::new(node)
            .arg(harness)
            .arg(&page)
            .arg(query)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).unwrap()
    }

    #[test]
    #[ignore = "needs ICI_TEST_NODE; run with --include-ignored"]
    fn on_demand_sections_show_exactly_the_server_rendered_text() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests");
        for (fixture, query) in [
            (
                "rust-parity/reporting/budget-one-file/details.json",
                "main.py",
            ),
            (
                "rust-parity/reporting/rich-19-families.json",
                "unused_function",
            ),
            ("fixtures/ici-next/run-success.json", "zzz-no-match"),
        ] {
            let bytes = std::fs::read(root.join(fixture)).unwrap();
            let stored = StoredResult::from_bytes(&bytes).unwrap();
            let html = render_html(&stored, &RenderOptions::default()).unwrap();
            let view = View::new(&stored).unwrap();
            let page = hydrated(&html, query);
            assert_eq!(
                numbers(&page["saved"]),
                numbers(&view.document),
                "{fixture}: inflated payload"
            );
            assert_eq!(
                page["declared_bytes"].as_u64().unwrap() as usize,
                ici_domain::canonical_json(&view.document).unwrap().len()
            );
            for section in page["sections"].as_array().unwrap() {
                assert_eq!(section["state"], "true", "{fixture}: {section}");
                let pointer = section["pointer"].as_str().unwrap();
                let mut value = numbers(view.document.pointer(pointer).unwrap());
                if let Some(indices) = section["indices"].as_str() {
                    value = Value::Array(
                        indices
                            .split(' ')
                            .map(|index| value[index.parse::<usize>().unwrap()].clone())
                            .collect(),
                    );
                }
                let mut expected = String::new();
                rich(&value, &mut expected).unwrap();
                assert_eq!(
                    section["text"].as_str().unwrap(),
                    text(&expected),
                    "{fixture}: {pointer}"
                );
            }
            for list in page["cards"].as_array().unwrap() {
                assert_eq!(list["state"], "true");
                for (pair, shown) in list["pointers"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(list["texts"].as_array().unwrap())
                {
                    let index = view
                        .finding_pointers
                        .iter()
                        .position(|(pointer, _)| pair[0] == pointer.as_str())
                        .unwrap();
                    let mut expected = String::new();
                    finding_card(&numbers(&view.findings[index]), &mut expected).unwrap();
                    assert_eq!(shown.as_str().unwrap(), text(&expected), "{fixture}");
                }
            }
            let filtered = &page["filtered"];
            assert!(filtered["total"].as_u64().unwrap() > 0, "{fixture}");
            if query == "zzz-no-match" {
                assert_eq!(filtered["shown"], 0);
            } else {
                assert!(
                    filtered["shown"].as_u64().unwrap() > 0,
                    "{fixture}: {filtered}"
                );
            }
            println!(
                "{fixture}: html={} sections={} card-lists={} filter={filtered}",
                html.len(),
                page["sections"].as_array().unwrap().len(),
                page["cards"].as_array().unwrap().len()
            );
        }
    }
}
