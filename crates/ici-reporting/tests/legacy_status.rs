//! Frozen `EngineStatus` class of saved native checks in the historical engine view.
use ici_domain::{AnalysisOutput, CheckRecord, GateVerdict, RunResult, TaskState};
use ici_reporting::{ProjectionContext, legacy_projection};
use serde_json::Value;
use std::collections::BTreeMap;

fn check(task: &str, check: &str, state: TaskState) -> CheckRecord {
    CheckRecord {
        task_id: task.into(),
        check_id: check.into(),
        component_id: "app".into(),
        language: if check.starts_with("cpp.") {
            "cpp"
        } else {
            "python"
        }
        .into(),
        scope_digest: format!("sha256:{}", "a".repeat(64)),
        state,
        evidence_complete: state == TaskState::Succeeded,
        analysis_unit_id: None,
        variant: None,
    }
}
fn output(task: &str, status: &str, summary: &str) -> (String, AnalysisOutput) {
    let mut output = AnalysisOutput::new(task, "native");
    output.status = status.into();
    output.summary = summary.into();
    (task.into(), output)
}

fn engines(checks: Vec<CheckRecord>, outputs: BTreeMap<String, AnalysisOutput>) -> Value {
    let mut run = RunResult::from_json(include_str!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap();
    run.schema_version = 2;
    run.execution.failed_task_ids = checks
        .iter()
        .filter(|check| check.state == TaskState::Failed)
        .map(|check| check.task_id.clone())
        .collect();
    run.execution.blocked_task_ids = checks
        .iter()
        .filter(|check| check.state == TaskState::Blocked)
        .map(|check| check.task_id.clone())
        .collect();
    run.execution.required_complete = false;
    run.gate.selected = GateVerdict::Incomplete;
    run.gate.reasons = vec!["incomplete".into()];
    run.checks = Some(checks);
    let document = legacy_projection(&run, &outputs, &ProjectionContext::default()).unwrap();
    let value = document.to_value();
    let mut engines = serde_json::Map::new();
    for engine in value["results"].as_array().unwrap() {
        engines.insert(
            engine["engine_name"].as_str().unwrap().into(),
            serde_json::json!({"status":engine["status"],"summary":engine["summary"]}),
        );
    }
    assert_eq!(
        value["suite_status"], "ERROR",
        "an incomplete run never passes"
    );
    Value::Object(engines)
}

#[test]
fn failed_checks_keep_the_producer_verdict_class_and_never_project_as_a_pass() {
    let engines = engines(
        vec![
            check("build", "cpp.artifact", TaskState::Failed),
            check("sanitize", "cpp.sanitize", TaskState::Failed),
            check("lint", "python.lint", TaskState::Failed),
            check("type", "python.type", TaskState::Failed),
            check("dead", "python.dead", TaskState::Failed),
            check("complexity", "python.complexity", TaskState::Blocked),
            check("ok", "python.cognitive", TaskState::Succeeded),
        ],
        BTreeMap::from([
            // Frozen BuildEngine: an adapter/recipe failure is a measured FAIL verdict.
            output(
                "build",
                "FAIL",
                "Build did not produce a complete artifact set",
            ),
            // Frozen tool/setup error: ERROR whose summary is the error itself.
            output(
                "sanitize",
                "ERROR",
                "Make configuration failed: build.make.sanitize_build_argv is required for sanitize",
            ),
            // A failed task cannot pass, warn or skip.
            output("lint", "PASS", ""),
            output("type", "SKIP", "pytest module is unavailable"),
            output("ok", "WARN", "1 warning"),
        ]),
    );
    let expect = |engine: &str, status: &str, summary: &str| {
        assert_eq!(
            engines[engine],
            serde_json::json!({"status":status,"summary":summary}),
            "{engine}"
        );
    };
    expect(
        "build",
        "FAIL",
        "Build did not produce a complete artifact set",
    );
    expect(
        "sanitize",
        "ERROR",
        "Make configuration failed: build.make.sanitize_build_argv is required for sanitize",
    );
    expect("lint", "ERROR", "analysis did not complete");
    expect("type", "ERROR", "pytest module is unavailable");
    expect("dead", "ERROR", "analysis did not complete");
    expect("complexity", "SKIP", "");
    expect("cognitive", "WARN", "1 warning");
}

#[test]
fn several_errors_of_one_engine_join_like_the_frozen_tool_error_summary() {
    let engines = engines(
        vec![
            check("a", "python.line", TaskState::Failed),
            check("b", "cpp.line", TaskState::Failed),
            check("c", "python.complexity", TaskState::Succeeded),
            check("d", "cpp.complexity", TaskState::Failed),
        ],
        BTreeMap::from([
            output("a", "ERROR", "first error"),
            output("b", "ERROR", "second error"),
            output("c", "PASS", "2 inspected targets"),
            output("d", "ERROR", "parser failed"),
        ]),
    );
    assert_eq!(engines["line"]["status"], "ERROR");
    assert_eq!(engines["line"]["summary"], "first error; second error");
    // A passing sibling's count never stands next to an ERROR verdict.
    assert_eq!(engines["complexity"]["status"], "ERROR");
    assert_eq!(engines["complexity"]["summary"], "parser failed");
}

#[test]
fn one_frozen_exit_and_severity_rule_serves_every_legacy_verdict() {
    use ici_reporting::{legacy_exit_code, legacy_worst_status};
    // Frozen `exit_code_for_status`.
    for (status, code) in [
        ("PASS", 0),
        ("WARN", 0),
        ("FAIL", 1),
        ("ERROR", 1),
        ("SKIP", 2),
        ("", 0),
    ] {
        assert_eq!(legacy_exit_code(status), code, "{status}");
    }
    assert_eq!(
        legacy_worst_status(["PASS", "FAIL", "WARN"], "SKIP"),
        "FAIL"
    );
    assert_eq!(legacy_worst_status(["FAIL", "ERROR"], "WARN"), "ERROR");
    assert_eq!(legacy_worst_status([], "WARN"), "WARN");
    // SKIP never hides a measured verdict and never outranks a pass.
    assert_eq!(legacy_worst_status(["SKIP"], "PASS"), "PASS");
}

#[test]
fn an_unknown_required_policy_never_explains_a_suite_with_a_required_engine() {
    // Unknown task policy (null) is not a required engine for the suite status and therefore
    // not for its gate reason either: both read one flag.
    let mut run = RunResult::from_json(include_str!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap();
    run.schema_version = 2;
    run.checks = Some(vec![check("lint", "python.lint", TaskState::Succeeded)]);
    let outputs = BTreeMap::from([output("lint", "ERROR", "tool crashed")]);
    let document = legacy_projection(&run, &outputs, &ProjectionContext::default()).unwrap();
    let value = document.to_value();
    assert_eq!(value["results"][0]["required"], Value::Null);
    assert_eq!(value["suite_status"], "WARN");
    let text = ici_reporting::render_console(
        &ici_reporting::StoredResult::Legacy(document),
        &ici_reporting::RenderOptions::default(),
    )
    .unwrap();
    assert!(text.contains("Suite: WARN"), "{text}");
    assert!(!text.contains("required engine 'lint'"), "{text}");
}
