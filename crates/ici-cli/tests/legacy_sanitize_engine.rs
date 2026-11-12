//! Frozen legacy `SanitizeEngine.run` verdicts across its Python and C++ scopes, replayed from
//! `tests/rust-parity/validation/legacy-sanitize-engine-oracle.json`
//! (`capture_legacy_sanitize_engine.py`, frozen v0.13.0 `ici sanitize --report` and deep `ici
//! verify --report`). Positive: measured Python scopes (clean, leak under every `mode`), a
//! tests-only tree, both scopes measured (direct g++ and pytest), the partial verdicts of a
//! measured scope beside a skipped one, and a declared hybrid project without any C/C++ source,
//! whose Python scope alone decides the verdict (no `C++Sanitizer` skip, no partial verdict).
//! Negative: the no-scope SKIP at '.' for a project without any scope (declared python, declared
//! hybrid, undeclared, a cpp project whose tests path holds no Python). Failure: the missing
//! Python scope a declared or detected python/hybrid type keeps for a tests path without test
//! files, nested or not, and for a hybrid project without a tests path (ERROR; native exit 3,
//! decisions.md "legacy 종료 코드 3의 범위").
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-sanitize-engine-oracle.json");

/// Frozen `capture_legacy_sanitize_engine.config`.
fn config(python: &str, kind: &Value, sanitize: &str) -> String {
    let mut text = String::from("[ici]\nprofile = \"deep\"\n[project]\n");
    if let Some(kind) = kind.as_str() {
        text += &format!("type = '{kind}'\n");
    }
    text += &format!("source_dirs = [\"src\"]\n[engines.test]\npython = {python:?}\n");
    if !sanitize.is_empty() {
        text += &format!("[engines.sanitize]\n{sanitize}");
    }
    text
}

struct Run {
    exit: i32,
    engine: Value,
    runs: Vec<Vec<String>>,
}

fn run(frozen: &Value, command: &str) -> Run {
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("explicit ICI_TEST_PYTHON (project CPython with pytest)");
    let work = tempfile::tempdir().unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let root = work.path().join("project");
    for (name, text) in frozen["files"].as_object().unwrap() {
        fs::create_dir_all(root.join(name).parent().unwrap()).unwrap();
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    fs::create_dir_all(&root).unwrap();
    let root = root.canonicalize().unwrap();
    fs::write(
        root.join("ici.toml"),
        config(
            &python,
            &frozen["type"],
            frozen["sanitize"].as_str().unwrap(),
        ),
    )
    .unwrap();
    // g++ comes from PATH like the frozen `shutil.which("g++")`.
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([command, "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", "/usr/bin:/bin")
        .env("LC_CTYPE", "C.UTF-8")
        .output()
        .unwrap();
    let log = String::from_utf8_lossy(&output.stdout).into_owned()
        + &String::from_utf8_lossy(&output.stderr);
    let report = format!("{}_report.json", command.replace('-', "_"));
    let text = fs::read_to_string(root.join(report))
        .unwrap_or_else(|_| panic!("{command}: no report: {log}"))
        .replace(&root.display().to_string(), "<ROOT>");
    let mut engine: Value = serde_json::from_str(&text).unwrap();
    if command == "verify" {
        engine = engine["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["engine_name"] == "sanitize")
            .unwrap()
            .clone();
    }
    Run {
        exit: output.status.code().unwrap(),
        engine,
        runs: owned_layout(&root),
    }
}

/// The children of every `.ici/rust-v1/runs/<label>/<component>` directory, sorted.
fn owned_layout(root: &Path) -> Vec<Vec<String>> {
    let names = |path: &Path| -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(path)
            .into_iter()
            .flatten()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    };
    let runs = root.join(".ici/rust-v1/runs");
    let mut layout = vec![];
    for label in names(&runs) {
        for component in names(&runs.join(&label)) {
            layout.push(names(&runs.join(&label).join(component)));
        }
    }
    layout
}

/// The frozen-shaped view the capture records.
fn view(engine: &Value, frozen: &Value) -> Value {
    let mut findings: Vec<Value> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            json!({
                "rule_id": finding["rule_id"],
                "path": finding["primary_location"]["path"],
                "start_line": finding["primary_location"]["start_line"],
                "message": finding["message"],
                "fingerprint": finding["fingerprint"],
                "severity": finding["severity"],
                "tool_name": finding["tool_name"],
            })
        })
        .collect();
    findings.sort_by(|a, b| a["fingerprint"].as_str().cmp(&b["fingerprint"].as_str()));
    // Only frozen's extra keys: native adds its own (native_checks, finding_identity_map, …).
    let extra: serde_json::Map<String, Value> = frozen["engine"]["extra"]
        .as_object()
        .unwrap()
        .keys()
        .map(|key| (key.clone(), engine["extra"][key].clone()))
        .collect();
    json!({
        "engine": {
            "status": engine["status"],
            "summary": engine["summary"],
            "evidence": engine["evidence"],
            "required": engine["required"],
            "extra": extra,
        },
        "targets": engine["targets"].as_array().unwrap().iter().map(|target| json!({
            "file_path": target["file_path"],
            "start_line": target["start_line"],
            "target_name": target["target_name"],
            "status": target["status"],
            "message": target["message"],
        })).collect::<Vec<_>>(),
        "findings": findings,
        "tool_evidence": engine["tool_evidence"].as_array().unwrap().iter()
            .map(|item| item["name"].clone()).collect::<Vec<_>>(),
    })
}

fn frozen_view(frozen: &Value) -> Value {
    json!({
        "engine": frozen["engine"],
        "targets": frozen["targets"],
        "findings": frozen["findings"],
        "tool_evidence": frozen["tool_evidence"],
    })
}

/// A required Python scope the engine could not measure: frozen exit 1, native 3.
fn missing_required(frozen: &Value) -> bool {
    frozen["engine"]["required"] == true
        && frozen["targets"].as_array().unwrap().iter().any(|target| {
            target["target_name"] == "PythonResourceWarnings"
                && target["status"] == "ERROR"
                && target["message"]
                    .as_str()
                    .unwrap()
                    .starts_with("Python ResourceWarning check skipped")
        })
}

fn replay(scenarios: &[&str]) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    for scenario in scenarios {
        let frozen = &oracle["runs"][scenario];
        let actual = run(frozen, "sanitize");
        let expected_exit = if missing_required(frozen) {
            3
        } else {
            frozen["exit_code"].as_i64().unwrap()
        };
        assert_eq!(i64::from(actual.exit), expected_exit, "{scenario}: exit");
        assert_eq!(
            view(&actual.engine, frozen),
            frozen_view(frozen),
            "{scenario}"
        );
    }
}

#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON)"]
fn python_scope_verdicts_and_the_no_scope_skip_equal_frozen() {
    replay(&[
        "measured-clean",
        "measured-clean-optional",
        "measured-leak",
        "measured-leak-pass-warn",
        "measured-leak-pass-warn-fail",
        "tests-only",
        "noscope-python-type",
        "noscope-undeclared",
        "noscope-cpp-type-tests-data",
        "python-type-empty-tests",
        "undeclared-empty-tests",
        "tests-helper-only",
        "noscope-hybrid-type",
        "hybrid-empty-tests",
        "hybrid-nested-empty-tests",
    ]);
}

/// A declared hybrid project without a C/C++ source or `tests/**/*.cpp` has no C++ scope at all
/// (`_analysis_scopes`: `bool(cpp_sources) or bool(cpp_tests)`), so the Python scope alone gives
/// the verdict: measured clean (MEASURED PASS, not the partial WARN), measured leak (FAIL), a
/// missing tests path (required ERROR) and the same path optional (SKIP, ESTIMATED).
#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON)"]
fn a_hybrid_project_without_cpp_sources_is_judged_by_its_python_scope_alone() {
    replay(&[
        "hybrid-python-only-measured",
        "hybrid-python-only-leak",
        "hybrid-python-only-missing-tests",
        "hybrid-python-only-missing-tests-optional",
    ]);
}

/// The thread-sanitize boundary: ThreadSanitizeEngine has no Python scope
/// (`CHECK_PYTHON_RESOURCES = False`), so a declared hybrid project without a C/C++ source keeps
/// the no-scope `ThreadSanitizer` SKIP at '.' whether its tests path is empty, holds a measured
/// pytest suite or is absent.
#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON)"]
fn thread_sanitize_puts_no_python_scope_in_play_for_a_hybrid_project() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let scenarios = oracle["thread_sanitize"].as_object().unwrap();
    assert_eq!(
        scenarios.keys().map(String::as_str).collect::<Vec<_>>(),
        [
            "hybrid-empty-tests",
            "hybrid-python-only-measured",
            "noscope-hybrid-type"
        ]
    );
    for (scenario, frozen) in scenarios {
        assert_eq!(frozen["targets"][0]["target_name"], "ThreadSanitizer");
        let actual = run(frozen, "thread-sanitize");
        assert_eq!(
            i64::from(actual.exit),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}: exit"
        );
        assert_eq!(
            view(&actual.engine, frozen),
            frozen_view(frozen),
            "{scenario}"
        );
    }
}

/// The partial verdict: a measured scope beside a skipped one, in either direction, under the
/// default and `pass_warn` modes; and both scopes skipped.
#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON) and g++ on /usr/bin"]
fn partial_and_skipped_scopes_equal_frozen() {
    replay(&[
        "hybrid-partial-clean",
        "hybrid-partial-leak",
        "hybrid-partial-leak-pass-warn",
        "cpp-type-partial-clean",
        "hybrid-both-skipped",
        "hybrid-cpp-measured-python-skipped",
    ]);
}

/// Both scopes measured. The C++ direct sanitizer and the Python suite each own a fresh leaf of
/// the run directory: `ici sanitize` creates the C++ leaf first and `ici test` the Python one
/// first (before the sibling layout, the second owner of the sanitize order was refused with
/// EEXIST because creating the C++ leaf had made its parent a plain directory).
#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON) and g++ on /usr/bin"]
fn both_measured_scopes_equal_frozen_in_either_creation_order() {
    replay(&["hybrid-both-measured", "hybrid-both-measured-leak"]);
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in ["hybrid-both-measured", "hybrid-both-measured-leak"] {
        let frozen = &oracle["runs"][scenario];
        for command in ["sanitize", "test"] {
            let actual = run(frozen, command);
            let leaves: Vec<_> = actual
                .runs
                .iter()
                .map(|names| names.iter().map(String::as_str).collect::<Vec<_>>())
                .collect();
            assert_eq!(leaves, [["cpp-direct", "python"]], "{scenario} {command}");
            if command == "test" {
                // The leak's test fails under `-W error::ResourceWarning` only; plain pytest
                // passes both suites.
                assert_eq!(actual.exit, 0, "{scenario} test: {}", actual.engine);
            }
        }
        // Deep `ici verify` composes the same engine (its exit belongs to every engine).
        let verified = run(&oracle["verify"][scenario], "verify");
        assert_eq!(
            view(&verified.engine, &oracle["verify"][scenario]),
            frozen_view(&oracle["verify"][scenario]),
            "verify {scenario}"
        );
    }
    let frozen = &oracle["verify"]["hybrid-partial-clean"];
    assert_eq!(
        view(&run(frozen, "verify").engine, frozen),
        frozen_view(frozen)
    );
}
