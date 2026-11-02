//! Legacy `ici verify` compiler include-trace process errors (frozen
//! `_cpp_include_graph._trace_process_error` and `_consume_successful_trace`) against the frozen
//! v0.13.0 run in `tests/rust-parity/cpp/include-trace-errors-oracle.json`
//! (`capture_include_trace_errors.py`): a fake `g++` driver first on PATH gives each unit of
//! parity-fixtures/cpp/include-trace-errors one branch. The units include nothing, so the real
//! `g++ -H` trace of ok.cpp and diag.cpp is empty whatever the host g++ version; the test needs
//! only /usr/bin/g++ and runs on CI.
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

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// Positive: ok.cpp traces cleanly and stays a resolved unit.
/// Failure: unexpected stdout, a signal-terminated driver, stderr over the frozen 1,000,000
/// character capture, a trace jumping over a parent depth and a trace followed by a non-trace
/// diagnostic each give the frozen `CppIncludeTraceError` sentence, tool error and counters.
/// Negative: the other units' errors do not stop ok.cpp's trace (frozen keeps going per unit).
#[test]
fn legacy_include_trace_process_errors_match_frozen_wordings_and_counters() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-trace-errors-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let expected = &oracle["case"];
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/include-trace-errors"),
        &root,
    );
    let bin = base.join("bin");
    fs::create_dir(&bin).unwrap();
    fs::write(bin.join("g++"), oracle["fake"].as_str().unwrap()).unwrap();
    fs::set_permissions(bin.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", bin.display()),
        ),
        ("HOME".to_owned(), home.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse([
        "ici",
        "verify",
        "--profile",
        "deep",
        "--no-cache",
        "--report",
    ])
    .unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "<root>")
        .replace(&bin.display().to_string(), "<bin>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "cycle")
        .unwrap();
    let mut findings: Vec<Value> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            json!({"rule_id": finding["rule_id"], "severity": finding["severity"],
                "message": finding["message"], "path": finding["primary_location"]["path"]})
        })
        .collect();
    findings.sort_by_key(|item| (item["path"].to_string(), item["message"].to_string()));
    let actual = json!({
        "exit_code": output.exit_code,
        "suite_status": report["suite_status"],
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "extra": pick(
            &engine["extra"],
            &expected["extra"].as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>(),
        ),
        "targets": engine["targets"].as_array().unwrap().iter()
            .map(|target| pick(target, &["target_name", "status", "message", "file_path", "start_line"]))
            .collect::<Vec<_>>(),
        "findings": findings,
        "tool_evidence": engine["tool_evidence"].as_array().unwrap().iter()
            .map(|item| pick(item, &["name", "returncode", "error"]))
            .collect::<Vec<_>>(),
    });
    for message in [
        "Compiler include trace emitted unexpected stdout: src/stdout.cpp",
        "Compiler include trace terminated unexpectedly: src/sig.cpp",
        "Compiler include trace was truncated: src/big.cpp",
        "Compiler include trace contained unexpected diagnostics: src/diag.cpp",
    ] {
        assert!(
            expected["targets"]
                .as_array()
                .unwrap()
                .iter()
                .any(|target| target["message"] == message),
            "the frozen capture lost {message}"
        );
    }
    assert_eq!(
        actual, *expected,
        "native: {actual:#}\nfrozen: {expected:#}"
    );
}
