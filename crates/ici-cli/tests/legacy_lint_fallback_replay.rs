//! Legacy C++ lint fallback with a project-contained `g++` first on PATH against frozen v0.13.0
//! (`tests/rust-parity/cpp/lint-fallback-replay-oracle.json`, `capture_lint_fallback_replay.py`):
//! frozen `_run_fallback` turns every rejected replay into a `C++SyntaxReplayError` and an engine
//! error (`C++ fallback project-compiler-rejected: <src>: ...`, ERROR NOT_RUN, exit 1) where the
//! native plan used to block the check (exit 3). Compared for `ici lint` and `ici verify`.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

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
#[ignore = "explicit actual /usr/bin/g++ behind the project wrapper's capability probe (ICI_TEST_GXX)"]
fn legacy_lint_fallback_rejects_a_project_contained_compiler_per_source_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-fallback-replay-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/lint-fallback-replay")
        .canonicalize()
        .unwrap();
    for (command, expected) in oracle["cases"].as_object().unwrap() {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&fixture, &root);
        fs::create_dir(root.join("bin")).unwrap();
        let wrapper = root.join("bin/g++");
        fs::write(&wrapper, oracle["wrapper"].as_str().unwrap()).unwrap();
        fs::set_permissions(&wrapper, fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            (
                "PATH".to_owned(),
                format!("{}:/usr/bin:/bin", root.join("bin").display()),
            ),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse(["ici", command.as_str(), "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let report: Value = serde_json::from_str(
            &fs::read_to_string(root.join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let engine = if command == "verify" {
            report["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == "lint")
                .unwrap()
                .clone()
        } else {
            report
        };
        let targets: Vec<Value> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({"target_name": target["target_name"], "status": target["status"],
                    "message": target["message"], "file_path": target["file_path"],
                    "start_line": target["start_line"]})
            })
            .collect();
        assert_eq!(
            json!({"exit_code": output.exit_code, "status": engine["status"],
                "evidence": engine["evidence"], "summary": engine["summary"], "targets": targets}),
            *expected,
            "{command}"
        );
    }
}
