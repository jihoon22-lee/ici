//! Explicit native suite/ELF/sanitizer gate; tool absence fails when invoked.
use ici_domain::{SourceInput, TaskState};
use ici_execution::{Cancellation, Runner, TaskOutcome};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
use ici_validation::*;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
};
fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native tool {name} missing"))
}
fn tool(root: &Path, id: &str, path: PathBuf) -> ResolvedTool {
    ToolRequest {
        id: id.into(),
        explicit: Some(path),
        project_root: root.into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap()
}
fn request(root: &Path, kind: ValidationKind, executable: PathBuf, run: &str) -> ValidationRequest {
    ValidationRequest {
        kind,
        task_id: format!("component.{}", kind.provider()),
        component_id: "component".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join(run),
        tool: tool(root, kind.provider(), executable),
        targets: vec!["suite".into()],
        source_files: vec!["main.cpp".into()],
        functions: vec![],
        options: ValidationOptions::default(),
    }
}
fn execute(plan: &ValidationPlan, owner: &OwnedRun) -> BTreeMap<String, TaskOutcome> {
    let mut outcomes = BTreeMap::new();
    loop {
        let ready = ready_steps(plan, &outcomes).unwrap();
        if ready.is_empty() {
            break;
        }
        for id in ready {
            let task = plan.tasks.iter().find(|task| task.id == id).unwrap();
            owner.mark_started(&id);
            outcomes.insert(id, Runner::default().run(task, &Cancellation::default()));
        }
    }
    outcomes
}
fn compile(root: &Path, source: &str, name: &str, flags: &[&str]) {
    fs::write(root.join("main.cpp"), source).unwrap();
    let output = Command::new(required("ICI_TEST_GXX"))
        .args([
            "-g",
            "-O0",
            "-fno-omit-frame-pointer",
            "main.cpp",
            "-o",
            name,
        ])
        .args(flags)
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}
#[test]
#[ignore = "explicit CTest native gate"]
fn ctest_native_pass_failure_disabled_and_empty_suites_are_distinct() {
    let root = tempfile::tempdir().unwrap();
    let build = root.path().join("build");
    fs::create_dir(&build).unwrap();
    let tests = build.join("CTestTestfile.cmake");
    fs::write(&tests, "add_test(green /usr/bin/true)\n").unwrap();
    let mut request = request(
        root.path(),
        ValidationKind::Ctest,
        required("ICI_TEST_CTEST").into(),
        "run-pass",
    );
    request.options.build_dir = Some(build.clone());
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let output = observe(
        &planned,
        &execute(&planned, &owner),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(output.observation.findings.is_empty());
    fs::write(&tests, "add_test(red /usr/bin/false)\n").unwrap();
    request.owned_run_dir = root.path().join("run-fail");
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let output = observe(
        &planned,
        &execute(&planned, &owner),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(output.observation.findings.len(), 1);
    fs::write(&tests,"add_test(disabled /usr/bin/true)\nset_tests_properties(disabled PROPERTIES DISABLED TRUE)\n").unwrap();
    request.owned_run_dir = root.path().join("run-disabled");
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    assert_eq!(
        observe(
            &planned,
            &execute(&planned, &owner),
            &CapturedArtifacts::default()
        )
        .observation
        .state,
        TaskState::Failed
    );
    fs::write(&tests, "").unwrap();
    request.owned_run_dir = root.path().join("run-empty");
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    assert_eq!(
        observe(
            &planned,
            &execute(&planned, &owner),
            &CapturedArtifacts::default()
        )
        .observation
        .state,
        TaskState::Failed
    );
}
#[test]
#[ignore = "explicit Qt5/Qt6 QTest native gate"]
fn qtest_qt5_and_qt6_native_cases_keep_failure_source_location() {
    for qmake in ["ICI_TEST_QMAKE", "ICI_TEST_QMAKE6"] {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("suite.pro"),"QT += core testlib\nQT -= gui\nCONFIG += console c++17 testcase\nSOURCES += main.cpp\nTARGET = suite\n").unwrap();
        fs::write(root.path().join("main.cpp"),"#include <QtTest>\nclass Suite: public QObject {\n Q_OBJECT\nprivate slots:\n void green(){QVERIFY(true);}\n void red(){QVERIFY(false);}\n};\nQTEST_GUILESS_MAIN(Suite)\n#include \"main.moc\"\n").unwrap();
        let configured = Command::new(required(qmake))
            .arg("suite.pro")
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            configured.status.success(),
            "{}",
            String::from_utf8_lossy(&configured.stderr)
        );
        let built = Command::new(required("ICI_TEST_MAKE"))
            .arg("-j2")
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            built.status.success(),
            "{}",
            String::from_utf8_lossy(&built.stderr)
        );
        let request = request(
            root.path(),
            ValidationKind::Qtest,
            root.path().join("suite"),
            "run-qt",
        );
        let planned = plan(&request).unwrap();
        let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
        let output = observe(
            &planned,
            &execute(&planned, &owner),
            &CapturedArtifacts::default(),
        );
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{qmake}: {output:?}"
        );
        assert_eq!(output.observation.findings.len(), 1);
        assert_eq!(
            output.observation.findings[0].primary_location.path,
            "main.cpp"
        );
        assert_eq!(
            output.observation.findings[0].primary_location.start_line,
            6
        );
        assert_eq!(output.observation.measurements[0].denominator, Some(4));
    }
}
#[test]
#[ignore = "explicit native readelf/ELF ABI gate"]
fn real_elf_loader_policy_detects_absolute_rpath_and_keeps_abi_facts() {
    let root = tempfile::tempdir().unwrap();
    compile(
        root.path(),
        "int main(){return 0;}\n",
        "suite",
        &["-Wl,-rpath,/tmp/ici-forbidden-loader"],
    );
    let mut request = request(
        root.path(),
        ValidationKind::BinaryCompatibility,
        required("ICI_TEST_READELF").into(),
        "run-elf",
    );
    request.options.elf_policy.expected_class = "ELF64".into();
    let mut planned = plan(&request).unwrap();
    capture_binary_headers(&mut planned).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let output = observe(
        &planned,
        &execute(&planned, &owner),
        &CapturedArtifacts::default(),
    );
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id.contains("rpath"))
    );
    assert_eq!(output.extra["artifacts"]["suite"]["elf_class"], "ELF64");
}
fn instrumented(root: &Path, kind: ValidationKind) -> ValidationPlan {
    let mut request = request(root, kind, root.join("suite"), "run-sanitizer");
    request.options.auxiliary_tools.insert(
        "readelf".into(),
        tool(root, "readelf", required("ICI_TEST_READELF").into()),
    );
    let mut planned = plan(&request).unwrap();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[SourceInput {
            path: "main.cpp".into(),
            bytes: fs::read(root.join("main.cpp")).unwrap(),
        }]),
    );
    planned
}
#[test]
#[ignore = "explicit ASan/UBSan instrumented native gate"]
fn actual_address_and_undefined_sanitizers_report_defects_after_instrumentation_proof() {
    for (flag, source, rule) in [
        (
            "-fsanitize=address",
            "int main(){int* value=new int(3);delete value;return *value;}\n",
            "ici.sanitize.asan.heap-use-after-free",
        ),
        (
            "-fsanitize=undefined",
            "#include <climits>\nint main(){volatile int value=INT_MAX;return value+1;}\n",
            "ici.sanitize.ubsan.signed-integer-overflow",
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        compile(root.path(), source, "suite", &[flag]);
        let planned = instrumented(root.path(), ValidationKind::Sanitizer);
        let owner = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
        let outcomes = execute(&planned, &owner);
        let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{flag}: {output:?}; outcomes={outcomes:?}"
        );
        assert!(
            output
                .observation
                .findings
                .iter()
                .any(|finding| finding.rule_id == rule),
            "{flag}: {output:?}"
        );
        assert_eq!(
            output.observation.findings[0].primary_location.path,
            "main.cpp"
        );
    }
}
#[test]
#[ignore = "explicit TSan instrumented native gate"]
fn actual_thread_sanitizer_reports_data_race() {
    let root = tempfile::tempdir().unwrap();
    compile(
        root.path(),
        "#include <thread>\nint shared=0;\nint main(){std::thread a([]{shared=1;});std::thread b([]{shared=2;});a.join();b.join();return 0;}\n",
        "suite",
        &["-fsanitize=thread", "-pthread"],
    );
    let planned = instrumented(root.path(), ValidationKind::ThreadSanitizer);
    let owner = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    let outcomes = execute(&planned, &owner);
    let output = observe(&planned, &outcomes, &CapturedArtifacts::default());
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{output:?}; outcomes={outcomes:?}"
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.sanitize.tsan.data-race"),
        "{output:?}"
    );
}
#[test]
#[ignore = "explicit non-instrumented negative gate"]
fn uninstrumented_binary_is_rejected_before_running_user_code() {
    let root = tempfile::tempdir().unwrap();
    compile(
        root.path(),
        "#include <fstream>\nint main(){std::ofstream(\"ran\")<<\"bad\";return 0;}\n",
        "suite",
        &[],
    );
    let planned = instrumented(root.path(), ValidationKind::Sanitizer);
    let owner = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    owner.mark_started(&planned.tasks[0].id);
    let probe = Runner::default().run(&planned.tasks[0], &Cancellation::default());
    let outcomes = BTreeMap::from([(probe.task_id.clone(), probe)]);
    assert!(ready_steps(&planned, &outcomes).is_err());
    assert!(!root.path().join("ran").exists());
    assert_eq!(
        observe(&planned, &outcomes, &CapturedArtifacts::default())
            .observation
            .state,
        TaskState::Failed
    );
}
#[test]
#[ignore = "explicit integration process contract gate"]
fn real_integration_process_checks_exit_streams_and_output_freshness() {
    let root = tempfile::tempdir().unwrap();
    compile(
        root.path(),
        "#include <fstream>\n#include <iostream>\nint main(){std::ofstream(\"output.bin\")<<\"evidence\";std::cout<<\"ready\\n\";std::cerr<<\"diagnostic\\n\";return 3;}\n",
        "suite",
        &[],
    );
    let mut request = request(
        root.path(),
        ValidationKind::Integration,
        root.path().join("suite"),
        "run-integration",
    );
    request.options.argv = vec![request.tool.path.to_string_lossy().into_owned()];
    request.options.expected_exit = 3;
    request.options.stdout_contains = vec!["ready".into()];
    request.options.stderr_contains = vec!["diagnostic".into()];
    request.options.outputs = vec![OutputAssertion {
        path: "output.bin".into(),
        min_size: 8,
    }];
    let planned = plan(&request).unwrap();
    let owner = OwnedRun::create(&request.owned_run_dir).unwrap();
    let outcomes = execute(&planned, &owner);
    let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    let output = observe(&planned, &outcomes, &artifacts);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(output.observation.findings.is_empty(), "{output:?}");
}
#[test]
#[ignore = "explicit native GCC/gcov instrumented coverage gate"]
fn gcov_reads_one_fresh_instrumented_ctest_run_and_refuses_stale_data() {
    let root = tempfile::tempdir().unwrap();
    compile(
        root.path(),
        "int never(){return 4;}\nint branch(int value){if(value)return 1;return 0;}\n/*😀*/int main(){return branch(1)==1?0:1;}\n",
        "suite",
        &["--coverage"],
    );
    fs::write(
        root.path().join("CTestTestfile.cmake"),
        format!(
            "add_test(suite \"{}\")\n",
            root.path().join("suite").display()
        ),
    )
    .unwrap();
    let mut test = request(
        root.path(),
        ValidationKind::Ctest,
        required("ICI_TEST_CTEST").into(),
        "run-gcov",
    );
    test.options.build_dir = Some(root.path().into());
    let test_plan = plan(&test).unwrap();
    let owner = OwnedRun::create(&test.owned_run_dir).unwrap();
    let mut outcomes = execute(&test_plan, &owner);
    let notes = fs::read_dir(root.path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "gcno")
        })
        .collect::<Vec<_>>();
    assert_eq!(notes.len(), 1);
    let mut coverage = request(
        root.path(),
        ValidationKind::Gcov,
        required("ICI_TEST_GCOV").into(),
        "run-gcov",
    );
    coverage.targets = notes
        .iter()
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect();
    coverage.options.shared_test_task_id = Some(test_plan.tasks[0].id.clone());
    coverage.options.shared_test_kind = Some(ValidationKind::Ctest);
    let mut planned = plan(&coverage).unwrap();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[SourceInput {
            path: "main.cpp".into(),
            bytes: fs::read(root.path().join("main.cpp")).unwrap(),
        }]),
    );
    for id in ready_steps(&planned, &outcomes).unwrap() {
        let task = planned.tasks.iter().find(|task| task.id == id).unwrap();
        owner.mark_started(&id);
        outcomes.insert(id, Runner::default().run(task, &Cancellation::default()));
    }
    let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    let output = observe(&planned, &outcomes, &artifacts);
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{output:?}; outcomes={outcomes:?}; artifacts={artifacts:?}"
    );
    let functions = output
        .observation
        .measurements
        .iter()
        .find(|measurement| measurement.name == "coverage.cpp.functions")
        .unwrap();
    assert_eq!(
        (functions.numerator, functions.denominator),
        (Some(2), Some(3)),
        "{output:?}"
    );
    let main = output.extra["coverage"]["function_rows"]
        .as_array()
        .unwrap()
        .iter()
        .find(|function| function["symbol"] == "main")
        .unwrap();
    assert_eq!(main["start_column"], 10);
    assert_eq!(main["native_columns"]["start"], 13);
    assert_eq!(output.score, Some(2.78));
    if let Ok(destination) = std::env::var("ICI_CAPTURE_GCOV_PATH") {
        let artifact = artifacts
            .files
            .values()
            .find(|artifact| artifact.id.starts_with("gcov-json:"))
            .unwrap();
        use std::io::Read;
        let mut decoded = String::new();
        flate2::read::GzDecoder::new(artifact.bytes.as_slice())
            .read_to_string(&mut decoded)
            .unwrap();
        let mut report: serde_json::Value = serde_json::from_str(&decoded).unwrap();
        let old_root = root.path().to_string_lossy().into_owned();
        fn normalize(value: &mut serde_json::Value, root: &str) {
            match value {
                serde_json::Value::String(text) => *text = text.replace(root, "<ROOT>"),
                serde_json::Value::Array(items) => {
                    for item in items {
                        normalize(item, root);
                    }
                }
                serde_json::Value::Object(items) => {
                    for item in items.values_mut() {
                        normalize(item, root);
                    }
                }
                _ => {}
            }
        }
        normalize(&mut report, &old_root);
        fs::write(destination, serde_json::to_vec_pretty(&report).unwrap()).unwrap();
    }
    let data = notes[0].with_extension("gcda");
    fs::File::options()
        .write(true)
        .open(data)
        .unwrap()
        .set_times(fs::FileTimes::new().set_modified(std::time::UNIX_EPOCH))
        .unwrap();
    let stale = capture_artifacts(&planned, &owner, &outcomes).unwrap();
    assert_eq!(
        observe(&planned, &outcomes, &stale).observation.state,
        TaskState::Failed
    );
}
