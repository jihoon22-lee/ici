//! Comparison never modifies its baseline. Missing scope evidence cannot prove a resolution.
use ici_domain::{
    BaselineComparison, BaselineState, CheckRecord, FINGERPRINT_VERSION, Finding, RunIdentity,
    RunResult,
};
use std::collections::{BTreeMap, BTreeSet};

pub fn compare(
    current: &[Finding],
    identity: &RunIdentity,
    checks: Option<&[CheckRecord]>,
    baseline: &RunResult,
    origin: impl Into<String>,
) -> BaselineComparison {
    let mut result = BaselineComparison {
        state: BaselineState::Incompatible,
        origin: origin.into(),
        ..Default::default()
    };
    // Frozen application/baseline.py `compare` reasons, verbatim.
    let reason = if baseline.identity.fingerprint_version != FINGERPRINT_VERSION
        || identity.fingerprint_version != FINGERPRINT_VERSION
    {
        "the baseline was written under a different fingerprint normalization — accept the current run as the new baseline instead of comparing across the change"
    } else if baseline.identity.policy_digest != identity.policy_digest {
        "policy changed since the baseline — accept the current run as the new baseline instead of comparing across the change"
    } else if baseline.identity.toolchain_digest != identity.toolchain_digest {
        "the provider toolchain changed since the baseline — findings from different tool versions are not the same identity"
    } else if baseline.checks.is_none() || checks.is_none() {
        "completed check scope is unknown — accept a new v2 result as the baseline; older results remain readable but cannot prove resolutions"
    } else {
        ""
    };
    if !reason.is_empty() {
        result.reason = reason.into();
        return result;
    }
    result.state = BaselineState::Comparable;
    let previous: BTreeSet<_> = baseline
        .findings
        .iter()
        .map(|f| f.fingerprint.clone())
        .collect();
    let now: BTreeSet<_> = current.iter().map(|f| f.fingerprint.clone()).collect();
    let old_records: BTreeMap<_, _> = baseline
        .checks
        .as_ref()
        .expect("checked")
        .iter()
        .map(|c| (c.task_id.as_str(), c))
        .collect();
    let completed: Vec<_> = checks
        .expect("checked")
        .iter()
        .filter(|c| c.evidence_complete)
        .collect();
    let mut resolved = BTreeSet::new();
    for finding in &baseline.findings {
        if now.contains(&finding.fingerprint) {
            continue;
        }
        let old = finding
            .task_id
            .as_deref()
            .and_then(|task_id| old_records.get(task_id));
        if let Some(old) = old
            && finding.component_id.as_deref() == Some(old.component_id.as_str())
            && completed.iter().any(|record| old.same_scope(record))
        {
            resolved.insert(finding.fingerprint.clone());
        }
    }
    result.new = now.difference(&previous).cloned().collect();
    result.unchanged = now.intersection(&previous).cloned().collect();
    result.carried = previous
        .difference(&now)
        .filter(|fp| !resolved.contains(*fp))
        .cloned()
        .collect();
    result.resolved = resolved.into_iter().collect();
    result
}
