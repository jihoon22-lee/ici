use ici_application::{
    plan::{Work, plan},
    request::RunRequest,
    verification::evaluate,
};
use ici_domain::{
    EvidenceLevel, Finding, FindingSuppression, GateVerdict, Observation, SourceSpan, TaskState,
};
use std::{collections::BTreeMap, fs};

fn fixture() -> (ici_application::plan::Plan, Vec<Observation>) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.py"), "x=1\n").unwrap();
    let project =
        ici_config::resolve_with_environment(dir.path(), None, None, &BTreeMap::new()).unwrap();
    let plan = plan(
        &project,
        &RunRequest {
            static_only: true,
            ..Default::default()
        },
        |_, _, _| Ok(Work::Internal),
    )
    .unwrap();
    let observations = plan
        .checks
        .iter()
        .map(|p| Observation::new(&p.task_id, "ici"))
        .collect();
    (plan, observations)
}
fn finding() -> Finding {
    let mut finding = Finding::new(
        "python.line.limit",
        "line limit exceeded",
        SourceSpan::line("main.py", 1),
        "ici",
    );
    finding.fingerprint = "provider-native-identity".into();
    finding
}
#[test]
fn completion_violations_and_workspace_scope_stay_independent() {
    let (plan, mut observations) = fixture();
    observations[0].findings.push(finding());
    let failed = evaluate(&plan, observations.clone(), false, |_| {}).unwrap();
    assert_eq!(failed.gate.selected, GateVerdict::Fail);
    assert_eq!(failed.gate.workspace, GateVerdict::NotEvaluated);
    assert!(failed.gate.has_violations);
    observations[1].truncated = true;
    let incomplete = evaluate(&plan, observations, false, |_| {}).unwrap();
    assert_eq!(incomplete.gate.selected, GateVerdict::Incomplete);
    assert!(incomplete.gate.has_violations);
    assert_eq!(incomplete.findings.len(), 1);
}
#[test]
fn estimated_optional_and_suppressed_findings_remain_advisory() {
    let (mut plan, mut observations) = fixture();
    observations[0].findings.push(finding());
    observations[0].findings[0].evidence = EvidenceLevel::Estimated;
    assert_eq!(
        evaluate(&plan, observations.clone(), false, |_| {})
            .unwrap()
            .gate
            .selected,
        GateVerdict::Pass
    );
    observations[0].findings[0].evidence = EvidenceLevel::Measured;
    plan.checks[0].check.required = false;
    assert_eq!(
        evaluate(&plan, observations.clone(), false, |_| {})
            .unwrap()
            .gate
            .selected,
        GateVerdict::Pass
    );
    plan.checks[0].check.required = true;
    let suppressed = evaluate(&plan, observations.clone(), false, |f| {
        f.suppression = FindingSuppression {
            suppressed: true,
            kind: "config".into(),
            reason: "accepted exception".into(),
            origin: "ici.toml:7".into(),
        }
    })
    .unwrap();
    assert_eq!(suppressed.gate.selected, GateVerdict::Pass);
    assert_eq!(suppressed.findings.len(), 1);
    observations[0].state = TaskState::Failed;
    assert_eq!(
        evaluate(&plan, observations, false, |f| f.suppression =
            suppressed.findings[0].suppression.clone())
        .unwrap()
        .gate
        .selected,
        GateVerdict::Incomplete
    );
}

#[test]
fn advisory_type_diagnostics_stay_measured_and_missing_tool_still_incomplete() {
    let (mut plan, mut observations) = fixture();
    let index = plan
        .checks
        .iter()
        .position(|p| p.check.id == "python.type")
        .unwrap();
    let task = ici_domain::TaskSpec::command(
        &plan.checks[index].task_id,
        "ty",
        vec!["ty".into(), "check".into()],
        "/project",
    );
    plan.checks[index].work = Work::process_advisory(task);
    observations[index].findings.push(finding());
    let advisory = evaluate(&plan, observations.clone(), false, |_| {}).unwrap();
    assert_eq!(advisory.gate.selected, GateVerdict::Pass);
    assert_eq!(advisory.findings[0].evidence, EvidenceLevel::Measured);
    assert!(
        advisory.findings[0]
            .tags
            .iter()
            .any(|tag| tag == "advisory")
    );
    observations[index].state = TaskState::Failed;
    assert_eq!(
        evaluate(&plan, observations, false, |_| {})
            .unwrap()
            .gate
            .selected,
        GateVerdict::Incomplete
    );
}
#[test]
fn missing_and_invalid_provider_evidence_cannot_pass_and_cancel_keeps_findings() {
    let (plan, mut observations) = fixture();
    observations.pop();
    assert_eq!(
        evaluate(&plan, observations, false, |_| {})
            .unwrap()
            .gate
            .selected,
        GateVerdict::Incomplete
    );
    let (_, mut observations) = fixture();
    observations[0].duration_seconds = Some(f64::NAN);
    assert_eq!(
        evaluate(&plan, observations, false, |_| {})
            .unwrap()
            .observations[0]
            .state,
        TaskState::Failed
    );
    let (_, mut observations) = fixture();
    observations[0].findings.push(finding());
    let cancelled = evaluate(&plan, observations, true, |_| {}).unwrap();
    assert_eq!(cancelled.gate.selected, GateVerdict::Incomplete);
    assert_eq!(cancelled.findings.len(), 1);
}
#[test]
fn attribution_prevents_cross_component_aliasing_and_full_coverage_preserves_violations() {
    let (mut plan, mut observations) = fixture();
    observations[0].findings.push(finding());
    let first = evaluate(&plan, observations.clone(), false, |_| {}).unwrap();
    plan.checks[0].component_id = "other".into();
    plan.coverage_gaps.push("required component omitted".into());
    let second = evaluate(&plan, observations, false, |_| {}).unwrap();
    assert_ne!(
        first.findings[0].fingerprint,
        second.findings[0].fingerprint
    );
    assert_eq!(second.findings[0].component_id.as_deref(), Some("other"));
    assert_eq!(second.gate.selected, GateVerdict::Incomplete);
    assert!(second.gate.has_violations);
}
