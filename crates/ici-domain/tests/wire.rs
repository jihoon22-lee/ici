use ici_domain::*;
use serde_json::json;

#[test]
fn published_result_goldens_roundtrip_without_lost_information() {
    for fixture in [
        include_str!("../../../tests/fixtures/ici-next/run-success.json"),
        include_str!("../../../tests/fixtures/ici-next/run-code-fail.json"),
        include_str!("../../../tests/fixtures/ici-next/run-required-incomplete.json"),
        include_str!("../../../tests/fixtures/ici-next/run-partial-selection.json"),
        include_str!("../../../tests/fixtures/ici-next/run-cancelled.json"),
        include_str!("../../../tests/rust-parity/domain/run-v2.json"),
    ] {
        let result = RunResult::from_json(fixture).expect("published golden");
        let expected: serde_json::Value = serde_json::from_str(fixture).unwrap();
        assert_eq!(result.to_value().unwrap(), expected);
    }
}
#[test]
fn refuse_unknown_or_inconsistent_evidence() {
    let original: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/domain/run-v2.json"
    ))
    .unwrap();
    for (pointer, value) in [
        ("/schema_version", json!(3)),
        ("/schema_version", json!(true)),
        ("/execution/required_complete", json!(false)),
        ("/scope/kind", json!("partial")),
        ("/checks/0/state", json!("FAILED")),
        ("/metrics/0/denominator", json!(0)),
    ] {
        let mut changed = original.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        assert!(RunResult::from_value(changed).is_err(), "{pointer}");
    }
    let mut changed = original.clone();
    changed.as_object_mut().unwrap().remove("checks");
    assert!(RunResult::from_value(changed).is_err());
    let mut changed = original;
    changed["checks"][0]
        .as_object_mut()
        .unwrap()
        .remove("variant");
    assert!(RunResult::from_value(changed).is_err());
}
#[test]
fn fingerprint_unicode_matches_frozen_python_oracle() {
    let mut span = SourceSpan::line("pkg/한글.py", 2);
    span.start_column = Some(3);
    let mut finding = Finding::new("example", "한글 😀 é", span, "ici");
    finding.fingerprint = "native-id".into();
    finding.component_id = Some("project".into());
    finding.analysis_unit_id = Some("project.python".into());
    let expected: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/domain/fingerprint.json"
    ))
    .unwrap();
    assert_eq!(
        attributed_fingerprint(&finding, &["project.python".into()]).unwrap(),
        expected["expected"]
    );
}
#[test]
fn bounded_events_preserve_gaps_and_unknown_optional_events() {
    let event = RunEvent {
        schema_id: EVENT_SCHEMA_ID.into(),
        schema_version: 1,
        run_id: "run".into(),
        seq: 1,
        event_type: "run.started".into(),
        timestamp: "2026-10-28T00:00:00Z".into(),
        task_id: None,
        component_id: None,
        message: String::new(),
    };
    let first = event.to_json().unwrap();
    let mut last = event.clone();
    last.seq = 3;
    last.event_type = "run.completed".into();
    let input = format!(
        "{first}\n{}\n{{\"schema_id\":\"ici.next.event\",\"schema_version\":1,\"event_type\":\"future\"}}\n{{",
        last.to_json().unwrap()
    );
    let (events, notes) = read_events(&input).unwrap();
    assert_eq!(events.len(), 2);
    assert_eq!(notes.len(), 3);
    let mut bad = event;
    bad.event_type = "task.started".into();
    assert!(bad.to_json().is_err());
    assert!(read_events("{\"schema_id\":\"ici.next.event\",\"schema_version\":2}").is_err());
}
#[test]
fn process_completion_does_not_imply_complete_evidence() {
    let mut observation = Observation::new("task", "ruff");
    observation.exit_code = Some(1);
    assert!(observation.evidence_is_complete());
    observation.truncated = true;
    assert!(!observation.evidence_is_complete());
    let mut task = TaskSpec::command("task", "cargo", vec!["cargo".into()], ".");
    task.kind = TaskKind::Prepare;
    task.cacheable = true;
    assert!(task.validate().is_err());
    assert_eq!(GateVerdict::Incomplete.exit_code(), 3);
}
