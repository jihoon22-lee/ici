use crate::{
    RenderOptions, ReportError, Result, StoredResult, paths, safe,
    view::{View, suppressed},
};
use serde_json::{Value, json};
use std::collections::BTreeMap;
const URI_SET: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC
    .remove(b'/')
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');
fn uri(path: &str) -> Result<String> {
    let encoded = percent_encoding::utf8_percent_encode(path, URI_SET).to_string();
    paths::external_path(&encoded)?;
    Ok(encoded)
}
fn physical(span: &Value) -> Result<Value> {
    let path = span["path"]
        .as_str()
        .ok_or_else(|| ReportError("SARIF location requires path".into()))?;
    let mut region = json!({"startLine":paths::positive(&span["start_line"],"start_line")?});
    for (native, sarif) in [("end_line", "endLine"), ("start_column", "startColumn")] {
        if let Some(value) = span.get(native).filter(|value| !value.is_null()) {
            region[sarif] = json!(paths::positive(value, native)?);
        }
    }
    if let Some(value) = span.get("end_column").filter(|value| !value.is_null()) {
        let column = paths::positive(value, "end_column")?
            .checked_add(1)
            .ok_or_else(|| ReportError("SARIF exclusive end column overflows".into()))?;
        region["endColumn"] = json!(column);
    }
    Ok(json!({"artifactLocation":{"uri":uri(path)?},"region":region}))
}
fn level(finding: &Value) -> &'static str {
    match finding["severity"].as_str() {
        Some("critical" | "fatal" | "error" | "high") => "error",
        Some("low" | "note" | "info" | "information") => "note",
        Some("none") => "none",
        _ => "warning",
    }
}
/// Export Unicode code-point columns with exclusive SARIF ends and no implicit URI base.
pub fn sarif_document(stored: &StoredResult, options: &RenderOptions) -> Result<Value> {
    let view = View::new(stored)?;
    if view.findings.len() > crate::json::MAX_RECORDS {
        return Err(ReportError("SARIF exceeds 100000 result bound".into()));
    }
    let mut rules = BTreeMap::<String, Value>::new();
    let mut results = vec![];
    for finding in &view.findings {
        let rule = finding["rule_id"].as_str().unwrap_or("unknown");
        rules.entry(rule.into()).or_insert_with(||json!({"id":rule,"defaultConfiguration":{"level":level(finding)},"properties":{"provider":finding.get("provider").or_else(||finding.get("tool_name")),"nativeRuleId":finding.get("native_rule_id").or_else(||finding.get("tool_rule_id")),"tags":finding["tags"]}}));
        let mut properties = finding.as_object().cloned().unwrap_or_default();
        for key in [
            "primary_location",
            "related_locations",
            "message",
            "rule_id",
            "fingerprint",
        ] {
            properties.remove(key);
        }
        let mut entry = json!({"ruleId":rule,"level":level(finding),"message":{"text":finding["message"]},
            "locations":[{"physicalLocation":physical(&finding["primary_location"])?}],
            "partialFingerprints":{"ici.fingerprint":finding["fingerprint"]},"properties":properties});
        if let Some(related) = finding["related_locations"]
            .as_array()
            .filter(|items| !items.is_empty())
        {
            let related=related.iter().enumerate().map(|(index,span)|Ok(json!({"id":index+1,"physicalLocation":physical(span)?,"message":{"text":span["label"].as_str().unwrap_or("")}}))).collect::<Result<Vec<_>>>()?;
            entry["relatedLocations"] = json!(related);
        }
        let baseline = view.baseline();
        if baseline["state"] == "comparable" {
            for key in ["new", "unchanged"] {
                if baseline[key]
                    .as_array()
                    .is_some_and(|items| items.contains(&finding["fingerprint"]))
                {
                    entry["baselineState"] = json!(key);
                }
            }
        }
        if suppressed(finding) {
            entry["suppressions"] = json!([{"kind":if finding["suppression"]["kind"]=="inline"{"inSource"}else{"external"},"justification":finding["suppression"]["reason"],"properties":{"origin":finding["suppression"]["origin"]}}]);
        }
        // Suggested edits are retained as literal properties. Historical fix coordinates
        // have producer-specific units/end conventions, so no executable edit is invented.
        results.push(entry);
    }
    let axes = view.axes();
    let version = view
        .run
        .as_ref()
        .map(|run| run["producer"]["ici_version"].clone())
        .unwrap_or_else(|| json!("historical-archive"));
    let mut supporting = vec![];
    for document in &options.auxiliary {
        document.validate()?;
        supporting.push(safe::redact(&document.to_value()?));
    }
    Ok(
        json!({"$schema":"https://json.schemastore.org/sarif-2.1.0.json","version":"2.1.0","runs":[{
            "tool":{"driver":{"name":"ici","version":version,"rules":rules.into_values().collect::<Vec<_>>()}},
            "columnKind":"unicodeCodePoints","invocations":[{"executionSuccessful":axes["execution"]["required_complete"]==true,"exitCode":axes["exit_code"].as_i64().unwrap_or(0)}],
            "results":results,"properties":{"ici.schemaId":stored.schema(),"axes":axes,"baseline":view.baseline(),"metrics":view.metrics(),"supportingEvidence":supporting}
        }]}),
    )
}
pub fn render_sarif(stored: &StoredResult, options: &RenderOptions) -> Result<String> {
    crate::canonical_json(&sarif_document(stored, options)?)
}
