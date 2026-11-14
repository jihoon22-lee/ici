//! Frozen `ici next verify` and `ici analyze` gates of the validation checks, replayed from
//! `tests/rust-parity/validation/next-validation-gates-oracle.json`
//! (`capture_next_validation_gates.py`, frozen v0.13.0): schema 1, one Python component, the static
//! tool checks off, seven scenarios (green, failing, collection, all-skipped, no-tests,
//! bare-interpreter, no-interpreter). Every run compares the exit code, the gate (selected verdict
//! and every reason, literally), each check's state and evidence completeness, and the console.
//! The reviewed native values are listed with their ruling; anything else must equal frozen.
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/next-validation-gates-oracle.json");

fn interpreter(kind: &Value) -> Option<String> {
    let variable = match kind.as_str()? {
        "python" => "ICI_TEST_PYTHON",
        "bare" => "ICI_TEST_PYTHON_BARE",
        other => panic!("unknown interpreter {other}"),
    };
    Some(std::env::var(variable).unwrap_or_else(|_| panic!("set {variable}")))
}

fn native_run(oracle: &Value, scenario: &Value, argv: &[&str]) -> Value {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    fs::create_dir(base.join("home")).unwrap();
    let root = base.join("p");
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::write(root.join("app.py"), "X = 1\n").unwrap();
    for (file, text) in scenario["files"].as_object().unwrap() {
        fs::write(root.join(file), text.as_str().unwrap()).unwrap();
    }
    let executable = interpreter(&scenario["interpreter"]);
    let config = match &executable {
        Some(path) => oracle["config"]
            .as_str()
            .unwrap()
            .replace("\"<PYTHON>\"", &serde_json::to_string(path).unwrap()),
        None => oracle["config_without_interpreter"]
            .as_str()
            .unwrap()
            .to_owned(),
    };
    fs::write(root.join("ici.toml"), config).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(argv)
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", base.join("home"))
        .output()
        .unwrap();
    let result: Value = serde_json::from_slice(
        &fs::read(root.join(".ici/rust-v1/result.json"))
            .unwrap_or_else(|_| panic!("{argv:?}: {}", String::from_utf8_lossy(&output.stderr))),
    )
    .unwrap();
    let console: Vec<String> = (String::from_utf8_lossy(&output.stdout).into_owned()
        + &String::from_utf8_lossy(&output.stderr))
        .lines()
        .map(str::to_owned)
        .collect();
    let mut checks = serde_json::Map::new();
    for check in result["checks"].as_array().unwrap() {
        checks.insert(
            check["check_id"].as_str().unwrap().to_owned(),
            json!([check["state"], check["evidence_complete"]]),
        );
    }
    let mut text = json!({
        "exit_code": output.status.code(),
        "console": console,
        "gate": {"selected": result["gate"]["selected"], "reasons": result["gate"]["reasons"]},
        "checks": checks,
    })
    .to_string()
    .replace(&root.to_string_lossy().into_owned(), "<ROOT>");
    if let Some(path) = &executable {
        text = text.replace(path.as_str(), "<PYTHON>");
    }
    serde_json::from_str(&text).unwrap()
}

/// Reviewed native values: (scenario, command, field, native value, ruling).
fn reviewed() -> Vec<(
    &'static str,
    &'static str,
    &'static str,
    Value,
    &'static str,
)> {
    const COUNT: &str = "root ruling 2026-11-12 (task 4, 1): the approved pytest.py:156 correction drops frozen's spurious short-summary collection-error finding, so the finding count, violation count and console number are 1; the real failure finding is kept";
    const COLLECTION: &str = "root ruling 2026-11-12 (task 4, 2): a real collection failure keeps python.test FAILED without complete evidence and blocks the dependent coverage (approved incomplete-collection policy, pytest.py:138/156); the collection-error finding is kept and no coverage is made up";
    const EMPTY: &str = "root ruling 2026-11-12 (task 4, 3): the empty-suite refusal 'pytest did not report a completed nonempty suite (exit 5)' is an approved display difference (same failure class and exit); the coverage prerequisite reason and its note are frozen's";
    const BARE: &str = "root ruling 2026-11-12 (task 4, 4): frozen read `coverage run -m pytest` failing with 'No module named coverage' as a complete green python.test; next never switches to unittest, the exact missing-pytest diagnostic is the interpreter's refusal (python.test FAILED, incomplete) and the dependent coverage is BLOCKED";
    vec![
        (
            "failing",
            "next verify",
            "console",
            json!([
                "root: <ROOT>",
                "scope: components=app languages=python profile=standard",
                "FAIL: 1 finding(s)",
                "  1 violation(s) found",
                "saved <ROOT>/.ici/rust-v1/result.json"
            ]),
            COUNT,
        ),
        (
            "failing",
            "next verify",
            "gate",
            json!({"selected": "FAIL", "reasons": ["1 violation(s) found"]}),
            COUNT,
        ),
        (
            "collection",
            "next verify",
            "exit_code",
            json!(3),
            COLLECTION,
        ),
        (
            "collection",
            "next verify",
            "gate",
            json!({"selected": "INCOMPLETE", "reasons": [
                "app.python.test: pytest collection failed; suite scope is incomplete",
                "app.python.coverage: prerequisite app.python.test has no complete evidence"
            ]}),
            COLLECTION,
        ),
        (
            "collection",
            "next verify",
            "console",
            json!([
                "root: <ROOT>",
                "scope: components=app languages=python profile=standard",
                "INCOMPLETE: 1 finding(s)",
                "  app.python.test: pytest collection failed; suite scope is incomplete",
                "  app.python.coverage: prerequisite app.python.test has no complete evidence",
                "saved <ROOT>/.ici/rust-v1/result.json"
            ]),
            COLLECTION,
        ),
        (
            "collection",
            "next verify",
            "checks/python.test",
            json!(["FAILED", false]),
            COLLECTION,
        ),
        (
            "collection",
            "next verify",
            "checks/python.coverage",
            json!(["BLOCKED", false]),
            COLLECTION,
        ),
        (
            "no-tests",
            "next verify",
            "gate",
            json!({"selected": "INCOMPLETE", "reasons": [
                "app.python.test: pytest did not report a completed nonempty suite (exit 5)",
                "app.python.coverage: prerequisite app.python.test did not run"
            ]}),
            EMPTY,
        ),
        (
            "no-tests",
            "next verify",
            "console",
            json!([
                "root: <ROOT>",
                "scope: components=app languages=python profile=standard",
                "INCOMPLETE: 0 finding(s)",
                "  app.python.test: pytest did not report a completed nonempty suite (exit 5)",
                "  app.python.coverage: prerequisite app.python.test did not run",
                "  note: app.python.coverage: prerequisite app.python.test did not run",
                "saved <ROOT>/.ici/rust-v1/result.json"
            ]),
            EMPTY,
        ),
        (
            "bare-interpreter",
            "next verify",
            "gate",
            json!({"selected": "INCOMPLETE", "reasons": [
                "app.python.test: the project interpreter cannot run pytest: <PYTHON>: No module named pytest (exit 1)",
                "app.python.coverage: prerequisite app.python.test has no complete evidence"
            ]}),
            BARE,
        ),
        (
            "bare-interpreter",
            "next verify",
            "console",
            json!([
                "root: <ROOT>",
                "scope: components=app languages=python profile=standard",
                "INCOMPLETE: 0 finding(s)",
                "  app.python.test: the project interpreter cannot run pytest: <PYTHON>: No module named pytest (exit 1)",
                "  app.python.coverage: prerequisite app.python.test has no complete evidence",
                "saved <ROOT>/.ici/rust-v1/result.json"
            ]),
            BARE,
        ),
        (
            "bare-interpreter",
            "next verify",
            "checks/python.test",
            json!(["FAILED", false]),
            BARE,
        ),
        (
            "bare-interpreter",
            "next verify",
            "checks/python.coverage",
            json!(["BLOCKED", false]),
            BARE,
        ),
    ]
}

#[test]
#[ignore = "explicit project interpreters: ICI_TEST_PYTHON (pytest and coverage) and ICI_TEST_PYTHON_BARE (neither)"]
fn validation_gates_equal_frozen_next_verify_and_analyze_except_reviewed() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let runs = oracle["runs"].as_object().unwrap();
    assert_eq!(runs.len(), 7);
    let reviewed = reviewed();
    let mut used = vec![false; reviewed.len()];
    let mut wrong = vec![];
    for (name, scenario) in runs {
        for argv in [
            ["next", "verify", "--no-cache"].as_slice(),
            ["analyze", "--no-cache"].as_slice(),
        ] {
            let command = argv[..argv.len() - 1].join(" ");
            let native = native_run(&oracle, scenario, argv);
            let mut expected = scenario[&command].clone();
            // The native result namespace (decisions.md 배포: `.ici/rust-v1` 상태).
            let console = expected["console"].as_array_mut().unwrap();
            for line in console.iter_mut() {
                if line == "saved <ROOT>/.ici/next/result.json" {
                    *line = json!("saved <ROOT>/.ici/rust-v1/result.json");
                }
            }
            // Root decision D4 (execution plan §2.1): `ici analyze` adds the offline page after the
            // frozen block (no finding preview: these analyze runs have no findings).
            if command == "analyze" {
                assert_eq!(scenario[&command]["console"][2], "PASS: 0 finding(s)");
                console.push(json!("HTML: <ROOT>/.ici/rust-v1/result.html"));
            }
            for (index, (case, on, field, value, ruling)) in reviewed.iter().enumerate() {
                if case != name || *on != command {
                    continue;
                }
                assert!(!ruling.is_empty());
                let pointer = format!("/{field}");
                assert_ne!(
                    expected.pointer(&pointer),
                    Some(value),
                    "{name} {command} {field}: a reviewed value that equals frozen is stale"
                );
                *expected.pointer_mut(&pointer).unwrap() = value.clone();
                used[index] = true;
            }
            if native != expected {
                wrong.push(format!(
                    "{name} {command}:\n native {native}\n expect {expected}"
                ));
            }
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
    assert!(used.iter().all(|used| *used), "unused reviewed entries");
}
