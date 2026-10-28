//! Explicit real-project-runtime gate. Missing pinned tools fail when invoked --ignored.
use ici_domain::TaskState;
use ici_execution::{Cancellation, Runner, TaskOutcome};
use ici_providers::resolver::ToolRequest;
use ici_validation::*;
use std::{collections::BTreeMap, fs, path::Path};

fn request(root: &Path, kind: ValidationKind, run_name: &str) -> ValidationRequest {
    let executable = std::env::var("ICI_TEST_PYTHON")
        .expect("ICI_TEST_PYTHON must name the explicit project interpreter with pytest/coverage");
    let tool = ToolRequest {
        id: "python".into(),
        explicit: Some(executable.into()),
        project_root: root.into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap();
    ValidationRequest {
        kind,
        task_id: format!("project.{}", kind.provider()),
        component_id: "project".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join(run_name),
        tool,
        targets: vec!["tests".into()],
        source_files: vec!["main.py".into()],
        functions: vec![],
        options: ValidationOptions::default(),
    }
}
fn execute(plan: &ValidationPlan, outcomes: &mut BTreeMap<String, TaskOutcome>) {
    execute_owned(plan, outcomes, None)
}
fn execute_owned(
    plan: &ValidationPlan,
    outcomes: &mut BTreeMap<String, TaskOutcome>,
    owner: Option<&OwnedRun>,
) {
    loop {
        let ready = ready_steps(plan, outcomes).unwrap();
        if ready.is_empty() {
            break;
        }
        for id in ready {
            let spec = plan.tasks.iter().find(|task| task.id == id).unwrap();
            if let Some(owner) = owner {
                owner.mark_started(&id);
            }
            let outcome = Runner::default().run(spec, &Cancellation::default());
            outcomes.insert(id, outcome);
        }
    }
}
#[test]
#[ignore = "explicit native Python pytest/coverage gate"]
fn one_real_suite_feeds_test_and_coverage_with_missing_one_line_function_not_covered() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("main.py"),
        "def never(): pass\ndef called(value):\n    if value:\n        return 1\n    return 0\nclass C:\n    @staticmethod\n    def never_one(): pass\n    @staticmethod\n    def called_one(): return 2\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/test_main.py"),
        "import main\ndef test_call():\n    assert main.called(True) == 1\n    assert main.C.called_one() == 2\n",
    )
    .unwrap();
    let mut test = request(root.path(), ValidationKind::PythonTest, "run-one");
    test.options.collect_coverage = true;
    let test_plan = plan(&test).unwrap();
    let owner = OwnedRun::create(&test.owned_run_dir).unwrap();
    let mut outcomes = BTreeMap::new();
    execute_owned(&test_plan, &mut outcomes, Some(&owner));
    let test_output = observe(&test_plan, &outcomes, &CapturedArtifacts::default());
    assert_eq!(
        test_output.observation.state,
        TaskState::Succeeded,
        "{test_output:?}"
    );
    assert!(test_output.observation.findings.is_empty());
    let mut coverage = request(root.path(), ValidationKind::PythonCoverage, "run-one");
    coverage.options.shared_test_task_id = Some(test_plan.tasks[0].id.clone());
    coverage.options.min_func_cov = Some(100.0);
    coverage.functions = vec![
        FunctionFact {
            path: "main.py".into(),
            name: "never".into(),
            qualified_name: "never".into(),
            start_line: 1,
            entry_line: None,
            body_start_line: 1,
            end_line: 1,
        },
        FunctionFact {
            path: "main.py".into(),
            name: "called".into(),
            qualified_name: "called".into(),
            start_line: 2,
            entry_line: None,
            body_start_line: 3,
            end_line: 5,
        },
        FunctionFact {
            path: "main.py".into(),
            name: "never_one".into(),
            qualified_name: "C.never_one".into(),
            start_line: 8,
            entry_line: Some(7),
            body_start_line: 8,
            end_line: 8,
        },
        FunctionFact {
            path: "main.py".into(),
            name: "called_one".into(),
            qualified_name: "C.called_one".into(),
            start_line: 10,
            entry_line: Some(9),
            body_start_line: 10,
            end_line: 10,
        },
    ];
    let coverage_plan = plan(&coverage).unwrap();
    execute_owned(&coverage_plan, &mut outcomes, Some(&owner));
    let artifacts = capture_artifacts(&coverage_plan, &owner, &outcomes).unwrap();
    let output = observe(&coverage_plan, &outcomes, &artifacts);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let functions = output
        .observation
        .measurements
        .iter()
        .find(|metric| metric.name == "coverage.functions")
        .unwrap();
    assert_eq!(
        (functions.numerator, functions.denominator),
        (Some(2), Some(4)),
        "{output:?}"
    );
    assert_eq!(
        output.extra["test_execution"]["canonical_suite_id"],
        test_output.extra["test_execution"]["canonical_suite_id"]
    );
    assert_eq!(test_output.extra["test_execution"]["phase"], "test");
    assert_eq!(output.extra["test_execution"]["phase"], "coverage");
    assert_eq!(output.extra["total_tests"], 1);
    assert_eq!(output.extra["function_coverage"], 50.0);
    assert_eq!(output.extra["coverage_source"], "coverage.py");
    assert_eq!(output.extra["coverage_totals"]["functions"], 4);
    assert_eq!(output.extra["coverage_totals"]["covered_functions"], 2);
    assert_eq!(output.extra["function_rows"].as_array().unwrap().len(), 4);
    assert_eq!(outcomes.len(), 2);
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.test.coverage.functions")
    );
    let mut permissive = coverage_plan.clone();
    permissive.context.options.min_func_cov = Some(50.0);
    assert!(
        observe(&permissive, &outcomes, &artifacts)
            .observation
            .findings
            .is_empty()
    );
    assert_eq!(
        output.extra["coverage"]["function_rows"][0]["covered"],
        false
    );
    assert!(!root.path().join(".pytest_cache").exists());
    assert!(!root.path().join("__pycache__").exists());
    assert!(!root.path().join(".coverage").exists());
}
#[test]
#[ignore = "explicit native Python pytest/coverage gate"]
fn failing_tests_still_allow_coverage_report_but_no_tests_and_collection_failure_do_not() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("main.py"), "def f():\n    return 1\n").unwrap();
    fs::write(
        root.path().join("tests/test_main.py"),
        "import main\ndef test_failure():\n    assert main.f() == 2\n",
    )
    .unwrap();
    let request = request(root.path(), ValidationKind::PythonCoverage, "run-failure");
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let mut outcomes = BTreeMap::new();
    execute_owned(&planned, &mut outcomes, Some(&owner));
    assert_eq!(outcomes[&planned.tasks[0].id].exit_code, Some(1));
    assert_eq!(outcomes[&planned.tasks[1].id].exit_code, Some(0));
    let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    assert_eq!(
        observe(&planned, &outcomes, &artifacts).observation.state,
        TaskState::Succeeded
    );
    fs::remove_file(root.path().join("tests/test_main.py")).unwrap();
    let mut empty = request.clone();
    empty.owned_run_dir = root.path().join("run-empty");
    let empty_plan = plan(&empty).unwrap();
    let _owner = OwnedRun::create(&empty.owned_run_dir).unwrap();
    let suite = Runner::default().run(&empty_plan.tasks[0], &Cancellation::default());
    assert_eq!(suite.exit_code, Some(5));
    assert!(
        ready_steps(
            &empty_plan,
            &BTreeMap::from([(suite.task_id.clone(), suite)])
        )
        .is_err()
    );
    fs::write(
        root.path().join("tests/test_bad.py"),
        "raise RuntimeError('collection failed')\n",
    )
    .unwrap();
    let mut bad = request;
    bad.owned_run_dir = root.path().join("run-collection");
    let bad_plan = plan(&bad).unwrap();
    let _owner = OwnedRun::create(&bad.owned_run_dir).unwrap();
    let suite = Runner::default().run(&bad_plan.tasks[0], &Cancellation::default());
    assert_eq!(suite.exit_code, Some(2));
    assert!(ready_steps(&bad_plan, &BTreeMap::from([(suite.task_id.clone(), suite)])).is_err());
}
#[test]
#[ignore = "explicit native Python runtime/import gate"]
fn explicit_imports_do_not_run_module_main_or_write_bytecode_and_keep_failures() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.py"),
        "print('initializing without newline', end='')\nif __name__ == '__main__':\n    open('main-ran','w').write('bad')\nVALUE=1\n",
    )
    .unwrap();
    fs::write(
        root.path().join("broken.py"),
        "raise ValueError('explicit import failure')\n",
    )
    .unwrap();
    fs::write(
        root.path().join("json.py"),
        "raise RuntimeError('project json module was reached')\n",
    )
    .unwrap();
    let mut request = request(root.path(), ValidationKind::PythonImports, "run-import");
    request.source_files.push("broken.py".into());
    request.source_files.push("json.py".into());
    request.options.modules = vec!["main".into(), "broken".into(), "json".into()];
    let planned = plan(&request).unwrap();
    let mut outcomes = BTreeMap::new();
    execute(&planned, &mut outcomes);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.observation.findings.len(), 2);
    assert_eq!(
        output.observation.findings[0].primary_location.path,
        "broken.py"
    );
    assert!(!root.path().join("main-ran").exists());
    assert!(!root.path().join("__pycache__").exists());
}
#[test]
#[ignore = "explicit native Python runtime/compile gate"]
fn runtime_metadata_compilation_and_declared_version_constraints_are_measured_separately() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.py"),
        "# prefix\n# second\ndef broken(:\n    pass\n",
    )
    .unwrap();
    let mut request = request(root.path(), ValidationKind::PythonRuntime, "run-runtime");
    request.options.compile_sources = true;
    request.options.requires_python = Some(">=99".into());
    let planned = plan(&request).unwrap();
    let _owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let mut outcomes = BTreeMap::new();
    execute(&planned, &mut outcomes);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.observation.findings.len(), 2);
    assert_eq!(
        output
            .observation
            .findings
            .iter()
            .find(|finding| finding.rule_id == "python.compat.runtime-syntax")
            .unwrap()
            .primary_location
            .start_line,
        3
    );
    assert!(output.extra["runtime"]["soabi"].is_string());
    assert!(!root.path().join("__pycache__").exists());
}
#[test]
#[ignore = "explicit native Python timeout gate"]
fn timed_out_suite_cannot_unlock_coverage_or_become_clean_evidence() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("tests/test_main.py"),
        "import time\ndef test_sleep():\n    time.sleep(5)\n",
    )
    .unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage, "run-timeout");
    request.options.timeout_seconds = 0.1;
    let planned = plan(&request).unwrap();
    let _owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let suite = Runner::default().run(&planned.tasks[0], &Cancellation::default());
    assert!(suite.timed_out);
    let outcomes = BTreeMap::from([(suite.task_id.clone(), suite)]);
    assert!(ready_steps(&planned, &outcomes).is_err());
    assert_eq!(
        observe(&planned, &outcomes, &CapturedArtifacts::default())
            .observation
            .state,
        TaskState::Failed
    );
}
#[test]
#[ignore = "explicit native Python quality repeat gate"]
fn quality_repeats_run_sequentially_and_keep_report_mode_findings_advisory() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("tests/test_flaky.py"),"from pathlib import Path\nimport time\ndef test_flaky():\n    counter=Path('runs')\n    value=int(counter.read_text())+1 if counter.exists() else 1\n    time.sleep(0.04)\n    counter.write_text(str(value))\n    assert value > 1\n").unwrap();
    let mut request = request(root.path(), ValidationKind::PythonTest, "run-quality");
    request.options.repeat_runs = 2;
    request.options.slow_threshold_seconds = Some(0.01);
    let planned = plan(&request).unwrap();
    assert_eq!(
        ready_steps(&planned, &BTreeMap::new()).unwrap(),
        vec![planned.tasks[0].id.clone()]
    );
    let mut outcomes = BTreeMap::new();
    execute(&planned, &mut outcomes);
    assert_eq!(fs::read_to_string(root.path().join("runs")).unwrap(), "2");
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let flaky = output
        .observation
        .findings
        .iter()
        .find(|finding| finding.rule_id == "ici.test.flaky-test")
        .unwrap();
    assert!(flaky.tags.iter().any(|tag| tag == "advisory"));
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.test.slow-test")
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .find(|finding| finding.rule_id == "pytest.failed")
            .unwrap()
            .tags
            .is_empty()
    );
}
#[test]
#[ignore = "explicit CPython3.10/3.14 runtime matrix gate"]
fn declared_runtime_matrix_compiles_each_interpreter_and_keeps_optional_and_required_verdicts() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"),"type Alias = int\nVALUE=1\nif __name__ == '__main__':\n    open('main-ran','w').write('bad')\n").unwrap();
    let mut request = request(root.path(), ValidationKind::PythonRuntime, "run-matrix");
    let new = ToolRequest {
        id: "python3.14".into(),
        explicit: Some(
            std::env::var("ICI_TEST_PYTHON_NEW")
                .expect("ICI_TEST_PYTHON_NEW explicit second project runtime required")
                .into(),
        ),
        project_root: root.path().into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap();
    request.options.runtimes = vec![
        RuntimeSpec {
            configured: "project-old".into(),
            tool: Some(request.tool.clone()),
            required: false,
        },
        RuntimeSpec {
            configured: "project-new".into(),
            tool: Some(new),
            required: true,
        },
        RuntimeSpec {
            configured: "missing-optional".into(),
            tool: None,
            required: false,
        },
    ];
    request.options.compile_sources = true;
    let mut planned = plan(&request).unwrap();
    planned.context.options.requires_python = Some(">=3.12".into());
    assert_eq!(planned.tasks.len(), 4);
    let _owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let mut outcomes = BTreeMap::new();
    execute(&planned, &mut outcomes);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.observation.findings.len(), 2);
    assert!(
        output
            .observation
            .findings
            .iter()
            .all(|finding| finding.tags.iter().any(|tag| tag == "advisory"))
    );
    assert_eq!(
        output.extra["runtime_matrix"][1]["runtime"]["version"][1],
        14
    );
    assert!(!root.path().join("main-ran").exists());
    assert!(!root.path().join("__pycache__").exists());
    let mut required = planned.clone();
    required.runtime_plans[2].required = true;
    assert_eq!(
        observe(&required, &outcomes, &CapturedArtifacts::default())
            .observation
            .state,
        TaskState::Failed
    );
}
#[test]
#[ignore = "explicit project mutation capability command gate"]
fn configured_mutation_capability_commands_preserve_available_failure_and_timeout_as_optional() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("tests/test_main.py"),
        "def test_good():\n    assert True\n",
    )
    .unwrap();
    let command = root.path().join("mutation-capability");
    fs::write(&command,"#!/bin/sh\nif [ \"$1\" = sleep ]; then sleep 1; exit 0; fi\necho explicit-capability-fixture\nexit \"$1\"\n").unwrap();
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&command, fs::Permissions::from_mode(0o700)).unwrap();
    let selected = ToolRequest {
        id: "mutation-capability".into(),
        explicit: Some(command),
        project_root: root.path().into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap();
    for (argument, available, timedout) in [
        ("0", true, false),
        ("7", false, false),
        ("sleep", false, true),
    ] {
        let mut request = request(
            root.path(),
            ValidationKind::PythonTest,
            &format!("run-mutation-{argument}"),
        );
        request.options.mutation_enabled = true;
        request.options.mutation_tool = Some(selected.clone());
        request.options.mutation_argv = vec![
            selected.path.to_string_lossy().into_owned(),
            argument.into(),
        ];
        request.options.quality_timeout_seconds = 0.05;
        let planned = plan(&request).unwrap();
        let mut outcomes = BTreeMap::new();
        execute(&planned, &mut outcomes);
        let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert!(
            output.observation.evidence_is_complete(),
            "{argument}: {output:?}"
        );
        assert_eq!(
            output.extra["mutation"]["mutation_available"],
            u8::from(available)
        );
        assert_eq!(output.extra["optional_steps"][0]["timed_out"], timedout);
        assert!(output.observation.findings.is_empty());
    }
}
