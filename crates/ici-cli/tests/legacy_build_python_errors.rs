//! Frozen BuildEngine `_package_python` error branches through legacy `ici verify` and `ici build`
//! (`tests/rust-parity/validation/capture_build_python_errors.py`). A Python artifact collision,
//! a malformed entrypoint, an unsafe launcher name and a missing entrypoint module are frozen
//! `_record_error` BuildErrors (ERROR, NOT_RUN, exit 1 under `ici build`); a symlinked source is
//! never selected, and a valid entrypoint packages the library and launcher.
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/build-python-errors-oracle.json");

fn project(frozen: &Value) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path();
    fs::write(dir.join("ici.toml"), frozen["config"].as_str().unwrap()).unwrap();
    fs::write(
        dir.join("pyproject.toml"),
        frozen["pyproject"].as_str().unwrap(),
    )
    .unwrap();
    for (name, text) in frozen["files"].as_object().unwrap() {
        fs::create_dir_all(dir.join(name).parent().unwrap()).unwrap();
        fs::write(dir.join(name), text.as_str().unwrap()).unwrap();
    }
    for (name, target) in frozen["links"].as_object().unwrap() {
        std::os::unix::fs::symlink(target.as_str().unwrap(), dir.join(name)).unwrap();
    }
    root
}

fn ici(root: &Path, args: &[&str]) -> i32 {
    Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(args)
        .current_dir(root)
        .env_clear()
        .env("HOME", root)
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap()
        .status
        .code()
        .unwrap()
}

fn outputs(root: &Path) -> Vec<String> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<String>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            out.push(path.strip_prefix(root).unwrap().display().to_string());
            if path.is_dir() {
                walk(root, &path, out);
            }
        }
    }
    let mut out = vec![];
    if root.join("v2.3.4").exists() {
        walk(root, &root.join("v2.3.4"), &mut out);
    }
    out.sort();
    out
}

struct Native {
    code: i32,
    build_code: i32,
    suite: Value,
    engine: Value,
    outputs: Vec<String>,
}

fn native(frozen: &Value) -> Native {
    let root = project(frozen);
    let code = ici(root.path(), &["verify", "--report", "--no-cache"]);
    let text = fs::read_to_string(root.path().join("verify_report.json")).unwrap();
    let canonical = root.path().canonicalize().unwrap().display().to_string();
    let report: Value = serde_json::from_str(&text.replace(&canonical, "<ROOT>")).unwrap();
    let outputs = outputs(root.path());
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "build")
        .unwrap()
        .clone();
    let fresh = project(frozen);
    let build_code = ici(fresh.path(), &["build"]);
    Native {
        code,
        build_code,
        suite: report["suite_status"].clone(),
        engine,
        outputs,
    }
}

fn targets(engine: &Value) -> Vec<Value> {
    engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!([
                target["file_path"],
                target["start_line"],
                target["target_name"],
                target["status"],
                target["message"]
            ])
        })
        .collect()
}

fn adapted(engine: &Value) -> Vec<Value> {
    engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["rule_id"] == "ici.legacy.build.target")
        .map(|finding| {
            json!([
                finding["fingerprint"],
                finding["message"],
                finding["severity"]
            ])
        })
        .collect()
}

#[test]
fn python_packaging_refusals_are_the_frozen_build_errors() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in [
        "collision",
        "invalid_entrypoint",
        "unsafe_launcher",
        "missing_module",
    ] {
        let frozen = &oracle["runs"][scenario];
        let native = native(frozen);
        assert_eq!(
            i64::from(native.code),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}"
        );
        assert_eq!(
            i64::from(native.build_code),
            frozen["build_exit_code"].as_i64().unwrap(),
            "{scenario}: ici build"
        );
        assert_eq!(native.suite, frozen["suite_status"], "{scenario}");
        for key in ["status", "summary", "evidence", "required"] {
            assert_eq!(
                native.engine[key], frozen["build"][key],
                "{scenario}: {key}"
            );
        }
        // Intended difference: native refuses the whole Python package before writing it, so
        // the frozen partial `lib/` copies (PythonLibrary PASS targets) and tree do not exist.
        let expected: Vec<Value> = targets(&frozen["build"])
            .into_iter()
            .filter(|target| target[2] != "PythonLibrary")
            .collect();
        assert_eq!(targets(&native.engine), expected, "{scenario}: targets");
        assert!(
            native.outputs.is_empty(),
            "{scenario}: {:?}",
            native.outputs
        );
        let frozen_adapted: Vec<Value> = adapted(&frozen["build"])
            .into_iter()
            .filter(|finding| !finding[1].as_str().unwrap().starts_with("Packaged Python"))
            .collect();
        assert_eq!(
            adapted(&native.engine),
            frozen_adapted,
            "{scenario}: legacy findings"
        );
    }
}

#[test]
fn symlinked_sources_are_never_selected_and_a_valid_entrypoint_packages_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in ["symlinked_source", "launcher_ok"] {
        let frozen = &oracle["runs"][scenario];
        let native = native(frozen);
        assert_eq!(
            i64::from(native.code),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}"
        );
        assert_eq!(
            i64::from(native.build_code),
            frozen["build_exit_code"].as_i64().unwrap(),
            "{scenario}: ici build"
        );
        for key in ["status", "summary", "evidence", "required"] {
            assert_eq!(
                native.engine[key], frozen["build"][key],
                "{scenario}: {key}"
            );
        }
        assert_eq!(
            targets(&native.engine),
            targets(&frozen["build"]),
            "{scenario}"
        );
        assert_eq!(
            adapted(&native.engine),
            adapted(&frozen["build"]),
            "{scenario}"
        );
        assert_eq!(
            json!(native.outputs),
            frozen["outputs"],
            "{scenario}: release tree"
        );
    }
}
