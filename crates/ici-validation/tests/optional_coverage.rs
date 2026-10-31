use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use std::{collections::BTreeMap, path::Path};
fn request(root: &Path, kind: ValidationKind) -> ValidationRequest {
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
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
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
            collect_coverage: kind == ValidationKind::PythonTest,
            coverage_module_probe: true,
            ..Default::default()
        },
    }
}
fn outcome(id: &str, stdout: &str, stderr: &str, exit: i32) -> TaskOutcome {
    let mut output = TaskOutcome::empty(id);
    output.state = TaskState::Succeeded;
    output.exit_code = Some(exit);
    output.stdout = stdout.as_bytes().to_vec();
    output.stderr = stderr.as_bytes().to_vec();
    output
}
fn cases() -> &'static str {
    "tests/test_app.py::test_ok PASSED\n============================== 1 passed in 0.01s ==============================\n"
}
#[test]
fn capability_selects_exactly_one_visible_suite_and_quality_repeats_share_its_evidence() {
    for missing in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let mut request = request(root.path(), ValidationKind::PythonTest);
        request.options.repeat_runs = 2;
        let plan = plan(&request).unwrap();
        let probe = python_coverage::probe_id(&plan).unwrap();
        let canonical = python_coverage::suite_id(&plan);
        let fallback = python_coverage::fallback_id(&plan);
        assert_eq!(
            ready_steps(&plan, &BTreeMap::new()).unwrap(),
            vec![probe.clone()]
        );
        assert_eq!(plan.conditional_steps.len(), 2);
        assert!(
            plan.tasks
                .iter()
                .find(|task| task.id == fallback)
                .unwrap()
                .argv
                .iter()
                .all(|arg| arg != "coverage")
        );
        let mut outcomes = BTreeMap::from([(
            probe.clone(),
            if missing {
                outcome(&probe, "", "/tools/python: No module named coverage\n", 1)
            } else {
                outcome(
                    &probe,
                    "Coverage.py, version 7.15.4 with C extension\n",
                    "",
                    0,
                )
            },
        )]);
        let active = if missing { fallback } else { canonical };
        assert_eq!(ready_steps(&plan, &outcomes).unwrap(), vec![active.clone()]);
        outcomes.insert(active.clone(), outcome(&active, cases(), "", 0));
        let repeat = format!("{}.repeat1", request.task_id);
        assert_eq!(ready_steps(&plan, &outcomes).unwrap(), vec![repeat.clone()]);
        outcomes.insert(repeat.clone(), outcome(&repeat, cases(), "", 0));
        assert!(ready_steps(&plan, &outcomes).unwrap().is_empty());
        let result = observe(&plan, &outcomes, &CapturedArtifacts::default());
        assert_eq!(result.observation.state, TaskState::Succeeded, "{result:?}");
        assert_eq!(
            result.extra["coverage_capability"]["availability"],
            if missing { "missing" } else { "available" }
        );
        assert_eq!(result.extra["coverage_capability"]["active_suite"], active);
        assert_eq!(result.observation.measurements[0].numerator, Some(1));
        assert_eq!(result.observation.measurements[0].denominator, Some(1));
    }
}
#[test]
fn broken_truncated_timed_out_and_ambiguous_coverage_probes_never_activate_plain_fallback() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::PythonTest)).unwrap();
    let probe = python_coverage::probe_id(&planned).unwrap();
    let mut truncated = outcome(&probe, "", "/tools/python: No module named coverage\n", 1);
    truncated.truncated = true;
    let mut timed_out = truncated.clone();
    timed_out.truncated = false;
    timed_out.timed_out = true;
    for broken in [
        outcome(&probe, "something else\n", "", 0),
        outcome(
            &probe,
            "",
            "Traceback (most recent call last):\nModuleNotFoundError: No module named 'coverage'\n",
            1,
        ),
        truncated,
        timed_out,
    ] {
        assert!(ready_steps(&planned, &BTreeMap::from([(probe.clone(), broken)])).is_err());
    }
}
#[test]
fn missing_optional_coverage_has_no_report_score_or_invented_zero_denominator() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonCoverage);
    request.options.shared_test_task_id = Some("project.python.test.suite".into());
    request.options.max_coverage_regression = Some(0.0);
    let planned = plan(&request).unwrap();
    let probe = python_coverage::probe_id(&planned).unwrap();
    let fallback = python_coverage::fallback_id(&planned);
    let outcomes = BTreeMap::from([
        (
            probe.clone(),
            outcome(&probe, "", "/tools/python: No module named coverage\n", 1),
        ),
        (fallback.clone(), outcome(&fallback, cases(), "", 0)),
    ]);
    assert!(ready_steps(&planned, &outcomes).unwrap().is_empty());
    let result = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(result.observation.state, TaskState::Failed);
    assert!(result.observation.measurements.is_empty());
    assert!(result.score.is_none());
    assert_eq!(
        result.extra["coverage_policy"]["raw_metrics"]["line"]["state"],
        "unavailable"
    );
    assert!(result.extra["coverage_policy"]["raw_metrics"]["line"]["total"].is_null());
    assert_eq!(result.extra["coverage_policy"]["regression_tolerance"], 0.0);
    assert_eq!(result.targets[0].status, "SKIP");
}
#[test]
fn coverage_run_selects_declared_source_directories_so_unimported_files_are_measured() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::PythonTest);
    request.source_files = vec!["src/pkg/a.py".into(), "main.py".into(), "README.md".into()];
    request.options.repeat_runs = 2;
    let plan = plan(&request).unwrap();
    let suite = plan
        .tasks
        .iter()
        .find(|task| task.id == python_coverage::suite_id(&plan))
        .unwrap();
    let source = format!(
        "--source={},{}",
        root.path().display(),
        root.path().join("src/pkg").display()
    );
    assert_eq!(suite.argv[7], source);
    assert_eq!(suite.argv[8..10], ["-m", "pytest"]);
    // The uncovered fallback and the quality repeat never inherit coverage options.
    for task in plan.tasks.iter().filter(|task| task.id != suite.id) {
        assert!(
            !task
                .argv
                .iter()
                .any(|argument| argument.starts_with("--source=")
                    || argument == "coverage" && task.argv.iter().any(|item| item == "pytest")),
            "{:?}",
            task.argv
        );
    }
    // Without declared Python sources, or beyond the argv bound, nothing is guessed.
    let mut none = request.clone();
    none.source_files = vec!["README.md".into()];
    assert!(
        !plan_argv(&none)
            .iter()
            .any(|argument| argument.starts_with("--source="))
    );
    let mut many = request;
    many.source_files = (0..257).map(|index| format!("d{index}/m.py")).collect();
    assert!(
        !plan_argv(&many)
            .iter()
            .any(|argument| argument.starts_with("--source="))
    );
}
fn plan_argv(request: &ValidationRequest) -> Vec<String> {
    let plan = plan(request).unwrap();
    let suite = python_coverage::suite_id(&plan);
    plan.tasks
        .into_iter()
        .find(|task| task.id == suite)
        .unwrap()
        .argv
}
