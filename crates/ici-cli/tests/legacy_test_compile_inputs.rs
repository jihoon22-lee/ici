//! The legacy C++ compile inputs against the frozen CLI
//! (`tests/rust-parity/cpp/capture_test_compile_inputs.py`, cpp36): frozen `TestEngine`
//! compiles each direct g++ test with the project include flags and the ordered
//! `project.cpp_pkg_config` flags against the compilable sources (every C/C++ source minus
//! `project.cpp_external_build_dirs`), while `complexity`, `cognitive` and `dead` keep reading
//! the full source list. Written by cpp36 and not run before the batch run.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
    "end_line",
];

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

/// Root ruling 2026-11-14 (b6(a)): a compiler excerpt frozen cut at 200 characters is
/// host-dependent past its first line, so a failed compile's message is compared by that line.
fn message(target: &Value) -> Value {
    let text = target["message"].as_str().unwrap_or_default();
    if target["status"] == "FAIL" && text.starts_with("Compilation Error: ") {
        return json!(text.lines().next().unwrap_or_default());
    }
    target["message"].clone()
}

/// The compared view of one saved report (frozen's capture view or native's report). A test
/// that never compiled carries frozen's fabricated estimate (summary, evidence, coverage), the
/// approved difference of decisions.md '실행되지 않은 legacy C++ test의 추정 coverage' (R9): its
/// exit code, status and targets stay compared.
fn view(command: &str, report: &Value, exit_code: &Value) -> Value {
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            let mut picked: serde_json::Map<String, Value> = FIELDS
                .iter()
                .map(|key| ((*key).to_owned(), target[*key].clone()))
                .collect();
            picked.insert("message".into(), message(target));
            Value::Object(picked)
        })
        .collect();
    let mut view = json!({
        "exit_code": exit_code,
        "status": report["status"],
        "targets": targets,
    });
    let unrun = command == "test" && report["status"] == "FAIL";
    if !unrun {
        view["summary"] = report["summary"].clone();
        view["evidence"] = report["evidence"].clone();
    }
    if command == "test" && !unrun {
        view["coverage_files"] = report["coverage_files"].clone();
        view["coverage"] = report["coverage"].clone();
    }
    view
}

/// Native's report in the capture's shape: the coverage file list and line/branch/function
/// coverage of the test engine's extra.
fn native_report(command: &str, report: &Value) -> Value {
    let mut shaped = report.clone();
    if command == "test" {
        let extra = &report["extra"];
        let mut files: Vec<Value> = extra["coverage_files"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|row| row["file"].clone())
            .collect();
        files.sort_by_key(|file| file.as_str().unwrap_or_default().to_owned());
        shaped["coverage_files"] = json!(files);
        shaped["coverage"] = json!({
            "line_coverage": extra["line_coverage"],
            "branch_coverage": extra["branch_coverage"],
            "function_coverage": extra["function_coverage"],
        });
    }
    shaped
}

fn run(case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &root,
    );
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(root.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    // As captured: PATH=/usr/bin:/bin, PKG_CONFIG_PATH=<project>/pc and no locale (frozen's
    // interpreter coerced the C locale and so do the stable commands, root ruling 2026-11-14
    // (b6(b))).
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), work.path().display().to_string()),
        (
            "PKG_CONFIG_PATH".to_owned(),
            root.join("pc").display().to_string(),
        ),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            work.path().display().to_string(),
        ),
    ]);
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join(format!("{command}_report.json")))
        .unwrap()
        .replace(&root.display().to_string(), "<root>");
    let report: Value = serde_json::from_str(&text).unwrap();
    view(
        command,
        &native_report(command, &report),
        &json!(output.exit_code),
    )
}

/// Positive: `cpp_external_build_dirs=['src/external']` keeps the uncompilable
/// src/external/broken.cpp out of the direct test compile (PASS, src/lib.cpp measured), and
/// `cpp_pkg_config=['icipkg']` passes that package's `-DICI_PKG_VALUE=0` to it (PASS).
/// Negative: without the key the test compile fails on broken.cpp, and an unknown package adds
/// nothing so src/lib.cpp fails to compile (both `[C++] test_ok.cpp` FAIL, exit 1); complexity,
/// cognitive and dead give the same result with and without the key (they read every source,
/// broken.cpp included). Failure: those two failing compiles.
#[test]
#[ignore = "explicit actual g++ 15.2.0, gcov and pkg-config runs, as captured (ICI_TEST_CXX=/usr/bin/g++)"]
fn legacy_cpp_compile_inputs_equal_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/test-compile-inputs-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 10);
    // Independent frozen literals: the key decides the test compile, not the source engines.
    for (name, status) in [
        ("test:direct-external", "PASS"),
        ("test:direct-external-unset", "FAIL"),
        ("test:direct-pkg-config", "PASS"),
        ("test:direct-pkg-config-missing", "FAIL"),
    ] {
        assert_eq!(cases[name]["frozen"]["status"], status, "{name}");
    }
    for command in ["complexity", "cognitive", "dead"] {
        let with = &cases[&format!("{command}:direct-external")]["frozen"];
        let without = &cases[&format!("{command}:direct-external-unset")]["frozen"];
        assert_eq!(with, without, "{command}");
    }
    let mut mismatches = vec![];
    for (name, case) in cases {
        let command = case["command"].as_str().unwrap();
        let frozen = &case["frozen"];
        let expected = view(command, frozen, &frozen["exit_code"]);
        let actual = run(case);
        if actual != expected {
            eprintln!(
                "== {name}\nfrozen: {}\nnative: {}",
                serde_json::to_string_pretty(&expected).unwrap(),
                serde_json::to_string_pretty(&actual).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
