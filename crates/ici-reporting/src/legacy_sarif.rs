//! Historical `ici verify --sarif`: frozen `ici/reporters/sarif.py` over a saved legacy suite.
//!
//! GitHub code scanning keys existing alerts by this document's `fingerprints` (`ici/v3`),
//! `%SRCROOT%` locations and rule ids, so the historical command keeps that shape exactly. The
//! stored-run SARIF of `ici analyze`/`ici next report` is `sarif.rs`.
use crate::{ReportError, Result, StoredResult, migration, safe};
use serde_json::{Map, Value, json};
use std::{cmp::Ordering, collections::BTreeMap, collections::VecDeque};

pub const MAX_LEGACY_SARIF_RESULTS: usize = 100_000;
pub const MAX_LEGACY_SARIF_RULES: usize = 10_000;
const URI_SET: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC
    .remove(b'/')
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

/// A Python sort key: tuples compare element-wise, shorter prefix first.
#[derive(Clone, Debug, PartialEq)]
enum Key {
    Text(String),
    Int(i64),
    Tuple(Vec<Key>),
}
impl Eq for Key {}
impl PartialOrd for Key {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Key {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Key::Text(a), Key::Text(b)) => a.cmp(b),
            (Key::Int(a), Key::Int(b)) => a.cmp(b),
            (Key::Tuple(a), Key::Tuple(b)) => a.cmp(b),
            // Frozen keys never mix kinds at one position; keep a total order regardless.
            (Key::Int(_), _) => Ordering::Less,
            (_, Key::Int(_)) => Ordering::Greater,
            (Key::Text(_), _) => Ordering::Less,
            (_, Key::Text(_)) => Ordering::Greater,
        }
    }
}
fn text(value: &Value) -> Key {
    Key::Text(value.as_str().unwrap_or_default().to_owned())
}
fn int(value: &Value) -> Key {
    Key::Int(value.as_i64().unwrap_or_default())
}
fn flag(value: &Value) -> Key {
    Key::Int(i64::from(value.as_bool() == Some(true)))
}
/// Frozen `_location_sort_key`; an absent location is `("", 0, 0, 0, 0, "")`.
fn location_key(location: &Value) -> Key {
    Key::Tuple(vec![
        text(&location["path"]),
        int(&location["start_line"]),
        int(&location["start_column"]),
        int(&location["end_line"]),
        int(&location["end_column"]),
        text(&location["label"]),
    ])
}
fn items(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}
/// Python `repr` of a finding metric value (int or float).
fn metric_repr(value: &Value) -> String {
    match value.as_number() {
        Some(number) if number.is_f64() => {
            migration::python_float(number.as_f64().unwrap_or_default())
        }
        Some(number) => number.to_string(),
        None => String::new(),
    }
}
/// Frozen `_finding_sort_key`: every field that can affect a rule or a result.
fn finding_key(engine: &str, finding: &Value) -> Vec<Key> {
    let suppression = &finding["suppression"];
    let metrics = finding["metrics"]
        .as_object()
        .map(|metrics| {
            metrics
                .iter()
                .map(|(name, metric)| {
                    Key::Tuple(vec![
                        Key::Text(name.clone()),
                        Key::Text(metric_repr(&metric["value"])),
                        text(&metric["unit"]),
                    ])
                })
                .collect()
        })
        .unwrap_or_default();
    let fixes = items(&finding["fixes"])
        .iter()
        .map(|fix| {
            Key::Tuple(vec![
                text(&fix["description"]),
                Key::Tuple(
                    items(&fix["replacements"])
                        .iter()
                        .map(|item| {
                            Key::Tuple(vec![
                                text(&item["path"]),
                                int(&item["start_line"]),
                                int(&item["start_column"]),
                                int(&item["end_line"]),
                                int(&item["end_column"]),
                                text(&item["replacement"]),
                            ])
                        })
                        .collect(),
                ),
            ])
        })
        .collect();
    vec![
        Key::Text(engine.to_owned()),
        text(&finding["rule_id"]),
        text(&finding["fingerprint"]),
        location_key(&finding["primary_location"]),
        text(&finding["message"]),
        text(&finding["severity"]),
        text(&finding["category"]),
        text(&finding["confidence"]),
        text(&finding["explanation"]),
        text(&finding["remediation"]),
        text(&finding["tool_name"]),
        text(&finding["tool_rule_id"]),
        text(&finding["tool_version"]),
        flag(&suppression["suppressed"]),
        text(&suppression["kind"]),
        text(&suppression["reason"]),
        Key::Tuple(
            items(&finding["related_locations"])
                .iter()
                .map(location_key)
                .collect(),
        ),
        Key::Tuple(metrics),
        text(&finding["snippet"]),
        Key::Tuple(fixes),
    ]
}
/// Frozen `_delta_sort_key`; no delta is `("", "", "", (), (), "", "", False, False, False)`.
fn delta_key(delta: Option<&Value>) -> Vec<Key> {
    let Some(delta) = delta else {
        return vec![
            Key::Text(String::new()),
            Key::Text(String::new()),
            Key::Text(String::new()),
            Key::Tuple(vec![]),
            Key::Tuple(vec![]),
            Key::Text(String::new()),
            Key::Text(String::new()),
            Key::Int(0),
            Key::Int(0),
            Key::Int(0),
        ];
    };
    vec![
        text(&delta["engine_name"]),
        text(&delta["fingerprint"]),
        text(&delta["state"]),
        location_key(&delta["current_location"]),
        location_key(&delta["baseline_location"]),
        text(&delta["message"]),
        text(&delta["current_severity"]),
        text(&delta["baseline_severity"]),
        flag(&delta["regressed"]),
        flag(&delta["suppressed"]),
        flag(&delta["gated"]),
    ]
}
/// Frozen `_current_delta_key`, also built from a current finding to pair it with its delta.
fn pairing_key(
    engine: &Value,
    fingerprint: &Value,
    location: &Value,
    message: &Value,
    severity: &Value,
) -> Vec<Key> {
    vec![
        text(engine),
        text(fingerprint),
        location_key(location),
        text(message),
        text(severity),
    ]
}
/// Frozen `_finding_severity`: an unknown severity reads as `info`.
fn severity(value: &Value) -> &'static str {
    match value.as_str() {
        Some("low") => "low",
        Some("medium") => "medium",
        Some("high") => "high",
        Some("critical") => "critical",
        _ => "info",
    }
}
fn rank(value: &Value) -> u8 {
    match severity(value) {
        "low" => 1,
        "medium" => 2,
        "high" => 3,
        "critical" => 4,
        _ => 0,
    }
}
fn level(value: &Value) -> &'static str {
    match severity(value) {
        "medium" => "warning",
        "high" | "critical" => "error",
        _ => "note",
    }
}
fn uri(path: &Value) -> String {
    percent_encoding::utf8_percent_encode(path.as_str().unwrap_or_default(), URI_SET).to_string()
}
/// Frozen `_location`: saved (inclusive) columns, `%SRCROOT%`-relative URI.
fn location(span: &Value) -> Value {
    let mut region = Map::new();
    region.insert("startLine".into(), span["start_line"].clone());
    for (saved, sarif) in [
        ("end_line", "endLine"),
        ("start_column", "startColumn"),
        ("end_column", "endColumn"),
    ] {
        if !span[saved].is_null() {
            region.insert(sarif.into(), span[saved].clone());
        }
    }
    json!({"physicalLocation":{"artifactLocation":{"uri":uri(&span["path"]),"uriBaseId":"%SRCROOT%"},
        "region":region}})
}
fn nonempty(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.is_empty())
}
fn related(finding: &Value, delta: Option<&Value>) -> Vec<Value> {
    let mut locations: Vec<(&Value, Option<&str>)> = items(&finding["related_locations"])
        .iter()
        .map(|span| (span, nonempty(&span["label"])))
        .collect();
    if let Some(delta) = delta
        && delta["state"] == "moved"
        && delta["baseline_location"].is_object()
        && delta["baseline_location"] != finding["primary_location"]
    {
        locations.push((
            &delta["baseline_location"],
            Some("Baseline location before move"),
        ));
    }
    locations
        .into_iter()
        .enumerate()
        .map(|(index, (span, label))| {
            let mut item = location(span);
            item["id"] = json!(index + 1);
            if let Some(label) = label {
                item["message"] = json!({ "text": label });
            }
            item
        })
        .collect()
}
fn suppressions(finding: &Value) -> Vec<Value> {
    let suppression = &finding["suppression"];
    if suppression["suppressed"] != true {
        return vec![];
    }
    let kind = if suppression["kind"] == "inline" {
        "inSource"
    } else {
        "external"
    };
    let mut item = json!({ "kind": kind });
    if let Some(reason) = nonempty(&suppression["reason"]) {
        item["justification"] = json!(reason);
    }
    vec![item]
}
/// Frozen `_fixes`: replacements grouped per artifact URI, empty fixes dropped.
fn fixes(finding: &Value) -> Vec<Value> {
    let mut exported = vec![];
    for fix in items(&finding["fixes"]) {
        let mut changes = BTreeMap::<String, Vec<Value>>::new();
        for item in items(&fix["replacements"]) {
            changes.entry(uri(&item["path"])).or_default().push(json!({
                "deletedRegion":{"startLine":item["start_line"],"startColumn":item["start_column"],
                    "endLine":item["end_line"],"endColumn":item["end_column"]},
                "insertedContent":{"text":item["replacement"]}}));
        }
        if changes.is_empty() {
            continue;
        }
        let mut payload = json!({"artifactChanges":changes.into_iter().map(|(uri, replacements)|
            json!({"artifactLocation":{"uri":uri},"replacements":replacements})).collect::<Vec<_>>()});
        if let Some(description) = nonempty(&fix["description"]) {
            payload["description"] = json!({ "text": description });
        }
        exported.push(payload);
    }
    exported
}
fn result(engine: &str, finding: &Value, delta: Option<&Value>) -> Value {
    let rule = &finding["rule_id"];
    let mut properties = json!({"engine":engine,"category":finding["category"],
        "confidence":finding["confidence"],"suppressed":finding["suppression"]["suppressed"] == true});
    for key in ["tool_name", "tool_rule_id", "tool_version"] {
        if let Some(value) = nonempty(&finding[key]) {
            properties[if key == "tool_name" { "tool" } else { key }] = json!(value);
        }
    }
    if let Some(delta) = delta {
        properties["delta_state"] = delta["state"].clone();
        properties["delta_regressed"] = json!(delta["regressed"] == true);
        properties["delta_gated"] = json!(delta["gated"] == true);
    }
    let message = nonempty(&finding["message"]).map_or_else(|| rule.clone(), |text| json!(text));
    let mut entry = json!({"ruleId":rule,"level":level(&finding["severity"]),"message":{"text":message},
        "locations":[location(&finding["primary_location"])],
        "fingerprints":{"ici/v3":finding["fingerprint"]},"properties":properties});
    let related = related(finding, delta);
    if !related.is_empty() {
        entry["relatedLocations"] = json!(related);
    }
    let suppressions = suppressions(finding);
    if !suppressions.is_empty() {
        entry["suppressions"] = json!(suppressions);
    }
    let fixes = fixes(finding);
    if !fixes.is_empty() {
        entry["fixes"] = json!(fixes);
    }
    if let Some(state) = delta.and_then(|delta| delta["state"].as_str()) {
        let state = match state {
            "new" => Some("new"),
            "unchanged" => Some("unchanged"),
            "moved" => Some("updated"),
            "resolved" => Some("absent"),
            _ => None,
        };
        if let Some(state) = state {
            entry["baselineState"] = json!(state);
        }
    }
    entry
}
fn rule(finding: &Value) -> Value {
    let id = &finding["rule_id"];
    let mut rule = json!({"id":id,"name":id,"shortDescription":{"text":id},
        "defaultConfiguration":{"level":level(&finding["severity"])},
        "properties":{"category":finding["category"],"confidence":finding["confidence"]}});
    if let Some(text) = nonempty(&finding["explanation"]) {
        rule["fullDescription"] = json!({ "text": text });
    }
    if let Some(text) = nonempty(&finding["remediation"]) {
        rule["help"] = json!({ "text": text });
    }
    if let Some(tool) = nonempty(&finding["tool_name"]) {
        rule["properties"]["tool"] = json!(tool);
    }
    rule
}
/// Frozen `_resolved_finding` + `canonicalize_finding`: the identity is recomputed from the rule,
/// the baseline location and its label, not copied from the delta. A native-identity delta
/// (a rule outside the `ici.` namespace, which frozen's own deltas never carried and frozen
/// would have raised on) keeps its own rule and fingerprint instead of refusing the document.
fn resolved_finding(delta: &Value) -> Result<Value> {
    let rule = delta["rule_id"].as_str().unwrap_or_default();
    let namespaced = rule.strip_prefix("ici.").is_some_and(|rest| {
        rest.starts_with(|c: char| c.is_ascii_lowercase() || c.is_ascii_digit())
            && rest
                .chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '.' || c == '-')
    });
    let location = &delta["baseline_location"];
    let label = location["label"].as_str().unwrap_or_default();
    let fingerprint = if namespaced {
        json!(migration::fingerprint(rule, location, label)?)
    } else {
        delta["fingerprint"].clone()
    };
    let suppressed = delta["suppressed"] == true;
    Ok(
        json!({"rule_id":rule,"category":"correctness","severity":severity(&delta["baseline_severity"]),
        "confidence":"low","fingerprint":fingerprint,
        "primary_location":location,"related_locations":[],"message":delta["message"],
        "explanation":"","remediation":"","tool_rule_id":"","tool_name":"","tool_version":"",
        "suppression":{"suppressed":suppressed,"kind":if suppressed {"baseline"} else {"none"},"reason":""},
        "metrics":{},"snippet":"","fixes":[]}),
    )
}
/// The deterministic historical SARIF 2.1.0 document of a saved legacy suite (or one engine).
pub fn legacy_sarif_document(document: &Value, version: &str) -> Result<Value> {
    let document = safe::redact(document);
    let engines: Vec<&Value> = match document["results"].as_array() {
        Some(results) => results.iter().collect(),
        None => vec![&document],
    };
    let status = document
        .get("suite_status")
        .unwrap_or(&document["status"])
        .clone();
    let baseline = document
        .get("baseline_comparison")
        .filter(|value| value.is_object());
    let mut pending = BTreeMap::<Vec<Key>, VecDeque<&Value>>::new();
    let mut resolved = vec![];
    if let Some(baseline) = baseline {
        let mut deltas: Vec<&Value> = items(&baseline["entries"]).iter().collect();
        deltas.sort_by_key(|delta| delta_key(Some(delta)));
        for delta in deltas {
            if delta["state"] == "resolved" {
                resolved.push(delta);
            } else if !delta["current_location"].is_null() {
                pending
                    .entry(pairing_key(
                        &delta["engine_name"],
                        &delta["fingerprint"],
                        &delta["current_location"],
                        &delta["message"],
                        &delta["current_severity"],
                    ))
                    .or_default()
                    .push_back(delta);
            }
        }
    }
    let mut current: Vec<(String, Value)> = engines
        .iter()
        .flat_map(|engine| {
            let name = engine["engine_name"]
                .as_str()
                .unwrap_or_default()
                .to_owned();
            items(&engine["findings"])
                .iter()
                .map(move |finding| (name.clone(), finding.clone()))
        })
        .collect();
    current.sort_by_cached_key(|(engine, finding)| finding_key(engine, finding));
    let mut records: Vec<(String, Value, Option<Value>)> = vec![];
    for (engine, finding) in current {
        let delta = pending
            .get_mut(&pairing_key(
                &json!(engine),
                &finding["fingerprint"],
                &finding["primary_location"],
                &finding["message"],
                &finding["severity"],
            ))
            .and_then(VecDeque::pop_front)
            .cloned();
        records.push((engine, finding, delta));
    }
    for delta in resolved {
        if delta["baseline_location"].is_object() {
            records.push((
                delta["engine_name"].as_str().unwrap_or_default().to_owned(),
                resolved_finding(delta)?,
                Some(delta.clone()),
            ));
        }
    }
    records.sort_by_cached_key(|(engine, finding, delta)| {
        let mut key = finding_key(engine, finding);
        key.extend(delta_key(delta.as_ref()));
        key
    });
    if records.len() > MAX_LEGACY_SARIF_RESULTS {
        return Err(ReportError(format!(
            "SARIF result count exceeds the bounded limit of {MAX_LEGACY_SARIF_RESULTS}"
        )));
    }
    let mut by_rule = BTreeMap::<String, &Value>::new();
    for (_, finding, _) in &records {
        let id = finding["rule_id"].as_str().unwrap_or_default().to_owned();
        match by_rule.get(&id) {
            Some(chosen) if rank(&finding["severity"]) <= rank(&chosen["severity"]) => {}
            _ => {
                by_rule.insert(id, finding);
            }
        }
    }
    if by_rule.len() > MAX_LEGACY_SARIF_RULES {
        return Err(ReportError(format!(
            "SARIF rule count exceeds the bounded limit of {MAX_LEGACY_SARIF_RULES}"
        )));
    }
    let rules: Vec<Value> = by_rule.values().map(|finding| rule(finding)).collect();
    let results: Vec<Value> = records
        .iter()
        .map(|(engine, finding, delta)| result(engine, finding, delta.as_ref()))
        .collect();
    let mut properties = json!({"suite_status":status,"engine_count":engines.len(),
        "finding_count":records.len()});
    if let Some(baseline) = baseline {
        properties["baseline_source"] = baseline["source_path"].clone();
        properties["baseline_gate_failed"] = json!(baseline["gate_failed"] == true);
    }
    let exit_code = match status.as_str() {
        Some("FAIL" | "ERROR") => 1,
        Some("SKIP") => 2,
        _ => 0,
    };
    Ok(
        json!({"$schema":"https://json.schemastore.org/sarif-2.1.0.json","version":"2.1.0","runs":[{
        "tool":{"driver":{"name":"ici","version":version,"rules":rules}},
        "results":results,
        "invocations":[{"executionSuccessful":status != "ERROR","exitCode":exit_code}],
        "properties":properties}]}),
    )
}
/// Render the historical document of a stored legacy result; the producing ici version names
/// the driver (frozen wrote its own `__version__`).
pub fn render_legacy_sarif(stored: &StoredResult) -> Result<String> {
    let document = stored.to_value()?;
    let version = document["native_run"]["producer"]["ici_version"]
        .as_str()
        .unwrap_or(env!("CARGO_PKG_VERSION"))
        .to_owned();
    crate::canonical_json(&legacy_sarif_document(&document, &version)?)
}
