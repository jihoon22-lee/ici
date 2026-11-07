use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use std::collections::BTreeMap;
fn request() -> ValidationRequest {
    ValidationRequest {
        kind: ValidationKind::PythonTest,
        task_id: "component.python.test".into(),
        component_id: "component".into(),
        root: "/workspace".into(),
        cwd: "/workspace".into(),
        owned_run_dir: "/workspace/run".into(),
        tool: ResolvedTool {
            id: "python".into(),
            path: "/tools/python".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["tests".into()],
        source_files: vec!["app.py".into()],
        functions: vec![],
        options: ValidationOptions {
            unittest_fallback: true,
            ..Default::default()
        },
    }
}
fn outcome(id: &str, stdout: &str, stderr: &str, exit: i32) -> TaskOutcome {
    let mut result = TaskOutcome::empty(id);
    result.state = TaskState::Succeeded;
    result.exit_code = Some(exit);
    result.stdout = stdout.as_bytes().to_vec();
    result.stderr = stderr.as_bytes().to_vec();
    result
}
fn pass() -> &'static str {
    "test_ok (test_sample.Sample) ... ok\n\n----------------------------------------------------------------------\nRan 1 test in 0.001s\n\nOK\n"
}
#[test]
fn exact_missing_pytest_selects_visible_stdlib_runner_and_retains_nonempty_case_evidence() {
    let context = request();
    let planned = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let unit = format!("{}.unittest0", context.task_id);
    assert_eq!(
        ready_steps(&planned, &BTreeMap::new()).unwrap(),
        vec![suite.clone()]
    );
    assert_eq!(
        planned.conditional_steps[&unit].missing_module.as_deref(),
        Some("pytest")
    );
    let mut outcomes = BTreeMap::from([(
        suite.clone(),
        outcome(&suite, "", "/tools/python: No module named pytest\n", 1),
    )]);
    assert_eq!(
        ready_steps(&planned, &outcomes).unwrap(),
        vec![unit.clone()]
    );
    outcomes.insert(unit.clone(), outcome(&unit, "", pass(), 0));
    assert!(ready_steps(&planned, &outcomes).unwrap().is_empty());
    let actual = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(actual.observation.state, TaskState::Succeeded, "{actual:?}");
    assert_eq!(actual.observation.measurements[0].name, "unittest.cases");
    assert_eq!(actual.observation.measurements[0].numerator, Some(1));
    assert_eq!(actual.observation.measurements[0].denominator, Some(1));
    assert_eq!(actual.extra["test_framework"]["selected"], "unittest");
}
#[test]
fn broken_truncated_timed_out_or_ordinary_pytest_failure_never_selects_unittest() {
    let context = request();
    let planned = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let unit = format!("{}.unittest0", context.task_id);
    let mut timed = outcome(&suite, "", "/tools/python: No module named pytest\n", 1);
    timed.timed_out = true;
    let mut truncated = timed.clone();
    truncated.timed_out = false;
    truncated.truncated = true;
    for broken in [
        timed,
        truncated,
        outcome(
            &suite,
            "",
            "Traceback (most recent call last):\nModuleNotFoundError: No module named 'pytest'\n",
            1,
        ),
        outcome(&suite, "tests/test_sample.py::test_x FAILED\n", "", 1),
        outcome(&suite, "No module named 'pytest'\n", "", 1),
    ] {
        let outcomes = BTreeMap::from([(suite.clone(), broken)]);
        assert!(!python_unittest::missing_pytest(&planned, &outcomes).unwrap());
        assert!(
            ready_steps(&planned, &outcomes)
                .unwrap()
                .iter()
                .all(|id| id != &unit)
        );
    }
}
#[test]
fn coverage_runner_missing_pytest_skips_report_and_never_invents_coverage_or_tem() {
    let mut context = request();
    context.options.coverage_module_probe = true;
    context.options.collect_coverage = true;
    let test = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let probe = python_coverage::probe_id(&test).unwrap();
    let unit = format!("{}.unittest0", context.task_id);
    let mut outcomes = BTreeMap::from([
        (
            probe.clone(),
            outcome(&probe, "Coverage.py, version 7.15.4\n", "", 0),
        ),
        (
            suite.clone(),
            outcome(&suite, "No module named 'pytest'\n", "", 1),
        ),
    ]);
    assert_eq!(ready_steps(&test, &outcomes).unwrap(), vec![unit.clone()]);
    outcomes.insert(unit.clone(), outcome(&unit, "", pass(), 0));
    assert_eq!(
        observe(&test, &outcomes, &CapturedArtifacts::default())
            .observation
            .state,
        TaskState::Succeeded
    );
    let mut coverage = context;
    coverage.kind = ValidationKind::PythonCoverage;
    coverage.task_id = "component.python.coverage".into();
    coverage.options.collect_coverage = false;
    coverage.options.shared_test_task_id = Some(suite);
    let reader = plan(&coverage).unwrap();
    assert!(ready_steps(&reader, &outcomes).unwrap().is_empty());
    let actual = observe(&reader, &outcomes, &CapturedArtifacts::default());
    assert_eq!(actual.observation.state, TaskState::Failed);
    assert!(actual.observation.measurements.is_empty());
    assert!(actual.observation.findings.is_empty());
    assert!(actual.score.is_none());
    assert_eq!(
        actual.extra["coverage_policy"]["raw_metrics"]["line"]["state"],
        "unavailable"
    );
    assert_eq!(
        actual.extra["unittest_fallback"]["test_framework"]["selected"],
        "unittest"
    );
}
#[test]
fn unittest_failures_skips_empty_and_malformed_totals_keep_distinct_evidence() {
    let context = request();
    let planned = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let unit = format!("{}.unittest0", context.task_id);
    for (text, exit, state, findings) in [
        (
            "test_bad (test_sample.Sample) ... FAIL\nRan 1 test in 0.001s\nFAILED (failures=1)\n",
            1,
            TaskState::Succeeded,
            1,
        ),
        (
            "test_skip (test_sample.Sample) ... skipped 'optional'\nRan 1 test in 0.001s\nOK (skipped=1)\n",
            0,
            TaskState::Failed,
            0,
        ),
        ("Ran 0 tests in 0.001s\nOK\n", 0, TaskState::Failed, 0),
        (
            "test_ok (test_sample.Sample) ... ok\nRan 2 tests in 0.001s\nOK\n",
            0,
            TaskState::Failed,
            0,
        ),
    ] {
        let outcomes = BTreeMap::from([
            (
                suite.clone(),
                outcome(&suite, "", "/tools/python: No module named pytest\n", 1),
            ),
            (unit.clone(), outcome(&unit, "", text, exit)),
        ]);
        let actual = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(actual.observation.state, state, "{actual:?}");
        assert_eq!(actual.observation.findings.len(), findings);
    }
}

#[test]
fn actual_python310_and314_docstrings_subtests_and_expected_outcomes_keep_parent_denominators() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/unittest-native.json"
    ))
    .unwrap();
    assert_eq!(
        sha256(fixture["source"].as_str().unwrap()).trim_start_matches("sha256:"),
        fixture["source_sha256"].as_str().unwrap()
    );
    for case in fixture["cases"].as_array().unwrap() {
        let context = request();
        let planned = plan(&context).unwrap();
        let suite = format!("{}.suite", context.task_id);
        let unit = format!("{}.unittest0", context.task_id);
        let outcomes = BTreeMap::from([
            (
                suite.clone(),
                outcome(&suite, "", "/tools/python: No module named pytest\n", 1),
            ),
            (
                unit.clone(),
                outcome(
                    &unit,
                    case["stdout"].as_str().unwrap(),
                    case["stderr"].as_str().unwrap(),
                    1,
                ),
            ),
        ]);
        let actual = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(
            actual.observation.state,
            TaskState::Succeeded,
            "{}: {actual:?}",
            case["version"]
        );
        assert_eq!(
            actual.observation.measurements[0].numerator,
            case["expected_passed"].as_u64()
        );
        assert_eq!(
            actual.observation.measurements[0].denominator,
            case["expected_cases"].as_u64()
        );
        assert_eq!(
            actual.observation.findings.len(),
            case["expected_failed_nodes"].as_u64().unwrap() as usize
        );
        assert_eq!(
            actual.extra["unittest_subtests"][&unit]
                .as_object()
                .unwrap()
                .len(),
            case["expected_subtests"].as_u64().unwrap() as usize
        );
        assert!(
            actual.extra["test_outcomes"]
                .as_object()
                .unwrap()
                .keys()
                .all(|node| !node.contains("documented test"))
        );
    }
}

#[test]
fn simultaneous_pytest_and_unittest_evidence_is_rejected() {
    let context = request();
    let planned = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let unit = format!("{}.unittest0", context.task_id);
    let outcomes = BTreeMap::from([
        (
            suite.clone(),
            outcome(&suite, "tests/test_sample.py::test_ok PASSED\n", "", 0),
        ),
        (unit.clone(), outcome(&unit, "", pass(), 0)),
    ]);
    assert!(ready_steps(&planned, &outcomes).is_err());
    assert_eq!(
        observe(&planned, &outcomes, &CapturedArtifacts::default())
            .observation
            .state,
        TaskState::Failed
    );
}
