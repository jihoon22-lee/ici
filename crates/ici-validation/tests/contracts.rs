use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::{coverage, tem, *};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path};

fn request(root: &Path, kind: ValidationKind) -> ValidationRequest {
    ValidationRequest {
        kind,
        task_id: format!("project.{}", kind.provider()),
        component_id: "project".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
        tool: ResolvedTool {
            id: "python".into(),
            path: "/usr/bin/true".into(),
            source: ToolSource::Explicit,
            digest: sha256("fake fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["tests".into()],
        source_files: vec!["main.py".into()],
        functions: vec![],
        options: ValidationOptions::default(),
    }
}
fn outcome(id: &str, text: &str, exit: i32) -> TaskOutcome {
    let mut result = TaskOutcome::empty(id);
    result.state = TaskState::Succeeded;
    result.exit_code = Some(exit);
    result.stdout = text.as_bytes().to_vec();
    result
}
fn report() -> Value {
    json!({"meta":{"branch_coverage":true},"files":{"main.py":{"executed_lines":[1,2],"missing_lines":[3],"summary":{"covered_lines":2,"num_statements":3,"missing_lines":1,"covered_branches":1,"num_branches":2},"executed_branches":[[1,2]],"missing_branches":[[1,3]],"functions":{"f":{"start_line":1,"executed_lines":[1,2],"missing_lines":[3],"summary":{"covered_lines":2,"num_statements":3,"missing_lines":1,"covered_branches":1,"num_branches":2},"executed_branches":[[1,2]],"missing_branches":[[1,3]]}}}},"totals":{"covered_lines":2,"num_statements":3,"missing_lines":1,"covered_branches":1,"num_branches":2}})
}
#[test]
fn plan_is_read_only_and_shared_coverage_names_one_external_suite() {
    let root = tempfile::tempdir().unwrap();
    let mut test = request(root.path(), ValidationKind::PythonTest);
    test.options.collect_coverage = true;
    test.options.repeat_runs = 2;
    let suite = plan(&test).unwrap();
    assert_eq!(suite.tasks.len(), 2);
    assert!(suite.tasks[0].argv.contains(&"--branch".into()));
    assert!(!suite.tasks[1].argv.contains(&"coverage".into()));
    assert!(suite.tasks.iter().all(|task| !task.cacheable));
    assert_eq!(
        ready_steps(&suite, &BTreeMap::new()).unwrap(),
        vec![suite.tasks[0].id.clone()]
    );
    let mut coverage = request(root.path(), ValidationKind::PythonCoverage);
    coverage.options.shared_test_task_id = Some(suite.tasks[0].id.clone());
    let reader = plan(&coverage).unwrap();
    assert_eq!(reader.tasks.len(), 1);
    assert_eq!(reader.artifacts.len(), 1);
    assert!(ready_steps(&reader, &BTreeMap::new()).unwrap().is_empty());
    let mut outcomes = BTreeMap::from([(
        suite.tasks[0].id.clone(),
        outcome(&suite.tasks[0].id, "tests/test_main.py::test_x FAILED\n", 1),
    )]);
    assert_eq!(
        ready_steps(&reader, &outcomes).unwrap(),
        vec![reader.tasks[0].id.clone()]
    );
    outcomes.get_mut(&suite.tasks[0].id).unwrap().truncated = true;
    assert!(ready_steps(&reader, &outcomes).is_err());
    assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    let mut native_discovery = test;
    native_discovery.targets = vec![".".into()];
    assert!(
        plan(&native_discovery).unwrap().tasks[0]
            .argv
            .contains(&root.path().join(".").to_string_lossy().into_owned())
    );
    native_discovery.source_files = vec![".".into()];
    assert!(plan(&native_discovery).is_err());
}
#[test]
fn pytest_preserves_parameter_spaces_last_verdicts_and_source_facts() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonTest);
    request.functions.push(FunctionFact {
        path: "tests/test_main.py".into(),
        name: "test_x".into(),
        qualified_name: "TestSuite.test_x".into(),
        start_line: 14,
        entry_line: None,
        body_start_line: 15,
        end_line: 16,
    });
    let planned = plan(&request).unwrap();
    let id = planned.tasks[0].id.clone();
    let text = "tests/test_main.py::TestSuite::test_x[a b] FAILED [ 50%]\ntests/test_main.py::TestSuite::test_x[a b] PASSED [100%]\ntests/test_main.py:32::test_y FAILED\n";
    let output = observe(
        &planned,
        &BTreeMap::from([(id.clone(), outcome(&id, text, 1))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.targets.len(), 2);
    assert_eq!(output.targets[0].start_line, 32);
    assert_eq!(output.targets[1].start_line, 14);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(output.observation.measurements[0].denominator, Some(2));
}
#[test]
fn no_tests_collection_usage_all_skipped_and_malformed_are_incomplete() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::PythonTest)).unwrap();
    let id = planned.tasks[0].id.clone();
    for (text, exit) in [
        ("no tests ran", 5),
        ("garbage", 0),
        ("ERROR collecting tests/test_bad.py\n", 2),
        ("tests/test_main.py::test_x SKIPPED\n", 0),
        ("tests/test_main.py::test_x PASSED\n", 4),
        ("tests/test_main.py::test_x PASSED\n", 1),
    ] {
        let output = observe(
            &planned,
            &BTreeMap::from([(id.clone(), outcome(&id, text, exit))]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(
            output.observation.state,
            TaskState::Failed,
            "{text}: {output:?}"
        );
        if text.contains("collecting") {
            assert_eq!(
                output.observation.findings[0].rule_id,
                "pytest.collection-error"
            );
        }
    }
}
#[test]
fn strict_coverage_counts_scope_and_function_metadata_are_independently_validated() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        path: "main.py".into(),
        name: "f".into(),
        qualified_name: "f".into(),
        start_line: 1,
        entry_line: None,
        body_start_line: 2,
        end_line: 3,
    });
    let good = report();
    let parsed = coverage::parse_python(&serde_json::to_vec(&good).unwrap(), &request).unwrap();
    assert_eq!(parsed.counts.covered_functions, Some(1));
    assert_eq!(parsed.counts.total_lines, 3);
    for field in [
        "covered_lines",
        "num_statements",
        "missing_lines",
        "num_branches",
        "covered_branches",
    ] {
        let mut bad = good.clone();
        bad["totals"][field] = json!(999);
        assert!(coverage::parse_python(&serde_json::to_vec(&bad).unwrap(), &request).is_err());
        let mut bad = good.clone();
        bad["files"]["main.py"]["summary"][field] = json!(true);
        assert!(coverage::parse_python(&serde_json::to_vec(&bad).unwrap(), &request).is_err());
    }
    let mut bad = good.clone();
    bad["files"]["main.py"]["missing_lines"] = json!([2]);
    assert!(coverage::parse_python(&serde_json::to_vec(&bad).unwrap(), &request).is_err());
    request.source_files.push("unmeasured.py".into());
    assert!(coverage::parse_python(&serde_json::to_vec(&good).unwrap(), &request).is_err());
}
#[test]
fn def_header_alone_never_proves_a_function_was_called() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        path: "main.py".into(),
        name: "f".into(),
        qualified_name: "f".into(),
        start_line: 1,
        entry_line: None,
        body_start_line: 1,
        end_line: 1,
    });
    let mut value = report();
    value["files"]["main.py"]["functions"]["f"]["executed_lines"] = json!([1]);
    value["files"]["main.py"]["functions"]["f"]["missing_lines"] = json!([]);
    value["files"]["main.py"]["functions"]["f"]["summary"] = json!({"covered_lines":1,"num_statements":1,"missing_lines":0,"covered_branches":1,"num_branches":2});
    value["files"]["main.py"]["functions"]["f"]["missing_branches"] = json!([[1, -1]]);
    let parsed = coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request).unwrap();
    assert_eq!(parsed.counts.covered_functions, Some(0));
    value["files"]["main.py"]["functions"]["f"]
        .as_object_mut()
        .unwrap()
        .remove("missing_branches");
    assert_eq!(
        coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request)
            .unwrap()
            .counts
            .covered_functions,
        None
    );
    value["files"]["main.py"]["functions"]["f"]["executed_branches"] = json!([[1, -1]]);
    assert_eq!(
        coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request)
            .unwrap()
            .counts
            .covered_functions,
        Some(1)
    );
}
#[test]
fn tem_requires_functions_and_merges_raw_counts_without_averaging() {
    let input = tem::TemInputs {
        passed: 1,
        total: 1,
        line_coverage: Some(80.0),
        ..Default::default()
    };
    assert_eq!(tem::calculate(&input).score, None);
    let mut input = input;
    input.function_coverage = Some(50.0);
    assert_eq!(tem::calculate(&input).score, Some(2.5));
    input.passed = 0;
    input.total = 0;
    assert_eq!(tem::calculate(&input).score, Some(0.0));
    let components = vec![
        tem::ComponentCounts {
            name: "tiny".into(),
            passed: 1,
            total: 1,
            coverage: Some(coverage::Counts {
                covered_lines: 1,
                total_lines: 1,
                covered_branches: 0,
                total_branches: 0,
                covered_functions: Some(1),
                total_functions: Some(1),
            }),
        },
        tem::ComponentCounts {
            name: "large".into(),
            passed: 1,
            total: 9,
            coverage: Some(coverage::Counts {
                covered_lines: 0,
                total_lines: 99,
                covered_branches: 0,
                total_branches: 0,
                covered_functions: None,
                total_functions: None,
            }),
        },
    ];
    let (merged, excluded) = tem::aggregate(&components).unwrap();
    assert!(excluded.is_empty());
    assert_eq!(merged.line_coverage, Some(1.0));
    assert_eq!(merged.function_coverage, None);
    assert_eq!(merged.total, 10);
}
#[test]
fn coverage_policy_honors_thresholds_statement_floor_and_changed_line_denominators() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        path: "main.py".into(),
        name: "f".into(),
        qualified_name: "f".into(),
        start_line: 1,
        entry_line: None,
        body_start_line: 2,
        end_line: 3,
    });
    request.options.min_line_cov = Some(80.0);
    request.options.min_file_cov = Some(80.0);
    request.options.min_branch_cov = Some(40.0);
    request.options.min_func_cov = Some(90.0);
    request.options.min_tem_score = Some(3.0);
    request.options.changed_lines = vec!["main.py:3".into()];
    let report = coverage::parse_python(&serde_json::to_vec(&report()).unwrap(), &request).unwrap();
    let planned = plan(&request).unwrap();
    let mut output = ici_domain::AnalysisOutput::new("coverage", "coverage");
    ici_validation::policy::evaluate(&planned, &report, Some(4.0), &mut output).unwrap();
    assert_eq!(
        output
            .observation
            .findings
            .iter()
            .map(|finding| finding.rule_id.as_str())
            .collect::<Vec<_>>(),
        vec![
            "ici.test.coverage.overall-line",
            "ici.test.coverage.changed-lines"
        ]
    );
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.target_name == "Function coverage" && target.status == "PASS")
    );
    request.options.min_file_statements = 1;
    let planned = plan(&request).unwrap();
    let mut output = ici_domain::AnalysisOutput::new("coverage", "coverage");
    ici_validation::policy::evaluate(&planned, &report, Some(4.0), &mut output).unwrap();
    assert_eq!(output.observation.findings.len(), 3);
    request.options.changed_lines = vec!["main.py:1-2".into(), "main.py:2".into()];
    assert!(plan(&request).is_err());
    request.options.changed_lines = vec!["main.py:99".into()];
    let planned = plan(&request).unwrap();
    let mut output = ici_domain::AnalysisOutput::new("coverage", "coverage");
    assert!(ici_validation::policy::evaluate(&planned, &report, Some(4.0), &mut output).is_err());
}
#[test]
fn measured_empty_axes_are_not_applicable_while_unknown_function_evidence_is_incomplete() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.options.function_facts_complete = true;
    request.options.min_branch_cov = Some(80.0);
    request.options.min_func_cov = Some(90.0);
    request.options.min_tem_score = Some(4.0);
    let mut value = report();
    value["totals"]["covered_branches"] = json!(0);
    value["totals"]["num_branches"] = json!(0);
    value["files"]["main.py"]["summary"]["covered_branches"] = json!(0);
    value["files"]["main.py"]["summary"]["num_branches"] = json!(0);
    value["files"]["main.py"]["executed_branches"] = json!([]);
    value["files"]["main.py"]["missing_branches"] = json!([]);
    let known = coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request).unwrap();
    let planned = plan(&request).unwrap();
    let mut output = ici_domain::AnalysisOutput::new("coverage", "coverage");
    ici_validation::policy::evaluate(&planned, &known, None, &mut output).unwrap();
    assert_eq!(known.counts.total_functions, Some(0));
    assert!(output.observation.findings.is_empty());
    assert!(output.targets.iter().all(|target| target.status == "SKIP"));
    request.options.function_facts_complete = false;
    let unknown = coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request).unwrap();
    let planned = plan(&request).unwrap();
    assert!(ici_validation::policy::evaluate(&planned, &unknown, None, &mut output).is_err());
}
#[test]
fn owned_artifacts_reject_reuse_symlinks_fifo_stale_and_replaced_owners() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::PythonCoverage)).unwrap();
    let run = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    assert!(OwnedRun::create(run.path()).is_err());
    let spec = &planned.artifacts[0];
    let outcomes = BTreeMap::from([(
        spec.producer_task_id.clone(),
        outcome(&spec.producer_task_id, "", 0),
    )]);
    let source = root.path().join("user.json");
    std::fs::write(&source, b"user data").unwrap();
    std::os::unix::fs::symlink(&source, &spec.path).unwrap();
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert!(captured.files.is_empty());
    assert!(captured.errors.contains_key("coverage-json"));
    std::fs::remove_file(&spec.path).unwrap();
    std::fs::write(&spec.path, b"stale").unwrap();
    std::fs::File::options()
        .write(true)
        .open(&spec.path)
        .unwrap()
        .set_times(std::fs::FileTimes::new().set_modified(std::time::UNIX_EPOCH))
        .unwrap();
    assert!(
        capture_artifacts(&planned, &run, &outcomes)
            .unwrap()
            .files
            .is_empty()
    );
    std::fs::remove_file(&spec.path).unwrap();
    std::fs::hard_link(&source, &spec.path).unwrap();
    assert!(
        capture_artifacts(&planned, &run, &outcomes)
            .unwrap()
            .files
            .is_empty()
    );
    std::fs::remove_file(&spec.path).unwrap();
    nix::unistd::mkfifo(
        &spec.path,
        nix::sys::stat::Mode::S_IRUSR | nix::sys::stat::Mode::S_IWUSR,
    )
    .unwrap();
    assert!(
        capture_artifacts(&planned, &run, &outcomes)
            .unwrap()
            .files
            .is_empty()
    );
    std::fs::remove_file(&spec.path).unwrap();
    std::fs::write(&spec.path, b"{}").unwrap();
    let capture = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert_eq!(capture.files["coverage-json"].bytes, b"{}");
    let old = root.path().join("old-run");
    std::fs::rename(run.path(), &old).unwrap();
    std::fs::create_dir(run.path()).unwrap();
    assert!(capture_artifacts(&planned, &run, &outcomes).is_err());
    assert_eq!(std::fs::read(&source).unwrap(), b"user data");
}
