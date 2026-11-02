//! Legacy `ici cognitive` / `ici complexity` over a C++ source that is not UTF-8 against frozen
//! v0.13.0 (`tests/rust-parity/cpp/metric-sources-oracle.json`, `capture_metric_sources.py`):
//! frozen reads every source strictly first, so cognitive keeps only a
//! `CppCognitiveSourceError` per unreadable source ("Cognitive complexity analysis did not
//! complete") and complexity one `CppComplexityAnalysisError` at the project root with no
//! function, both ERROR NOT_RUN; dup's source intake reports only the first such source
//! (`SourceInputError` 'invalid-utf8: Analysis source is not valid UTF-8'). The `unterminated` frozen cases (brace-heuristic spans) are an
//! open difference recorded on `_cpp_cognitive.py:649`, not replayed here. The `empty` tree (a
//! C++ project with no source) replays frozen's not-applicable `ici exception` / `ici type`.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

#[test]
fn legacy_cpp_metrics_stop_at_a_non_utf8_source_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    for command in ["cognitive", "complexity", "dup"] {
        let expected = &oracle["cases"][format!("{command}:unreadable")];
        let (_, mut actual) = run(&oracle, "unreadable", command);
        // Accepted policy (root decision, reporting command:ici verify): a required check whose
        // evidence did not complete exits 3 natively where frozen exited 1 (complexity and dup
        // are required by default, cognitive is not).
        if command != "cognitive" {
            assert_eq!(actual["exit_code"], 3, "{command}");
            actual["exit_code"] = expected["exit_code"].clone();
        }
        assert_eq!(actual, *expected, "{command}");
    }
}

fn run(oracle: &Value, tree: &str, command: &str) -> (tempfile::TempDir, Value) {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/metric-sources")
        .canonicalize()
        .unwrap();
    {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&fixtures.join(tree), &root);
        fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let report: Value = serde_json::from_str(
            &fs::read_to_string(root.join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let targets: Vec<Value> = report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({"target_name": target["target_name"], "status": target["status"],
                    "message": target["message"], "file_path": target["file_path"],
                    "start_line": target["start_line"], "end_line": target["end_line"]})
            })
            .collect();
        let actual = json!({"exit_code": output.exit_code, "status": report["status"],
            "evidence": report["evidence"], "summary": report["summary"], "targets": targets});
        (work, actual)
    }
}

#[test]
fn legacy_cpp_exception_and_type_without_sources_skip_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-sources-oracle.json"
    ))
    .unwrap();
    for command in ["exception", "type"] {
        let (_, actual) = run(&oracle, "empty", command);
        assert_eq!(
            actual,
            oracle["cases"][format!("{command}:empty")],
            "{command}"
        );
    }
}
