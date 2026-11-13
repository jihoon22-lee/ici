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

/// The include-trace timeout hook is process-wide: the tests of this binary never overlap.
static HOOK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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

/// One frozen-shaped `ici verify --profile deep --no-cache --report` run over `fixture` with
/// `fake` as the first `g++` on PATH: the cycle engine's exit, statuses, summary, the frozen extra
/// keys of `expected`, targets, findings, tool evidence and the CppIncludeTrace metrics.
fn run(fixture: &str, fake: &str, expected: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(fixture),
        &root,
    );
    let bin = base.join("bin");
    fs::create_dir(&bin).unwrap();
    fs::write(bin.join("g++"), fake).unwrap();
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
    let traces: Vec<&Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| target["target_name"] == "CppIncludeTrace")
        .collect();
    json!({
        "trace_metrics": traces.iter().map(|target| json!({
            "file_path": target["file_path"],
            "resolved_edges": target["metrics"]["resolved_edges"],
            "resolution": target["metrics"]["resolution"],
        })).collect::<Vec<_>>(),
        "trace_configurations": traces.iter()
            .map(|target| target["metrics"]["configuration"].to_string())
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
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
    })
}

/// Positive: ok.cpp traces cleanly and stays a resolved unit.
/// Failure: unexpected stdout, a signal-terminated driver, stderr over the frozen 1,000,000
/// character capture, a trace jumping over a parent depth and a trace followed by a non-trace
/// diagnostic each give the frozen `CppIncludeTraceError` sentence, tool error and counters.
/// Negative: the other units' errors do not stop ok.cpp's trace (frozen keeps going per unit).
#[test]
fn legacy_include_trace_process_errors_match_frozen_wordings_and_counters() {
    let _hook = HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-trace-errors-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let expected = &oracle["case"];
    let mut actual = run(
        "include-trace-errors",
        oracle["fake"].as_str().unwrap(),
        expected,
    );
    // The first capture predates the kept trace metrics (compared in the branches test).
    if expected.get("trace_metrics").is_none() {
        let object = actual.as_object_mut().unwrap();
        object.remove("trace_metrics");
        object.remove("trace_configurations");
    }
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

/// Positive: a clang `'<header>' file not found` stop is a located CppIncludeUnresolved warning, a
/// trace entry naming a directory is an edge (frozen `resolve(strict=True)` accepts it), and one
/// source under two configurations gives one CppIncludeTrace per configuration with its own
/// resolved edge (`resolved_edges` 1 each, three distinct trace configurations in the run).
/// Failure: a missing-header line over ten digits and a missing header beside an unrecognized
/// line fail with the exit code; a trace deeper than 4,096 levels and 200,001 trailer entries
/// exceed the bounded shape; a stale and a backslash path are stale; a path starting with a space
/// and a repeated trailer header are unexpected diagnostics.
/// Negative: none of these stops the other units (frozen evaluates each unit on its own).
#[test]
fn legacy_include_trace_parser_branches_match_frozen() {
    let _hook = HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-trace-errors-oracle.json"
    ))
    .unwrap();
    let expected = &oracle["branches"]["case"];
    for (source, message) in [
        (
            "src/clang.cpp",
            "Compiler could not resolve active include \"clang_missing.h\"; no dependency edge was recorded",
        ),
        (
            "src/depth.cpp",
            "Compiler include trace was not parseable: src/depth.cpp: compiler include trace exceeds its bounded shape",
        ),
        (
            "src/backslash.cpp",
            "Compiler include trace was not parseable: src/backslash.cpp: compiler include trace references a stale path",
        ),
        (
            "src/space.cpp",
            "Compiler include trace contained unexpected diagnostics: src/space.cpp",
        ),
        (
            "src/hugeline.cpp",
            "Compiler include trace failed with exit code 1: src/hugeline.cpp",
        ),
    ] {
        assert!(
            expected["targets"]
                .as_array()
                .unwrap()
                .iter()
                .any(|target| { target["file_path"] == source && target["message"] == message }),
            "the frozen capture lost {source}: {message}"
        );
    }
    assert_eq!(expected["trace_configurations"], 3);
    let actual = run(
        "include-trace-branches",
        oracle["branches"]["fake"].as_str().unwrap(),
        expected,
    );
    assert_eq!(
        actual, *expected,
        "native: {actual:#}\nfrozen: {expected:#}"
    );
}

/// Failure: a trace that outlives its bound is frozen's 'Compiler include trace timed out: <src>'
/// ERROR with tool error and returncode 124. Frozen's bound is `run_process`'s 300 s default; the
/// capture replaced it with `seconds` for the include traces only (oracle tree untouched) and the
/// native run sets the same bound through its process-local test hook (root ruling 2026-11-13).
/// Negative: ok.cpp, traced under the same bound, still completes.
#[test]
fn legacy_include_trace_timeout_matches_frozen() {
    let _hook = HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-trace-errors-oracle.json"
    ))
    .unwrap();
    let expected = &oracle["timeout"]["case"];
    assert!(
        expected["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| {
                target["message"] == "Compiler include trace timed out: src/slow.cpp"
            }),
        "the frozen capture lost the timeout"
    );
    /// Restores the 300 s bound even when the comparison below fails.
    struct Reset;
    impl Drop for Reset {
        fn drop(&mut self) {
            ici_cli::cpp::set_legacy_include_trace_timeout_for_tests(None);
        }
    }
    let _reset = Reset;
    ici_cli::cpp::set_legacy_include_trace_timeout_for_tests(Some(
        std::time::Duration::from_secs_f64(oracle["timeout"]["seconds"].as_f64().unwrap()),
    ));
    let actual = run(
        "include-trace-timeout",
        oracle["timeout"]["fake"].as_str().unwrap(),
        expected,
    );
    ici_cli::cpp::set_legacy_include_trace_timeout_for_tests(None);
    assert_eq!(
        actual, *expected,
        "native: {actual:#}\nfrozen: {expected:#}"
    );
}

/// Failure (native only; frozen v0.13.0 crashes here with an uncaught `canonical_project_path`
/// ValueError and writes no report): a missing header reported in a file outside the project is
/// not a located unresolved include. Root ruling 2026-11-13 accepts native's
/// 'Compiler include trace failed with exit code 1: <src>' provided it is fail-closed: the unit
/// is one `CppIncludeTraceError` ERROR, nothing about it passes, and the cycle engine is ERROR.
/// Negative: ok.cpp still traces.
#[test]
fn legacy_include_trace_missing_header_outside_the_project_fails_closed() {
    let _hook = HOOK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-trace-errors-oracle.json"
    ))
    .unwrap();
    let fake = "#!/bin/sh\ncase \"$*\" in\n  *--version*) exec /usr/bin/g++ \"$@\" ;;\n  *slow.cpp*) printf '/nonexistent-ici/ext.h:3:10: fatal error: ext_missing.h: No such file or directory\\ncompilation terminated.\\n' >&2; exit 1 ;;\nesac\nexec /usr/bin/g++ \"$@\"\n";
    let actual = run("include-trace-timeout", fake, &oracle["timeout"]["case"]);
    let targets = actual["targets"].as_array().unwrap();
    let slow: Vec<&Value> = targets
        .iter()
        .filter(|target| target["file_path"] == "src/slow.cpp")
        .collect();
    assert_eq!(slow.len(), 1, "{actual:#}");
    assert_eq!(slow[0]["target_name"], "CppIncludeTraceError", "{actual:#}");
    assert_eq!(slow[0]["status"], "ERROR", "{actual:#}");
    assert_eq!(
        slow[0]["message"],
        "Compiler include trace failed with exit code 1: src/slow.cpp"
    );
    assert!(
        targets
            .iter()
            .all(|target| target["target_name"] != "CppIncludeUnresolved"),
        "{actual:#}"
    );
    assert!(
        targets
            .iter()
            .any(|target| target["file_path"] == "src/ok.cpp"
                && target["target_name"] == "CppIncludeTrace"
                && target["status"] == "PASS"),
        "{actual:#}"
    );
    assert_eq!(actual["status"], "ERROR", "{actual:#}");
}
