//! Explicit historical v2 adapter. StoredResult intake never guesses a migration.
use crate::{LegacyDocument, ReportError, Result, safe};
use serde_json::{Value, json};
use std::collections::BTreeMap;
/// Python `int(value)` as the frozen `_optional_int` applies it (bool and failures are None).
fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(_) | Value::Null => None,
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_f64().filter(|v| v.is_finite()).map(|v| v as i64)),
        Value::String(text) => python_int(text),
        _ => None,
    }
}
/// `int(str)`: surrounding whitespace, one sign, decimal digits with single `_` separators.
fn python_int(text: &str) -> Option<i64> {
    let text = text.trim_matches(char::is_whitespace);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
        || !digits.chars().all(|c| c.is_ascii_digit() || c == '_')
    {
        return None;
    }
    let value: i64 = digits.replace('_', "").parse().ok()?;
    Some(if negative { -value } else { value })
}
/// Python `bool(value)` truthiness for JSON values.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
    }
}
/// Python `repr(float)`: shortest round-trip digits, exponent outside [1e-4, 1e16).
fn python_float(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.into();
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("exponent");
    let exponent: i32 = exponent.parse().expect("exponent digits");
    if (-4..16).contains(&exponent) {
        let fixed = format!("{value}");
        if fixed.contains('.') {
            fixed
        } else {
            format!("{fixed}.0")
        }
    } else {
        format!(
            "{mantissa}e{}{:02}",
            if exponent < 0 { '-' } else { '+' },
            exponent.abs()
        )
    }
}
fn python_repr_text(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}
fn python_repr(value: &Value) -> String {
    match value {
        Value::String(text) => python_repr_text(text),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(python_repr).collect::<Vec<_>>().join(", ")
        ),
        // JSON objects reach here in key order; the frozen reader kept insertion order.
        Value::Object(items) => format!(
            "{{{}}}",
            items
                .iter()
                .map(|(key, item)| format!("{}: {}", python_repr_text(key), python_repr(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => python_str(other),
    }
}
/// Python `str(value)` for a decoded JSON value.
fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(text) => text.clone(),
        Value::Number(number) => match (number.as_i64(), number.as_u64()) {
            (Some(value), _) => value.to_string(),
            (_, Some(value)) => value.to_string(),
            _ => python_float(number.as_f64().expect("finite JSON number")),
        },
        other => python_repr(other),
    }
}
/// `str(payload.get(key, default))`: only an absent key takes the default.
fn string_field(payload: &Value, key: &str, default: &str) -> String {
    payload.get(key).map_or_else(|| default.into(), python_str)
}
fn string(value: &Value, default: &str) -> String {
    if value.is_null() {
        default.into()
    } else {
        python_str(value)
    }
}
/// `_payload_number`: finite non-bool numbers become Python floats, otherwise the default.
fn number(value: &Value, default: Value, nonnegative: bool) -> Value {
    match value.as_f64() {
        Some(v) if v.is_finite() && (!nonnegative || v >= 0.0) => json!(v),
        _ => default,
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
        "target_name":string_field(value,"target_name",""),"status":if value.get("status").is_some(){status(&value["status"],"WARN")}else{"PASS".into()},"message":string_field(value,"message",""),"snippet":string_field(value,"snippet",""),"metrics":value.get("metrics").filter(|v|v.is_object()).cloned().unwrap_or_else(||json!({}))})
}
fn drive_absolute(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' && bytes[2] == b'/'
}
/// Windows paths compare case-insensitively (frozen `_comparison_value`).
fn comparison(value: &str) -> String {
    if drive_absolute(value) {
        value.to_lowercase()
    } else {
        value.into()
    }
}
/// Frozen `canonical_project_path`: lexical, slash-separated and project-relative.
pub(crate) fn project_path(raw: &str, root: Option<&str>) -> Result<String> {
    let slashed = raw.replace('\\', "/");
    let mut path = slashed.trim_end_matches('/').to_string();
    if path.is_empty() {
        path = ".".into();
    }
    let bytes = path.as_bytes();
    if bytes.len() >= 2
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && bytes.get(2) != Some(&b'/')
    {
        return Err(ReportError(
            "Drive-relative historical finding path is ambiguous".into(),
        ));
    }
    if path.starts_with('/') || drive_absolute(&path) {
        let root_value = root
            .ok_or_else(|| {
                ReportError(
                    "Absolute historical finding paths require explicit project_root".into(),
                )
            })?
            .replace('\\', "/");
        let mut root = root_value.trim_end_matches('/').to_string();
        if root.is_empty() && root_value.starts_with('/') {
            root = "/".into();
        }
        if root.is_empty() {
            return Err(ReportError(
                "Absolute historical finding paths require explicit project_root".into(),
            ));
        }
        let (value, base) = (comparison(&path), comparison(&root));
        if value == base {
            path = ".".into();
        } else if root == "/" {
            path = path.trim_start_matches('/').to_string();
        } else if value.starts_with(&format!("{base}/")) {
            path = path.chars().skip(root.chars().count() + 1).collect();
        } else {
            return Err(ReportError(
                "Historical finding leaves explicit project_root".into(),
            ));
        }
    }
    let mut parts = vec![];
    for part in path.split('/') {
        let drive =
            part.len() == 2 && part.as_bytes()[0].is_ascii_alphabetic() && part.ends_with(':');
        if part.is_empty() || part == "." || drive {
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
pub(crate) fn fingerprint(rule: &str, location: &Value, symbol: &str) -> Result<String> {
    let mut region = serde_json::Map::new();
    for key in ["start_line", "end_line", "start_column", "end_column"] {
        region.insert(key.into(), location[key].clone());
    }
    Ok(ici_domain::sha256(crate::canonical_json(
        &json!({"version":"ici-fingerprint/v1","rule_id":rule,"path":location["path"],"symbol":symbol.trim(),"region":if symbol.trim().is_empty(){Value::Object(region)}else{Value::Null}}),
    )?))
}
pub(crate) fn adapted_findings(engine: &Value) -> Result<Vec<Value>> {
    let mut payload = json!({"engine_name":engine["engine_name"],"evidence":engine["evidence"],"targets":engine["targets"],"tool_evidence":engine["tool_evidence"]});
    migrate_engine(&mut payload, None)?;
    Ok(payload["findings"]
        .as_array()
        .expect("adapted findings")
        .clone())
}
/// Frozen `_canonical_fix`: every replacement path is canonicalized like a location and its
/// region must be a 1-indexed, non-reversed span; a malformed replacement refuses the finding
/// rather than being dropped. Fixes stay out of the fingerprint.
fn canonical_fixes(value: &mut Value) -> Result<()> {
    let Some(fixes) = value.get_mut("fixes").and_then(Value::as_array_mut) else {
        return Ok(());
    };
    for fix in fixes {
        let Some(replacements) = fix.get_mut("replacements").and_then(Value::as_array_mut) else {
            continue;
        };
        for replacement in replacements {
            let path = replacement["path"]
                .as_str()
                .ok_or_else(|| ReportError("Historical fix replacement requires a path".into()))?;
            replacement["path"] = json!(project_path(path, None)?);
            let start = crate::paths::positive(&replacement["start_line"], "fix.start_line")?;
            let mut bounds = [None; 3];
            for (slot, key) in bounds
                .iter_mut()
                .zip(["end_line", "start_column", "end_column"])
            {
                if let Some(number) = replacement.get(key).filter(|value| !value.is_null()) {
                    *slot = Some(crate::paths::positive(number, &format!("fix.{key}"))?);
                }
            }
            let [end_line, start_column, end_column] = bounds;
            if end_line.is_some_and(|end| end < start)
                || (end_line.is_none_or(|end| end == start)
                    && start_column
                        .zip(end_column)
                        .is_some_and(|(first, last)| last < first))
            {
                return Err(ReportError(
                    "Historical fix replacement region is reversed".into(),
                ));
            }
        }
    }
    Ok(())
}
pub(crate) fn canonical_finding(value: &Value) -> Result<Value> {
    let mut value = value.clone();
    let rule = value["rule_id"]
        .as_str()
        .ok_or_else(|| ReportError("Historical native finding requires rule_id".into()))?;
    if !rule.starts_with("ici.")
        || !rule
            .chars()
            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '.' | '-'))
    {
        return Err(ReportError(
            "Historical native rule_id must use ici namespace".into(),
        ));
    }
    let rule = rule.to_string();
    fn location(source: &Value) -> Result<Value> {
        let path = project_path(
            source["path"]
                .as_str()
                .ok_or_else(|| ReportError("Historical source location requires a path".into()))?,
            None,
        )?;
        let sl = crate::paths::positive(&source["start_line"], "location.start_line")?;
        let mut location = json!({"path":path,"start_line":sl,"end_line":null,"start_column":null,"end_column":null,"label":source["label"].as_str().unwrap_or("")});
        for key in ["end_line", "start_column", "end_column"] {
            if let Some(value) = source.get(key).filter(|value| !value.is_null()) {
                location[key] = json!(crate::paths::positive(value, key)?);
            }
        }
        let el = location["end_line"].as_u64().unwrap_or(u64::from(sl));
        if el < u64::from(sl)
            || (el == u64::from(sl)
                && location["start_column"]
                    .as_u64()
                    .zip(location["end_column"].as_u64())
                    .is_some_and(|(start, end)| end < start))
        {
            return Err(ReportError("Historical source region is reversed".into()));
        }
        Ok(location)
    }
    value["primary_location"] = location(&value["primary_location"])?;
    value["related_locations"] = json!(
        value["related_locations"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .map(location)
            .collect::<Result<Vec<_>>>()?
    );
    canonical_fixes(&mut value)?;
    value["fingerprint"] = json!(fingerprint(
        &rule,
        &value["primary_location"],
        value["primary_location"]["label"].as_str().unwrap_or("")
    )?);
    if let Some(related) = value["related_locations"].as_array_mut() {
        related.sort_by_key(|location| {
            (
                plain_string(&location["path"]),
                location["start_line"].as_u64().unwrap_or(1),
                location["start_column"].as_u64().unwrap_or(0),
                location["end_line"].as_u64().unwrap_or(0),
                location["end_column"].as_u64().unwrap_or(0),
                plain_string(&location["label"]),
            )
        });
    }
    Ok(value)
}
fn plain_string(value: &Value) -> String {
    value.as_str().unwrap_or("").to_string()
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
    let tools: Vec<_> = engine["tool_evidence"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter(|value| value.is_object())
        .map(|tool| {
            let argv: Vec<String> = tool["argv"]
                .as_array()
                .map(|values| values.iter().map(python_str).collect())
                .unwrap_or_default();
            json!({"name":string_field(tool,"name",""),"path":string_field(tool,"path",""),
                "version":string_field(tool,"version",""),"argv":safe::redact_argv(&argv),
                "returncode":integer(&tool["returncode"]),"timed_out":tool.get("timed_out").is_some_and(truthy),
                "truncated":tool.get("truncated").is_some_and(truthy),"error":string_field(tool,"error","")})
        })
        .collect();
    engine["tool_evidence"] = json!(tools);
    let targets: Vec<_> = engine["targets"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter(|value| value.is_object())
        .map(target)
        // Identity derives from the reporting-safe target, as the frozen serializer did.
        .map(|target| safe::redact_plain(&target))
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
        engine[key] = json!(string_field(engine, key, ""));
    }
    for key in ["score", "max_score"] {
        engine[key] = number(&engine[key], Value::Null, false);
    }
    engine["duration"] = number(&engine["duration"], json!(0.0), true);
    engine["required"] = json!(engine.get("required").is_none_or(truthy));
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
        let mut value = payload.clone();
        if let Some(engines) = value.get_mut("results").and_then(Value::as_array_mut) {
            for engine in engines {
                engine["schema_version"] = json!("ici.result/v3");
                if !engine["findings"].is_array() {
                    migrate_engine(engine, project_root)?;
                }
            }
        } else if !value["findings"].is_array() {
            migrate_engine(&mut value, project_root)?;
        }
        return LegacyDocument::from_value(safe::redact(&value));
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

#[cfg(test)]
mod frozen_fix_tests {
    use serde_json::{Value, json};

    /// tests/rust-parity/reporting/fix-canonicalization.json (capture_fix_canonicalization.py):
    /// frozen `canonicalize_finding` without a project root, one fix replacement per case.
    fn cases() -> Vec<Value> {
        let golden: Value = serde_json::from_str(include_str!(
            "../../../tests/rust-parity/reporting/fix-canonicalization.json"
        ))
        .unwrap();
        assert_eq!(golden["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
        golden["cases"].as_array().unwrap().clone()
    }

    fn native(case: &Value) -> crate::Result<Value> {
        super::canonical_finding(&json!({
            "rule_id":"ici.lint.rule","message":"m","severity":"low",
            "primary_location":{"path":"src/a.py","start_line":1},
            "fixes":[{"description":"d","replacements":[case["replacement"]]}]
        }))
    }

    #[test]
    fn fix_replacement_paths_canonicalize_like_the_frozen_finding_reader() {
        let accepted: Vec<Value> = cases()
            .into_iter()
            .filter(|case| case["refused"].is_null())
            .collect();
        assert_eq!(accepted.len(), 4);
        for case in accepted {
            let finding = native(&case).unwrap();
            assert_eq!(
                finding["fixes"][0]["replacements"][0]["path"], case["canonical_path"],
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn escaping_absolute_drive_and_malformed_fix_regions_refuse_the_finding_like_frozen() {
        let refused: Vec<Value> = cases()
            .into_iter()
            .filter(|case| !case["refused"].is_null())
            .collect();
        assert_eq!(refused.len(), 8);
        for case in refused {
            assert!(native(&case).is_err(), "{}", case["name"]);
        }
    }

    #[test]
    fn fixes_stay_out_of_the_fingerprint() {
        let base = json!({"rule_id":"ici.lint.rule","message":"m",
            "primary_location":{"path":"src/a.py","start_line":1}});
        let mut with_fix = base.clone();
        with_fix["fixes"] = json!([{"description":"d","replacements":[{"path":"./src/a.py",
            "start_line":1,"start_column":1,"end_line":1,"end_column":2,"replacement":"x"}]}]);
        assert_eq!(
            super::canonical_finding(&base).unwrap()["fingerprint"],
            super::canonical_finding(&with_fix).unwrap()["fingerprint"]
        );
    }
}
