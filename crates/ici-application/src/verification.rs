use crate::{ApplicationError, Result, plan::Plan};
use ici_domain::{
    Finding, GateOutcome, GateVerdict, Observation, TaskState, Validate, attributed_fingerprint,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Verification {
    pub gate: GateOutcome,
    pub observations: Vec<Observation>,
    pub findings: Vec<Finding>,
    /// Presentation details retain their producer fingerprint without extending run v2.
    #[serde(skip)]
    pub raw_fingerprints: BTreeMap<String, String>,
}

/// Attribute first, then suppress/deduplicate, then judge. Requiredness belongs to the plan and
/// cannot be decided from whether a tool happened to complete.
pub fn evaluate(
    plan: &Plan,
    observations: Vec<Observation>,
    cancelled: bool,
    mut suppress: impl FnMut(&mut Finding),
) -> Result<Verification> {
    let mut by_id = BTreeMap::new();
    let selected: BTreeSet<_> = plan.checks.iter().map(|p| p.task_id.as_str()).collect();
    for observation in observations {
        if !selected.contains(observation.task_id.as_str()) {
            return Err(ApplicationError(format!(
                "observation for unselected task {}",
                observation.task_id
            )));
        }
        if by_id
            .insert(observation.task_id.clone(), observation)
            .is_some()
        {
            return Err(ApplicationError("duplicate task observation".into()));
        }
    }
    let mut incomplete = vec![];
    let mut observations = vec![];
    let mut required_fingerprints = BTreeSet::new();
    let mut findings = vec![];
    let mut raw_fingerprints = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for planned in &plan.checks {
        let mut observation = by_id.remove(&planned.task_id).unwrap_or_else(|| {
            let mut observation = Observation::new(&planned.task_id, "ici");
            observation.state = if cancelled {
                TaskState::Cancelled
            } else {
                TaskState::Blocked
            };
            observation.limitations.push(
                planned
                    .blocked()
                    .unwrap_or("no observation returned for selected check")
                    .into(),
            );
            observation
        });
        if let Err(error) = observation.validate() {
            observation.state = TaskState::Failed;
            observation.findings.clear();
            observation.measurements.clear();
            observation
                .limitations
                .push(format!("invalid provider evidence: {error}"));
            // Keep failure facts, but an invalid duration must not poison the saved result.
            if observation
                .duration_seconds
                .is_some_and(|v| !v.is_finite() || v < 0.0)
            {
                observation.duration_seconds = None;
            }
        }
        // Blocked plan entries cannot be completed by an unrelated or stale observation.
        if let Some(reason) = planned.blocked() {
            observation.state = TaskState::Blocked;
            observation.limitations.push(reason.into());
        }
        let units = planned.analysis_units();
        let variants = planned.variants();
        for finding in &mut observation.findings {
            finding.component_id = Some(planned.component_id.clone());
            finding.task_id = Some(planned.task_id.clone());
            if units.len() == 1 {
                finding.analysis_unit_id = Some(units[0].clone());
            } else if units.len() > 1
                && !finding
                    .analysis_unit_id
                    .as_ref()
                    .is_some_and(|u| units.contains(u))
            {
                finding.analysis_unit_id = None;
            }
            if variants.len() == 1 {
                finding.variant = Some(variants[0].clone());
            } else if variants.len() > 1
                && !finding
                    .variant
                    .as_ref()
                    .is_some_and(|variant| variants.contains(variant))
            {
                finding.variant = None;
            }

            if (!planned.check.required || planned.advisory())
                && !finding.tags.iter().any(|tag| tag == "advisory")
            {
                finding.tags.push("advisory".into());
            }
            let raw_fingerprint = finding.fingerprint.clone();
            finding.fingerprint = attributed_fingerprint(finding, &units)
                .map_err(|e| ApplicationError(e.to_string()))?;
            raw_fingerprints.insert(finding.fingerprint.clone(), raw_fingerprint);
            if planned.check.required
                && !planned.advisory()
                && !finding.tags.iter().any(|tag| tag == "advisory")
            {
                required_fingerprints.insert(finding.fingerprint.clone());
            }
            let mut marked = finding.clone();
            suppress(&mut marked);
            marked
                .validate()
                .map_err(|e| ApplicationError(e.to_string()))?;
            if seen.insert(marked.fingerprint.clone()) {
                findings.push(marked);
            }
        }
        if planned.check.required && !observation.evidence_is_complete() {
            incomplete.push(format!(
                "{}: {}",
                planned.task_id,
                observation
                    .limitations
                    .last()
                    .cloned()
                    .unwrap_or_else(|| format!("{:?}", observation.state))
            ));
        }
        observations.push(observation);
    }
    if cancelled {
        incomplete.push("run cancelled".into());
    }
    let blocking = findings
        .iter()
        .filter(|f| f.counts_against_gate() && required_fingerprints.contains(&f.fingerprint))
        .count();
    let mut gate = GateOutcome {
        has_violations: blocking > 0,
        ..Default::default()
    };
    if !incomplete.is_empty() {
        gate.selected = GateVerdict::Incomplete;
        gate.reasons = incomplete;
    } else if blocking > 0 {
        gate.selected = GateVerdict::Fail;
        gate.reasons.push(format!("{blocking} violation(s) found"));
    } else {
        gate.selected = GateVerdict::Pass;
    }
    if !plan.coverage_gaps.is_empty() {
        gate.selected = GateVerdict::Incomplete;
        gate.workspace = GateVerdict::Incomplete;
        gate.reasons
            .splice(0..0, plan.coverage_gaps.iter().cloned());
    }
    Ok(Verification {
        gate,
        observations,
        findings,
        raw_fingerprints,
    })
}
