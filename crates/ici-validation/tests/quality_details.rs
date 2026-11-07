//! Frozen producer fields and independent optional quality execution contracts.
use ici_domain::{SourceInput, TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use serde_json::Value;
use std::collections::BTreeMap;

fn request(kind: ValidationKind) -> ValidationRequest {
    let tool = ResolvedTool {
        id: "fixture".into(),
        path: "/tools/suite".into(),
        source: ToolSource::Explicit,
        digest: sha256("fixture"),
        version: None,
        configuration: vec![],
    };
    ValidationRequest {
        kind,
        task_id: format!("component.{}", kind.provider()),
        component_id: "component".into(),
        root: "/workspace".into(),
        cwd: "/workspace".into(),
        owned_run_dir: "/workspace/run".into(),
        tool: tool.clone(),
        targets: vec!["tests".into()],
        source_files: vec!["tests/test_sample.py".into()],
        functions: vec![FunctionFact {
            ast_depth: 0,
            path: "tests/test_sample.py".into(),
            name: "test_sample".into(),
            qualified_name: "test_sample".into(),
            start_line: 4,
            entry_line: None,
            body_start_line: 5,
            end_line: 5,
        }],
        options: ValidationOptions {
            auxiliary_tools: BTreeMap::from([("readelf".into(), tool)]),
            ..Default::default()
        },
    }
}
fn outcome(id: &str, stdout: &str, stderr: &str, exit: i32) -> TaskOutcome {
    let mut item = TaskOutcome::empty(id);
    item.state = TaskState::Succeeded;
    item.exit_code = Some(exit);
    item.stdout = stdout.as_bytes().to_vec();
    item.stderr = stderr.as_bytes().to_vec();
    item
}
fn oracle(id: &str) -> Value {
    let frozen: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/finding-details-oracle.json"
    ))
    .unwrap();
    assert_eq!(
        frozen["source_revision"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    frozen["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["id"] == id)
        .unwrap()["finding"]
        .clone()
}
fn matches_original(
    output: &ici_domain::AnalysisOutput,
    item: &ici_domain::Finding,
    expected: &Value,
) {
    let metadata = &output.extra["finding_details"][&item.fingerprint];
    for (key, original) in [
        ("legacy_rule_id", "rule_id"),
        ("legacy_category", "category"),
        ("legacy_severity", "severity"),
        ("legacy_confidence", "confidence"),
        ("tool_name", "tool_name"),
        ("tool_rule_id", "tool_rule_id"),
        ("explanation", "explanation"),
        ("remediation", "remediation"),
    ] {
        assert_eq!(metadata[key], expected[original], "{key}");
    }
    assert_eq!(
        metadata["legacy_label"],
        expected["primary_location"]["label"]
    );
    if metadata.get("metrics").is_some() {
        assert_eq!(metadata["metrics"], expected["metrics"]);
    }
}
#[test]
fn quality_findings_preserve_original_fields_and_stable_identity_when_durations_change() {
    let mut context = request(ValidationKind::PythonTest);
    context.options.repeat_runs = 2;
    context.options.slow_threshold_seconds = Some(1.0);
    let planned = plan(&context).unwrap();
    let base = format!("{}.suite", context.task_id);
    let repeat = format!("{}.repeat1", context.task_id);
    let actual = observe(
        &planned,
        &BTreeMap::from([
            (
                base.clone(),
                outcome(
                    &base,
                    "tests/test_sample.py::test_sample PASSED\n1.500s call tests/test_sample.py::test_sample\n",
                    "",
                    0,
                ),
            ),
            (
                repeat.clone(),
                outcome(&repeat, "tests/test_sample.py::test_sample FAILED\n", "", 1),
            ),
        ]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
    for (id, rule) in [
        ("slow", "ici.test.slow-test"),
        ("flaky", "ici.test.flaky-test"),
    ] {
        let item = actual
            .observation
            .findings
            .iter()
            .find(|finding| finding.rule_id == rule)
            .unwrap();
        let expected = oracle(id);
        matches_original(&actual, item, &expected);
        assert_eq!(item.message, expected["message"]);
    }
    let changed = observe(
        &planned,
        &BTreeMap::from([
            (
                base.clone(),
                outcome(
                    &base,
                    "tests/test_sample.py::test_sample PASSED\n2.500s call tests/test_sample.py::test_sample\n",
                    "",
                    0,
                ),
            ),
            (
                repeat.clone(),
                outcome(&repeat, "tests/test_sample.py::test_sample FAILED\n", "", 1),
            ),
        ]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(
        actual
            .observation
            .findings
            .iter()
            .map(|item| &item.fingerprint)
            .collect::<Vec<_>>(),
        changed
            .observation
            .findings
            .iter()
            .map(|item| &item.fingerprint)
            .collect::<Vec<_>>()
    );
}
#[test]
fn optional_repeat_timeout_malformed_output_and_changed_collection_never_erase_the_base_suite() {
    for malformed in [false, true] {
        let mut context = request(ValidationKind::PythonTest);
        context.options.repeat_runs = 3;
        let planned = plan(&context).unwrap();
        let base = format!("{}.suite", context.task_id);
        let first = format!("{}.repeat1", context.task_id);
        let second = format!("{}.repeat2", context.task_id);
        let mut broken = outcome(&first, "malformed output", "", 0);
        broken.timed_out = !malformed;
        let mut outcomes = BTreeMap::from([
            (
                base.clone(),
                outcome(&base, "tests/test_sample.py::test_sample PASSED\n", "", 0),
            ),
            (first.clone(), broken),
        ]);
        assert_eq!(
            ready_steps(&planned, &outcomes).unwrap(),
            vec![second.clone()]
        );
        outcomes.insert(
            second.clone(),
            outcome(&second, "tests/test_other.py::test_other PASSED\n", "", 0),
        );
        assert!(ready_steps(&planned, &outcomes).unwrap().is_empty());
        let actual = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
        assert!(!actual.observation.timed_out);
        assert_eq!(
            actual.extra["test_quality"][if malformed {
                "repeat_unavailable"
            } else {
                "repeat_timeouts"
            }],
            1
        );
        assert_eq!(actual.extra["test_quality"]["flaky_tests"], 2);
        assert!(
            actual
                .observation
                .findings
                .iter()
                .all(|finding| finding.tags.contains(&"advisory".into()))
        );
        assert!(
            actual.extra["optional_steps"]
                .as_array()
                .unwrap()
                .iter()
                .any(|step| step["task_id"] == first)
        );
    }
}
#[test]
fn sanitizer_original_metadata_and_duplicate_labels_survive_without_inventing_unlocated_findings() {
    for (kind, tool, defect, description) in [
        (
            "asan",
            "AddressSanitizer",
            "heap-use-after-free",
            "heap-use-after-free",
        ),
        (
            "lsan",
            "LeakSanitizer",
            "memory-leak",
            "detected memory leaks",
        ),
        (
            "ubsan",
            "UndefinedBehaviorSanitizer",
            "signed-integer-overflow",
            "signed integer overflow",
        ),
        ("tsan", "ThreadSanitizer", "data-race", "data race"),
    ] {
        let mut context = request(if kind == "tsan" {
            ValidationKind::ThreadSanitizer
        } else {
            ValidationKind::Sanitizer
        });
        context.targets = vec!["suite".into()];
        context.source_files = vec!["src/suite.cpp".into()];
        let mut planned = plan(&context).unwrap();
        attach_sources(
            &mut planned,
            ici_providers::source_coordinates(&[SourceInput {
                path: "src/suite.cpp".into(),
                bytes: b"int main() {\n int value = 0;\n return value;\n}\n".to_vec(),
            }]),
        );
        let probe = format!("{}.instrumentation", context.task_id);
        let suite = format!("{}.suite", context.task_id);
        let symbols = if kind == "tsan" {
            "__tsan_init"
        } else if kind == "ubsan" {
            "__ubsan_handle_add_overflow"
        } else {
            "__asan_init"
        };
        let diagnostic = format!(
            "{}: {tool}: {description}\n#0 in work /workspace/src/suite.cpp:3:2\n",
            if kind == "tsan" { "WARNING" } else { "ERROR" }
        );
        for duplicate in [false, true] {
            let text = if duplicate {
                diagnostic.repeat(2)
            } else {
                diagnostic.clone()
            };
            let actual = observe(
                &planned,
                &BTreeMap::from([
                    (
                        probe.clone(),
                        outcome(
                            &probe,
                            &format!("Symbol table '.dynsym' contains 1 entry:\n 0 {symbols}\n"),
                            "",
                            0,
                        ),
                    ),
                    (suite.clone(), outcome(&suite, "", &text, 1)),
                ]),
                &CapturedArtifacts::default(),
            );
            assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
            let item = actual
                .observation
                .findings
                .iter()
                .find(|item| item.native_rule_id == defect)
                .unwrap();
            matches_original(
                &actual,
                item,
                &oracle(&format!(
                    "{kind}{}",
                    if duplicate { "-duplicate" } else { "" }
                )),
            );
        }
        let actual = observe(
            &planned,
            &BTreeMap::from([
                (
                    probe.clone(),
                    outcome(
                        &probe,
                        &format!("Symbol table '.dynsym' contains 1 entry:\n 0 {symbols}\n"),
                        "",
                        0,
                    ),
                ),
                (
                    suite.clone(),
                    outcome(
                        &suite,
                        "",
                        &format!(
                            "{}: {tool}: {description}\n",
                            if kind == "tsan" { "WARNING" } else { "ERROR" }
                        ),
                        1,
                    ),
                ),
            ]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(actual.observation.state, TaskState::Failed, "{actual:?}");
        assert!(actual.observation.findings.is_empty());
        assert_eq!(
            actual.extra["sanitizer_diagnostics"][0]["primary_location"],
            Value::Null
        );
    }
}
#[test]
fn known_non_elf_policy_preserves_original_binary_finding_details() {
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("suite"), b"ordinary non-ELF bytes").unwrap();
    let mut context = request(ValidationKind::BinaryCompatibility);
    context.root = root.path().into();
    context.cwd = root.path().into();
    context.owned_run_dir = root.path().join("run");
    context.targets = vec!["suite".into()];
    let mut planned = plan(&context).unwrap();
    capture_binary_headers(&mut planned).unwrap();
    let actual = observe(&planned, &BTreeMap::new(), &CapturedArtifacts::default());
    assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
    matches_original(&actual, &actual.observation.findings[0], &oracle("binary"));
    assert_eq!(actual.observation.findings[0].confidence, "exact");
}
#[test]
fn slow_rows_strip_ansi_and_keep_the_frozen_duration_path_line_order() {
    let mut context = request(ValidationKind::PythonTest);
    context.options.slow_threshold_seconds = Some(1.0);
    let planned = plan(&context).unwrap();
    let base = format!("{}.suite", context.task_id);
    // Frozen `_pytest_duration_summary`: ANSI colour is stripped, rows below the threshold or
    // without a node id are dropped, the slowest duration of a repeated row wins, and ties sort
    // by file path, line, node id and phase.
    let text = "tests/z_test.py::test_z PASSED\ntests/a_test.py::test_a PASSED\n\
        \x1b[33m2.000s call     tests/z_test.py::test_z\x1b[0m\n\
        2.000s call     tests/a_test.py::test_a\n\
        2.000s call     tests/a.py:30::test_b\n\
        2.000s call     tests/a.py:4::test_c\n\
        1.200s setup    tests/a_test.py::test_a\n\
        1.900s setup    tests/a_test.py::test_a\n\
        0.500s call     tests/z_test.py::test_fast\n\
        3.000s call     summary-only\n";
    let actual = observe(
        &planned,
        &BTreeMap::from([(base.clone(), outcome(&base, text, "", 0))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
    let rows: Vec<&str> = actual
        .targets
        .iter()
        .map(|target| target.target_name.as_str())
        .filter(|name| name.starts_with("[test-quality] Slow test:"))
        .collect();
    assert_eq!(
        rows,
        [
            "[test-quality] Slow test: tests/a.py:4::test_c (call)",
            "[test-quality] Slow test: tests/a.py:30::test_b (call)",
            "[test-quality] Slow test: tests/a_test.py::test_a (call)",
            "[test-quality] Slow test: tests/z_test.py::test_z (call)",
            "[test-quality] Slow test: tests/a_test.py::test_a (setup)",
        ]
    );
}
