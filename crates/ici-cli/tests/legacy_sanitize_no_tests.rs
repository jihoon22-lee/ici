//! Legacy `ici sanitize`, `ici thread-sanitize` and deep `ici verify` on a CMake or Make project
//! whose sanitizer build succeeds but whose build system reports no test, against
//! tests/rust-parity/cpp/sanitize-no-tests-oracle.json
//! (tests/rust-parity/cpp/capture_sanitize_no_tests.py, frozen v0.13.0, two captures
//! byte-identical). Frozen `_validated_adapter_process` (engines/sanitize.py:449) records one
//! `C++Sanitizer` ERROR at the descriptor ("The build system reported no tests, so nothing ran
//! under the sanitizers", `_append_scope_error`, sanitize.py:838): engine ERROR, NOT_RUN,
//! required, one critical/low `ici.legacy.<engine>.target` finding, exit 1. Compared: the exit
//! of the standalone commands, and per engine its status, evidence, required, summary, score,
//! frozen `extra` keys, every target and every finding (rule, severity, confidence, category,
//! message, location, fingerprint, tool name). The tool evidence records are not compared:
//! native records the processes it ran (decisions.md "C++ 도구 증거는 실제 실행 기록"). The
//! deep verify exit is not compared: its test engine reports 0/0 tests with frozen's invented
//! coverage estimate, which native does not produce (R9). CMake without `enable_testing()` writes
//! no CTestTestfile.cmake: native binding refuses the suite before ctest runs and the legacy
//! projection reports frozen's scope error from that refusal (no `ctest` record).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/sanitize-no-tests-oracle.json");
const MESSAGE: &str = "The build system reported no tests, so nothing ran under the sanitizers";
/// Frozen literals, so a recapture that drifts fails here too: (case, engine, descriptor).
const EXPECTED: [(&str, &str, &str); 8] = [
    ("sanitize:cmake-no-add-test", "sanitize", "CMakeLists.txt"),
    (
        "thread-sanitize:cmake-no-add-test",
        "thread_sanitize",
        "CMakeLists.txt",
    ),
    (
        "sanitize:cmake-no-enable-testing",
        "sanitize",
        "CMakeLists.txt",
    ),
    (
        "thread-sanitize:cmake-no-enable-testing",
        "thread_sanitize",
        "CMakeLists.txt",
    ),
    ("sanitize:make-silent-test", "sanitize", "Makefile"),
    (
        "thread-sanitize:make-silent-test",
        "thread_sanitize",
        "Makefile",
    ),
    ("verify:cmake-no-add-test", "sanitize", "CMakeLists.txt"),
    (
        "verify:cmake-no-add-test",
        "thread_sanitize",
        "CMakeLists.txt",
    ),
];
const TARGET_FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
];
const FINDING_FIELDS: [&str; 8] = [
    "rule_id",
    "severity",
    "confidence",
    "category",
    "message",
    "primary_location",
    "fingerprint",
    "tool_name",
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

fn pick(value: &Value, fields: &[&str]) -> Value {
    Value::Object(
        fields
            .iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// The capture's `engine_view` without the tool evidence names.
fn engine_view(engine: &Value, frozen_extra: &Value) -> Value {
    let keys = frozen_extra.as_object().cloned().unwrap_or_default();
    let extra: serde_json::Map<String, Value> = engine["extra"]
        .as_object()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|(key, _)| keys.contains_key(key))
        .collect();
    json!({
        "status": engine["status"], "evidence": engine["evidence"],
        "required": engine["required"], "summary": engine["summary"], "score": engine["score"],
        "extra": extra,
        "targets": engine["targets"].as_array().unwrap().iter()
            .map(|target| pick(target, &TARGET_FIELDS)).collect::<Vec<_>>(),
        "findings": engine["findings"].as_array().unwrap().iter()
            .map(|finding| pick(finding, &FINDING_FIELDS)).collect::<Vec<_>>(),
    })
}

/// Run one captured case natively; `(exit, report)` with the root spelled `<ROOT>`.
fn run(oracle: &Value, case: &str) -> (i64, Value) {
    let spec = &oracle["cases"][case];
    let (tree, command, extra) = (
        spec[0].as_str().unwrap(),
        spec[1].as_str().unwrap(),
        spec[2].as_str().unwrap(),
    );
    let definition = &oracle["trees"][tree];
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    if let Some(fixture) = definition["fixture"].as_str() {
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp")
                .join(fixture),
            &root,
        );
    }
    for (relative, text) in definition["files"].as_object().into_iter().flatten() {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::write(
        root.join("ici.toml"),
        format!("{extra}{}", definition["config"].as_str().unwrap()),
    )
    .unwrap();
    for args in [
        &["init", "-q", "."][..],
        &["add", "-A"],
        &["commit", "-qm", "fixture"],
    ] {
        let status = Command::new("git")
            .args([
                "-c",
                "user.email=ici@example.invalid",
                "-c",
                "user.name=ici",
            ])
            .args(args)
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(status.success());
    }
    let root = root.canonicalize().unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    // The frozen interpreter coerces the C locale to LC_CTYPE=C.UTF-8 (PEP 538) for its tools.
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("LC_CTYPE".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            work.path().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let result = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join(format!("{}_report.json", command.replace('-', "_"))))
        .unwrap();
    let report =
        serde_json::from_str(&text.replace(&root.display().to_string(), "<ROOT>")).unwrap();
    (i64::from(result.exit_code), report)
}

fn native_engine<'a>(report: &'a Value, engine: &str) -> Option<&'a Value> {
    match report["results"].as_array() {
        Some(results) => results
            .iter()
            .find(|result| result["engine_name"] == engine),
        None => (report["engine_name"] == engine).then_some(report),
    }
}

#[test]
#[ignore = "explicit actual CMake/ctest/GNU Make/g++ runs: /usr/bin/cmake (4.2.3), make (4.4.1) and /usr/bin/g++ (15.2.0) must be the captured tools"]
fn sanitizer_adapter_without_reported_tests_equals_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cmake = Command::new("cmake").arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(cmake.stdout).unwrap().lines().next(),
        oracle["tools"]["cmake"].as_str()
    );
    assert_eq!(oracle["runs"].as_object().unwrap().len(), 7);
    let mut mismatches = vec![];
    let mut reports: BTreeMap<&str, (i64, Value)> = BTreeMap::new();
    for (case, engine, descriptor) in EXPECTED {
        let frozen = &oracle["runs"][case];
        let view = &frozen["engines"][engine];
        // Independent literals of the frozen verdict.
        assert_eq!(frozen["exit_code"], 1, "{case}: frozen exit");
        assert_eq!(view["status"], "ERROR", "{case} {engine}");
        assert_eq!(view["evidence"], "NOT_RUN", "{case} {engine}");
        assert_eq!(view["required"], true, "{case} {engine}");
        assert_eq!(view["summary"], MESSAGE, "{case} {engine}");
        assert_eq!(
            view["targets"],
            json!([{"target_name": "C++Sanitizer", "status": "ERROR", "message": MESSAGE,
                "file_path": descriptor, "start_line": 1}]),
            "{case} {engine}"
        );
        let rule = if engine == "thread_sanitize" {
            "ici.legacy.thread-sanitize.target"
        } else {
            "ici.legacy.sanitize.target"
        };
        assert_eq!(view["findings"][0]["rule_id"], rule, "{case} {engine}");
        assert_eq!(
            view["findings"][0]["severity"], "critical",
            "{case} {engine}"
        );
        let (exit, report) = reports.entry(case).or_insert_with(|| run(&oracle, case));
        if !case.starts_with("verify:") && *exit != 1 {
            mismatches.push(format!("{case}: native exit {exit}, frozen 1"));
        }
        let Some(native) = native_engine(report, engine) else {
            mismatches.push(format!("{case}: native has no {engine} engine"));
            continue;
        };
        let native = engine_view(native, &view["extra"]);
        let mut expected = view.clone();
        expected.as_object_mut().unwrap().remove("tool_evidence");
        if native != expected {
            mismatches.push(format!(
                "{case} {engine}:\nnative {native:#}\nfrozen {expected:#}"
            ));
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n\n"));
}
