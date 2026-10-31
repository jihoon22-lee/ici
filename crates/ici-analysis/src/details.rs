//! Historical producer metadata, derived only from already computed native facts.
use ici_domain::{AnalysisOutput, Finding, InspectionTarget};
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

fn target<'a>(output: &'a AnalysisOutput, finding: &Finding) -> Option<&'a InspectionTarget> {
    output.targets.iter().find(|target| {
        target.file_path == finding.primary_location.path
            && target.start_line == finding.primary_location.start_line
            && target.message == finding.message
            && target.target_name == finding.native_rule_id
    })
}
fn numeric_metrics(target: &InspectionTarget) -> Value {
    Value::Object(
        target
            .metrics
            .iter()
            .filter(|(_, value)| value.is_number())
            .map(|(name, value)| (name.clone(), json!({"value":value,"unit":""})))
            .collect(),
    )
}
pub fn decorate(output: &mut AnalysisOutput) {
    let mut counts = BTreeMap::<(&str, &str), usize>::new();
    for target in &output.targets {
        *counts
            .entry((&target.file_path, target.target_name.trim()))
            .or_default() += 1;
    }
    let mut details = Map::new();
    for finding in &output.observation.findings {
        if finding.rule_id.starts_with("ici.package.") {
            details.insert(finding.fingerprint.clone(),json!({"legacy_rule_id":finding.rule_id,"legacy_category":finding.category,"legacy_confidence":finding.confidence,"legacy_severity":finding.severity,"legacy_label":finding.primary_location.label,"tool_rule_id":finding.native_rule_id}));
            continue;
        }
        let Some(target) = target(output, finding) else {
            continue;
        };
        let label =
            if counts.get(&(target.file_path.as_str(), target.target_name.trim())) == Some(&1) {
                &target.target_name
            } else {
                ""
            };
        let mut detail = json!({"legacy_label":label,"snippet":target.snippet,"metrics":numeric_metrics(target)});
        if finding.rule_id.starts_with("dead.") {
            detail["legacy_rule_id"] = json!("ici.legacy.dead.target");
            detail["legacy_category"] = json!(finding.category);
            detail["legacy_confidence"] = json!(finding.confidence);
            detail["legacy_severity"] = json!(finding.severity);
            detail["explanation"] = json!(
                "Python dead-code evidence is derived from bounded AST reachability and name-reference analysis."
            );
            detail["remediation"] = json!(
                "Review the reported symbol or unreachable block and either remove it or make the intended reference explicit."
            );
        } else if finding.rule_id.starts_with("resource.") {
            let Some(issue) = output.extra["resource_issues"]
                .as_array()
                .and_then(|issues| {
                    issues.iter().find(|issue| {
                        issue["target"]["file_path"] == target.file_path
                            && issue["target"]["start_line"] == target.start_line
                            && issue["target"]["target_name"] == target.target_name
                            && issue["target"]["message"] == target.message
                    })
                })
            else {
                continue;
            };
            detail["legacy_rule_id"] = json!("ici.legacy.resource.target");
            detail["legacy_category"] = issue["category"].clone();
            detail["legacy_confidence"] = issue["confidence"].clone();
            detail["legacy_severity"] = json!("medium");
            detail["tool_name"] = json!("ici Python AST");
            detail["tool_rule_id"] = json!(target.target_name);
            detail["explanation"] = issue["explanation"].clone();
            detail["remediation"] = issue["remediation"].clone();
        } else {
            continue;
        }
        details.insert(finding.fingerprint.clone(), detail);
    }
    if !details.is_empty() {
        output.extra["finding_details"] = Value::Object(details);
    }
}
