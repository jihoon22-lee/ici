//! Frozen legacy baseline deltas (`core/baseline.py`) derived from a native comparison. The
//! native comparator decides new/unchanged/resolved/carried by fingerprint; severity is not part
//! of a native fingerprint, so the frozen `_is_regressed` rule is applied to unchanged pairs here.
use crate::projection::legacy_engine;
use ici_domain::{BaselineComparison, BaselineState, Finding, RunResult};
use serde_json::{Value, json};
use std::collections::BTreeMap;

/// Frozen `_SEVERITY_RANK` (info < low < medium < high < critical) with the native aliases.
/// An unknown severity has no rank, so no regression is claimed for it.
fn severity_rank(severity: &str) -> Option<u8> {
    Some(match severity {
        "info" | "note" => 0,
        "low" => 1,
        "medium" | "warning" => 2,
        "high" | "error" => 3,
        "critical" | "fatal" => 4,
        _ => return None,
    })
}

/// Frozen `_is_regressed`: a higher severity than the baseline, or a baseline suppression that no
/// longer applies.
pub fn severity_regressed(current: &Finding, baseline: &Finding) -> bool {
    matches!(
        (severity_rank(&current.severity), severity_rank(&baseline.severity)),
        (Some(now), Some(before)) if now > before
    ) || (baseline.suppression.suppressed && !current.suppression.suppressed)
}

/// Whether a new or regressed finding blocks `--fail-on-new` (frozen `_is_actionable`): measured,
/// not suppressed, not informational, and not a native advisory unless the legacy policy made
/// it advisory or it exists only for the baseline.
pub fn baseline_gated(finding: &Finding) -> bool {
    finding.counts_against_gate()
        && severity_rank(&finding.severity) != Some(0)
        && (finding
            .tags
            .iter()
            .any(|tag| tag == "baseline-only" || tag == "legacy-policy-advisory")
            || !finding.tags.iter().any(|tag| tag == "advisory"))
}

/// Fingerprints that fail `--fail-on-new`: gated new findings and gated severity regressions.
pub fn baseline_gate_failures(
    current: &RunResult,
    baseline: &RunResult,
    comparison: &BaselineComparison,
) -> Vec<String> {
    if comparison.state != BaselineState::Comparable {
        return vec![];
    }
    let before = by_fingerprint(baseline);
    current
        .findings
        .iter()
        .filter(|finding| baseline_gated(finding))
        .filter(|finding| {
            comparison.new.contains(&finding.fingerprint)
                || comparison.unchanged.contains(&finding.fingerprint)
                    && before
                        .get(finding.fingerprint.as_str())
                        .is_some_and(|old| severity_regressed(finding, old))
        })
        .map(|finding| finding.fingerprint.clone())
        .collect()
}

fn by_fingerprint(run: &RunResult) -> BTreeMap<&str, &Finding> {
    run.findings
        .iter()
        .map(|finding| (finding.fingerprint.as_str(), finding))
        .collect()
}

fn engine_name(run: &RunResult, finding: &Finding, names: &BTreeMap<String, String>) -> String {
    let task = finding.task_id.as_deref().unwrap_or("");
    names
        .get(task)
        .cloned()
        .or_else(|| {
            run.checks
                .as_deref()
                .unwrap_or_default()
                .iter()
                .find(|check| check.task_id == task)
                .and_then(|check| legacy_engine(&check.check_id))
                .map(str::to_owned)
        })
        .unwrap_or_else(|| finding.provider.clone())
}

fn location(finding: &Finding) -> Value {
    let span = &finding.primary_location;
    json!({"path":span.path,"start_line":span.start_line,"end_line":span.end_line,
        "start_column":span.start_column,"end_column":span.end_column,"label":span.label})
}

/// The frozen `baseline_comparison` object of a legacy view for a comparable native baseline:
/// one entry per new, unchanged, resolved and carried fingerprint, with the frozen
/// regressed/gated flags and fail-on-new gate. `None` when the baseline was not comparable.
pub fn legacy_baseline_comparison(
    current: &RunResult,
    baseline: &RunResult,
    comparison: &BaselineComparison,
    engine_names: &BTreeMap<String, String>,
    fail_on_new: bool,
) -> Option<Value> {
    if comparison.state != BaselineState::Comparable {
        return None;
    }
    let now = by_fingerprint(current);
    let before = by_fingerprint(baseline);
    let mut entries = vec![];
    let mut regressed_count = 0usize;
    let mut gated_count = 0usize;
    for fingerprint in &comparison.new {
        let Some(finding) = now.get(fingerprint.as_str()) else {
            continue;
        };
        let gated = baseline_gated(finding);
        gated_count += usize::from(gated);
        entries.push(
            json!({"state":"new","engine_name":engine_name(current, finding, engine_names),
            "fingerprint":fingerprint,"rule_id":finding.rule_id,"message":finding.message,
            "current_location":location(finding),"baseline_location":null,
            "current_severity":finding.severity,"baseline_severity":null,"regressed":false,
            "suppressed":finding.suppression.suppressed,"gated":gated}),
        );
    }
    for fingerprint in &comparison.unchanged {
        let (Some(finding), Some(old)) = (
            now.get(fingerprint.as_str()),
            before.get(fingerprint.as_str()),
        ) else {
            continue;
        };
        let regressed = severity_regressed(finding, old);
        let gated = regressed && baseline_gated(finding);
        regressed_count += usize::from(regressed);
        gated_count += usize::from(gated);
        entries.push(
            json!({"state":"unchanged","engine_name":engine_name(current, finding, engine_names),
            "fingerprint":fingerprint,"rule_id":finding.rule_id,"message":finding.message,
            "current_location":location(finding),"baseline_location":location(old),
            "current_severity":finding.severity,"baseline_severity":old.severity,
            "regressed":regressed,"suppressed":finding.suppression.suppressed,"gated":gated}),
        );
    }
    for (state, list) in [
        ("resolved", &comparison.resolved),
        ("carried", &comparison.carried),
    ] {
        for fingerprint in list {
            let Some(old) = before.get(fingerprint.as_str()) else {
                continue;
            };
            entries.push(
                json!({"state":state,"engine_name":engine_name(baseline, old, engine_names),
                "fingerprint":fingerprint,"rule_id":old.rule_id,"message":old.message,
                "current_location":null,"baseline_location":location(old),
                "current_severity":null,"baseline_severity":old.severity,"regressed":false,
                "suppressed":old.suppression.suppressed,"gated":false}),
            );
        }
    }
    Some(json!({
        "source_path":comparison.origin,"warnings":[],"baseline_metadata":null,
        "fail_on_new":fail_on_new,"gate_failed":fail_on_new && gated_count > 0,
        "new_count":comparison.new.len(),"unchanged_count":comparison.unchanged.len(),
        "moved_count":0,"resolved_count":comparison.resolved.len(),
        "carried_count":comparison.carried.len(),
        "regressed_count":regressed_count,"gated_count":gated_count,"entries":entries
    }))
}

#[cfg(test)]
mod tests {
    use super::{baseline_gated, severity_regressed};
    use ici_domain::{EvidenceLevel, Finding, SourceSpan};

    fn finding(severity: &str, suppressed: bool) -> Finding {
        let mut finding = Finding::new("rule", "message", SourceSpan::line("a.py", 1), "native");
        finding.severity = severity.into();
        finding.evidence = EvidenceLevel::Measured;
        finding.suppression.suppressed = suppressed;
        finding
    }

    #[test]
    fn frozen_is_regressed_and_is_actionable() {
        assert!(severity_regressed(
            &finding("high", false),
            &finding("medium", false)
        ));
        assert!(severity_regressed(
            &finding("error", false),
            &finding("warning", false)
        ));
        assert!(!severity_regressed(
            &finding("medium", false),
            &finding("high", false)
        ));
        assert!(!severity_regressed(
            &finding("medium", false),
            &finding("medium", false)
        ));
        // A baseline suppression that no longer applies is a regression.
        assert!(severity_regressed(
            &finding("low", false),
            &finding("low", true)
        ));
        // Unknown severities are never claimed as a regression.
        assert!(!severity_regressed(
            &finding("custom", false),
            &finding("low", false)
        ));
        // Informational and suppressed findings never gate.
        assert!(!baseline_gated(&finding("info", false)));
        assert!(!baseline_gated(&finding("high", true)));
        assert!(baseline_gated(&finding("low", false)));
        let mut advisory = finding("high", false);
        advisory.tags = vec!["advisory".into()];
        assert!(!baseline_gated(&advisory));
        advisory.tags.push("legacy-policy-advisory".into());
        assert!(baseline_gated(&advisory));
    }
}
