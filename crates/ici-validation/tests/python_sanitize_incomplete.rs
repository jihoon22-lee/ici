//! Root ruling 2026-11-14 (f2): the legacy Python ResourceWarning scope's incomplete pytest runs
//! against frozen captures (`tests/rust-parity/validation/python-sanitize-incomplete-oracle.json`,
//! `capture_python_sanitize_incomplete.py`): a run that raises ('Pytest could not execute'), a
//! timed-out run (the pytest call's deadline patched to 3.0 s at runtime), a truncated one (past
//! 1,000,000 characters of stdout) and a signalled one (SIGKILL). Native receives the streams
//! frozen's run_process returned, with the matching process state, and must give frozen's
//! `PythonResourceWarnings` ERROR target at tests:1 and the record's error; the legacy suite runs
//! under frozen run_process's 300 s / 1,000,000-byte bounds.
use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use serde_json::Value;
use std::{collections::BTreeMap, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/python-sanitize-incomplete-oracle.json");

fn request(root: &Path) -> ValidationRequest {
    let mut request = ValidationRequest {
        kind: ValidationKind::PythonSanitize,
        task_id: "project.python.sanitize".into(),
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
        source_files: vec!["src/calc.py".into()],
        functions: vec![],
        options: ValidationOptions::default(),
    };
    request.options.legacy_pytest_result = true;
    request
}

/// A path of the capture's project length: `tempfile.mkdtemp(prefix=
/// "ici-python-sanitize-incomplete-")` plus `/project` (capture_python_sanitize_incomplete.py).
const CAPTURED_ROOT: &str = "/tmp/ici-python-sanitize-incomplete-XXXXXXXX/project";

/// Native's text over the stand-in path, normalized like the capture.
fn normalized(text: &str) -> String {
    text.replace(CAPTURED_ROOT, "<root>")
}

/// The suite outcome native's runner gives for what frozen's run_process returned.
fn outcome(id: &str, mode: &str, process: Option<&Value>) -> TaskOutcome {
    let mut outcome = TaskOutcome::empty(id);
    outcome.state = TaskState::Failed;
    let Some(process) = process else {
        // The pytest call raised before any process ran: native's runner error in its place.
        assert_eq!(mode, "raise");
        outcome.error = Some("injected runner failure".into());
        return outcome;
    };
    // The capture normalized its project path to `<root>`; frozen's 300-character snippet was
    // cut from the real stream, so native reads a path of the same length in its place.
    outcome.stdout = process["stdout_prefix"]
        .as_str()
        .unwrap()
        .replace("<root>", CAPTURED_ROOT)
        .into_bytes();
    let stderr = process["stderr_prefix"].as_str().unwrap();
    match mode {
        "timeout" => {
            assert_eq!(process["timed_out"], true);
            assert_eq!(process["returncode"], 124);
            // Frozen's stream is its own note over the test's empty stderr; native's runner
            // keeps the process's stderr and the planned deadline names the note.
            assert_eq!(stderr, "Command timed out after 3.0s: ");
            outcome.timed_out = true;
            outcome.signal = Some(9);
        }
        "truncated" => {
            assert_eq!(process["truncated"], true);
            assert_eq!(process["stdout_length"], 1_000_000);
            outcome.truncated = true;
            outcome.exit_code =
                Some(i32::try_from(process["returncode"].as_i64().unwrap()).unwrap());
            outcome.stderr = stderr.as_bytes().to_vec();
        }
        "signal" => {
            assert_eq!(process["returncode"], -9);
            outcome.signal = Some(9);
            outcome.stderr = stderr.as_bytes().to_vec();
        }
        other => panic!("unknown case {other}"),
    }
    // The prefix holds every character the 300-character snippet reads.
    for key in ["stdout", "stderr"] {
        let length = process[format!("{key}_length")].as_u64().unwrap();
        let prefix = process[format!("{key}_prefix")]
            .as_str()
            .unwrap()
            .replace("<root>", CAPTURED_ROOT);
        assert!(
            prefix.chars().count() as u64 == length
                || prefix
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
                    .chars()
                    .count()
                    > 300,
            "{mode}: {key} prefix"
        );
    }
    outcome
}

#[test]
fn incomplete_resource_warning_runs_are_frozen_scope_errors() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert_eq!(oracle["frozen_default_timeout_seconds"], 300.0);
    assert_eq!(oracle["frozen_default_output_chars"], 1_000_000);
    let bound = oracle["patched_timeout_seconds"].as_f64().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    for (mode, case) in cases {
        let report = &case["report"];
        assert_eq!(case["exit_code"], 1, "{mode}");
        assert_eq!(report["status"], "ERROR", "{mode}");
        assert_eq!(report["evidence"], "NOT_RUN", "{mode}");
        let targets: Vec<&Value> = report["targets"].as_array().unwrap().iter().collect();
        assert_eq!(targets.len(), 1, "{mode}");
        let frozen = targets[0];
        assert_eq!(frozen["target_name"], "PythonResourceWarnings", "{mode}");
        assert_eq!(frozen["file_path"], "tests", "{mode}");
        assert_eq!(frozen["start_line"], 1, "{mode}");
        assert_eq!(frozen["status"], "ERROR", "{mode}");
        let record = report["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["name"] == "pytest resource warnings")
            .unwrap();

        let root = tempfile::tempdir().unwrap();
        let mut planned = plan(&request(root.path())).unwrap();
        let id = "project.python.sanitize.suite";
        let suite = planned.tasks.iter_mut().find(|task| task.id == id).unwrap();
        // Frozen run_process's defaults (core/runner.py:478-480).
        assert_eq!(suite.timeout_seconds, Some(300.0), "{mode}");
        assert_eq!(suite.output_limit_bytes, Some(1_000_000), "{mode}");
        // The capture's patched deadline for the timed-out run.
        let deadline = (mode == "timeout").then_some(bound);
        if let Some(seconds) = deadline {
            suite.timeout_seconds = Some(seconds);
        }
        let suite_outcome = outcome(
            id,
            mode,
            case["process"].as_object().map(|_| &case["process"]),
        );
        let outcomes = BTreeMap::from([(id.to_owned(), suite_outcome.clone())]);
        let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(output.observation.state, TaskState::Failed, "{mode}");
        // Evidence: the legacy projection (ici-reporting projection.rs `legacy_status`) gives a
        // Failed check without a FAIL verdict the ERROR class and pairs ERROR with NOT_RUN, the
        // frozen engine evidence above. The exit differs by ruling (native 3, frozen 1):
        // decisions.md "legacy 종료 코드 3의 범위" with root ruling 2026-11-14 (f3).
        assert_ne!(output.status, "FAIL", "{mode}: {output:?}");
        let native_evidence = match (output.observation.state, output.status.as_str()) {
            (TaskState::Failed, status) if status != "FAIL" => "NOT_RUN",
            _ => "MEASURED",
        };
        assert_eq!(native_evidence, report["evidence"], "{mode}");
        let native: Vec<_> = output
            .targets
            .iter()
            .filter(|target| target.target_name == "PythonResourceWarnings")
            .collect();
        assert_eq!(native.len(), 1, "{mode}: {output:?}");
        assert_eq!(normalized(&native[0].message), frozen["message"], "{mode}");
        assert_eq!(native[0].status, "ERROR", "{mode}");
        assert_eq!(native[0].file_path, "tests", "{mode}");
        assert_eq!(native[0].start_line, 1, "{mode}");
        if mode == "raise" {
            // Frozen `_record_tool_exception` records `<ExceptionType>: <exc>`; native's record
            // keeps its runner's error (execute.rs python_record), the same exception text.
            assert_eq!(record["error"], "RuntimeError: injected runner failure");
            assert_eq!(
                frozen["message"],
                "Pytest could not execute: injected runner failure"
            );
        } else {
            assert_eq!(record["error"], frozen["message"], "{mode}");
            assert_eq!(
                normalized(&legacy_evidence_error(
                    &suite_outcome,
                    Some(deadline.unwrap_or(300.0))
                )),
                frozen["message"].as_str().unwrap(),
                "{mode}"
            );
        }
    }
}
