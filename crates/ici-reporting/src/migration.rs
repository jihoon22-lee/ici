//! Explicit historical v2 adapter. StoredResult intake never guesses a migration.
use crate::{LegacyDocument, ReportError, Result, safe};
use serde_json::{Value, json};
use std::collections::BTreeMap;
fn integer(value: &Value) -> Option<i64> {
    if value.is_boolean() {
        None
    } else {
        value
            .as_i64()
            .or_else(|| value.as_f64().filter(|v| v.is_finite()).map(|v| v as i64))
            .or_else(|| value.as_str().and_then(|v| v.parse().ok()))
    }
}
fn string(value: &Value, default: &str) -> String {
    if value.is_null() {
        default.into()
    } else if let Some(text) = value.as_str() {
        text.into()
    } else {
        value.to_string()
    }
}
fn number(value: &Value, default: Value, nonnegative: bool) -> Value {
    if value
        .as_f64()
        .is_some_and(|v| v.is_finite() && (!nonnegative || v >= 0.0))
    {
        value.clone()
    } else {
        default
    }
}
fn status(value: &Value, default: &str) -> String {
    let value = value.as_str().unwrap_or(default);
    if ["PASS", "WARN", "FAIL", "ERROR", "SKIP"].contains(&value) {
        value.into()
    } else {
        default.into()
    }
}
fn target(value: &Value) -> Value {
    let start = integer(&value["start_line"]).unwrap_or(1).max(1);
    let end = integer(&value["end_line"]).filter(|end| *end >= start);
    let sc = integer(&value["start_column"]).filter(|column| *column > 0);
    let ec = integer(&value["end_column"])
        .filter(|column| *column > 0)
        .filter(|column| end.unwrap_or(start) != start || sc.is_none_or(|start| *column >= start));
    json!({"file_path":value["file_path"].as_str().filter(|p|!p.is_empty()).unwrap_or("unknown"),"start_line":start,"end_line":end,"start_column":sc,"end_column":ec,
        "target_name":string(&value["target_name"],""),"status":if value.get("status").is_some(){status(&value["status"],"WARN")}else{"PASS".into()},"message":string(&value["message"],""),"snippet":string(&value["snippet"],""),"metrics":value.get("metrics").filter(|v|v.is_object()).cloned().unwrap_or_else(||json!({}))})
}
fn project_path(raw: &str, root: Option<&str>) -> Result<String> {
    let mut path = raw.replace('\\', "/");
    let absolute = path.starts_with('/') || path.as_bytes().get(1) == Some(&b':');
    if absolute {
        let root = root
            .ok_or_else(|| {
                ReportError(
                    "Absolute historical finding paths require explicit project_root".into(),
                )
            })?
            .replace('\\', "/");
        let root = root.trim_end_matches('/');
        if path == root {
            path = ".".into();
        } else if let Some(relative) = path.strip_prefix(&format!("{root}/")) {
            path = relative.into();
        } else {
            return Err(ReportError(
                "Historical finding leaves explicit project_root".into(),
            ));
        }
    }
    let mut parts = vec![];
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            if parts.pop().is_none() {
                return Err(ReportError(
                    "Historical finding path escapes project root".into(),
                ));
            }
        } else {
            parts.push(part);
        }
    }
    Ok(if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    })
}
fn metric_unit(name: &str) -> &'static str {
    let name = name.to_ascii_lowercase();
    if name.ends_with("_pct") || name.ends_with("_percent") {
        return "percent";
    }
    for (fragment, unit) in [
        ("branch_coverage", "percent"),
        ("coverage", "percent"),
        ("dup_pct", "percent"),
        ("function_coverage", "percent"),
        ("line_coverage", "percent"),
        ("lines", "lines"),
        ("missed", "lines"),
        ("nesting", "levels"),
    ] {
        if name.contains(fragment) {
            return unit;
        }
    }
    ""
}
fn migrate_engine(engine: &mut Value, root: Option<&str>) -> Result<()> {
    let name = engine["engine_name"]
        .as_str()
        .filter(|v| !v.is_empty())
        .unwrap_or("unknown")
        .to_string();
    let evidence = engine["evidence"]
        .as_str()
        .filter(|v| ["MEASURED", "ESTIMATED", "NOT_RUN", "NOT_APPLICABLE"].contains(v))
        .unwrap_or("NOT_RUN")
        .to_string();
    let tools:Vec<_>=engine["tool_evidence"].as_array().map(Vec::as_slice).unwrap_or_default().iter().filter(|value|value.is_object()).map(|tool|json!({"name":string(&tool["name"],""),"path":string(&tool["path"],""),"version":string(&tool["version"],""),"argv":tool["argv"].as_array().map(|values|values.iter().map(|value|string(value,"")).collect::<Vec<_>>()).unwrap_or_default(),"returncode":integer(&tool["returncode"]),"timed_out":tool["timed_out"].as_bool().unwrap_or(false),"truncated":tool["truncated"].as_bool().unwrap_or(false),"error":string(&tool["error"],"")})).collect();
    engine["tool_evidence"] = json!(tools);
    let targets: Vec<_> = engine["targets"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter(|value| value.is_object())
        .map(target)
        .collect();
    let mut keys = BTreeMap::<(String, String), usize>::new();
    for target in &targets {
        *keys
            .entry((
                project_path(target["file_path"].as_str().expect("path"), root)?,
                target["target_name"].as_str().expect("name").trim().into(),
            ))
            .or_default() += 1;
    }
    let slug = regex::Regex::new("[^a-z0-9]+")
        .expect("constant")
        .replace_all(&name.to_lowercase(), "-")
        .trim_matches('-')
        .to_string();
    let rule = format!(
        "ici.legacy.{}.target",
        if slug.is_empty() { "unknown" } else { &slug }
    );
    let mut findings = vec![];
    for target in &targets {
        let path = project_path(target["file_path"].as_str().expect("path"), root)?;
        let name = target["target_name"].as_str().expect("name");
        let symbol = if keys[&(path.clone(), name.trim().to_string())] == 1 {
            name.trim()
        } else {
            ""
        };
        let mut region = serde_json::Map::new();
        for key in ["start_line", "end_line", "start_column", "end_column"] {
            region.insert(key.into(), target[key].clone());
        }
        let fingerprint = ici_domain::sha256(crate::canonical_json(
            &json!({"version":"ici-fingerprint/v1","rule_id":rule,"path":path,"symbol":symbol,"region":if symbol.is_empty(){Value::Object(region.clone())}else{Value::Null}}),
        )?);
        let mut location = Value::Object(region);
        location["path"] = json!(path);
        location["label"] = json!(name);
        let mut metrics = serde_json::Map::new();
        for (key, value) in target["metrics"].as_object().expect("metrics") {
            if value.is_number() {
                metrics.insert(key.clone(), json!({"value":value,"unit":metric_unit(key)}));
            }
        }
        let tool = &engine["tool_evidence"][0];
        let category = match engine["engine_name"].as_str().unwrap_or("") {
            "build" | "compile_db" => "build",
            "binary_compat" => "compatibility",
            "complexity" | "cognitive" | "dead" | "dup" | "line" | "lint" => "maintainability",
            "cycle" => "architecture",
            "resource" => "resource",
            "security" => "security",
            "test" => "test",
            "type" => "type",
            _ => "correctness",
        };
        findings.push(json!({"rule_id":rule,"category":category,"severity":match target["status"].as_str(){Some("PASS"|"SKIP")=>"info",Some("WARN")=>"medium",Some("FAIL")=>"high",_=>"critical"},
            "confidence":match evidence.as_str(){"MEASURED"=>"high","ESTIMATED"=>"medium",_=>"low"},"fingerprint":fingerprint,"primary_location":location,"related_locations":[],"message":target["message"],
            "explanation":format!("Adapted from the legacy {} InspectionTarget contract.",engine["engine_name"].as_str().unwrap_or("unknown")),"remediation":"","tool_rule_id":if name.contains(':'){name}else{""},
            "tool_name":tool["name"].as_str().unwrap_or(""),"tool_version":tool["version"].as_str().unwrap_or(""),"suppression":{"kind":"none","reason":"","suppressed":false},"metrics":metrics,"snippet":target["snippet"],"fixes":[]}));
    }
    findings.sort_by_key(|finding| {
        (
            string(&finding["fingerprint"], ""),
            string(&finding["primary_location"]["path"], ""),
            finding["primary_location"]["start_line"]
                .as_u64()
                .unwrap_or(1),
            string(&finding["message"], ""),
        )
    });
    engine["schema_version"] = json!("ici.result/v3");
    engine["engine_name"] = json!(name);
    engine["status"] = json!(status(&engine["status"], "ERROR"));
    engine["evidence"] = json!(evidence);
    for key in ["summary", "raw_output"] {
        engine[key] = json!(string(&engine[key], ""));
    }
    for key in ["score", "max_score"] {
        engine[key] = number(&engine[key], Value::Null, false);
    }
    engine["duration"] = number(&engine["duration"], json!(0.0), true);
    engine["required"] = json!(engine["required"].as_bool().unwrap_or(true));
    engine["extra"] = engine
        .get("extra")
        .filter(|v| v.is_object())
        .cloned()
        .unwrap_or_else(|| json!({}));
    engine["targets"] = json!(targets);
    engine["findings"] = json!(findings);
    engine["cache_hit"] = json!(false);
    engine["cache_key"] = Value::Null;
    engine["support_matrix"] = Value::Null;
    engine["artifact_manifests"] = json!([]);
    if !engine["tool_evidence"].is_array() {
        engine["tool_evidence"] = json!([]);
    }
    Ok(())
}
pub fn migrate_report_payload(
    payload: &Value,
    project_root: Option<&str>,
) -> Result<LegacyDocument> {
    crate::json::validate_shape(payload)?;
    if payload["schema_version"] == "ici.result/v3" {
        return LegacyDocument::from_value(safe::redact(payload));
    }
    if payload["schema_version"] != "ici.result/v2" || !payload.is_object() {
        return Err(ReportError(
            "Migration requires explicit ici.result/v2 or ici.result/v3".into(),
        ));
    }
    let mut value = payload.clone();
    if let Some(engines) = value.get_mut("results").and_then(Value::as_array_mut) {
        engines.retain(Value::is_object);
        for engine in engines.iter_mut() {
            migrate_engine(engine, project_root)?;
        }
        let count = |status: &str| engines.iter().filter(|e| e["status"] == status).count();
        let counts = [
            ("passed_count", count("PASS")),
            ("warned_count", count("WARN")),
            ("failed_count", count("FAIL") + count("ERROR")),
            ("error_count", count("ERROR")),
            ("skipped_count", count("SKIP")),
            ("total_count", engines.len()),
        ];
        for (key, count) in counts {
            value[key] = json!(count);
        }
        value["suite_status"] = json!(status(&value["suite_status"], "ERROR"));
        value["duration"] = number(&value["duration"], json!(0.0), true);
        value["tem_score"] = number(&value["tem_score"], Value::Null, false);
        value["max_tem_score"] = number(&value["max_tem_score"], json!(5.0), true);
        for key in [
            "support_matrix",
            "analysis_metadata",
            "baseline_comparison",
            "capability_inventory",
            "analysis_context",
        ] {
            value[key] = Value::Null;
        }
    } else {
        migrate_engine(&mut value, project_root)?;
    }
    value["schema_version"] = json!("ici.result/v3");
    LegacyDocument::from_value(safe::redact(&value))
}
