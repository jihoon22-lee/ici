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
    let mut value = serde_json::to_value(finding)?;
    value["tool_name"] = json!(finding.provider);
    value["tool_rule_id"] = json!(finding.native_rule_id);
    value["tool_version"] = json!(finding.rule_version);
    for key in ["explanation", "remediation", "snippet"] {
        value[key] = json!("");
    }
    value["metrics"] = json!({});
    value["fixes"] = json!([]);
    Ok(value)
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
        let status = if check.state != TaskState::Succeeded || !check.evidence_complete {
            if check.state == TaskState::Failed {
                "ERROR"
            } else {
                "SKIP"
            }
        } else {
            output.map_or("PASS", |output| output.status.as_str())
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
                } else if check.evidence_complete {
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
            engine["targets"]
                .as_array_mut()
                .expect("array")
                .extend(output.targets.iter().map(|target| {
                    let mut target = serde_json::to_value(target).expect("serializable target");
                    target["task_id"] = json!(check.task_id);
                    target["component_id"] = json!(check.component_id);
                    target["variant"] = json!(check.variant);
                    target
                }));
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
                for key in ["explanation", "remediation", "snippet", "metrics", "fixes"] {
                    if let Some(detail) = details.get(key) {
                        value[key] = detail.clone();
                    }
                }
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
    let suite_status = if !run.execution.required_complete {
        "ERROR"
    } else if run.gate.has_violations {
        "FAIL"
    } else {
        "PASS"
    };
    let value = json!({"schema_version":"ici.result/v3","suite_status":suite_status,"project_name":context.project_name,
        "duration":context.duration_seconds,"passed_count":count("PASS"),"warned_count":count("WARN"),
        "failed_count":count("FAIL")+count("ERROR"),"error_count":count("ERROR"),"skipped_count":count("SKIP"),
        "total_count":results.len(),"tem_score":run.metrics.iter().find(|m|m.name=="tem.workspace").map(|m|m.value),"max_tem_score":5.0,
        "results":results,"analysis_context":context.analysis_context,"support_matrix":context.support_matrix,
        "capability_inventory":context.capability_inventory,"baseline_comparison":run.baseline,
        "native_run":run.to_value()?,"analysis_metadata":{"producer_version":run.producer.ici_version,"fingerprint_version":run.identity.fingerprint_version}});
    LegacyDocument::from_value(crate::safe::redact(&value))
}
