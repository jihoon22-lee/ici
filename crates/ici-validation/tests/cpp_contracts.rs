use ici_domain::{SourceInput, TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use std::{collections::BTreeMap, path::Path};
fn request(root: &Path, kind: ValidationKind) -> ValidationRequest {
    ValidationRequest {
        kind,
        task_id: format!("component.{}", kind.provider()),
        component_id: "component".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
        tool: ResolvedTool {
            id: "native".into(),
            path: "/tools/native".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["suite".into()],
        source_files: vec!["src/suite.cpp".into()],
        functions: vec![],
        options: ValidationOptions {
            build_dir: Some(root.join("build")),
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
#[test]
fn ctest_partial_skipped_and_missing_terminal_counts_are_distinct_from_failures() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::Ctest)).unwrap();
    let id = planned.tasks[0].id.clone();
    for (text, exit, state, findings) in [
        (
            "1/1 Test #1: suite ........ Passed 0.01 sec\n100% tests passed, 0 tests failed out of 1\n",
            0,
            TaskState::Succeeded,
            0,
        ),
        (
            "1/1 Test #1: suite ........ ***Failed 0.01 sec\n0% tests passed, 1 tests failed out of 1\n",
            8,
            TaskState::Succeeded,
            1,
        ),
        (
            "1/1 Test #1: suite ........ ***Not Run (Disabled) 0.00 sec\n0% tests passed, 1 tests failed out of 1\n",
            0,
            TaskState::Failed,
            0,
        ),
        ("No tests were found!!!", 0, TaskState::Failed, 0),
        (
            "1/1 Test #1: suite ........ Passed 0.01 sec\n",
            0,
            TaskState::Failed,
            0,
        ),
    ] {
        let output = observe(
            &planned,
            &BTreeMap::from([(id.clone(), outcome(&id, text, "", exit))]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(output.observation.state, state, "{output:?}");
        assert_eq!(output.observation.findings.len(), findings);
    }
}
#[test]
fn qtest_retains_following_failure_location_and_validates_totals() {
    let root = tempfile::tempdir().unwrap();
    let planned = plan(&request(root.path(), ValidationKind::Qtest)).unwrap();
    let id = planned.tasks[0].id.clone();
    let text = "PASS   : Suite::initTestCase()\nFAIL!  : Suite::red() condition failed\n   Loc: [src/suite.cpp(42)]\nPASS   : Suite::cleanupTestCase()\nTotals: 2 passed, 1 failed, 0 skipped, 0 blacklisted, 1ms\n";
    let output = observe(
        &planned,
        &BTreeMap::from([(id.clone(), outcome(&id, text, "", 1))]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        42
    );
    assert_eq!(
        output.observation.findings[0].primary_location.path,
        "src/suite.cpp"
    );
    assert_eq!(output.observation.measurements[0].denominator, Some(3));
    let bad = text.replace("2 passed", "9 passed");
    assert_eq!(
        observe(
            &planned,
            &BTreeMap::from([(id.clone(), outcome(&id, &bad, "", 1))]),
            &CapturedArtifacts::default()
        )
        .observation
        .state,
        TaskState::Failed
    );
}
#[test]
fn sanitizer_is_instrumentation_gated_and_normalizes_bounded_owned_stacks() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::Sanitizer);
    request
        .options
        .auxiliary_tools
        .insert("readelf".into(), request.tool.clone());
    let mut planned = plan(&request).unwrap();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[SourceInput {
            path: "src/suite.cpp".into(),
            bytes: b"int main() {\n int* value = nullptr;\n return *value;\n}\n".to_vec(),
        }]),
    );
    assert_eq!(
        ready_steps(&planned, &BTreeMap::new()).unwrap(),
        vec![planned.tasks[0].id.clone()]
    );
    let bad = outcome(
        &planned.tasks[0].id,
        "Symbol table '.dynsym' contains 1 entry:\n 0 no_symbols\n",
        "",
        0,
    );
    assert!(ready_steps(&planned, &BTreeMap::from([(bad.task_id.clone(), bad)])).is_err());
    let probe = outcome(
        &planned.tasks[0].id,
        "Symbol table '.dynsym' contains 1 entry:\n 0 __asan_init\n",
        "",
        0,
    );
    let transcript = format!(
        "==123==ERROR: AddressSanitizer: heap-use-after-free on address 0x123\n    #0 0xabc in main {}/src/suite.cpp:3:9\n    #1 0xdef in external /usr/lib/c.cpp:20:1\nSUMMARY: AddressSanitizer: heap-use-after-free\n",
        root.path().display()
    );
    let suite = outcome(&planned.tasks[1].id, "", &transcript, 1);
    let output = observe(
        &planned,
        &BTreeMap::from([
            (probe.task_id.clone(), probe),
            (suite.task_id.clone(), suite),
        ]),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(
        output.observation.findings[0].rule_id,
        "ici.sanitize.asan.heap-use-after-free"
    );
    assert_eq!(
        output.observation.findings[0].primary_location.path,
        "src/suite.cpp"
    );
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        3
    );
    assert_eq!(
        output.observation.findings[0].primary_location.start_column,
        Some(9)
    );
    let huge = format!(
        "{transcript}{}",
        (0..33)
            .map(|index| format!(
                "#{index} in main {}/src/suite.cpp:3\n",
                root.path().display()
            ))
            .collect::<String>()
    );
    assert!(ici_validation::sanitizer::parse(&huge, &planned).is_err());
}
#[test]
fn integration_checks_declared_streams_exit_and_fresh_output_without_touching_user_files() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::Integration);
    request.options.argv = vec![request.tool.path.to_string_lossy().into_owned()];
    request.options.expected_exit = 3;
    request.options.stdout_contains = vec!["ready".into()];
    request.options.stderr_not_contains = vec!["token".into()];
    request.options.outputs = vec![OutputAssertion {
        path: "result.bin".into(),
        min_size: 2,
    }];
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let id = planned.tasks[0].id.clone();
    std::fs::write(root.path().join("result.bin"), b"old").unwrap();
    let outcomes = BTreeMap::from([(id.clone(), outcome(&id, "ready", "", 3))]);
    owner.mark_started(&id);
    let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    let stale = observe(&planned, &outcomes, &artifacts);
    assert_eq!(stale.observation.state, TaskState::Succeeded);
    assert_eq!(stale.observation.findings.len(), 1);
    assert_eq!(
        std::fs::read(root.path().join("result.bin")).unwrap(),
        b"old"
    );
    std::fs::write(root.path().join("result.bin"), b"new output").unwrap();
    let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    assert!(
        observe(&planned, &outcomes, &artifacts)
            .observation
            .findings
            .is_empty()
    );
    let mut bad = outcomes;
    bad.get_mut(&id).unwrap().timed_out = true;
    assert_eq!(
        observe(&planned, &bad, &artifacts).observation.state,
        TaskState::Failed
    );
}
#[test]
fn native_binary_header_policy_distinguishes_non_elf_from_tool_failure_and_honors_limits() {
    let root = tempfile::tempdir().unwrap();
    let mut request = request(root.path(), ValidationKind::BinaryCompatibility);
    std::fs::write(root.path().join("suite"), b"plain declared artifact").unwrap();
    let mut planned = plan(&request).unwrap();
    capture_binary_headers(&mut planned).unwrap();
    assert!(ready_steps(&planned, &BTreeMap::new()).unwrap().is_empty());
    let output = observe(&planned, &BTreeMap::new(), &CapturedArtifacts::default());
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.observation.findings[0].rule_id, "ici.binary.non-elf");
    planned.context.options.allow_non_elf = true;
    let output = observe(&planned, &BTreeMap::new(), &CapturedArtifacts::default());
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.targets[0].status, "SKIP");
    assert_eq!(output.extra["artifact_headers"]["suite"]["is_elf"], false);
    let owner = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    std::fs::write(root.path().join("suite"), b"changed after capture").unwrap();
    assert!(capture_artifacts(&planned, &owner, &BTreeMap::new()).is_err());
    request.options.max_artifacts = 1;
    request.targets.push("second".into());
    assert!(plan(&request).is_err());
    request.options.max_artifacts = 2;
    request.targets = vec!["suite".into(), "suite".into()];
    assert!(plan(&request).is_err());
}
