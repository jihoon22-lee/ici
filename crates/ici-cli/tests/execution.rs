use ici_cli::{execute, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::{Cancellation, storage};
use std::{collections::BTreeMap, fs};

fn fixture(selected: &[&str]) -> tempfile::TempDir {
    let directory = tempfile::tempdir().unwrap();
    fs::write(
        directory.path().join("main.py"),
        "def hello():\n    return 1\n",
    )
    .unwrap();
    let mut config = String::from("schema_version=2\n");
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "python")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            selected.contains(&check.id.as_str())
        ));
    }
    fs::write(directory.path().join("ici.toml"), config).unwrap();
    directory
}
fn invocation(args: &[&str]) -> registry::Invocation {
    registry::parse(args.iter().copied()).unwrap()
}

#[test]
fn native_analysis_saves_valid_result_and_actual_targets_without_python_on_path() {
    let directory = fixture(&["python.line", "python.security"]);
    let old = directory.path().join(".ici/next/result.json");
    fs::create_dir_all(old.parent().unwrap()).unwrap();
    fs::write(&old, "old preserved bytes").unwrap();
    let call = invocation(&["ici", "analyze", "--events", "events.jsonl"]);
    let run = execute::execute(
        &call,
        directory.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(run.result.gate.selected, GateVerdict::Pass);
    assert_eq!(run.result.checks.as_ref().unwrap().len(), 2);
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .all(|check| check.evidence_complete)
    );
    assert!(!run.outputs["project.python.line"].targets.is_empty());
    assert_eq!(fs::read_to_string(old).unwrap(), "old preserved bytes");
    assert_eq!(storage::read_result(&run.result_path).unwrap(), run.result);
    let (events, notes) =
        storage::read_event_stream(&directory.path().join("events.jsonl")).unwrap();
    assert!(notes.is_empty());
    assert_eq!(events.first().unwrap().event_type, "run.started");
    assert_eq!(events.last().unwrap().event_type, "run.completed");
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started")
            .count(),
        2
    );
}

#[test]
fn cancelled_run_preserves_partial_artifact_without_claiming_unstarted_tasks() {
    let directory = fixture(&["python.line"]);
    let call = invocation(&["ici", "analyze", "--events", "events.jsonl"]);
    let cancelled = Cancellation::default();
    cancelled.cancel();
    let run =
        execute::execute(&call, directory.path(), &BTreeMap::new(), true, &cancelled).unwrap();
    assert_eq!(run.result.exit_code(), 130);
    assert_eq!(run.result.gate.selected, GateVerdict::Incomplete);
    assert!(run.result.execution.cancelled);
    assert_eq!(
        run.result.checks.as_ref().unwrap()[0].state,
        TaskState::Cancelled
    );
    let (events, _) = storage::read_event_stream(&directory.path().join("events.jsonl")).unwrap();
    assert!(
        !events
            .iter()
            .any(|event| event.event_type == "task.started")
    );
    assert!(run.result_path.is_file());
}

#[test]
fn source_baseline_and_old_result_destinations_are_protected_before_work() {
    let directory = fixture(&["python.line"]);
    for args in [
        vec!["ici", "analyze", "--result", "main.py"],
        vec!["ici", "analyze", "--result", "ici.toml"],
        vec![
            "ici",
            "analyze",
            "--result",
            "same.json",
            "--baseline",
            "same.json",
        ],
        vec![
            "ici",
            "analyze",
            "--result",
            "same.json",
            "--events",
            "./same.json",
        ],
    ] {
        assert!(
            execute::execute(
                &invocation(&args),
                directory.path(),
                &BTreeMap::new(),
                true,
                &Cancellation::default()
            )
            .is_err()
        );
    }
    let prior = directory.path().join("old.json");
    fs::write(&prior, r#"{"schema_version":3,"engines":[]}"#).unwrap();
    let call = invocation(&["ici", "analyze", "--result", "old.json"]);
    assert!(
        execute::execute(
            &call,
            directory.path(),
            &BTreeMap::new(),
            true,
            &Cancellation::default()
        )
        .unwrap_err()
        .to_string()
        .contains("choose a new")
    );
    assert_eq!(
        fs::read_to_string(prior).unwrap(),
        r#"{"schema_version":3,"engines":[]}"#
    );
}

#[test]
fn symlink_state_directory_is_refused_before_running_analysis() {
    let directory = fixture(&["python.line"]);
    let elsewhere = tempfile::tempdir().unwrap();
    std::os::unix::fs::symlink(elsewhere.path(), directory.path().join(".ici")).unwrap();
    assert!(
        execute::execute(
            &invocation(&["ici", "analyze"]),
            directory.path(),
            &BTreeMap::new(),
            true,
            &Cancellation::default()
        )
        .is_err()
    );
    assert_eq!(fs::read_dir(elsewhere.path()).unwrap().count(), 0);
}

fn fake_ruff(directory: &std::path::Path, body: &str) {
    use std::os::unix::fs::PermissionsExt;
    let tool = directory.join("ruff");
    fs::write(&tool,format!("#!/bin/sh\ncase \" $* \" in\n  *' --show-files '*) printf '%s\\n' '{}/main.py'; exit 0;;\nesac\n{}\n",directory.display(),body)).unwrap();
    fs::set_permissions(&tool, fs::Permissions::from_mode(0o755)).unwrap();
    let config = directory.join("ici.toml");
    let mut text = fs::read_to_string(&config).unwrap();
    text.push_str(&format!(
        "[tools.ruff]\npath={}\n",
        serde_json::to_string(&tool.to_string_lossy()).unwrap()
    ));
    fs::write(config, text).unwrap();
}

#[test]
fn external_source_drift_keeps_findings_but_revokes_complete_scope() {
    let directory = fixture(&["python.line", "python.lint"]);
    fake_ruff(
        directory.path(),
        "printf '\\n# changed by the project tool\\n' >> main.py\nprintf '[]\\n'",
    );
    let call = invocation(&["ici", "analyze"]);
    let run = execute::execute(
        &call,
        directory.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(run.result.gate.selected, GateVerdict::Incomplete);
    assert!(
        run.result
            .gate
            .reasons
            .iter()
            .any(|reason| reason.contains("inputs changed"))
    );
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .all(|check| !check.evidence_complete)
    );
    assert!(!run.outputs["project.python.line"].targets.is_empty());
}

#[test]
fn cancellation_during_tool_execution_reaps_process_and_saves_partial_evidence() {
    let directory = fixture(&["python.lint"]);
    fake_ruff(
        directory.path(),
        "printf started > marker\n/bin/sleep 30\nprintf '[]\\n'",
    );
    let cancellation = Cancellation::default();
    let child_cancel = cancellation.clone();
    let marker = directory.path().join("marker");
    let thread = std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !marker.is_file() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(marker.is_file(), "tool never started");
        let requested = std::time::Instant::now();
        child_cancel.cancel();
        requested
    });
    let run = execute::execute(
        &invocation(&["ici", "analyze", "--events", "events.jsonl"]),
        directory.path(),
        &BTreeMap::new(),
        true,
        &cancellation,
    )
    .unwrap();
    let cancelled_at = thread.join().unwrap();
    assert!(cancelled_at.elapsed() < std::time::Duration::from_secs(2));
    assert_eq!(run.result.exit_code(), 130);
    assert!(run.result_path.is_file());
    assert_eq!(
        run.outputs["project.python.lint"].observation.state,
        TaskState::Cancelled
    );
}

#[test]
fn warm_native_cache_preserves_targets_and_corruption_or_no_cache_recomputes() {
    let directory = fixture(&["python.line", "python.security"]);
    let invoke = |extra: &[&str]| {
        let mut args = vec!["ici", "analyze"];
        args.extend_from_slice(extra);
        execute::execute(
            &invocation(&args),
            directory.path(),
            &BTreeMap::new(),
            true,
            &Cancellation::default(),
        )
        .unwrap()
    };
    let cold = invoke(&[]);
    assert!(cold.result.execution.reused_task_ids.is_empty());
    let warm = invoke(&["--events", "warm.jsonl"]);
    assert_eq!(warm.result.execution.reused_task_ids.len(), 2);
    assert_eq!(
        serde_json::to_value(&warm.outputs["project.python.line"].targets).unwrap(),
        serde_json::to_value(&cold.outputs["project.python.line"].targets).unwrap()
    );
    let events = storage::read_event_stream(&directory.path().join("warm.jsonl"))
        .unwrap()
        .0;
    assert!(
        !events
            .iter()
            .any(|event| event.event_type == "task.started")
    );
    let uncached = invoke(&["--no-cache"]);
    assert!(uncached.result.execution.reused_task_ids.is_empty());
    let entry = fs::read_dir(directory.path().join(".ici/rust-v1/observations"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    fs::write(entry, b"broken").unwrap();
    let repaired = invoke(&[]);
    assert_eq!(repaired.result.execution.reused_task_ids.len(), 1);
    assert!(
        repaired
            .result
            .limitations
            .iter()
            .any(|limitation| limitation.contains("cache ignored"))
    );
    fs::write(
        directory.path().join("main.py"),
        "def changed():\n    return 2\n",
    )
    .unwrap();
    assert!(invoke(&[]).result.execution.reused_task_ids.is_empty());
}
