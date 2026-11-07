use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::{artifacts::CapturedArtifact, *};
use serde_json::json;
use std::collections::BTreeMap;
fn request(kind: ValidationKind) -> ValidationRequest {
    ValidationRequest {
        kind,
        task_id: format!(
            "project.{}",
            if kind == ValidationKind::PythonCoverage {
                "python.coverage"
            } else {
                "python.test"
            }
        ),
        component_id: "project".into(),
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
        options: ValidationOptions::default(),
    }
}
fn outcome(id: &str, text: &str, exit: i32) -> TaskOutcome {
    let mut value = TaskOutcome::empty(id);
    value.state = TaskState::Succeeded;
    value.exit_code = Some(exit);
    value.stdout = text.as_bytes().to_vec();
    value
}
#[test]
fn expected_failure_is_a_passed_tool_case_without_claiming_a_normal_baseline_completion() {
    let context = request(ValidationKind::PythonTest);
    let plan = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let output = observe(
        &plan,
        &BTreeMap::from([(
            suite.clone(),
            outcome(&suite, "tests/test_app.py::test_known XFAIL\n", 0),
        )]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.targets[0].status, "PASS");
    assert_eq!(output.extra["passed_tests"], 1);
    assert_eq!(output.extra["total_tests"], 1);
    assert_eq!(output.extra["pass_rate"], 1.0);
    assert_eq!(output.extra["test_execution"]["complete"], true);
    assert_eq!(
        output.extra["test_outcomes"]["tests/test_app.py::test_known"],
        "XFAIL"
    );
    assert_eq!(output.extra["test_suites"][0]["passed"], 1);
    assert!(output.extra["line_coverage"].is_null());
    assert!(output.extra["function_coverage"].is_null());
    assert!(output.extra["tem_score"].is_null());
}
#[test]
fn shared_case_counts_keep_one_physical_suite_identity_when_coverage_report_is_unavailable() {
    let context = request(ValidationKind::PythonTest);
    let test = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let outcomes = BTreeMap::from([(
        suite.clone(),
        outcome(&suite, "tests/test_app.py::test_ok PASSED\n", 0),
    )]);
    let tested = observe(&test, &outcomes, &CapturedArtifacts::default());
    let mut coverage = request(ValidationKind::PythonCoverage);
    coverage.options.shared_test_task_id = Some(suite.clone());
    let coverage = plan(&coverage).unwrap();
    let measured = observe(&coverage, &outcomes, &CapturedArtifacts::default());
    assert_eq!(measured.observation.state, TaskState::Failed);
    assert_eq!(
        tested.extra["test_execution"]["canonical_suite_id"],
        measured.extra["test_execution"]["canonical_suite_id"]
    );
    assert_eq!(
        measured.extra["test_execution"]["canonical_suite_id"],
        suite
    );
    assert_eq!(tested.extra["test_execution"]["phase"], "test");
    assert_eq!(measured.extra["test_execution"]["phase"], "coverage");
    assert_eq!(measured.extra["passed_tests"], 1);
    assert_eq!(measured.extra["test_execution"]["complete"], true);
    assert!(measured.extra["line_coverage"].is_null());
    assert!(measured.extra["coverage_totals"].is_null());
    assert!(measured.extra["tem_score"].is_null());
}
#[test]
fn measured_coverage_flat_fields_preserve_raw_denominators_and_unknown_function_axes() {
    let mut context = request(ValidationKind::PythonCoverage);
    context.options.shared_test_task_id = Some("project.python.test.suite".into());
    let planned = plan(&context).unwrap();
    let report = format!("{}.report", context.task_id);
    let suite = context.options.shared_test_task_id.clone().unwrap();
    let bytes=serde_json::to_vec(&json!({"meta":{"branch_coverage":true},"files":{"app.py":{"executed_lines":[1],"missing_lines":[2,3],"summary":{"covered_lines":1,"num_statements":3,"missing_lines":2,"covered_branches":0,"num_branches":0},"executed_branches":[],"missing_branches":[]}},"totals":{"covered_lines":1,"num_statements":3,"missing_lines":2,"covered_branches":0,"num_branches":0}})).unwrap();
    let artifacts = CapturedArtifacts {
        files: BTreeMap::from([(
            "coverage-json".into(),
            CapturedArtifact {
                id: "coverage-json".into(),
                producer_task_id: report.clone(),
                digest: sha256(&bytes),
                bytes,
            },
        )]),
        ..Default::default()
    };
    let output = observe(
        &planned,
        &BTreeMap::from([
            (
                suite.clone(),
                outcome(&suite, "tests/test_app.py::test_ok PASSED\n", 0),
            ),
            (report.clone(), outcome(&report, "", 0)),
        ]),
        &artifacts,
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.extra["coverage_totals"]["stmts"], 3);
    assert_eq!(output.extra["coverage_totals"]["covered"], 1);
    assert_eq!(output.extra["coverage_totals"]["miss"], 2);
    assert_eq!(output.extra["coverage_totals"]["nb"], 0);
    assert_eq!(output.extra["line_coverage"], 33.3);
    assert!(output.extra["branch_coverage"].is_null());
    assert!(output.extra["function_coverage"].is_null());
    assert!(output.extra["tem_score"].is_null());
    assert_eq!(
        output.extra["coverage_files"][0]["missing_lines"],
        json!([2, 3])
    );
    assert_eq!(output.extra["coverage_source"], "coverage.py");
}
#[test]
fn incomplete_case_output_keeps_observed_counts_separate_from_complete_scope() {
    let context = request(ValidationKind::PythonTest);
    let plan = plan(&context).unwrap();
    let suite = format!("{}.suite", context.task_id);
    let mut timed = outcome(&suite, "tests/test_app.py::test_ok PASSED\n", 0);
    timed.timed_out = true;
    let output = observe(
        &plan,
        &BTreeMap::from([(suite, timed)]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Failed);
    assert_eq!(output.extra["test_execution"]["complete"], false);
    assert_eq!(output.extra["total_tests"], 1);
    assert_eq!(output.extra["quality_enabled"], false);
}
