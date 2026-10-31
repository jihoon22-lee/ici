//! Explicit project-runtime acceptance. Invoked with --ignored and a pinned ICI_TEST_PYTHON.
use ici_cli::{execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::{Cancellation, storage};
use std::{collections::BTreeMap, fs};

fn fixture(failing: bool) -> (tempfile::TempDir, BTreeMap<String, String>) {
    let directory = tempfile::tempdir().unwrap();
    fs::create_dir(directory.path().join("src")).unwrap();
    fs::create_dir(directory.path().join("tests")).unwrap();
    fs::write(
        directory.path().join("src/app.py"),
        "def never(): pass\ndef called(value):\n    if value:\n        return 1\n    return 0\n",
    )
    .unwrap();
    fs::write(
        directory.path().join("tests/test_app.py"),
        format!(
            "import app\ndef test_called():\n    assert app.called(True) == {}\n",
            if failing { 2 } else { 1 }
        ),
    )
    .unwrap();
    fs::write(
        directory.path().join("pyproject.toml"),
        "[project]\nname='fixture'\nversion='1'\nrequires-python='>=3.10'\n",
    )
    .unwrap();
    let executable = std::env::var("ICI_TEST_PYTHON")
        .expect("set explicit ICI_TEST_PYTHON with pytest/coverage");
    let mut config = String::from("schema_version=2\n");
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "python")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            matches!(
                check.id.as_str(),
                "python.test" | "python.coverage" | "python.compat-runtime"
            )
        ));
    }
    config.push_str(&format!("[engines.test]\nmin_func_cov=100.0\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n[components.python]\nexecutable={}\ntest_paths=['tests']\n",serde_json::to_string(&executable).unwrap()));
    fs::write(directory.path().join("ici.toml"), config).unwrap();
    let environment = BTreeMap::from([
        ("PATH".into(), std::env::var("PATH").unwrap_or_default()),
        (
            "PYTHONPATH".into(),
            directory.path().join("src").to_string_lossy().into_owned(),
        ),
    ]);
    (directory, environment)
}
#[test]
#[ignore = "explicit project Python runtime+pytest+coverage acceptance"]
fn one_real_failing_suite_still_feeds_owned_coverage_and_preserves_source_facts() {
    let (directory, environment) = fixture(true);
    let invocation =
        registry::parse(["ici", "next", "verify", "--events", "events.jsonl"]).unwrap();
    let preview = planning::prepare(&invocation, directory.path(), &environment, false).unwrap();
    assert!(!directory.path().join(".ici").exists());
    let coverage = &preview.validations["project.python.coverage"][0];
    assert_eq!(coverage.tasks.len(), 1);
    assert!(coverage.context.targets.contains(&"tests".into()));
    assert_eq!(
        coverage.context.options.shared_test_task_id.as_deref(),
        Some("project.python.test.suite")
    );
    let run = execute::execute(
        &invocation,
        directory.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.gate.selected,
        GateVerdict::Fail,
        "{:?}",
        run.outputs
    );
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .all(|check| check.state == TaskState::Succeeded)
    );
    assert!(
        run.result
            .findings
            .iter()
            .any(|finding| finding.rule_id == "pytest.failed")
    );
    assert!(
        run.result
            .findings
            .iter()
            .any(|finding| finding.rule_id.contains("function")),
        "{:?}",
        run.result.findings
    );
    let events = storage::read_event_stream(&directory.path().join("events.jsonl"))
        .unwrap()
        .0;
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started"
                && event.task_id.as_deref() == Some("project.python.test.suite"))
            .count(),
        1
    );
    assert!(
        !events
            .iter()
            .any(|event| event.task_id.as_deref() == Some("project.python.coverage.suite"))
    );
    assert!(
        events
            .iter()
            .any(|event| event.task_id.as_deref() == Some("project.python.coverage.report"))
    );
    // A newly passing test with unchanged actual nodes resolves its earlier failure, despite a
    // fresh owned run directory and changed source bytes.
    storage::write_result(
        &directory.path().join("baseline.json"),
        &run.result,
        storage::WriteMode::CreateNew,
    )
    .unwrap();
    fs::write(
        directory.path().join("tests/test_app.py"),
        "import app\ndef test_called():\n    assert app.called(True) == 1\n",
    )
    .unwrap();
    let fixed = execute::execute(
        &registry::parse(["ici", "next", "verify", "--baseline", "baseline.json"]).unwrap(),
        directory.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let comparison = fixed.result.baseline.as_ref().unwrap();
    assert_eq!(
        comparison.state,
        ici_domain::BaselineState::Comparable,
        "{comparison:?}"
    );
    let old_test = run
        .result
        .findings
        .iter()
        .find(|finding| finding.rule_id == "pytest.failed")
        .unwrap();
    assert!(
        comparison.resolved.contains(&old_test.fingerprint),
        "{comparison:?}\nold={:?}\nnew={:?}",
        run.result.checks,
        fixed.result.checks
    );
}

#[test]
#[ignore = "explicit project Python runtime+pytest acceptance independent of core decoder"]
fn unsupported_core_source_encoding_does_not_suppress_actual_runtime_or_tests() {
    let (directory, environment) = fixture(false);
    let source = directory.path().join("src/app.py");
    let old = fs::read_to_string(&source).unwrap();
    // Every standard text codec now decodes natively, so the trigger is a UTF-7
    // source whose `(` `)` tokens split one base64 group: CPython imports it, but
    // the core decoder cannot prove an original byte boundary between the tokens
    // and refuses the geometry (unmapped-source-boundary) instead of guessing.
    fs::write(
        source,
        format!("# coding: utf-7\n{old}UNSUPPORTED = +ACgAKQ-\n"),
    )
    .unwrap();
    let config = directory.path().join("ici.toml");
    let text = fs::read_to_string(&config).unwrap().replace(
        "[checks.\"python.coverage\"]\nenabled=true",
        "[checks.\"python.coverage\"]\nenabled=false",
    );
    fs::write(config, text).unwrap();
    let run = execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        directory.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        run.outputs
    );
    assert_eq!(
        run.outputs["project.python.test"].observation.state,
        TaskState::Succeeded
    );
    assert_eq!(
        run.outputs["project.python.compat-runtime"]
            .observation
            .state,
        TaskState::Succeeded
    );
    assert!(
        run.result
            .limitations
            .iter()
            .any(|limitation| limitation.contains("source geometry unavailable"))
    );
    assert!(
        run.outputs["project.python.compat-runtime"]
            .targets
            .iter()
            .any(|target| target.file_path == "src/app.py" && target.status == "PASS")
    );
}
