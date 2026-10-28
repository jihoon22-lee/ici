//! Explicit native compiler gate. Invoke --ignored with ICI_TEST_CXX.
use ici_cli::{execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::{Cancellation, storage};
use std::{collections::BTreeMap, fs};

fn fixture() -> tempfile::TempDir {
    let directory = tempfile::tempdir().unwrap();
    fs::write(directory.path().join("main.cpp"),"#ifndef MODE\n#define MODE 0\n#endif\n#if MODE\nint choose() { return \"bad\"; }\n#else\nint choose() { return 1; }\n#endif\n").unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit native ICI_TEST_CXX");
    let rows:Vec<_>=(0..2).map(|mode|serde_json::json!({"directory":directory.path(),"file":directory.path().join("main.cpp"),"arguments":[compiler,"-std=c++17",format!("-DMODE={mode}"),"-c",directory.path().join("main.cpp"),"-o",format!("mode{mode}.o")]})).collect();
    fs::write(
        directory.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            matches!(
                check.id.as_str(),
                "cpp.line" | "cpp.compile" | "cpp.diagnostics"
            )
        ));
    }
    fs::write(directory.path().join("ici.toml"), config).unwrap();
    directory
}
#[test]
#[ignore = "explicit native C++ compiler diagnostic/configuration acceptance"]
fn actual_recorded_variants_keep_diagnostic_unit_ownership_without_python() {
    let directory = fixture();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let invocation = registry::parse(["ici", "analyze", "--events", "events.jsonl"]).unwrap();
    let preview = planning::prepare(&invocation, directory.path(), &environment, true).unwrap();
    assert!(!directory.path().join(".ici").exists());
    assert!(
        preview
            .cpp
            .diagnostics
            .contains_key("project.cpp.diagnostics"),
        "{}\n{:?}",
        preview.text(),
        preview.cpp.contexts
    );
    assert_eq!(preview.cpp.diagnostics["project.cpp.diagnostics"].len(), 2);
    assert!(
        preview.cpp.diagnostics["project.cpp.diagnostics"]
            .iter()
            .all(|provider| provider
                .task
                .argv
                .iter()
                .any(|argument| argument == "-fsyntax-only"))
    );
    let bad = preview.cpp.diagnostics["project.cpp.diagnostics"]
        .iter()
        .find(|provider| {
            provider
                .task
                .argv
                .iter()
                .any(|argument| argument == "-DMODE=1")
        })
        .unwrap()
        .context
        .analysis_unit_ids[0]
        .clone();
    let run = execute::execute(
        &invocation,
        directory.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.gate.selected,
        GateVerdict::Fail,
        "{:?}",
        run.outputs
    );
    assert_eq!(
        run.outputs["project.cpp.compile"].observation.state,
        TaskState::Succeeded
    );
    assert_eq!(run.result.findings.len(), 1, "{:?}", run.result.findings);
    let finding = &run.result.findings[0];
    assert_eq!(finding.primary_location.path, "main.cpp");
    assert_eq!(finding.primary_location.start_line, 5);
    assert_eq!(finding.task_id.as_deref(), Some("project.cpp.diagnostics"));
    assert_eq!(finding.analysis_unit_id.as_deref(), Some(bad.as_str()));
    assert!(
        run.result
            .checks
            .as_ref()
            .unwrap()
            .iter()
            .find(|check| check.check_id == "cpp.diagnostics")
            .unwrap()
            .analysis_unit_id
            .is_none()
    );
    let events = storage::read_event_stream(&directory.path().join("events.jsonl"))
        .unwrap()
        .0;
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started"
                && event
                    .task_id
                    .as_deref()
                    .is_some_and(|id| id.starts_with("project.cpp.diagnostics.unit")))
            .count(),
        2
    );
}

fn runtime_config(selected: &[&str], extra: &str) -> String {
    let mut config = String::from("schema_version=2\n");
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "cpp")
    {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            selected.contains(&check.id.as_str())
        ));
    }
    config.push_str(extra);
    config.push_str("\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['main.cpp']\nbuild='native'\n");
    config
}

#[test]
fn missing_declared_runtime_outputs_are_blocked_without_build_or_directory_creation() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    fs::write(
        root.path().join("ici.toml"),
        runtime_config(
            &["cpp.test", "cpp.coverage", "cpp.binary-compat"],
            "[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n",
        ),
    )
    .unwrap();
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let plan = planning::prepare(&invocation, root.path(), &BTreeMap::new(), false).unwrap();
    assert!(!root.path().join("build").exists());
    assert!(!root.path().join(".ici").exists());
    for check in &plan.plan.checks {
        assert!(
            matches!(check.work, ici_application::plan::Work::Blocked { .. }),
            "{}",
            plan.text()
        );
    }
}

#[test]
#[ignore = "explicit native CTest/gcov/ELF CLI acceptance"]
fn actual_existing_cmake_artifacts_feed_one_ctest_suite_gcov_and_binary_abi() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "int main(int argc,char**){if(argc>4)return 1; return 0;}\n",
    )
    .unwrap();
    fs::write(root.path().join("CMakeLists.txt"),"cmake_minimum_required(VERSION 3.16)\nproject(native LANGUAGES CXX)\nenable_testing()\nadd_executable(suite main.cpp)\ntarget_compile_options(suite PRIVATE --coverage -O0)\ntarget_link_options(suite PRIVATE --coverage)\nadd_test(NAME green COMMAND suite)\n").unwrap();
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake");
    for args in [
        vec!["-S", ".", "-B", "build"],
        vec!["--build", "build", "--parallel", "2"],
    ] {
        let output = std::process::Command::new(&cmake)
            .args(args)
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let tools = [
        ("ctest", "ICI_TEST_CTEST"),
        ("gcov", "ICI_TEST_GCOV"),
        ("readelf", "ICI_TEST_READELF"),
    ]
    .into_iter()
    .map(|(name, var)| {
        format!(
            "[tools.{name}]\npath={}\n",
            serde_json::to_string(&std::env::var(var).expect(var)).unwrap()
        )
    })
    .collect::<String>();
    fs::write(root.path().join("ici.toml"),runtime_config(&["cpp.test","cpp.coverage","cpp.artifact","cpp.binary-compat"],&format!("{tools}[engines.test]\nmin_line_cov=100.0\nmin_file_statements=1\n[builds.native]\nsystem='cmake'\ndirectory='build'\nartifacts=['suite']\n"))).unwrap();
    let invocation =
        registry::parse(["ici", "next", "verify", "--events", "events.jsonl"]).unwrap();
    let environment = BTreeMap::from([("PATH".into(), String::new())]);
    let preview = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
    assert!(!root.path().join(".ici").exists());
    assert_eq!(
        preview.validations["project.cpp.coverage"].len(),
        1,
        "{}",
        preview.text()
    );
    assert_eq!(
        preview.validations["project.cpp.coverage"][0]
            .context
            .options
            .shared_test_task_id
            .as_deref(),
        Some("project.cpp.test.suite")
    );
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    for id in [
        "project.cpp.test",
        "project.cpp.coverage",
        "project.cpp.binary-compat",
        "project.cpp.artifact",
    ] {
        assert_eq!(
            run.outputs[id].observation.state,
            TaskState::Succeeded,
            "{id}: {:?}",
            run.outputs
        );
    }
    assert!(
        run.outputs["project.cpp.coverage"]
            .targets
            .iter()
            .any(|target| target.file_path == "main.cpp")
    );
    assert!(
        run.outputs["project.cpp.binary-compat"]
            .targets
            .iter()
            .any(|target| target.file_path == "build/suite")
    );
    let events = storage::read_event_stream(&root.path().join("events.jsonl"))
        .unwrap()
        .0;
    assert_eq!(
        events
            .iter()
            .filter(|event| event.event_type == "task.started"
                && event.task_id.as_deref() == Some("project.cpp.test.suite"))
            .count(),
        1
    );
    assert!(
        !events
            .iter()
            .any(|event| event.task_id.as_deref() == Some("project.cpp.coverage.tests.suite"))
    );
}
