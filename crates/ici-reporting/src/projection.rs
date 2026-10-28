//! Lower saved native facts into the historical suite without re-running analysis.
use crate::{LegacyDocument, ReportError, Result};
use ici_domain::{
    AnalysisOutput, EvidenceLevel, Finding, RunResult, TaskState, Validate, builtin_catalog,
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Default)]
pub struct ProjectionContext {
    pub project_name: String,
    pub duration_seconds: f64,
    pub analysis_context: Option<Value>,
    pub support_matrix: Option<Value>,
    pub capability_inventory: Option<Value>,
    /// Historical identities/baselines require their own proven compatibility metadata.
    pub analysis_metadata: Option<Value>,
    pub legacy_baseline: Option<Value>,
    /// Attributed run fingerprint -> pre-attribution producer fingerprint.
    pub raw_fingerprints: BTreeMap<String, String>,
    /// Exact task policy supplied by the application. Missing means unknown.
    pub required_checks: BTreeMap<String, bool>,
    /// Explicit presentation family for additional checks outside the frozen registry.
    pub engine_names: BTreeMap<String, String>,
}
pub fn legacy_engine(check: &str) -> Option<&'static str> {
    Some(match check {
        "python.line" | "cpp.line" => "line",
        "python.lint" | "python.format" | "cpp.tidy" | "cpp.diagnostics" | "rust.clippy"
        | "javascript.lint" | "typescript.lint" => "lint",
        "cpp.compile" => "compile_db",
        "python.test" | "python.coverage" | "cpp.test" | "cpp.coverage" => "test",
        "python.type" | "typescript.type" => "type",
        "python.compat" | "python.compat-runtime" => "python_compat",
        "python.complexity" | "cpp.complexity" => "complexity",
        "python.cognitive" | "cpp.cognitive" => "cognitive",
        "cpp.sanitize" | "python.sanitize" => "sanitize",
        "cpp.tsan" => "thread_sanitize",
        "python.dead" | "cpp.dead" => "dead",
        "python.dup" | "cpp.dup" => "dup",
        "python.exception" | "cpp.exception" => "exception",
        "python.cycle" | "cpp.cycle" => "cycle",
        "python.security" => "security",
        "python.resource" => "resource",
        "cpp.artifact" | "python.artifact" => "build",
        "cpp.binary-compat" => "binary_compat",
        "integration" => "integration",
        _ => return None,
    })
}
fn rank(status: &str) -> u8 {
    match status {
        "ERROR" => 5,
        "FAIL" => 4,
        "WARN" => 3,
        "PASS" => 2,
        _ => 1,
    }
}
pub(crate) fn legacy_finding(finding: &Finding) -> Result<Value> {
    fn location(span: &ici_domain::SourceSpan) -> Value {
        json!({"path":span.path,"start_line":span.start_line,"end_line":span.end_line,"start_column":span.start_column,"end_column":span.end_column,"label":span.label})
    }
    Ok(
        json!({"rule_id":finding.rule_id,"category":if finding.category.is_empty(){"correctness"}else{&finding.category},"severity":finding.severity,"confidence":finding.confidence,
        "fingerprint":finding.fingerprint,"primary_location":location(&finding.primary_location),"related_locations":finding.related_locations.iter().map(location).collect::<Vec<_>>(),
        "message":finding.message,"explanation":"","remediation":"","tool_name":finding.provider,"tool_rule_id":finding.native_rule_id,"tool_version":finding.rule_version,
        "suppression":{"suppressed":finding.suppression.suppressed,"kind":if finding.suppression.kind.is_empty(){"none"}else{&finding.suppression.kind},"reason":finding.suppression.reason},"metrics":{},"snippet":"","fixes":[]}),
    )
}
pub fn legacy_projection(
    run: &RunResult,
    outputs: &BTreeMap<String, AnalysisOutput>,
    context: &ProjectionContext,
) -> Result<LegacyDocument> {
    run.validate()?;
    if !context.duration_seconds.is_finite() || context.duration_seconds < 0.0 {
        return Err(ReportError(
            "Projection duration must be finite and nonnegative".into(),
        ));
    }
    let mut engines = BTreeMap::<String, Value>::new();
    let mut attributed = BTreeSet::new();
    for check in run.checks.as_deref().unwrap_or_default() {
        let name = context
            .engine_names
            .get(&check.task_id)
            .map(String::as_str)
            .or_else(|| legacy_engine(&check.check_id))
            .filter(|name| !name.trim().is_empty())
            .ok_or_else(|| {
                ReportError(format!(
                    "No historical descriptor mapping for {}",
                    check.check_id
                ))
            })?;
        let engine = engines.entry(name.into()).or_insert_with(|| json!({
            "schema_version":"ici.result/v3", "engine_name":name, "status":"SKIP", "summary":"",
            "score":null, "max_score":null, "duration":0.0, "evidence":"NOT_RUN",
            "targets":[], "findings":[], "extra":{"native_checks":[]}, "tool_evidence":[], "raw_output":""
        }));
        let output = outputs.get(&check.task_id);
        let status = if check.state != TaskState::Succeeded {
            if check.state == TaskState::Failed {
                "ERROR"
            } else {
                "SKIP"
            }
        } else {
            output.map_or("SKIP", |output| output.status.as_str())
        };
        if rank(status) > rank(engine["status"].as_str().unwrap_or("SKIP")) {
            engine["status"] = json!(status);
        }
        let evidence = output
            .map(|output| {
                if output
                    .observation
                    .findings
                    .iter()
                    .any(|f| f.evidence == EvidenceLevel::Estimated)
                    || output
                        .observation
                        .measurements
                        .iter()
                        .any(|m| m.evidence == EvidenceLevel::Estimated)
                {
                    EvidenceLevel::Estimated
                } else if output.observation.state == TaskState::Succeeded {
                    EvidenceLevel::Measured
                } else {
                    EvidenceLevel::NotRun
                }
            })
            .unwrap_or_default();
        // Different checks keep their own evidence; aggregate cannot upgrade an estimate.
        let evidence = serde_json::to_value(evidence)?;
        if engine["extra"]["native_checks"]
            .as_array()
            .expect("array")
            .is_empty()
            || evidence == "NOT_RUN"
            || (engine["evidence"] != "NOT_RUN" && evidence == "ESTIMATED")
        {
            engine["evidence"] = evidence.clone();
        }
        let native = json!({"task_id":check.task_id,"check_id":check.check_id,"component_id":check.component_id,
            "analysis_unit_id":check.analysis_unit_id,"variant":check.variant,"state":check.state,
            "evidence_complete":check.evidence_complete,"evidence":evidence,"required":context.required_checks.get(&check.task_id),
            "summary":output.map(|o|o.summary.as_str()).unwrap_or("No saved analysis output"),
            "score":output.and_then(|o|o.score),"max_score":output.and_then(|o|o.max_score),
            "extra":output.map(|o|o.extra.clone()).unwrap_or_else(||json!({})),
            "metrics":output.map(|o|o.observation.measurements.clone()).unwrap_or_default(),
            "observation":output.map(|o|o.observation.clone()),
            "limitations":output.map(|o|o.observation.limitations.clone()).unwrap_or_default()});
        engine["extra"]["native_checks"]
            .as_array_mut()
            .expect("array")
            .push(native);
        if let Some(output) = output {
            let offset = engine["targets"].as_array().expect("array").len();
            let scopes:Vec<_>=output.targets.iter().enumerate().map(|(index,_)|json!({"target_index":offset+index,"task_id":check.task_id,"component_id":check.component_id,"variant":check.variant})).collect();
            if engine["extra"].get("target_scopes").is_none() {
                engine["extra"]["target_scopes"] = json!([]);
            }
            engine["extra"]["target_scopes"]
                .as_array_mut()
                .expect("array")
                .extend(scopes);
            engine["targets"].as_array_mut().expect("array").extend(
                output
                    .targets
                    .iter()
                    .map(|target| serde_json::to_value(target).expect("serializable target")),
            );
            if engine["summary"] == "" {
                engine["summary"] = json!(output.summary);
            }
            if engine["extra"]["native_checks"]
                .as_array()
                .expect("array")
                .len()
                == 1
            {
                engine["score"] = json!(output.score);
                engine["max_score"] = json!(output.max_score);
            } else {
                engine["score"] = Value::Null;
                engine["max_score"] = Value::Null;
            }
        }
        for finding in run
            .findings
            .iter()
            .filter(|finding| finding.task_id.as_deref() == Some(&check.task_id))
        {
            attributed.insert(finding.fingerprint.clone());
            let mut value = legacy_finding(finding)?;
            let raw = context
                .raw_fingerprints
                .get(&finding.fingerprint)
                .unwrap_or(&finding.fingerprint);
            if let Some(details) = output
                .and_then(|output| output.extra.get("finding_details"))
                .and_then(|details| details.get(raw))
                .and_then(Value::as_object)
            {
                for key in [
                    "explanation",
                    "remediation",
                    "snippet",
                    "metrics",
                    "fixes",
                    "related_locations",
                ] {
                    if let Some(detail) = details.get(key) {
                        value[key] = detail.clone();
                    }
                }
            }
            value["_modern_fingerprint"] = json!(finding.fingerprint);
            value["_raw_fingerprint"] = json!(raw);
            value["_task_id"] = json!(check.task_id);
            if let Some(details) = output
                .and_then(|output| output.extra.get("finding_details"))
                .and_then(|details| details.get(raw))
            {
                value["_legacy_details"] = details.clone();
            }
            engine["findings"]
                .as_array_mut()
                .expect("array")
                .push(value);
        }
    }
    // Run v1 has no check inventory. Preserve every finding in an explicit unassigned facet.
    let unassigned: Vec<_> = run
        .findings
        .iter()
        .filter(|finding| !attributed.contains(&finding.fingerprint))
        .map(legacy_finding)
        .collect::<Result<_>>()?;
    if !unassigned.is_empty() {
        engines.insert("native_unassigned".into(),json!({"schema_version":"ici.result/v3","engine_name":"native_unassigned","status":"WARN","summary":"Saved findings without check attribution","targets":[],"findings":unassigned,"extra":{},"evidence":"NOT_RUN"}));
    }
    for engine in engines.values_mut() {
        if let Some(checks) = engine["extra"]["native_checks"].as_array() {
            if checks.iter().any(|check| check["required"] == true) {
                engine["required"] = json!(true);
            } else if checks.iter().all(|check| check["required"].is_boolean()) {
                engine["required"] = json!(false);
            }
        }
        normalize_findings(engine)?;
    }
    let mut results: Vec<_> = builtin_catalog()
        .legacy
        .iter()
        .filter_map(|descriptor| engines.remove(&descriptor.name))
        .collect();
    results.extend(engines.into_values());
    let count = |status: &str| {
        results
            .iter()
            .filter(|result| result["status"] == status)
            .count()
    };
    let suite_status = if !run.execution.required_complete
        || results.is_empty()
        || results.iter().any(|engine| {
            engine["required"] == true
                && engine["evidence"] != "NOT_APPLICABLE"
                && (matches!(engine["status"].as_str(), Some("ERROR" | "SKIP"))
                    || engine["evidence"] == "NOT_RUN")
        }) {
        "ERROR"
    } else if results
        .iter()
        .any(|engine| engine["required"] == true && engine["status"] == "FAIL")
    {
        "FAIL"
    } else if results.iter().any(|engine| {
        engine["evidence"] != "NOT_APPLICABLE"
            && (engine["status"] == "WARN"
                || engine["required"]
                    .as_bool()
                    .is_none_or(|required| !required)
                    && (engine["status"] != "PASS" || engine["evidence"] != "MEASURED"))
    }) {
        "WARN"
    } else {
        "PASS"
    };
    let value = json!({"schema_version":"ici.result/v3","suite_status":suite_status,"project_name":context.project_name,
        "duration":context.duration_seconds,"passed_count":count("PASS"),"warned_count":count("WARN"),
        "failed_count":count("FAIL")+count("ERROR"),"error_count":count("ERROR"),"skipped_count":count("SKIP"),
        "total_count":results.len(),"tem_score":run.metrics.iter().find(|m|m.name=="tem.workspace").map(|m|m.value),"max_tem_score":5.0,
        "results":results,"analysis_context":context.analysis_context,"support_matrix":context.support_matrix,
        "capability_inventory":context.capability_inventory,"baseline_comparison":context.legacy_baseline,
        "native_run":run.to_value()?,"analysis_metadata":context.analysis_metadata});
    LegacyDocument::from_value(crate::safe::redact(&value))
}

fn normalize_findings(engine: &mut Value) -> Result<()> {
    let mut adapted = crate::migration::adapted_findings(engine)?;
    let candidates = engine["findings"].as_array().cloned().unwrap_or_default();
    let targets = engine["targets"].as_array().cloned().unwrap_or_default();
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let mut target_index = BTreeMap::<(String, u64, String), Vec<usize>>::new();
    let mut symbol_counts = BTreeMap::<(String, String), usize>::new();
    for (index, target) in targets.iter().enumerate() {
        target_index
            .entry((
                text(&target["file_path"]),
                target["start_line"].as_u64().unwrap_or(1),
                text(&target["message"]),
            ))
            .or_default()
            .push(index);
        *symbol_counts
            .entry((text(&target["file_path"]), text(&target["target_name"])))
            .or_default() += 1;
    }
    let scopes: BTreeMap<_, _> = engine["extra"]["target_scopes"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(|scope| {
            Some((
                scope["target_index"].as_u64()? as usize,
                text(&scope["task_id"]),
            ))
        })
        .collect();
    let mut finding_index = BTreeMap::<(String, u64, String, String), Vec<usize>>::new();
    for (index, finding) in adapted.iter().enumerate() {
        finding_index
            .entry((
                text(&finding["primary_location"]["path"]),
                finding["primary_location"]["start_line"]
                    .as_u64()
                    .unwrap_or(1),
                text(&finding["primary_location"]["label"]),
                text(&finding["message"]),
            ))
            .or_default()
            .push(index);
    }
    let mut explicit = vec![];
    let mut mappings = vec![];
    for mut native in candidates {
        let details = native
            .get("_legacy_details")
            .cloned()
            .unwrap_or(Value::Null);
        let matched_message = details.get("legacy_message").unwrap_or(&native["message"]);
        let matched = target_index
            .get(&(
                text(&native["primary_location"]["path"]),
                native["primary_location"]["start_line"]
                    .as_u64()
                    .unwrap_or(1),
                text(matched_message),
            ))
            .and_then(|indices| {
                indices.iter().find(|index| {
                    native["_task_id"]
                        .as_str()
                        .is_none_or(|task| scopes.get(index).is_none_or(|scope| scope == task))
                })
            })
            .map(|index| &targets[*index]);
        let original_fp = native["_modern_fingerprint"].clone();
        let raw_fp = native["_raw_fingerprint"].clone();
        let task = native["_task_id"].clone();
        let native_rule = details["legacy_rule_id"].as_str();
        let adapted_index = matched.and_then(|target| {
            finding_index
                .get(&(
                    text(&target["file_path"]),
                    target["start_line"].as_u64().unwrap_or(1),
                    text(&target["target_name"]),
                    text(&target["message"]),
                ))
                .and_then(|indices| indices.first().copied())
        });
        if let Some(rule) = native_rule {
            native["rule_id"] = json!(rule);
            if let Some(message) = details.get("legacy_message") {
                native["message"] = message.clone();
            }
            if let Some(label) = details.get("legacy_label") {
                native["primary_location"]["label"] = label.clone();
            } else if let Some(target) = matched {
                let count = symbol_counts
                    .get(&(text(&target["file_path"]), text(&target["target_name"])))
                    .copied()
                    .unwrap_or(0);
                native["primary_location"]["label"] = if count == 1 {
                    target["target_name"].clone()
                } else {
                    json!("")
                };
            }
            for (detail, key) in [
                ("legacy_category", "category"),
                ("legacy_confidence", "confidence"),
                ("legacy_severity", "severity"),
                ("tool_name", "tool_name"),
                ("tool_rule_id", "tool_rule_id"),
                ("tool_version", "tool_version"),
            ] {
                if let Some(value) = details.get(detail) {
                    native[key] = value.clone();
                }
            }
            for key in [
                "_legacy_details",
                "_modern_fingerprint",
                "_raw_fingerprint",
                "_task_id",
            ] {
                native.as_object_mut().expect("object").remove(key);
            }
            strip_fix_markers(&mut native);
            let native = crate::migration::canonical_finding(&native)?;
            mappings.push(json!({"legacy_fingerprint":native["fingerprint"],"modern_fingerprint":original_fp,"raw_fingerprint":raw_fp,"task_id":task}));
            explicit.push(native);
        } else if let Some(index) = adapted_index {
            // Modern issues do not become extra historical findings: their corresponding
            // target already has the exact historical identity, including PASS inventory.
            adapted[index]["suppression"] = native["suppression"].clone();
            for key in [
                "explanation",
                "remediation",
                "snippet",
                "metrics",
                "fixes",
                "related_locations",
            ] {
                if let Some(detail) = details.get(key) {
                    adapted[index][key] = detail.clone();
                }
            }
            strip_fix_markers(&mut adapted[index]);
            mappings.push(json!({"legacy_fingerprint":adapted[index]["fingerprint"],"modern_fingerprint":original_fp,"raw_fingerprint":raw_fp,"task_id":task}));
        } else {
            for key in [
                "_legacy_details",
                "_modern_fingerprint",
                "_raw_fingerprint",
                "_task_id",
            ] {
                native.as_object_mut().expect("object").remove(key);
            }
            if !native["rule_id"]
                .as_str()
                .is_some_and(|rule| rule.starts_with("ici."))
            {
                let rule = native["rule_id"].as_str().unwrap_or("unknown");
                native["rule_id"] = json!(format!(
                    "ici.native.{}",
                    rule.to_ascii_lowercase().replace(['_', ':', '/'], "-")
                ));
            }
            strip_fix_markers(&mut native);
            let native = crate::migration::canonical_finding(&native)?;
            mappings.push(json!({"legacy_fingerprint":native["fingerprint"],"modern_fingerprint":original_fp,"raw_fingerprint":raw_fp,"task_id":task}));
            explicit.push(native);
        }
    }
    let mut by_fingerprint = BTreeMap::<String, std::collections::VecDeque<usize>>::new();
    for (index, native) in explicit.iter().enumerate() {
        by_fingerprint
            .entry(native["fingerprint"].as_str().unwrap_or("").into())
            .or_default()
            .push_back(index);
    }
    let mut used = BTreeSet::new();
    for finding in &mut adapted {
        if let Some(index) = by_fingerprint
            .get_mut(finding["fingerprint"].as_str().unwrap_or(""))
            .and_then(std::collections::VecDeque::pop_front)
        {
            *finding = explicit[index].clone();
            used.insert(index);
        }
    }
    adapted.extend(
        explicit
            .into_iter()
            .enumerate()
            .filter(|(index, _)| !used.contains(index))
            .map(|(_, finding)| finding),
    );
    adapted.sort_by_key(|finding| {
        (
            finding["fingerprint"].as_str().unwrap_or("").to_string(),
            finding["primary_location"]["path"]
                .as_str()
                .unwrap_or("")
                .to_string(),
            finding["primary_location"]["start_line"]
                .as_u64()
                .unwrap_or(1),
            finding["message"].as_str().unwrap_or("").to_string(),
        )
    });
    engine["findings"] = json!(adapted);
    engine["extra"]["finding_identity_map"] = json!(mappings);
    Ok(())
}
fn strip_fix_markers(finding: &mut Value) {
    if let Some(fixes) = finding["fixes"].as_array_mut() {
        for fix in fixes {
            if let Some(object) = fix.as_object_mut() {
                object.remove("column_kind");
                object.remove("end_exclusive");
            }
            if let Some(replacements) = fix["replacements"].as_array_mut() {
                for replacement in replacements {
                    if let Some(object) = replacement.as_object_mut() {
                        object.remove("column_kind");
                        object.remove("end_exclusive");
                    }
                }
            }
        }
    }
}
