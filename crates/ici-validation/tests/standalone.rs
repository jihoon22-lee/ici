use ici_domain::{SourceInput, TaskState, sha256};
use ici_execution::{Cancellation, Runner, TaskOutcome};
use ici_providers::resolver::{ResolvedTool, ToolRequest, ToolSource};
use ici_validation::*;
use std::{collections::BTreeMap, fs, path::Path, process::Command};
fn request(root: &Path) -> ValidationRequest {
    ValidationRequest {
        kind: ValidationKind::CppExecutable,
        task_id: "app.test0".into(),
        component_id: "app".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
        tool: ResolvedTool {
            id: "test0".into(),
            path: root.join("suite"),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["tests/red.cpp".into()],
        source_files: vec!["tests/red.cpp".into(), "src/math.cpp".into()],
        functions: vec![],
        options: ValidationOptions {
            executable_test_source: Some("tests/red.cpp".into()),
            ..Default::default()
        },
    }
}
#[test]
fn plain_executable_verdicts_keep_located_process_evidence_and_no_incomplete_counts() {
    let root = tempfile::tempdir().unwrap();
    let request = request(root.path());
    let planned = plan(&request).unwrap();
    for (exit, incomplete) in [(0, false), (7, false), (0, true)] {
        let mut outcome = TaskOutcome::empty(&planned.tasks[0].id);
        outcome.state = TaskState::Succeeded;
        outcome.exit_code = Some(exit);
        outcome.timed_out = incomplete;
        let output = observe(
            &planned,
            &BTreeMap::from([(outcome.task_id.clone(), outcome)]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(
            output.observation.state,
            if incomplete {
                TaskState::Failed
            } else {
                TaskState::Succeeded
            },
            "{output:?}"
        );
        assert_eq!(output.targets[0].file_path, "tests/red.cpp");
        assert_eq!(
            output.extra["test_execution"]["canonical_suite_id"],
            "app.test0.suite"
        );
        if incomplete {
            assert!(output.extra["total_tests"].is_null());
            assert!(output.observation.findings.is_empty());
        } else {
            assert_eq!(output.extra["total_tests"], 1);
            assert_eq!(output.extra["passed_tests"], u64::from(exit == 0));
            assert_eq!(output.observation.findings.len(), usize::from(exit != 0));
            assert_eq!(output.extra["test_suites"][0]["file"], "tests/red.cpp");
        }
    }
    let mut invalid = request;
    invalid.options.executable_test_source = Some("outside.cpp".into());
    assert!(plan(&invalid).is_err());
    invalid.options.executable_test_source = Some("tests/red.cpp".into());
    invalid.options.argv = vec!["/usr/bin/true".into()];
    assert!(plan(&invalid).is_err());
}
fn tool(root: &Path, path: impl AsRef<Path>) -> ResolvedTool {
    ToolRequest {
        id: "native".into(),
        explicit: Some(path.as_ref().into()),
        project_root: root.into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap()
}
fn execute(plan: &ValidationPlan) -> BTreeMap<String, TaskOutcome> {
    let mut outcomes = BTreeMap::new();
    loop {
        let steps = ready_steps(plan, &outcomes).unwrap();
        if steps.is_empty() {
            break;
        }
        for id in steps {
            let task = plan.tasks.iter().find(|task| task.id == id).unwrap();
            outcomes.insert(id, Runner::default().run(task, &Cancellation::default()));
        }
    }
    outcomes
}
#[test]
#[ignore = "explicit native GCC/plain executable/sanitizer gate"]
fn actual_plain_cpp_exit_timeout_signal_output_and_ubsan_use_observed_verdicts() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    fs::create_dir(root.join("tests")).unwrap();
    let source = "#include <csignal>\n#include <iostream>\n#include <limits>\nint main(int argc, char** argv) { if(argc<2)return 0; switch(argv[1][0]) { case 'f':return 7; case 's':raise(SIGTERM);break;case 't':while(true){};case 'o':for(int i=0;i<100000;i++)std::cout<<\"abcdefghijklmnopqrstuvwxyz\";break;case 'u':{volatile int n=std::numeric_limits<int>::max();return (n+1)==0;} }return 0;}\n";
    fs::write(root.join("tests/red.cpp"), source).unwrap();
    let compiler = std::env::var("ICI_TEST_GXX").expect("explicit GCC required");
    let compiled = Command::new(&compiler)
        .current_dir(root)
        .args([
            "-g",
            "-O0",
            "-fsanitize=undefined",
            "tests/red.cpp",
            "-o",
            "suite",
        ])
        .output()
        .unwrap();
    assert!(
        compiled.status.success(),
        "{}",
        String::from_utf8_lossy(&compiled.stderr)
    );
    let mut request = request(root);
    request.tool = tool(root, root.join("suite"));
    request.source_files = vec!["tests/red.cpp".into()];
    request.options.timeout_seconds = 0.3;
    for (mode, complete, findings) in [
        ("p", true, 0),
        ("f", true, 1),
        ("s", false, 0),
        ("t", false, 0),
        ("o", false, 0),
    ] {
        request.options.argv = vec![request.tool.path.display().to_string(), mode.into()];
        request.options.output_limit_bytes = Some(4096);
        let planned = plan(&request).unwrap();
        let outcomes = execute(&planned);
        let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(
            output.observation.state,
            if complete {
                TaskState::Succeeded
            } else {
                TaskState::Failed
            },
            "{mode}: {output:?}"
        );
        assert_eq!(
            output.observation.findings.len(),
            findings,
            "{mode}: {output:?}"
        );
        assert_eq!(
            output.extra["total_tests"].as_u64(),
            complete.then_some(1),
            "{mode}: {output:?}"
        );
    }
    request.kind = ValidationKind::Sanitizer;
    request.options.sanitizer_suite_kind = Some(ValidationKind::CppExecutable);
    request.options.instrumented_artifacts = vec!["suite".into()];
    request.options.output_limit_bytes = None;
    request.options.timeout_seconds = 10.0;
    request.options.auxiliary_tools.insert(
        "readelf".into(),
        tool(
            root,
            std::env::var("ICI_TEST_READELF").expect("explicit readelf required"),
        ),
    );
    request.options.argv = vec![request.tool.path.display().to_string(), "u".into()];
    let mut planned = plan(&request).unwrap();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[SourceInput {
            path: "tests/red.cpp".into(),
            bytes: source.as_bytes().to_vec(),
        }]),
    );
    let output = observe(&planned, &execute(&planned), &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.sanitize.ubsan.signed-integer-overflow"),
        "{output:?}"
    );
    assert_eq!(
        output.observation.findings[0].primary_location.path,
        "tests/red.cpp"
    );
    assert_eq!(
        output.extra["sanitizer_test_evidence"]["kind"],
        "cpp_executable"
    );
}

#[test]
fn plain_sanitizer_runtime_failure_is_located_incomplete_without_a_code_failure() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path());
    request.kind = ValidationKind::ThreadSanitizer;
    request.options.sanitizer_suite_kind = Some(ValidationKind::CppExecutable);
    request.options.instrumented_artifacts = vec!["suite".into()];
    request
        .options
        .auxiliary_tools
        .insert("readelf".into(), request.tool.clone());
    let planned = plan(&request).unwrap();
    let mut outcomes = BTreeMap::new();
    for task in &planned.tasks {
        let mut outcome = TaskOutcome::empty(&task.id);
        outcome.state = TaskState::Succeeded;
        if task.id.ends_with(".suite") {
            outcome.exit_code = Some(66);
            outcome.stderr =
                b"FATAL: ThreadSanitizer: unexpected memory mapping 0x1234-0x5678\n".to_vec();
        } else {
            outcome.exit_code = Some(0);
            outcome.stdout=b"Symbol table '.dynsym' contains 2 entries:\n 1: 0 0 FUNC GLOBAL DEFAULT UND __tsan_init\n".to_vec();
        }
        outcomes.insert(task.id.clone(), outcome);
    }
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Failed, "{output:?}");
    assert!(output.observation.findings.is_empty());
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "tests/red.cpp" && target.status == "ERROR"),
        "{output:?}"
    );
}

/// Failure mode: a dotted executable that only looks inside the workspace is refused, while the
/// same spelling of another tool is planned as given (`legacy_dotted_interpreter.rs`).
#[test]
fn a_dotted_executable_cannot_pass_the_workspace_containment_check() {
    let root = tempfile::tempdir().unwrap();
    let mut escaping = request(root.path());
    escaping.tool.path = root.path().join("../outside/suite");
    assert!(escaping.tool.path.starts_with(root.path()));
    assert_eq!(
        plan(&escaping).unwrap_err().to_string(),
        "validation paths must be normalized"
    );
    let mut inside = request(root.path());
    inside.tool.path = root.path().join("build/../suite");
    assert_eq!(
        plan(&inside).unwrap_err().to_string(),
        "validation paths must be normalized"
    );
}

/// A direct instrumented test of each sanitizer kind, with or without the legacy engines' scope.
fn direct_sanitizer(root: &Path, kind: ValidationKind, legacy: bool) -> ValidationRequest {
    let mut request = request(root);
    request.kind = kind;
    request.options.sanitizer_suite_kind = Some(ValidationKind::CppExecutable);
    request.options.instrumented_artifacts = vec!["suite".into()];
    request.options.legacy_unlocated_sanitizer_scope = legacy;
    request
        .options
        .auxiliary_tools
        .insert("readelf".into(), request.tool.clone());
    request
}

/// Frozen SanitizeEngine runs each direct instrumented test with `max_output_chars =
/// (MAX_SANITIZER_OUTPUT_BYTES - 1) // 2` (engines/sanitize.py:324, 1_048_576 at
/// analysis/_sanitizer_diagnostics.py:13), which ThreadSanitizeEngine inherits: 524,287 bytes per
/// stream (root ruling 2026-11-13). A native direct suite keeps 1 MiB.
#[test]
fn a_legacy_direct_sanitizer_test_keeps_the_frozen_output_bound() {
    for kind in [ValidationKind::Sanitizer, ValidationKind::ThreadSanitizer] {
        for (legacy, wanted) in [(false, 1_048_576), (true, 524_287)] {
            let root = tempfile::tempdir().unwrap();
            let planned = plan(&direct_sanitizer(root.path(), kind, legacy)).unwrap();
            let limits: Vec<(String, Option<usize>)> = planned
                .tasks
                .iter()
                .map(|task| (task.id.clone(), task.output_limit_bytes))
                .collect();
            assert_eq!(
                limits,
                [
                    (
                        "app.test0.artifact0.instrumentation".to_owned(),
                        Some(8_388_608)
                    ),
                    ("app.test0.suite".to_owned(), Some(wanted)),
                ],
                "{kind:?} legacy: {legacy}"
            );
        }
    }
}

/// Frozen `_process_incomplete` comes before the transcript is read (engines/sanitize.py:335-340):
/// a direct test whose stdout passed the bound is its `SanitizerExecution` ERROR "Sanitizer
/// execution output was truncated: <snippet>" (whitespace-joined, 300 characters) and the run is
/// incomplete (sanitize:direct-output-past-bound in validation-full-oracle.json). Positive: the
/// same 524,287 bytes complete are the clean verdict. Negative: a native direct suite keeps its
/// own incomplete verdict.
#[test]
fn a_truncated_legacy_direct_sanitizer_test_is_frozen_execution_error() {
    for kind in [ValidationKind::Sanitizer, ValidationKind::ThreadSanitizer] {
        for (legacy, truncated) in [(true, true), (true, false), (false, true)] {
            let root = tempfile::tempdir().unwrap();
            let planned = plan(&direct_sanitizer(root.path(), kind, legacy)).unwrap();
            let symbols: &[u8] = if kind == ValidationKind::ThreadSanitizer {
                b"Symbol table '.dynsym' contains 2 entries:\n 1: 0 0 FUNC GLOBAL DEFAULT UND __tsan_init\n"
            } else {
                b"Symbol table '.dynsym' contains 2 entries:\n 1: 0 0 FUNC GLOBAL DEFAULT UND __asan_init\n"
            };
            let mut outcomes = BTreeMap::new();
            for task in &planned.tasks {
                let mut outcome = TaskOutcome::empty(&task.id);
                outcome.state = TaskState::Succeeded;
                outcome.exit_code = Some(0);
                if task.id.ends_with(".suite") {
                    outcome.stdout = vec![b'a'; 524_287];
                    outcome.truncated = truncated;
                } else {
                    outcome.stdout = symbols.to_vec();
                }
                outcomes.insert(task.id.clone(), outcome);
            }
            let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
            let label = format!("{kind:?} legacy: {legacy} truncated: {truncated}");
            let execution: Vec<_> = output
                .targets
                .iter()
                .filter(|target| target.target_name == "SanitizerExecution")
                .collect();
            if legacy && truncated {
                let message = format!(
                    "Sanitizer execution output was truncated: {}",
                    "a".repeat(300)
                );
                assert_eq!(output.observation.state, TaskState::Failed, "{label}");
                assert_eq!(output.summary, message, "{label}");
                assert_eq!(execution.len(), 1, "{label}: {output:?}");
                assert_eq!(execution[0].file_path, "tests/red.cpp", "{label}");
                assert_eq!(execution[0].start_line, 1, "{label}");
                assert_eq!(execution[0].status, "ERROR", "{label}");
                assert_eq!(execution[0].message, message, "{label}");
                assert!(
                    !output
                        .targets
                        .iter()
                        .any(|target| target.target_name == "Instrumented suite"),
                    "{label}"
                );
                assert_eq!(
                    output.extra["sanitizer_test_evidence"]["targets"][0]["file_path"],
                    "tests/red.cpp",
                    "{label}"
                );
            } else if legacy {
                assert_eq!(output.observation.state, TaskState::Succeeded, "{label}");
                assert!(execution.is_empty(), "{label}");
                assert!(output.observation.findings.is_empty(), "{label}");
            } else {
                assert_eq!(output.observation.state, TaskState::Failed, "{label}");
                assert!(execution.is_empty(), "{label}: {output:?}");
                assert!(
                    output
                        .targets
                        .iter()
                        .any(|target| target.target_name == "Sanitizer execution"
                            && target.message
                                == "instrumented process did not return a complete verdict"),
                    "{label}: {output:?}"
                );
            }
        }
    }
}

/// Frozen `_process_incomplete` checks `timed_out` first (engines/sanitize.py:335-340,
/// `_incomplete_message` :895-897): a direct instrumented test still running at run_process's
/// bound (300 s DEFAULT_TIMEOUT; the legacy direct plan's `timeout_seconds`, pinned by
/// ici-cli `cpp_validation::direct::LEGACY_DIRECT_TEST_TIMEOUT_SECONDS`) is its
/// `SanitizerExecution` ERROR "Sanitizer execution timed out: <snippet of stderr or stdout>"
/// and the run is incomplete (root ruling 2026-11-14; frozen capture with the bound patched to a
/// few seconds: tests/rust-parity/validation/sanitizer-timeout-oracle.json). The bound is the
/// injectable `ValidationOptions::timeout_seconds`, set to 1 s here; a real process sleeps past
/// it under the real runner. Positive: the same script that exits at once is the clean verdict.
/// Negative: a native direct suite keeps its own incomplete verdict, with no frozen target.
#[test]
fn a_timed_out_legacy_direct_sanitizer_test_is_frozen_execution_error() {
    for kind in [ValidationKind::Sanitizer, ValidationKind::ThreadSanitizer] {
        let symbol = if kind == ValidationKind::ThreadSanitizer {
            "__tsan_init"
        } else {
            "__asan_init"
        };
        for (legacy, sleeps) in [(true, true), (true, false), (false, true)] {
            let label = format!("{kind:?} legacy: {legacy} sleeps: {sleeps}");
            let root = tempfile::tempdir().unwrap();
            let suite = root.path().join("suite");
            // The script is both the instrumentation probe's `readelf` (its `--symbols` call)
            // and the instrumented test.
            fs::write(
                &suite,
                format!(
                    "#!/bin/sh\ncase \"$1\" in --symbols) printf \"Symbol table '.dynsym' contains 2 entries:\\n 1: 0 0 FUNC GLOBAL DEFAULT UND {symbol}\\n\"; exit 0;; esac\nprintf 'test   started\\n' >&2\n{}\n",
                    if sleeps { "exec sleep 30" } else { "exit 0" }
                ),
            )
            .unwrap();
            let mut permissions = fs::metadata(&suite).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o755);
            fs::set_permissions(&suite, permissions).unwrap();
            let mut request = direct_sanitizer(root.path(), kind, legacy);
            request.tool = tool(root.path(), &suite);
            request
                .options
                .auxiliary_tools
                .insert("readelf".into(), request.tool.clone());
            request.options.timeout_seconds = 1.0;
            let planned = plan(&request).unwrap();
            let suite_id = "app.test0.suite";
            assert_eq!(
                planned
                    .tasks
                    .iter()
                    .find(|task| task.id == suite_id)
                    .and_then(|task| task.timeout_seconds),
                Some(1.0),
                "{label}"
            );
            let started = std::time::Instant::now();
            let outcomes = execute(&planned);
            assert!(started.elapsed().as_secs() < 25, "{label}: the bound held");
            assert_eq!(outcomes[suite_id].timed_out, sleeps, "{label}");
            let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
            let execution: Vec<_> = output
                .targets
                .iter()
                .filter(|target| target.target_name == "SanitizerExecution")
                .collect();
            if legacy && sleeps {
                // Frozen's run_process prefix with the bound in Python's float spelling, then the
                // stream (sanitizer-timeout-oracle.json at its patched 3.0 s bound reads
                // "...: Command timed out after 3.0s: test started").
                let message =
                    "Sanitizer execution timed out: Command timed out after 1.0s: test started";
                assert_eq!(output.observation.state, TaskState::Failed, "{label}");
                assert_eq!(output.summary, message, "{label}");
                assert_eq!(execution.len(), 1, "{label}: {output:?}");
                assert_eq!(execution[0].file_path, "tests/red.cpp", "{label}");
                assert_eq!(execution[0].start_line, 1, "{label}");
                assert_eq!(execution[0].status, "ERROR", "{label}");
                assert_eq!(execution[0].message, message, "{label}");
                assert_eq!(
                    output.extra["sanitizer_test_evidence"]["targets"][0]["file_path"],
                    "tests/red.cpp",
                    "{label}"
                );
            } else if legacy {
                assert_eq!(output.observation.state, TaskState::Succeeded, "{label}");
                assert!(execution.is_empty(), "{label}");
                assert!(output.observation.findings.is_empty(), "{label}");
            } else {
                assert_eq!(output.observation.state, TaskState::Failed, "{label}");
                assert!(execution.is_empty(), "{label}: {output:?}");
            }
        }
    }
}

/// The frozen capture of a timed-out direct sanitizer test (capture_sanitizer_timeout.py, the
/// execution bound patched to 3.0 s at runtime): native's `SanitizerExecution` message, from a
/// timed-out suite outcome holding the same stderr under the same bound, equals frozen's target
/// message and summary for both sanitizer kinds, and frozen's record is the timed-out 124.
#[test]
fn the_timed_out_direct_sanitizer_message_equals_the_frozen_capture() {
    let oracle: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/sanitizer-timeout-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert_eq!(oracle["frozen_default_timeout_seconds"], 300.0);
    let bound = oracle["patched_execution_timeout_seconds"]
        .as_f64()
        .unwrap();
    for (command, kind) in [
        ("sanitize", ValidationKind::Sanitizer),
        ("thread-sanitize", ValidationKind::ThreadSanitizer),
    ] {
        let case = &oracle["cases"][command];
        let report = &case["report"];
        assert_eq!(case["exit_code"], 1, "{command}");
        assert_eq!(report["status"], "ERROR", "{command}");
        assert_eq!(report["evidence"], "NOT_RUN", "{command}");
        let frozen: Vec<&serde_json::Value> = report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|target| target["target_name"] == "SanitizerExecution")
            .collect();
        assert_eq!(frozen.len(), 1, "{command}");
        assert_eq!(frozen[0]["file_path"], "tests/test_slow.cpp", "{command}");
        let record = report["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["name"] == "sanitizer execution")
            .unwrap();
        assert_eq!(record["returncode"], 124, "{command}");
        assert_eq!(record["timed_out"], true, "{command}");
        assert_eq!(record["error"], frozen[0]["message"], "{command}");
        let root = tempfile::tempdir().unwrap();
        let mut request = direct_sanitizer(root.path(), kind, true);
        request.options.executable_test_source = Some("tests/test_slow.cpp".into());
        request.targets = vec!["tests/test_slow.cpp".into()];
        request.source_files = vec!["tests/test_slow.cpp".into()];
        request.options.timeout_seconds = bound;
        let planned = plan(&request).unwrap();
        let symbol = if kind == ValidationKind::ThreadSanitizer {
            "__tsan_init"
        } else {
            "__asan_init"
        };
        let mut outcomes = BTreeMap::new();
        for task in &planned.tasks {
            let mut outcome = TaskOutcome::empty(&task.id);
            outcome.state = TaskState::Succeeded;
            if task.id.ends_with(".suite") {
                outcome.state = TaskState::Failed;
                outcome.timed_out = true;
                outcome.exit_code = None;
                outcome.signal = Some(9);
                outcome.stderr = b"test   started\n".to_vec();
            } else {
                outcome.exit_code = Some(0);
                outcome.stdout = format!(
                    "Symbol table '.dynsym' contains 2 entries:\n 1: 0 0 FUNC GLOBAL DEFAULT UND {symbol}\n"
                )
                .into_bytes();
            }
            outcomes.insert(task.id.clone(), outcome);
        }
        let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
        let native: Vec<_> = output
            .targets
            .iter()
            .filter(|target| target.target_name == "SanitizerExecution")
            .collect();
        assert_eq!(native.len(), 1, "{command}: {output:?}");
        assert_eq!(native[0].message, frozen[0]["message"], "{command}");
        assert_eq!(native[0].status, "ERROR", "{command}");
        assert_eq!(output.summary, report["summary"], "{command}");
    }
}
