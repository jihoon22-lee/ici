use crate::{
    ApplicationError, Result, plan::Plan, request::RunRequest, verification::Verification,
};
use ici_config::ResolvedProject;
use ici_domain::{
    BaselineComparison, CheckRecord, ExecutionSummary, FINGERPRINT_VERSION, GateVerdict, Producer,
    PublicationOutcome, RUN_SCHEMA_ID, RUN_SCHEMA_VERSION, RunIdentity, RunResult, ScopeKind,
    ScopeSelection, SourceSnapshot, TaskState, Validate,
};
use std::collections::BTreeSet;

pub struct RunContext {
    pub run_id: String,
    pub ici_version: String,
    pub source: SourceSnapshot,
    pub policy_digest: String,
    pub toolchain_digest: String,
    pub bundle_digest: Option<String>,
    pub cancelled: bool,
    pub inputs_stable: bool,
    pub checks: Vec<CheckRecord>,
    pub reused_task_ids: Vec<String>,
    pub limitations: Vec<String>,
    pub baseline: Option<BaselineComparison>,
}
pub fn assemble(
    project: &ResolvedProject,
    request: &RunRequest,
    plan: &Plan,
    mut verification: Verification,
    context: RunContext,
) -> Result<RunResult> {
    if context.checks.len() != plan.checks.len() {
        return Err(ApplicationError(
            "new v2 result must record every selected check".into(),
        ));
    }
    for planned in &plan.checks {
        let record = context
            .checks
            .iter()
            .find(|record| record.task_id == planned.task_id)
            .ok_or_else(|| {
                ApplicationError(format!("missing check record for {}", planned.task_id))
            })?;
        let observation = verification
            .observations
            .iter()
            .find(|observation| observation.task_id == planned.task_id)
            .ok_or_else(|| {
                ApplicationError(format!("missing observation for {}", planned.task_id))
            })?;
        if record.check_id != planned.check.id
            || record.component_id != planned.component_id
            || record.language != planned.check.language
            || record.state != observation.state
            || (record.evidence_complete && !observation.evidence_is_complete())
        {
            return Err(ApplicationError(format!(
                "check record disagrees with measured selection/completion for {}",
                planned.task_id
            )));
        }
    }
    if !context.inputs_stable {
        verification.gate.selected = GateVerdict::Incomplete;
        verification
            .gate
            .reasons
            .push("inputs changed while running; the saved findings are partial evidence".into());
    }
    if context.cancelled && verification.gate.selected != GateVerdict::Incomplete {
        verification.gate.selected = GateVerdict::Incomplete;
        verification.gate.reasons.push("run cancelled".into());
    }
    let complete = verification.gate.selected != GateVerdict::Incomplete && !context.cancelled;
    let declared: BTreeSet<_> = project
        .components
        .iter()
        .flat_map(|c| c.languages.iter().cloned())
        .collect();
    let selected_languages: BTreeSet<_> = plan.languages.iter().cloned().collect();
    let kind = if project.scope_kind == ScopeKind::Standalone {
        ScopeKind::Standalone
    } else if request.static_only
        || !request.components.is_empty()
        || declared != selected_languages
        || !complete
    {
        ScopeKind::Partial
    } else {
        ScopeKind::Full
    };
    let omitted: Vec<_> = project
        .components
        .iter()
        .filter(|c| !plan.components.contains(&c.id))
        .map(|c| c.id.clone())
        .collect();
    let required: Vec<_> = project
        .components
        .iter()
        .filter(|c| c.required)
        .map(|c| c.id.clone())
        .collect();
    let full_satisfied = kind == ScopeKind::Full
        && complete
        && required.iter().all(|id| plan.components.contains(id))
        && omitted.is_empty();
    if full_satisfied {
        verification.gate.workspace = verification.gate.selected;
    } else if request.require_full {
        verification.gate.workspace = GateVerdict::Incomplete;
    } else {
        verification.gate.workspace = GateVerdict::NotEvaluated;
    }
    let mut limitations = vec![];
    let mut seen = BTreeSet::new();
    for limitation in verification
        .observations
        .iter()
        .flat_map(|o| o.limitations.iter())
        .chain(plan.limitations.iter())
        .chain(context.limitations.iter())
    {
        if !limitation.is_empty() && seen.insert(limitation) {
            limitations.push(limitation.clone());
        }
    }
    let mut checks = context.checks;
    if !context.inputs_stable {
        for check in &mut checks {
            check.evidence_complete = false;
        }
    }
    let result = RunResult {
        schema_id: RUN_SCHEMA_ID.into(),
        schema_version: RUN_SCHEMA_VERSION,
        run_id: context.run_id,
        producer: Producer {
            ici_version: context.ici_version,
            bundle_digest: context.bundle_digest,
        },
        identity: RunIdentity {
            source: context.source,
            policy_digest: context.policy_digest,
            toolchain_digest: context.toolchain_digest,
            fingerprint_version: FINGERPRINT_VERSION.into(),
        },
        scope: ScopeSelection {
            kind,
            selected_components: plan.components.clone(),
            selected_languages: plan.languages.clone(),
            required_components: required,
            omitted_components: omitted,
            full_required_satisfied: full_satisfied,
        },
        execution: ExecutionSummary {
            required_complete: complete,
            cancelled: context.cancelled,
            blocked_task_ids: if complete {
                vec![]
            } else {
                verification
                    .observations
                    .iter()
                    .filter(|o| matches!(o.state, TaskState::Blocked | TaskState::Cancelled))
                    .map(|o| o.task_id.clone())
                    .collect()
            },
            failed_task_ids: verification
                .observations
                .iter()
                .filter(|o| o.state == TaskState::Failed)
                .map(|o| o.task_id.clone())
                .collect(),
            reused_task_ids: context.reused_task_ids,
        },
        metrics: verification
            .observations
            .iter()
            .flat_map(|o| o.measurements.iter().cloned())
            .collect(),
        gate: verification.gate,
        findings: verification.findings,
        publication: PublicationOutcome::default(),
        limitations,
        baseline: context.baseline,
        checks: Some(checks),
    };
    result
        .validate()
        .map_err(|e| ApplicationError(e.to_string()))?;
    Ok(result)
}
