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

const FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];

/// The capture script's normalization: project root, ASCII quotes, first line of a frozen
/// 200-character `Compilation Error:` cut.
fn normalized_targets(report: &Value, project: &Path) -> Vec<Value> {
    let root = project.display().to_string();
    let mut targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            let mut row: serde_json::Map<String, Value> = FIELDS
                .iter()
                .map(|key| ((*key).to_owned(), target[*key].clone()))
                .collect();
            let mut message = target["message"]
                .as_str()
                .unwrap()
                .replace(&root, "<ROOT>")
                .replace(['\u{2018}', '\u{2019}'], "'");
            if message.starts_with("Compilation Error: ") {
                message = message.lines().next().unwrap().to_owned();
            }
            row.insert("message".into(), Value::String(message));
            Value::Object(row)
        })
        .collect();
    targets.sort_by_key(|target| {
        (
            target["target_name"].as_str().unwrap().to_owned(),
            target["file_path"].as_str().unwrap().to_owned(),
        )
    });
    targets
}

fn compiler_only_path(directory: &Path) -> String {
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler (ICI_TEST_CXX)");
    fs::create_dir_all(directory).unwrap();
    std::os::unix::fs::symlink(&compiler, directory.join("g++")).unwrap();
    for tool in ["as", "ld", "x86_64-linux-gnu-as", "x86_64-linux-gnu-ld"] {
        let system = Path::new("/usr/bin").join(tool);
        if system.exists() {
            std::os::unix::fs::symlink(system, directory.join(tool)).unwrap();
        }
    }
    directory.display().to_string()
}

#[test]
#[ignore = "explicit actual g++/gcov direct C++ test runs (ICI_TEST_CXX)"]
fn legacy_direct_cpp_tests_report_frozen_targets_counts_and_verdicts() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/test-cli-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut seen = 0;
    for (name, expected) in oracle["cases"].as_object().unwrap() {
        if !name.starts_with("direct-") {
            continue;
        }
        seen += 1;
        let input = &oracle["cases_input"][name];
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        let path = if input[2] == "compiler" {
            compiler_only_path(&root.path().join("bin"))
        } else {
            tool_path()
        };
        let (exit, report) = run_at(
            &project,
            input[0].as_str().unwrap(),
            "test",
            input[1].as_str().unwrap(),
            &path,
        );
        let mut actual = normalized_targets(&report, &project);
        // Intended: an empty branch denominator is a SKIP, not the frozen vacuous 100.0%.
        actual.retain(|target| target["target_name"] != "Threshold: Branch coverage");
        let mut frozen = expected["targets"].as_array().unwrap().clone();
        let only_tests = |targets: &mut Vec<Value>| {
            targets.retain(|target| {
                target["target_name"]
                    .as_str()
                    .unwrap()
                    .starts_with("[C++] ")
            })
        };
        // Known frozen defect (direct-broken): the unsorted `rglob` order decides whether the
        // re-compiled library notes still match their counters; in this capture gcov exited 5
        // and coverage vanished. Rust compiles each object once. Frozen direct-abort keeps
        // coverage next to the ERROR; Rust keeps coverage NOT_RUN once a test did not finish.
        if matches!(name.as_str(), "direct-broken" | "direct-abort") {
            only_tests(&mut actual);
            only_tests(&mut frozen);
        }
        assert_eq!(actual, frozen, "{name}: {}", report["summary"]);
        let (status, code) = match name.as_str() {
            // Frozen tool ERROR is exit 1; the legacy verdict maps an incomplete run to 3.
            "direct-abort" => ("ERROR", 3),
            // Frozen gcov exit 5 above; Rust measured coverage of the compiled tests.
            "direct-broken" => ("FAIL", 1),
            _ => (expected["status"].as_str().unwrap(), 1),
        };
        assert_eq!(report["status"], status, "{name}: {}", report["summary"]);
        assert_eq!(exit, code, "{name}");
        if !report["extra"]["total_tests"].is_null() {
            assert_eq!(
                report["extra"]["passed_tests"], expected["passed_tests"],
                "{name}"
            );
            assert_eq!(
                report["extra"]["total_tests"], expected["total_tests"],
                "{name}"
            );
        }
        if expected["evidence"] == "MEASURED" {
            assert_eq!(report["evidence"], "MEASURED", "{name}");
            assert_eq!(
                report["summary"]
                    .as_str()
                    .unwrap()
                    .replace("Branch: n/a", "Branch: 100.0%"),
                expected["summary"].as_str().unwrap(),
                "{name}"
            );
        }
    }
    assert_eq!(seen, 6);
}
