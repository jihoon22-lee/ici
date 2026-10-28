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
