//! Legacy `ici test` for C++ projects with real tests (direct g++ and adapter builds), against
//! the frozen CLI oracle (`tests/rust-parity/cpp/capture_test_cli.py`).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn tool_path() -> String {
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler (ICI_TEST_CXX)");
    format!(
        "{}:/usr/bin:/bin",
        Path::new(&compiler).parent().unwrap().display()
    )
}

/// Runs `ici <command> --report` on a copy of `parity-fixtures/cpp/<fixture>` placed at
/// `project` and returns (exit code, report).
fn run_at(project: &Path, fixture: &str, command: &str, config: &str, path: &str) -> (i32, Value) {
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(fixture),
        project,
    );
    fs::write(
        project.join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), path.to_string())]);
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(project.join(format!("{}_report.json", command.replace('-', "_")))).unwrap(),
    )
    .unwrap();
    (output.exit_code, report)
}

#[test]
#[ignore = "explicit actual g++/gcov direct test and coverage run (ICI_TEST_CXX)"]
fn direct_coverage_completes_for_a_project_deep_below_the_filesystem_root() {
    // gcov `-p` used to mangle the whole absolute notes path into each report name; ~100
    // characters of project depth pushed the name over NAME_MAX and gcov exited 6.
    let root = tempfile::tempdir().unwrap();
    let project = root
        .path()
        .join("d".repeat(60))
        .join("e".repeat(60))
        .join("project");
    let (_, report) = run_at(&project, "test-direct/mixed", "test", "", &tool_path());
    let coverage = report["extra"]["native_checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "cpp.coverage")
        .expect("coverage check");
    assert_eq!(coverage["state"], "SUCCEEDED", "{coverage}");
    assert_eq!(report["evidence"], "MEASURED", "{}", report["summary"]);
    assert_eq!(
        report["extra"]["line_coverage"], 100.0,
        "{}",
        report["summary"]
    );
}
