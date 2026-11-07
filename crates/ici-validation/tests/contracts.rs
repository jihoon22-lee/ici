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
/// The legacy reading runs frozen `_check_python_resource_warnings`'s command, with relative
/// targets; every other reading keeps native's own flags and absolute targets.
#[test]
fn python_sanitize_argv_depends_on_the_reading() {
    let root = tempfile::tempdir().unwrap();
    let arguments = |legacy: bool| -> Vec<String> {
        let mut request = request(root.path(), ValidationKind::PythonSanitize);
        request.targets = vec!["tests".into()];
        request.options.legacy_pytest_result = legacy;
        let plan = ici_validation::plan(&request).unwrap();
        plan.tasks.last().unwrap().argv[1..].to_vec()
    };
    assert_eq!(
        arguments(true),
        [
            "-W",
            "error::ResourceWarning",
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "tests"
        ]
    );
    let native = arguments(false);
    let target = root.path().join("tests");
    assert_eq!(
        native,
        [
            "-W",
            "error::ResourceWarning",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--tb=long",
            "--color=no",
            "-o",
            "addopts=",
            target.to_str().unwrap()
        ]
    );
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
        ast_depth: 0,
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
    // Legacy targets are frozen `_parse_pytest_stdout`'s: every verdict line in execution
    // order, named by its first token (so `[a b]` reads `[a`), repeats kept.
    let targets: Vec<_> = output
        .targets
        .iter()
        .map(|target| {
            (
                target.target_name.as_str(),
                target.status.as_str(),
                target.start_line,
            )
        })
        .collect();
    assert_eq!(
        targets,
        [
            ("tests/test_main.py::TestSuite::test_x[a", "FAIL", 14),
            ("tests/test_main.py::TestSuite::test_x[a", "PASS", 14),
            ("tests/test_main.py:32::test_y", "FAIL", 32),
        ]
    );
    // Findings and the next metric stay per unique node, last verdict winning.
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(output.observation.measurements[0].denominator, Some(2));
    assert_eq!(output.observation.measurements[0].numerator, Some(1));
    // An escaping or absolute node path keeps its verdict at the frozen `tests` scope.
    let text = "../outside/test_a.py::test_a FAILED\n/elsewhere/test_b.py:9::test_b FAILED\n";
    let output = observe(
        &planned,
        &BTreeMap::from([(id.clone(), outcome(&id, text, 1))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.observation.findings.len(), 2);
    for finding in &output.observation.findings {
        assert_eq!(finding.primary_location.path, "tests");
        assert_eq!(finding.primary_location.start_line, 1);
    }
}
#[test]
fn no_tests_collection_usage_and_malformed_are_incomplete() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::PythonTest)).unwrap();
    let id = planned.tasks[0].id.clone();
    for (text, exit) in [
        ("no tests ran", 5),
        ("garbage", 0),
        ("ERROR collecting tests/test_bad.py\n", 2),
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
/// Root decision D2 (frozen `adapters/providers/pytest.py`): a collected suite in which no case
/// passed is an answer with the frozen limitation, never a refusal and never silent.
#[test]
fn all_skipped_suite_succeeds_with_the_frozen_no_green_limitation() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::PythonTest)).unwrap();
    let id = planned.tasks[0].id.clone();
    for (text, limitation) in [
        (
            "tests/test_main.py::test_x SKIPPED\n",
            Some("no test ran green (1 skipped of 1 collected)"),
        ),
        (
            "tests/test_main.py::test_x SKIPPED\ntests/test_main.py::test_y XFAIL\n",
            Some("no test ran green (1 skipped of 2 collected)"),
        ),
        ("tests/test_main.py::test_x PASSED\n", None),
    ] {
        let output = observe(
            &planned,
            &BTreeMap::from([(id.clone(), outcome(&id, text, 0))]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{text}: {output:?}"
        );
        let named: Vec<_> = output
            .observation
            .limitations
            .iter()
            .filter(|text| text.starts_with("no test ran green"))
            .cloned()
            .collect();
        assert_eq!(
            named,
            limitation
                .map(str::to_owned)
                .into_iter()
                .collect::<Vec<_>>(),
            "{text}"
        );
    }
}
#[test]
fn strict_coverage_counts_scope_and_function_metadata_are_independently_validated() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        ast_depth: 0,
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
/// decisions.md "coverage.py JSON 입력의 추가 검증": fail-closed checks frozen
/// `parse_coverage_json` does not have. coverage.py never writes these reports, so no real-CLI
/// fixture reaches them; each is a fabricated report refused.
#[test]
fn native_only_coverage_report_checks_fail_closed() {
    let root = tempfile::tempdir().unwrap();
    let request = request(root.path(), ValidationKind::PythonCoverage);
    let refused = |report: &serde_json::Value| {
        coverage::parse_python(&serde_json::to_vec(report).unwrap(), &request)
            .unwrap_err()
            .0
    };
    let good = report();
    assert!(coverage::parse_python(&serde_json::to_vec(&good).unwrap(), &request).is_ok());
    // A report that does not claim measured branches (`coverage json` without `--branch`).
    let mut bad = good.clone();
    bad["meta"]["branch_coverage"] = json!(false);
    assert_eq!(
        refused(&bad),
        "coverage report does not contain measured branch coverage"
    );
    bad["meta"]
        .as_object_mut()
        .unwrap()
        .remove("branch_coverage");
    assert_eq!(
        refused(&bad),
        "coverage report does not contain measured branch coverage"
    );
    // The 16,384-file cap.
    let mut bad = good.clone();
    for index in 0..16384 {
        bad["files"][format!("extra{index}.py")] = json!({});
    }
    assert_eq!(refused(&bad), "coverage file limit exceeded");
    // Branch arcs: an array of the summary's count, of two-integer pairs, each once.
    let mut bad = good.clone();
    bad["files"]["main.py"]["summary"]["num_branches"] = json!(2);
    bad["files"]["main.py"]["summary"]["covered_branches"] = json!(1);
    bad["files"]["main.py"]["executed_branches"] = json!("arcs");
    assert_eq!(refused(&bad), "coverage branches must be arrays");
    bad["files"]["main.py"]["executed_branches"] = json!([]);
    assert_eq!(refused(&bad), "coverage branch count mismatch");
    bad["files"]["main.py"]["executed_branches"] = json!([[1]]);
    assert_eq!(refused(&bad), "coverage arc must have two lines");
    bad["files"]["main.py"]["executed_branches"] = json!([[1, "2"]]);
    assert_eq!(refused(&bad), "coverage arc line must be integer");
    bad["files"]["main.py"]["executed_branches"] = json!([[1, 2]]);
    bad["files"]["main.py"]["missing_branches"] = json!([[1, 3], [1, 3]]);
    bad["files"]["main.py"]["summary"]["num_branches"] = json!(3);
    assert_eq!(refused(&bad), "duplicate coverage branch");
    // A line number past u32 (and a zero line) names no source line.
    let mut bad = good.clone();
    bad["files"]["main.py"]["executed_lines"][0] = json!(u64::from(u32::MAX) + 1);
    assert_eq!(refused(&bad), "coverage line must be a positive integer");
    bad["files"]["main.py"]["executed_lines"][0] = json!(0);
    assert_eq!(refused(&bad), "coverage line must be a positive integer");
}
/// Frozen `parse_coverage_json` (coverage_support.py:222-225), reachable through the CLI
/// (thresholds/coverage-no-statements, coverage-extra-file): no statement in the totals on both
/// paths; files other than the discovered sources only where frozen passes `expected_files`.
#[test]
fn coverage_report_statement_and_file_set_refusals_match_frozen() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    let mut extra = report();
    extra["files"]["other.py"] = json!({
        "summary": {"covered_lines": 0, "num_statements": 0, "missing_lines": 0,
            "num_branches": 0, "covered_branches": 0},
        "executed_lines": [], "missing_lines": [],
    });
    let bytes = serde_json::to_vec(&extra).unwrap();
    assert_eq!(
        coverage::parse_python(&bytes, &request)
            .unwrap()
            .excluded_files,
        ["other.py"]
    );
    request.options.legacy_pytest_result = true;
    assert_eq!(
        coverage::parse_python(&bytes, &request).unwrap_err().0,
        "coverage report names files outside the discovered sources"
    );
    let mut empty = report();
    for field in ["covered_lines", "num_statements", "missing_lines"] {
        empty["totals"][field] = json!(0);
        empty["files"]["main.py"]["summary"][field] = json!(0);
    }
    empty["files"]["main.py"]["executed_lines"] = json!([]);
    empty["files"]["main.py"]["missing_lines"] = json!([]);
    request.options.legacy_pytest_result = false;
    assert_eq!(
        coverage::parse_python(&serde_json::to_vec(&empty).unwrap(), &request)
            .unwrap_err()
            .0,
        "coverage report counts no executable statement"
    );
}
/// Frozen `compute_python_function_coverage` (coverage_support.py:437, 444):
/// `body_start = node.body[0].lineno if node.body else node.lineno` and
/// `"covered": bool(body_lines & executed)`. A one-line function's body starts on its executed
/// `def` line, so it reads covered whatever coverage.py's per-function arcs say.
#[test]
fn one_line_function_reads_covered_when_its_def_line_ran_like_frozen() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        ast_depth: 0,
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
    assert_eq!(parsed.counts.covered_functions, Some(1));
    value["files"]["main.py"]["functions"]["f"]
        .as_object_mut()
        .unwrap()
        .remove("missing_branches");
    assert_eq!(
        coverage::parse_python(&serde_json::to_vec(&value).unwrap(), &request)
            .unwrap()
            .counts
            .covered_functions,
        Some(1)
    );
}
#[test]
fn multiline_body_hits_remain_measured_when_older_coverage_has_no_function_sections() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.functions.push(FunctionFact {
        ast_depth: 0,
        path: "main.py".into(),
        name: "f".into(),
        qualified_name: "f".into(),
        start_line: 1,
        entry_line: None,
        body_start_line: 2,
        end_line: 3,
    });
    let mut report = report();
    report["files"]["main.py"]
        .as_object_mut()
        .unwrap()
        .remove("functions");
    let measured = coverage::parse_python(&serde_json::to_vec(&report).unwrap(), &request).unwrap();
    assert_eq!(measured.counts.covered_functions, Some(1));
    // Frozen reads line data only: an executed one-line `def` is a covered function.
    request.functions[0].body_start_line = 1;
    request.functions[0].end_line = 1;
    let one_line = coverage::parse_python(&serde_json::to_vec(&report).unwrap(), &request).unwrap();
    assert_eq!(one_line.counts.covered_functions, Some(1));
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
        ast_depth: 0,
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
            .any(|target| target.target_name == "Coverage:Functions" && target.status == "PASS")
    );
    request.options.min_file_statements = 1;
    let planned = plan(&request).unwrap();
    let mut output = ici_domain::AnalysisOutput::new("coverage", "coverage");
    ici_validation::policy::evaluate(&planned, &report, Some(4.0), &mut output).unwrap();
    assert_eq!(output.observation.findings.len(), 3);
    request.options.changed_lines = vec!["main.py:1-2".into(), "main.py:2".into()];
    assert!(plan(&request).is_err());
    // A project file outside the captured source scope, a reversed range and a non-canonical
    // line are refused at plan time (frozen refused them as a policy configuration error, or
    // reported an out-of-scope file as having no executable measured lines).
    for spec in ["README.md:1", "main.py:3-2", "main.py:03", "./main.py:1"] {
        request.options.changed_lines = vec![spec.into()];
        assert!(plan(&request).is_err(), "{spec}");
    }
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
    run.mark_started(&planned, &spec.producer_task_id);
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
#[test]
fn artifact_patterns_never_adopt_an_earlier_producers_reports_from_the_same_run() {
    let root = tempfile::tempdir().unwrap();
    let mut planned = plan(&request(root.path(), ValidationKind::PythonCoverage)).unwrap();
    planned.artifacts.clear();
    planned.artifact_patterns = vec![ArtifactPattern {
        id_prefix: "reports".into(),
        suffix: ".json".into(),
        producer_task_id: "phase-a".into(),
        max_files: 8,
        max_bytes_each: 1024,
        max_bytes_total: 8192,
    }];
    let run = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    run.mark_started(&planned, "phase-a");
    std::fs::write(run.path().join("old.json"), b"old").unwrap();
    let mut outcomes = BTreeMap::from([("phase-a".into(), outcome("phase-a", "", 0))]);
    assert_eq!(
        capture_artifacts(&planned, &run, &outcomes)
            .unwrap()
            .files
            .len(),
        1
    );
    planned.artifact_patterns[0].producer_task_id = "phase-b".into();
    run.mark_started(&planned, "phase-b");
    std::fs::write(run.path().join("new.json"), b"new").unwrap();
    outcomes.insert("phase-b".into(), outcome("phase-b", "", 0));
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert_eq!(captured.files.len(), 1);
    assert_eq!(captured.files["reports:new.json"].bytes, b"new");
    assert!(!captured.files.contains_key("reports:old.json"));
}
#[test]
fn optional_mutation_probe_limits_preserve_raw_facts_without_changing_the_base_test_gate() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonTest);
    request.options.mutation_enabled = true;
    request.options.mutation_tool = Some(request.tool.clone());
    let planned = plan(&request).unwrap();
    let suite = &planned.tasks[0];
    let probe = planned
        .tasks
        .iter()
        .find(|task| task.id.ends_with(".mutation"))
        .unwrap();
    assert_eq!(
        ready_steps(&planned, &BTreeMap::new()).unwrap(),
        vec![suite.id.clone()]
    );
    assert!(planned.optional_task_ids.contains(&probe.id));
    let mut limited = outcome(&probe.id, "partial version", 0);
    limited.timed_out = true;
    limited.truncated = true;
    let outcomes = BTreeMap::from([
        (
            suite.id.clone(),
            outcome(&suite.id, "tests/test_main.py::test_good PASSED\n", 0),
        ),
        (probe.id.clone(), limited),
    ]);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert!(output.observation.evidence_is_complete(), "{output:?}");
    assert_eq!(output.extra["mutation"]["mutation_status"], "unavailable");
    assert_eq!(output.extra["optional_steps"][0]["timed_out"], true);
    assert_eq!(output.extra["optional_steps"][0]["truncated"], true);
    assert!(output.observation.findings.is_empty());
}
