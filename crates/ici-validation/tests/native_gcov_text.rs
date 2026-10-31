//! Explicit old/new native GCOV capability acceptance; no tool installation in product execution.
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
    std::env::var(name).unwrap_or_else(|_| panic!("explicit native tool {name} required"))
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
fn execute(
    planned: &ValidationPlan,
    owner: &OwnedRun,
    outcomes: &mut BTreeMap<String, TaskOutcome>,
) {
    loop {
        let ready = ready_steps(planned, outcomes).unwrap();
        if ready.is_empty() {
            break;
        }
        for id in ready {
            let task = planned.tasks.iter().find(|task| task.id == id).unwrap();
            owner.mark_started(planned, &id);
            outcomes.insert(id, Runner::default().run(task, &Cancellation::default()));
        }
    }
}
#[test]
#[ignore = "explicit actual GCC/gcov 7 text and modern GCC JSON capability gate"]
fn actual_old_gcc_selects_text_and_modern_gcc_selects_json_from_complete_probes() {
    for (compiler, gcov, format, version) in [
        (
            required("ICI_TEST_LEGACY_CC"),
            required("ICI_TEST_LEGACY_GCOV"),
            GcovFormat::Text,
            "7.4.0",
        ),
        (
            required("ICI_TEST_GXX"),
            required("ICI_TEST_GCOV"),
            GcovFormat::Json,
            "15.2.0",
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        let source = "int never(){return 4;}\nint branch(int value){if(value)return 1;return 0;}\nint main(){return branch(1)==1?0:1;}\n";
        fs::write(root.path().join("main.c"), source).unwrap();
        let mut command = Command::new(&compiler);
        command
            .args(["-g", "-O0", "--coverage"])
            .arg(root.path().join("main.c"))
            .arg("-o")
            .arg(root.path().join("suite"))
            .current_dir(root.path());
        if format == GcovFormat::Text
            && let Ok(library) = std::env::var("ICI_TEST_LEGACY_LIBDIR")
        {
            command.env("LD_LIBRARY_PATH", library);
        }
        let compiled = command.output().unwrap();
        assert!(
            compiled.status.success(),
            "{}",
            String::from_utf8_lossy(&compiled.stderr)
        );
        let test = ValidationRequest {
            kind: ValidationKind::CppExecutable,
            task_id: "project.cpp.test".into(),
            component_id: "project".into(),
            root: root.path().into(),
            cwd: root.path().into(),
            owned_run_dir: root.path().join("run"),
            tool: tool(root.path(), "suite", root.path().join("suite")),
            targets: vec!["main.c".into()],
            source_files: vec!["main.c".into()],
            functions: vec![],
            options: ValidationOptions {
                executable_test_source: Some("main.c".into()),
                ..Default::default()
            },
        };
        let test_plan = plan(&test).unwrap();
        let mut coverage = test.clone();
        coverage.kind = ValidationKind::Gcov;
        coverage.task_id = "project.cpp.coverage".into();
        coverage.tool = tool(root.path(), "gcov", gcov.into());
        coverage.targets = fs::read_dir(root.path())
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.extension()
                    .is_some_and(|extension| extension == "gcno")
            })
            .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(coverage.targets.len(), 1);
        coverage.options.gcov_capability_probe = true;
        coverage.options.shared_test_task_id = Some("project.cpp.test.suite".into());
        coverage.options.shared_test_kind = Some(ValidationKind::CppExecutable);
        let mut planned = plan(&coverage).unwrap();
        attach_sources(
            &mut planned,
            ici_providers::source_coordinates(&[SourceInput {
                path: "main.c".into(),
                bytes: source.as_bytes().to_vec(),
            }]),
        );
        let owner = OwnedRun::create(&test.owned_run_dir).unwrap();
        let mut outcomes = BTreeMap::new();
        execute(&test_plan, &owner, &mut outcomes);
        execute(&planned, &owner, &mut outcomes);
        let artifacts = capture_artifacts(&planned, &owner, &outcomes).unwrap();
        let output = observe(&planned, &outcomes, &artifacts);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{format:?}: {output:?};{outcomes:?};{artifacts:?}"
        );
        assert_eq!(output.extra["gcov_capability"]["version"], version);
        assert_eq!(
            output.extra["gcov_capability"]["format"],
            if format == GcovFormat::Text {
                "text"
            } else {
                "json"
            }
        );
        assert_eq!(output.extra["coverage_totals"]["stmts"], 3);
        assert_eq!(output.extra["coverage_totals"]["covered"], 2);
        assert_eq!(output.extra["coverage_totals"]["nb"], 2);
        assert_eq!(output.extra["coverage_totals"]["cb"], 1);
        assert_eq!(output.extra["coverage_totals"]["functions"], 3);
        assert_eq!(output.extra["coverage_totals"]["covered_functions"], 2);
        assert_eq!(
            outcomes.keys().filter(|id| id.ends_with(".suite")).count(),
            1
        );
        let active = if format == GcovFormat::Text {
            "project.cpp.coverage.text-report"
        } else {
            "project.cpp.coverage.report"
        };
        let inactive = if format == GcovFormat::Text {
            "project.cpp.coverage.report"
        } else {
            "project.cpp.coverage.text-report"
        };
        assert!(outcomes.contains_key(active));
        assert!(!outcomes.contains_key(inactive));
        assert_eq!(
            output.extra["coverage_provenance"]["format"],
            if format == GcovFormat::Text {
                "gcov-text"
            } else {
                "gcov-json"
            }
        );
        if format == GcovFormat::Text {
            assert!(
                output.extra["function_rows"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .all(|row| row["end_line"].is_null() && row["start_column"].is_null())
            );
            if let Ok(destination) = std::env::var("ICI_CAPTURE_GCOV_TEXT_PATH") {
                let artifact = artifacts
                    .files
                    .values()
                    .find(|artifact| artifact.id.starts_with("gcov-text:"))
                    .unwrap();
                let document = serde_json::json!({"gcov_version":version,"source":source,"help":String::from_utf8(outcomes["project.cpp.coverage.capability"].stdout.clone()).unwrap(),"version":String::from_utf8(outcomes["project.cpp.coverage.version"].stdout.clone()).unwrap(),"report":String::from_utf8(artifact.bytes.clone()).unwrap().replace(&root.path().to_string_lossy().into_owned(),"<ROOT>"),"counts":output.extra["coverage_totals"],"format":"gcov-text"});
                fs::write(destination, serde_json::to_vec_pretty(&document).unwrap()).unwrap();
            }
        }
    }
}
