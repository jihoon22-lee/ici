use ici_application::{
    assemble::{RunContext, assemble},
    plan::{Work, plan},
    request::RunRequest,
    verification::evaluate,
};
use ici_domain::{
    CheckRecord, Finding, GateVerdict, Observation, ScopeKind, SourceSnapshot, SourceSpan,
    TaskState, sha256,
};
use std::{collections::BTreeMap, fs};
fn context(
    plan: &ici_application::plan::Plan,
    verification: &ici_application::verification::Verification,
) -> RunContext {
    RunContext {
        run_id: "run-native".into(),
        ici_version: "0.14.0-dev.0".into(),
        source: SourceSnapshot {
            digest: sha256("sources"),
            files: vec!["main.py".into()],
            generated: vec![],
            external_inputs: vec![],
            commit: None,
            dirty: false,
        },
        policy_digest: sha256("policy"),
        toolchain_digest: sha256("tools"),
        bundle_digest: None,
        cancelled: false,
        inputs_stable: true,
        checks: plan
            .checks
            .iter()
            .map(|planned| {
                let observation = verification
                    .observations
                    .iter()
                    .find(|o| o.task_id == planned.task_id)
                    .unwrap();
                CheckRecord {
                    task_id: planned.task_id.clone(),
                    check_id: planned.check.id.clone(),
                    component_id: planned.component_id.clone(),
                    language: planned.check.language.clone(),
                    scope_digest: sha256("scope"),
                    state: observation.state,
                    evidence_complete: observation.evidence_is_complete(),
                    analysis_unit_id: None,
                    variant: None,
                }
            })
            .collect(),
        reused_task_ids: vec![],
        limitations: vec![],
        baseline: None,
    }
}
#[test]
fn static_pass_is_partial_and_only_complete_full_verification_can_satisfy_workspace() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x=1").unwrap();
    let project =
        ici_config::resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    for static_only in [true, false] {
        let request = RunRequest {
            static_only,
            ..Default::default()
        };
        let plan = plan(&project, &request, |_, _, _| Ok(Work::Internal)).unwrap();
        // Synthetic completed observations test envelope policy; they are not external-tool evidence.
        let observations = plan
            .checks
            .iter()
            .map(|c| Observation::new(&c.task_id, "fixture"))
            .collect();
        let verification = evaluate(&plan, observations, false, |_| {}).unwrap();
        let context = context(&plan, &verification);
        let result = assemble(&project, &request, &plan, verification, context).unwrap();
        assert_eq!(result.gate.selected, GateVerdict::Pass);
        assert_eq!(
            result.scope.kind,
            if static_only {
                ScopeKind::Partial
            } else {
                ScopeKind::Full
            }
        );
        assert_eq!(result.scope.full_required_satisfied, !static_only);
        assert_eq!(
            result.gate.workspace,
            if static_only {
                GateVerdict::NotEvaluated
            } else {
                GateVerdict::Pass
            }
        );
    }
}
#[test]
fn drift_keeps_findings_and_completed_process_fact_but_cannot_prove_resolution_or_pass() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x=1").unwrap();
    let project =
        ici_config::resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    let request = RunRequest::default();
    let plan = plan(&project, &request, |_, _, _| Ok(Work::Internal)).unwrap();
    let mut observations: Vec<_> = plan
        .checks
        .iter()
        .map(|c| Observation::new(&c.task_id, "fixture"))
        .collect();
    let mut finding = Finding::new(
        "rule",
        "violation",
        SourceSpan::line("main.py", 1),
        "fixture",
    );
    finding.fingerprint = "native".into();
    observations[0].findings.push(finding);
    let verification = evaluate(&plan, observations, false, |_| {}).unwrap();
    let mut context = context(&plan, &verification);
    context.inputs_stable = false;
    let result = assemble(&project, &request, &plan, verification, context).unwrap();
    assert_eq!(result.gate.selected, GateVerdict::Incomplete);
    assert!(result.gate.has_violations);
    assert!(!result.execution.required_complete);
    assert_eq!(result.findings.len(), 1);
    assert_eq!(
        result.checks.as_ref().unwrap()[0].state,
        TaskState::Succeeded
    );
    assert!(!result.checks.as_ref().unwrap()[0].evidence_complete);
}
#[test]
fn cancellation_has_exit130_and_does_not_change_publication_into_a_code_verdict() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x=1").unwrap();
    let project =
        ici_config::resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    let request = RunRequest::default();
    let plan = plan(&project, &request, |_, _, _| Ok(Work::Internal)).unwrap();
    let verification = evaluate(&plan, vec![], true, |_| {}).unwrap();
    let mut context = context(&plan, &verification);
    context.cancelled = true;
    let result = assemble(&project, &request, &plan, verification, context).unwrap();
    assert_eq!(result.exit_code(), 130);
    assert!(!result.scope.full_required_satisfied);
    assert_eq!(
        result.publication.state,
        ici_domain::PublicationState::NotConfigured
    );
}
