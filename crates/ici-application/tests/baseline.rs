use ici_application::baseline::compare;
use ici_domain::{BaselineState, Finding, RunResult, SourceSpan, TaskState, sha256};

fn old_result() -> RunResult {
    let mut result = RunResult::from_json(include_str!(
        "../../../tests/rust-parity/domain/run-v2.json"
    ))
    .unwrap();
    let mut finding = Finding::new(
        "python.line.limit",
        "too many lines",
        SourceSpan::line("main.py", 1),
        "ici",
    );
    finding.fingerprint = "old-fingerprint".into();
    finding.component_id = Some("project".into());
    finding.task_id = Some("project.python.line".into());
    result.findings = vec![finding];
    result
}

#[test]
fn complete_matching_check_resolves_but_omitted_incomplete_and_changed_policy_do_not() {
    let old = old_result();
    let checks = old.checks.as_ref().unwrap();
    let delta = compare(&[], &old.identity, Some(checks), &old, "old.json");
    assert_eq!(delta.resolved, ["old-fingerprint"]);
    assert!(delta.carried.is_empty());
    let omitted = compare(&[], &old.identity, Some(&[]), &old, "old.json");
    assert!(omitted.resolved.is_empty());
    assert_eq!(omitted.carried, ["old-fingerprint"]);
    let mut incomplete = checks.clone();
    incomplete[0].evidence_complete = false;
    incomplete[0].state = TaskState::Cancelled;
    assert_eq!(
        compare(&[], &old.identity, Some(&incomplete), &old, "old.json").carried,
        ["old-fingerprint"]
    );
    let mut identity = old.identity.clone();
    identity.policy_digest = sha256("different policy");
    let mismatch = compare(&[], &identity, Some(checks), &old, "old.json");
    assert_eq!(mismatch.state, BaselineState::Incompatible);
    assert!(mismatch.resolved.is_empty() && mismatch.new.is_empty());
}

#[test]
fn rule_configuration_or_plugin_change_cannot_turn_missing_finding_into_resolved() {
    let old = old_result();
    let mut changed = old.checks.clone().unwrap();
    changed[0].scope_digest = sha256("same sources, ESLint rule off or plugin/lockfile changed");
    let delta = compare(&[], &old.identity, Some(&changed), &old, "old.json");
    assert_eq!(delta.state, BaselineState::Comparable);
    assert!(delta.resolved.is_empty());
    assert_eq!(delta.carried, ["old-fingerprint"]);
    let mut replaced_tool = old.identity.clone();
    replaced_tool.toolchain_digest = sha256("executable bytes replaced at same path");
    assert_eq!(
        compare(&[], &replaced_tool, Some(&changed), &old, "old.json").state,
        BaselineState::Incompatible
    );
}

#[test]
fn v1_unknown_scope_and_foreign_fingerprint_are_readable_but_not_comparable() {
    let mut old = old_result();
    old.schema_version = 1;
    old.checks = None;
    let parsed = RunResult::from_json(&old.to_json().unwrap()).unwrap();
    assert!(
        compare(&[], &parsed.identity, Some(&[]), &parsed, "old.json")
            .reason
            .contains("scope is unknown")
    );
    old.identity.fingerprint_version = "legacy".into();
    assert!(
        compare(&[], &parsed.identity, Some(&[]), &old, "old.json")
            .reason
            .contains("normalization")
    );
}

#[test]
fn fingerprints_deduplicate_and_require_matching_owner_before_resolution() {
    let old = old_result();
    let mut finding = old.findings[0].clone();
    finding.fingerprint = "new-fingerprint".into();
    let current = vec![old.findings[0].clone(), finding.clone(), finding];
    let delta = compare(
        &current,
        &old.identity,
        old.checks.as_deref(),
        &old,
        "old.json",
    );
    assert_eq!(delta.new, ["new-fingerprint"]);
    assert_eq!(delta.unchanged, ["old-fingerprint"]);
    let mut other = old.clone();
    other.findings[0].component_id = Some("another-component".into());
    assert_eq!(
        compare(
            &[],
            &old.identity,
            old.checks.as_deref(),
            &other,
            "old.json"
        )
        .carried,
        ["old-fingerprint"]
    );
}
