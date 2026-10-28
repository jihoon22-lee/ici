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
fn fixes(finding: &Value) -> Result<(Vec<Value>, Vec<String>)> {
    let mut exported = vec![];
    let mut limitations = vec![];
    for fix in finding["fixes"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let Some(replacements) = fix["replacements"]
            .as_array()
            .filter(|values| !values.is_empty())
        else {
            continue;
        };
        if !replacements.iter().all(|replacement| {
            replacement
                .get("column_kind")
                .or_else(|| fix.get("column_kind"))
                .and_then(Value::as_str)
                == Some("unicodeCodePoints")
                && replacement
                    .get("end_exclusive")
                    .or_else(|| fix.get("end_exclusive"))
                    .and_then(Value::as_bool)
                    == Some(true)
        }) {
            limitations.push("Suggested edit retained as native properties: Unicode column units and exclusive end bounds were not explicitly confirmed".into());
            continue;
        }
        let mut changes = BTreeMap::<String, Vec<Value>>::new();
        for replacement in replacements {
            let path = replacement["path"]
                .as_str()
                .ok_or_else(|| ReportError("Marked fix requires path".into()))?;
            let sl = paths::positive(&replacement["start_line"], "fix.start_line")?;
            let sc = paths::positive(&replacement["start_column"], "fix.start_column")?;
            let el = paths::positive(&replacement["end_line"], "fix.end_line")?;
            let ec = paths::positive(&replacement["end_column"], "fix.end_column")?;
            if (el, ec) < (sl, sc) {
                return Err(ReportError(
                    "Marked fix has reversed exclusive source region".into(),
                ));
            }
            let inserted = replacement["replacement"].as_str().ok_or_else(|| {
                ReportError("Marked fix requires literal replacement text".into())
            })?;
            changes.entry(uri(path)?).or_default().push(json!({"deletedRegion":{"startLine":sl,"startColumn":sc,"endLine":el,"endColumn":ec},"insertedContent":{"text":inserted}}));
        }
        exported.push(json!({"description":{"text":fix["description"].as_str().unwrap_or("")},"artifactChanges":changes.into_iter().map(|(uri,replacements)|json!({"artifactLocation":{"uri":uri},"replacements":replacements})).collect::<Vec<_>>()}));
    }
    limitations.sort();
    limitations.dedup();
    Ok((exported, limitations))
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
        rules.entry(rule.into()).or_insert_with(||json!({"id":rule,"defaultConfiguration":{"level":level(finding)},"properties":{"provider":finding.get("provider").or_else(||finding.get("tool_name")),"nativeRuleId":finding.get("native_rule_id").or_else(||finding.get("tool_rule_id")),"tags":finding.get("tags").cloned().unwrap_or_else(||json!([]))}}));
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
        let mut entry = json!({"ruleId":rule,"level":level(finding),"message":{"text":finding["message"].as_str().unwrap_or("")},
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
        if let Some(delta) = baseline["entries"].as_array().and_then(|entries| {
            entries
                .iter()
                .find(|entry| entry["fingerprint"] == finding["fingerprint"])
        }) {
            if matches!(delta["state"].as_str(), Some("new" | "unchanged")) {
                entry["baselineState"] = delta["state"].clone();
            } else if delta["state"] == "moved" {
                entry["baselineState"] = json!("updated");
            }
        }
        if suppressed(finding) {
            entry["suppressions"] = json!([{"kind":if finding["suppression"]["kind"]=="inline"{"inSource"}else{"external"},"justification":finding["suppression"]["reason"],"properties":{"origin":finding["suppression"]["origin"]}}]);
        }
        let (exported, limitations) = fixes(finding)?;
        if !exported.is_empty() {
            entry["fixes"] = json!(exported);
        }
        if !limitations.is_empty() {
            entry["properties"]["fixLimitations"] = json!(limitations);
        }
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
    let mut document = json!({"$schema":"https://json.schemastore.org/sarif-2.1.0.json","version":"2.1.0","runs":[{
        "tool":{"driver":{"name":"ici","version":version,"rules":rules.into_values().collect::<Vec<_>>()}},
        "columnKind":"unicodeCodePoints",
        "results":results,"properties":{"ici.schemaId":stored.schema(),"axes":axes,"baseline":view.baseline(),"metrics":view.metrics(),"supportingEvidence":supporting}
    }]});
    if view.run.is_some() {
        document["runs"][0]["invocations"] = json!([{"executionSuccessful":axes["execution"]["required_complete"]==true,"exitCode":axes["exit_code"]}]);
    }
    Ok(document)
}
pub fn render_sarif(stored: &StoredResult, options: &RenderOptions) -> Result<String> {
    let document = sarif_document(stored, options)?;
    crate::json::validate_stored_shape(&document)?;
    let text = ici_domain::canonical_json(&document)?;
    if text.len() > 128 * 1024 * 1024 {
        return Err(ReportError(
            "SARIF exceeds 128 MiB expanded output bound".into(),
        ));
    }
    Ok(text)
}
