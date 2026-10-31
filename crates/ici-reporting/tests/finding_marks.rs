//! Frozen offline view marks: each finding keeps provider and comparable-baseline state.
use ici_domain::{AnalysisOutput, CheckRecord, Finding, RunResult, SourceSpan, TaskState};
use ici_reporting::{
    ProjectionContext, RenderOptions, StoredResult, legacy_projection, render_html,
    render_markdown, sarif_document,
};
use std::collections::BTreeMap;

fn run() -> RunResult {
    let mut run = RunResult::from_json(include_str!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap();
    let mut finding = Finding::new(
        "ici.example",
        "visible message",
        SourceSpan {
            path: "src/a.py".into(),
            start_line: 2,
            end_line: Some(2),
            start_column: Some(1),
            end_column: Some(4),
            label: "".into(),
        },
        "external-tool",
    );
    finding.fingerprint = "fp-new".into();
    finding.task_id = Some("task-lint".into());
    let mut same = finding.clone();
    same.fingerprint = "fp-same".into();
    same.primary_location.start_line = 9;
    same.primary_location.end_line = Some(9);
    run.findings = vec![finding, same];
    run.baseline = Some(ici_domain::BaselineComparison {
        state: ici_domain::BaselineState::Comparable,
        origin: "prior.json".into(),
        new: vec!["fp-new".into()],
        unchanged: vec!["fp-same".into()],
        resolved: vec![],
        carried: vec![],
        reason: "".into(),
    });
    run
}

#[test]
fn run_findings_show_provider_and_baseline_state_in_every_view() {
    let stored = StoredResult::from_run(run()).unwrap();
    let options = RenderOptions::default();
    let html = render_html(&stored, &options).unwrap();
    let markdown = render_markdown(&stored, &options).unwrap();
    for text in [
        "baseline\\_state: new",
        "baseline\\_state: unchanged",
        "provider: external-tool",
    ] {
        assert!(markdown.contains(text), "{text}\n{markdown}");
        let visible = html.split("<script id=").next().unwrap();
        assert!(visible.contains(&text.replace('\\', "")), "{text}");
    }
    // SARIF keeps the standard property and no duplicate private key.
    let sarif = sarif_document(&stored, &options).unwrap();
    let results = sarif["runs"][0]["results"].as_array().unwrap();
    assert!(
        results
            .iter()
            .any(|result| result["baselineState"] == "new")
    );
    assert!(
        results
            .iter()
            .all(|result| result["properties"].get("baseline_state").is_none())
    );
}

#[test]
fn projected_legacy_findings_keep_the_modern_baseline_state() {
    let mut run = run();
    run.schema_version = 2;
    run.findings.truncate(1);
    run.checks = Some(vec![CheckRecord {
        task_id: "task-lint".into(),
        check_id: "python.lint".into(),
        component_id: "tool-a".into(),
        language: "python".into(),
        scope_digest: format!("sha256:{}", "a".repeat(64)),
        state: TaskState::Succeeded,
        evidence_complete: true,
        analysis_unit_id: None,
        variant: None,
    }]);
    let outputs = BTreeMap::from([(
        "task-lint".to_string(),
        AnalysisOutput::new("task-lint", "native"),
    )]);
    let document = legacy_projection(&run, &outputs, &ProjectionContext::default()).unwrap();
    let html = render_html(&StoredResult::Legacy(document), &RenderOptions::default()).unwrap();
    let visible = html.split("<script id=").next().unwrap();
    assert!(visible.contains("baseline_state: new"), "{visible}");
    // Without a comparable baseline no state is invented.
    let mut plain = run;
    plain.baseline = None;
    plain.checks = None;
    plain.schema_version = 1;
    let html = render_html(
        &StoredResult::from_run(plain).unwrap(),
        &RenderOptions::default(),
    )
    .unwrap();
    assert!(
        !html
            .split("<script id=")
            .next()
            .unwrap()
            .contains("baseline_state")
    );
}
