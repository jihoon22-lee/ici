//! Frozen legacy coverage-probe tool errors and `_interpreter_from_command` through the binary
//! (`tests/rust-parity/validation/legacy-coverage-probe-oracle.json`,
//! `capture_legacy_coverage_probe.py`).
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-coverage-probe-oracle.json");
const PROBES: [(&str, &str); 4] = [
    ("exit", "echo broken >&2; exit 2"),
    ("signal", "kill -9 $$"),
    (
        "truncated",
        "head -c 1100000 /dev/zero | tr '\\0' a; exit 0",
    ),
    ("missing-exit-2", "echo 'No module named coverage'; exit 2"),
];

fn run(case: &str, python: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::create_dir(&bin).unwrap();
    fs::write(
        root.join("src/calc.py"),
        "def add(a, b):\n    return a + b\n",
    )
    .unwrap();
    fs::write(
        root.join("tests/test_calc.py"),
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    .unwrap();
    let configured = if case == "pytest-name" {
        let pytest = Path::new(python).parent().unwrap().join("pytest");
        fs::write(
            bin.join("python"),
            format!("#!/bin/sh\nexec {python} \"$@\"\n"),
        )
        .unwrap();
        fs::write(
            bin.join("pytest"),
            format!("#!/bin/sh\nexec {} \"$@\"\n", pytest.display()),
        )
        .unwrap();
        bin.join("pytest")
    } else {
        let probe = PROBES.iter().find(|(name, _)| *name == case).unwrap().1;
        fs::write(
            bin.join("python"),
            format!(
                "#!/bin/sh\nif [ \"$1\" = \"-m\" ] && [ \"$2\" = \"coverage\" ] && [ \"$3\" = \"--version\" ]; then\n  {probe}\nfi\nexec {python} \"$@\"\n"
            ),
        )
        .unwrap();
        bin.join("python")
    };
    for name in ["python", "pytest"] {
        if bin.join(name).exists() {
            fs::set_permissions(
                bin.join(name),
                std::os::unix::fs::PermissionsExt::from_mode(0o755),
            )
            .unwrap();
        }
    }
    fs::write(
        root.join("ici.toml"),
        format!(
            "[ici]\nprofile = \"standard\"\n[project]\nsource_dirs = [\"src\"]\n[engines.test]\npython = \"{}\"\n",
            configured.display()
        ),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["test", "--report"])
        .current_dir(&root)
        .env("HOME", work.path())
        .output()
        .unwrap();
    let engine: Value =
        serde_json::from_slice(&fs::read(root.join("test_report.json")).unwrap()).unwrap();
    json!({
        "exit_code": output.status.code(),
        "engine": {"status": engine["status"], "summary": engine["summary"], "evidence": engine["evidence"]},
        "targets": engine["targets"].as_array().unwrap().iter().map(|target| target["target_name"].clone()).collect::<Vec<_>>(),
    })
}

/// exit N / signal / more than 1,000,000 characters: the frozen ERROR verdict, its tool-error
/// summary, NOT_RUN evidence, exit 1 and the plain suite's case target.
#[test]
#[ignore = "explicit actual project interpreter with pytest and coverage (ICI_TEST_PYTHON)"]
fn failed_coverage_probes_report_the_frozen_tool_error_after_the_plain_suite() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for case in ["exit", "signal", "truncated"] {
        let frozen = &oracle["cases"][case];
        let native = run(case, &python);
        assert_eq!(native["exit_code"], frozen["exit_code"], "{case}");
        assert_eq!(native["engine"], frozen["engine"], "{case}");
        let names: Vec<_> = frozen["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| target["target_name"].clone())
            .collect();
        assert_eq!(native["targets"], json!(names), "{case}");
    }
}

/// The bare missing-module line (exit 2) is a missing module, not a tool error: the plain
/// suite's verdict stands with optional coverage unmeasured (frozen's invented 85%/95% estimate
/// is the accepted no-fabrication difference). A `pytest`-named interpreter measures coverage
/// through the `python` beside it.
#[test]
#[ignore = "explicit actual project interpreter with pytest and coverage (ICI_TEST_PYTHON)"]
fn missing_module_and_pytest_named_interpreters_follow_the_frozen_selection() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let missing = run("missing-exit-2", &python);
    let frozen = &oracle["cases"]["missing-exit-2"];
    assert_eq!(missing["exit_code"], frozen["exit_code"]);
    assert_eq!(missing["engine"]["status"], frozen["engine"]["status"]);
    let summary = missing["engine"]["summary"].as_str().unwrap();
    assert!(
        summary.starts_with(
            "1/1 Tests Passed; coverage not measured (optional): coverage module is unavailable"
        ),
        "{summary}"
    );
    let named = run("pytest-name", &python);
    let frozen = &oracle["cases"]["pytest-name"];
    assert_eq!(named["exit_code"], frozen["exit_code"]);
    assert_eq!(named["engine"]["status"], frozen["engine"]["status"]);
    assert_eq!(named["engine"]["evidence"], frozen["engine"]["evidence"]);
    let summary = named["engine"]["summary"].as_str().unwrap();
    assert!(
        summary.starts_with("1/1 Tests Passed | Line: 100.0%"),
        "{summary}"
    );
}
