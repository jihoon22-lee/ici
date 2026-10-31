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
