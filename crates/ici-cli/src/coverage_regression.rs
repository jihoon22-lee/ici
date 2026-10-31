//! Baseline-only policy uses the exact saved rich report and completed matching checks.
use ici_application::{plan::Plan, suppressions::SuppressionPolicy};
use ici_domain::{AnalysisOutput, BaselineState, RunResult, Validate};
use ici_reporting::StoredResult;
use ici_validation::coverage_baseline::{CoveragePolicySnapshot, compare};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub fn configured_checks(prepared: &crate::planning::PreparedPlan) -> BTreeSet<String> {
    prepared
        .plan
        .checks
        .iter()
        .filter(|check| matches!(check.check.id.as_str(), "python.coverage" | "cpp.coverage"))
        .filter(|check| {
            let component = crate::inputs::component(prepared, check);
            component
                .settings
                .pointer("/engines/test/max_coverage_regression")
                .or_else(|| {
                    prepared
                        .project
                        .effective
                        .pointer("/engines/test/max_coverage_regression")
                })
                .is_some_and(|value| value.as_f64().is_some())
        })
        .map(|check| check.task_id.clone())
        .collect()
}

fn snapshots(value: &Value, output: &mut Vec<CoveragePolicySnapshot>) -> Result<(), String> {
    if let Some(object) = value.as_object() {
        if let Some(policy) = object.get("coverage_policy") {
            output.push(
                CoveragePolicySnapshot::from_value(policy.clone()).map_err(|e| e.to_string())?,
            );
        }
        for (key, child) in object {
            if key != "coverage_policy" && key != "coverage_regression" && child.is_object() {
                snapshots(child, output)?;
            }
        }
    }
    Ok(())
}
fn previous_extra<'a>(stored: &'a StoredResult, task: &str) -> Option<&'a Value> {
    let StoredResult::Legacy(legacy) = stored else {
        return None;
    };
    let mut matches = legacy
        .engines()
        .into_iter()
        .flat_map(|engine| {
            engine
                .pointer("/extra/native_checks")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter(|check| check["task_id"] == task);
    let found = matches.next()?;
    if matches.next().is_some() {
        return None;
    }
    found.get("extra")
}

pub fn apply(
    result: &mut RunResult,
    outputs: &mut BTreeMap<String, AnalysisOutput>,
    plan: &Plan,
    baseline: &RunResult,
    document: &StoredResult,
    suppressions: &SuppressionPolicy,
    configured_checks: &BTreeSet<String>,
) -> BTreeMap<String, String> {
    let mut raw_fingerprints = BTreeMap::new();
    if !result
        .baseline
        .as_ref()
        .is_some_and(|delta| delta.state == BaselineState::Comparable)
    {
        return raw_fingerprints;
    }
    for planned in &plan.checks {
        let Some(output) = outputs.get_mut(&planned.task_id) else {
            continue;
        };
        let mut current = vec![];
        if let Err(reason) = snapshots(&output.extra, &mut current) {
            result
                .limitations
                .push(format!("coverage baseline {}: {reason}", planned.task_id));
            output.extra["coverage_regression"] =
                json!({"policy_enabled":true,"comparison_complete":false,"limitations":[reason]});
            continue;
        }
        if !current
            .iter()
            .any(|policy| policy.regression_tolerance.is_some())
        {
            if configured_checks.contains(&planned.task_id) {
                let reason =
                    "configured coverage regression policy has no current measured snapshot";
                result
                    .limitations
                    .push(format!("coverage baseline {}: {reason}", planned.task_id));
                output.extra["coverage_regression"] = json!({"policy_enabled":true,"comparison_complete":false,"limitations":[reason]});
            }
            continue;
        }
        let mut previous = vec![];
        let old_snapshot = previous_extra(document, &planned.task_id)
            .map(|extra| snapshots(extra, &mut previous))
            .transpose();
        let scope_matches = result.checks.as_ref().into_iter().flatten().any(|now| {
            now.task_id == planned.task_id
                && now.evidence_complete
                && baseline
                    .checks
                    .as_ref()
                    .into_iter()
                    .flatten()
                    .any(|old| old.evidence_complete && old.same_scope(now))
        });
        let mut limitations = vec![];
        let mut comparisons = vec![];
        let mut complete = true;
        if let Err(reason) = old_snapshot {
            limitations.push(reason);
            previous.clear();
        }
        for policy in current
            .iter()
            .filter(|policy| policy.regression_tolerance.is_some())
        {
            let matching: Vec<_> = previous
                .iter()
                .filter(|old| {
                    old.task_id == policy.task_id
                        && old.component_id == policy.component_id
                        && old.provider == policy.provider
                })
                .collect();
            let old = (scope_matches && matching.len() == 1).then(|| matching[0]);
            match compare(policy, old) {
                Err(reason) => {
                    complete = false;
                    limitations.push(reason.to_string());
                }
                Ok(mut delta) => {
                    complete &= delta.comparison_complete;
                    limitations.extend(delta.limitations.clone());
                    if !scope_matches {
                        limitations.push("coverage checks lack completed matching scope; no regression delta inferred".into());
                    }
                    if delta.gating_applicable {
                        for mut finding in delta.findings.drain(..) {
                            finding.task_id = Some(planned.task_id.clone());
                            finding.component_id = Some(planned.component_id.clone());
                            let units = planned.task_analysis_units();
                            finding.analysis_unit_id = (units.len() == 1).then(|| units[0].clone());
                            let variants = planned.variants();
                            finding.variant = (variants.len() == 1).then(|| variants[0].clone());
                            let raw = finding.fingerprint.clone();
                            let attributed = ici_domain::attributed_fingerprint(&finding, &units);
                            match attributed {
                                Ok(fingerprint) => finding.fingerprint = fingerprint,
                                Err(reason) => {
                                    complete = false;
                                    limitations.push(reason.to_string());
                                    continue;
                                }
                            }
                            suppressions.apply(&mut finding);
                            if let Err(reason) = finding.validate() {
                                complete = false;
                                limitations.push(reason.to_string());
                                continue;
                            }
                            raw_fingerprints.insert(finding.fingerprint.clone(), raw);
                            let comparison = result.baseline.as_mut().expect("comparable baseline");
                            comparison.unchanged.retain(|fp| fp != &finding.fingerprint);
                            comparison.resolved.retain(|fp| fp != &finding.fingerprint);
                            comparison.carried.retain(|fp| fp != &finding.fingerprint);
                            if !comparison.new.contains(&finding.fingerprint) {
                                comparison.new.push(finding.fingerprint.clone());
                            }
                            if !result
                                .findings
                                .iter()
                                .any(|old| old.fingerprint == finding.fingerprint)
                            {
                                result.findings.push(finding);
                            }
                        }
                    }
                    output.targets.extend(delta.targets.clone());
                    comparisons.push(json!(delta));
                }
            }
        }
        for reason in &limitations {
            result
                .limitations
                .push(format!("coverage baseline {}: {reason}", planned.task_id));
        }
        output.extra["coverage_regression"] = json!({"policy_enabled":true,"comparison_complete":complete,"comparisons":comparisons,"limitations":limitations});
    }
    if let Some(delta) = &mut result.baseline {
        delta.new.sort();
        delta.new.dedup();
    }
    raw_fingerprints
}

pub fn incomplete(outputs: &BTreeMap<String, AnalysisOutput>) -> bool {
    outputs.values().any(|output| {
        output.extra["coverage_regression"]["policy_enabled"] == true
            && output.extra["coverage_regression"]["comparison_complete"] == false
    })
}
