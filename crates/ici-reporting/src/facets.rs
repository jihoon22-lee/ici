//! Historical report families retain targets, metrics and producer-specific details.
use crate::{
    Result,
    render::{lazy, rich},
    safe::{html, plain},
};
use serde_json::Value;
use std::{collections::BTreeMap, fmt::Write};
fn title(name: &str) -> &str {
    match name {
        "line" => "Line analysis",
        "test" => "Tests, coverage and TEM",
        "lint" => "Lint and compiler diagnostics",
        "compile_db" => "Compilation database and build scope",
        "type" => "Static types",
        "python_compat" => "Python compatibility",
        "complexity" => "Cyclomatic complexity",
        "cognitive" => "Cognitive complexity",
        "sanitize" => "Sanitizer evidence",
        "thread_sanitize" => "Thread sanitizer evidence",
        "dead" => "Dead code and linker reachability",
        "dup" => "Duplicate code",
        "exception" => "Exception safety",
        "cycle" => "Dependency cycles",
        "security" => "Security patterns",
        "resource" => "Resource ownership",
        "build" => "Build and artifact evidence",
        "binary_compat" => "Binary compatibility",
        "integration" => "Declared integration",
        _ => name,
    }
}
pub(crate) fn render(engines: &[Value], pointers: &[String], out: &mut String) -> Result<()> {
    if engines.is_empty() {
        return Ok(());
    }
    out.push_str("<h2>Analysis families</h2><nav aria-label=\"Analysis families\">");
    for (index, engine) in engines.iter().enumerate() {
        let name = engine["engine_name"].as_str().unwrap_or("unknown");
        let _ = write!(out, "<a href=\"#family-{index}\">{}</a>", html(title(name)));
    }
    out.push_str("</nav>");
    for (index, (engine, pointer)) in engines.iter().zip(pointers).enumerate() {
        let name = engine["engine_name"].as_str().unwrap_or("unknown");
        let _ = write!(
            out,
            "<section id=family-{index} data-family=\"{}\"><h2>{}</h2><p><span class=badge>{}</span> {} · evidence {} · score {} / {}</p>",
            html(name),
            html(title(name)),
            html(&plain(&engine["status"])),
            html(&plain(&engine["summary"])),
            html(&plain(&engine["evidence"])),
            html(&plain(&engine["score"])),
            html(&plain(&engine["max_score"]))
        );
        let extra = &engine["extra"];
        if let Some(checks) = extra["native_checks"].as_array() {
            for (position, check) in checks.iter().enumerate() {
                let _ = write!(
                    out,
                    "<details open><summary>{} · {} · {} · {}</summary>",
                    html(&plain(&check["component_id"])),
                    html(&plain(&check["check_id"])),
                    html(&plain(&check["state"])),
                    html(&plain(&check["evidence"]))
                );
                facet(name, &check["extra"], out)?;
                // The complete record is built on demand from the embedded saved result.
                lazy(
                    out,
                    &format!("{pointer}/extra/native_checks/{position}"),
                    None,
                    "Complete check record",
                );
                out.push_str("</details>");
            }
        } else {
            facet(name, extra, out)?;
        }
        target_groups(&engine["targets"], &format!("{pointer}/targets"), out)?;
        for key in ["extra", "tool_evidence", "raw_output"] {
            if engine
                .get(key)
                .is_some_and(|value| !value.is_null() && value != "")
            {
                lazy(
                    out,
                    &format!("{pointer}/{key}"),
                    None,
                    &format!("Complete {key}"),
                );
            }
        }
        out.push_str("</section>");
    }
    Ok(())
}
fn facet(name: &str, extra: &Value, out: &mut String) -> Result<()> {
    match name {
        "line" => lines(extra, out)?,
        "test" => {
            out.push_str("<h3>Saved test and coverage measurements</h3>");
            let mut summary = serde_json::Map::new();
            for key in [
                "passed_tests",
                "total_tests",
                "pass_rate",
                "line_coverage",
                "branch_coverage",
                "function_coverage",
                "tem_score",
                "coverage_source",
                "coverage_totals",
            ] {
                if let Some(value) = extra.get(key) {
                    summary.insert(key.into(), value.clone());
                }
            }
            rich(&Value::Object(summary), out)?;
            for (key, label) in [
                ("coverage_files", "Module coverage"),
                ("function_rows", "Function coverage"),
                ("test_suites", "Test suite results"),
                ("test_quality", "Test quality"),
                ("quality", "Test quality"),
            ] {
                if let Some(value) = extra.get(key) {
                    let _ = write!(out, "<h3>{label}</h3>");
                    rich(value, out)?;
                }
            }
        }
        "complexity" | "cognitive" => {
            for key in [
                "top_complex_funcs",
                "top_cognitive_funcs",
                "functions",
                "distribution",
                "histogram",
            ] {
                if let Some(value) = extra.get(key) {
                    let _ = write!(out, "<h3>{}</h3>", html(key));
                    rich(value, out)?;
                }
            }
        }
        "dup" => {
            if let Some(groups) = extra.get("clone_groups") {
                out.push_str("<h3>Clone groups and instances</h3>");
                rich(groups, out)?;
            }
        }
        "cycle" => {
            out.push_str("<h3>Recorded dependency cycles</h3>");
            for key in ["cycles", "py_cycles", "cpp_cycles", "cycle_groups"] {
                if let Some(value) = extra.get(key) {
                    let _ = write!(out, "<h4>{}</h4>", html(key));
                    rich(value, out)?;
                }
            }
        }
        _ => {}
    }
    Ok(())
}
fn lines(extra: &Value, out: &mut String) -> Result<()> {
    out.push_str("<div data-line-scope><h3>Source and whole-project line inventory</h3><label>File scope <select data-scope-toggle><option value=source>Source files</option><option value=all>All project files</option></select></label>");
    let mut summary = serde_json::Map::new();
    for key in ["code", "comment", "blank", "total", "all"] {
        if let Some(value) = extra.get(key) {
            summary.insert(key.into(), value.clone());
        }
    }
    rich(&Value::Object(summary), out)?;
    if let Some(top) = extra["top_files"].as_array() {
        out.push_str("<h3>Largest source files</h3><div class=bars>");
        let maximum = top
            .iter()
            .filter_map(|item| item["code"].as_f64())
            .fold(1.0, f64::max);
        for item in top {
            let code = item["code"].as_f64().unwrap_or(0.0).clamp(0.0, maximum);
            let _ = write!(
                out,
                "<div class=bar><span>{}</span><meter min=0 max={maximum} value={code}>{code}</meter><span>{code}</span></div>",
                html(&plain(&item["path"]))
            );
        }
        out.push_str("</div>");
    }
    if let Some(files) = extra["files_data"].as_array() {
        let mut folders = BTreeMap::<String, Vec<&Value>>::new();
        for file in files {
            let path = file["path"].as_str().unwrap_or("unknown");
            let folder = path
                .rsplit_once('/')
                .map_or(".", |(folder, _)| folder)
                .to_string();
            folders.entry(folder).or_default().push(file);
        }
        for (folder, files) in folders {
            let _ = write!(
                out,
                "<details><summary>{} ({} files)</summary><table><tr><th>Path</th><th>Language</th><th>Scope</th><th>Code</th><th>Comment</th><th>Blank</th><th>Total</th></tr>",
                html(&folder),
                files.len()
            );
            for file in files {
                let scope = file["scope"].as_str().unwrap_or("source");
                let _ = write!(
                    out,
                    "<tr data-search-row data-source-scope=\"{}\"{}>",
                    html(scope),
                    if scope == "source" { "" } else { " hidden" }
                );
                for key in ["path", "lang", "scope", "code", "comment", "blank", "total"] {
                    let _ = write!(out, "<td>{}</td>", html(&plain(&file[key])));
                }
                out.push_str("</tr>");
            }
            out.push_str("</table></details>");
        }
    }
    out.push_str("</div>");
    Ok(())
}
fn target_groups(targets: &Value, pointer: &str, out: &mut String) -> Result<()> {
    let Some(targets) = targets.as_array() else {
        return Ok(());
    };
    let mut groups = BTreeMap::<(bool, String), Vec<(usize, Value)>>::new();
    for (index, target) in targets.iter().enumerate() {
        let status = target["status"].as_str().unwrap_or("UNKNOWN");
        let collapsed = matches!(status, "PASS" | "SKIP");
        let path = target["file_path"].as_str().unwrap_or("unknown");
        let folder = path.rsplit_once('/').map_or(".", |(folder, _)| folder);
        groups
            .entry((collapsed, format!("{status} · {folder}")))
            .or_default()
            .push((index, target.clone()));
    }
    for ((collapsed, label), targets) in groups {
        let summary = format!("{label} ({} inspected targets)", targets.len());
        if collapsed {
            // Normal PASS/SKIP inventory stays collapsed and is built from the saved result.
            let indices: Vec<usize> = targets.iter().map(|(index, _)| *index).collect();
            lazy(out, pointer, Some(&indices), &summary);
            continue;
        }
        let _ = write!(out, "<details open><summary>{}</summary>", html(&summary));
        rich(
            &Value::Array(targets.into_iter().map(|(_, target)| target).collect()),
            out,
        )?;
        out.push_str("</details>");
    }
    Ok(())
}
