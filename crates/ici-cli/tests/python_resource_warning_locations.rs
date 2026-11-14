//! What `ici sanitize` reads as a ResourceWarning in pytest's output, through the real CLI over
//! the raw shapes `capture_resource_warning_locations.py` recorded under frozen v0.13.0. Only what
//! pytest reports about the exception a test raised is a warning: source it echoes, exception text
//! and a test's captured output are not (decisions.md "pytest 출력을 ResourceWarning으로 잘못 읽은
//! 판정"). Frozen's own count stands beside each expectation, so each correction stays visible.
//! Positive: a real warning in every traceback style is never lost. Negative: every look-alike is
//! no defect, and a failed run says so without a success. Failure: pytest failing for any other
//! reason stays a tool failure.
//!
//! Frozen `next` has no sanitize check (the capture's `next_plan`): the `next verify` regression
//! here is native's own check, not parity with frozen.
use ici_cli::{commands, execute, registry};
use ici_domain::GateVerdict;
use ici_execution::Cancellation;
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/resource-warning-locations-oracle.json");
const UNATTRIBUTED: &str =
    "ResourceWarning source location could not be attributed to a regular file in this workspace";
const UNLOCATED: &str = "ResourceWarning was reported without a source location";

#[derive(Debug, Clone, Copy, PartialEq)]
enum Reading {
    /// A real warning at each of these locations.
    Located(&'static [(&'static str, u32)]),
    /// One real warning pytest gave no location for.
    Unlocated,
    /// Pytest failed and nothing it reported is a ResourceWarning.
    ToolFailure,
    /// A suite that never ran: pytest's counts are missing, whatever the output says.
    NotExecuted,
    /// Pytest passed; frozen and native share not seeing these warnings.
    Clean,
}
use Reading::*;

const CALC: &[(&str, u32)] = &[("src/calc.py", 5)];
const EXTERNAL: &[(&str, u32)] = &[("[external]", 5)];

/// Each captured case: frozen's defect count, then what the output really reports.
const CASES: [(&str, usize, Reading); 28] = [
    ("normal", 1, Located(CALC)),
    ("external", 1, Located(EXTERNAL)),
    ("echo-only", 1, ToolFailure),
    ("echo-plus-warning", 2, Located(CALC)),
    ("echo-in-message", 2, ToolFailure),
    ("summary-warning", 0, Clean),
    ("captured-stdout-look-alike", 2, ToolFailure),
    ("captured-stderr-look-alike", 2, ToolFailure),
    ("quoted-without-location", 1, ToolFailure),
    (
        "file-name-with-space-and-quote",
        1,
        Located(&[("src/it's a.py", 5)]),
    ),
    (
        "two-warnings",
        2,
        Located(&[("src/calc.py", 5), ("src/calc.py", 5)]),
    ),
    ("raised-directly", 1, Located(&[("tests/test_a.py", 2)])),
    ("chained", 1, Located(CALC)),
    ("fixture-teardown", 1, Located(CALC)),
    ("at-collection", 0, NotExecuted),
    // Frozen saved `src/gone.py`, a file the run deleted.
    ("file-gone", 1, Located(EXTERNAL)),
    ("unclosed-file", 0, Clean),
    ("traceback-short", 1, Unlocated),
    ("traceback-line", 1, Located(CALC)),
    ("traceback-native", 1, Unlocated),
    ("traceback-no", 1, Unlocated),
    ("parametrized-id", 1, ToolFailure),
    ("parametrized-id-no-traceback", 1, ToolFailure),
    // A summary with a parametrized id cannot say what was raised (the row's known limit).
    ("parametrized-real-no-traceback", 1, ToolFailure),
    ("multiline-message", 1, ToolFailure),
    ("multiline-message-native", 1, ToolFailure),
    ("quoted-location-line-style", 3, ToolFailure),
    ("no-summary-section", 1, Unlocated),
];

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}

fn cases() -> Value {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["cases"].as_object().unwrap().len(), CASES.len());
    oracle["cases"].clone()
}

fn write_tree(root: &Path, tree: &Value, outside: &Path) {
    for (name, text) in tree.as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = text
            .as_str()
            .unwrap()
            .replace("<outside>", &outside.display().to_string());
        fs::write(path, text).unwrap();
    }
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(
        outside.join("ext.py"),
        "import warnings\n\n\ndef leak():\n    warnings.warn('unclosed handle', ResourceWarning)\n",
    )
    .unwrap();
}

/// The count frozen reported: its saved report's FAIL targets, or its console when saving raised.
fn frozen_defects(case: &Value) -> usize {
    match case["report"]["targets"].as_array() {
        Some(targets) => targets
            .iter()
            .filter(|target| target["status"] == "FAIL")
            .count(),
        None => case["without_report"]["stdout"]
            .as_str()
            .unwrap()
            .strip_prefix("\u{2718} ")
            .unwrap()
            .split(' ')
            .next()
            .unwrap()
            .parse()
            .unwrap(),
    }
}

fn places(targets: &Value) -> Vec<(String, u64, String)> {
    targets
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            (
                target["file_path"].as_str().unwrap().to_owned(),
                target["start_line"].as_u64().unwrap(),
                target["status"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

fn located(expected: &[(&str, u32)]) -> Vec<(String, u64, String)> {
    expected
        .iter()
        .map(|(path, line)| ((*path).to_owned(), u64::from(*line), "FAIL".to_owned()))
        .collect()
}

#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON)"]
fn legacy_sanitize_counts_only_what_pytest_reports_as_a_resource_warning() {
    let cases = cases();
    for (name, frozen, reading) in CASES {
        let case = &cases[name];
        assert_eq!(frozen_defects(case), frozen, "{name}: the capture changed");
        let home = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        write_tree(&root, &case["tree"], outside.path());
        let mut environment = BTreeMap::from([
            ("HOME".to_owned(), home.path().display().to_string()),
            (
                "XDG_CONFIG_HOME".to_owned(),
                home.path().display().to_string(),
            ),
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("ICI_PYTHON".to_owned(), python()),
        ]);
        if let Some(addopts) = case["addopts"].as_str() {
            environment.insert("PYTEST_ADDOPTS".into(), addopts.into());
        }
        let output = commands::dispatch(
            &registry::parse(["ici", "sanitize", "--report"]).unwrap(),
            &root,
            &environment,
        )
        .unwrap();
        let text = fs::read_to_string(root.join("sanitize_report.json")).unwrap();
        let report: Value = serde_json::from_str(&text).unwrap();
        let places = places(&report["targets"]);
        let limitations = report["extra"]["native_checks"][0]["limitations"].to_string();
        let state = (
            output.exit_code,
            report["status"].as_str().unwrap(),
            report["evidence"].as_str().unwrap(),
        );
        match reading {
            Located(expected) => {
                assert_eq!(state, (1, "FAIL", "MEASURED"), "{name}");
                assert_eq!(places, located(expected), "{name}");
                assert_eq!(
                    report["summary"],
                    format!("{} Memory / Resource Defect(s) Detected", expected.len()),
                    "{name}"
                );
                assert_eq!(
                    limitations.contains(UNATTRIBUTED),
                    expected.iter().any(|(path, _)| *path == "[external]"),
                    "{name}: {limitations}"
                );
            }
            Unlocated => {
                assert_eq!(state, (1, "FAIL", "MEASURED"), "{name}");
                assert_eq!(places, located(&[("tests", 1)]), "{name}");
                assert!(limitations.contains(UNLOCATED), "{name}: {limitations}");
            }
            ToolFailure => {
                assert_eq!(state, (3, "ERROR", "NOT_RUN"), "{name}");
                assert_eq!(
                    places,
                    [("tests".to_owned(), 1, "ERROR".to_owned())],
                    "{name}"
                );
                assert!(
                    report["targets"][0]["message"]
                        .as_str()
                        .unwrap()
                        .starts_with("Pytest ResourceWarning check failed with exit code 1: "),
                    "{name}: {}",
                    report["targets"][0]
                );
            }
            NotExecuted => {
                assert_eq!(state, (3, "ERROR", "NOT_RUN"), "{name}");
                assert_eq!(
                    report["targets"][0]["message"],
                    "Pytest returned success without parseable test results",
                    "{name}"
                );
            }
            Clean => {
                assert_eq!(state, (0, "PASS", "MEASURED"), "{name}");
                assert_eq!(
                    places,
                    [("tests".to_owned(), 1, "PASS".to_owned())],
                    "{name}"
                );
            }
        }
        // No location outside the project reaches the report, and no fragment of a line is a path.
        assert!(
            !text.contains(&outside.path().display().to_string()),
            "{name}: {text}"
        );
        for (path, ..) in &places {
            assert!(
                !path.contains("/usr/lib/outside") && !path.contains(['(', '=', '>']),
                "{name}: {path}"
            );
        }
    }
}

/// `next verify` with `python.sanitize` as the only check, so the gate is this check's own.
fn next_verify(case: &Value, tree: &Value, name: &str) -> (execute::ExecutedRun, String) {
    next_verify_scope(case, tree, name, true)
}

/// `next_verify` with `python.sanitize` required or optional.
fn next_verify_scope(
    case: &Value,
    tree: &Value,
    name: &str,
    required: bool,
) -> (execute::ExecutedRun, String) {
    let outside = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap();
    write_tree(&root, tree, outside.path());
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        config.push_str(&format!(
            "[checks.{:?}]\nenabled=false\nrequired=false\n",
            check.id
        ));
    }
    config.push_str(&format!(
        "[checks.\"python.sanitize\"]\nenabled=true\nrequired={required}\n"
    ));
    config.push_str(&format!(
        "[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n\
         [components.python]\nexecutable={:?}\ntest_paths=['tests']\n",
        python()
    ));
    fs::write(root.join("ici.toml"), config).unwrap();
    // Next clears the caller's options, so the project's own plugin asks for the shape captured.
    if let Some(addopts) = case["addopts"].as_str() {
        let mut lines = String::from("def pytest_configure(config):\n");
        for option in addopts.split_whitespace() {
            match option.split_once('=') {
                Some(("--tb", style)) => {
                    lines.push_str(&format!("    config.option.tbstyle = {style:?}\n"));
                }
                None if option == "-rN" => {
                    lines.push_str("    config.option.reportchars = 'N'\n");
                }
                _ => panic!("{name}: no plugin form for {option}"),
            }
        }
        fs::write(root.join("conftest.py"), lines).unwrap();
    }
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), "/must-not-leak".to_owned()),
    ]);
    let run = execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        &root,
        &environment,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    (run, outside.path().display().to_string())
}

#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON)"]
fn next_python_sanitize_gate_follows_what_pytest_reports_as_a_resource_warning() {
    let cases = cases();
    for (name, _, reading) in CASES {
        let case = &cases[name];
        let (run, outside) = next_verify(case, &case["tree"], name);
        let output = &run.outputs["project.python.sanitize"];
        let mut places: Vec<(String, u32)> = output
            .targets
            .iter()
            .filter(|target| target.target_name == "ResourceWarning")
            .map(|target| (target.file_path.clone(), target.start_line))
            .collect();
        let findings = output.observation.findings.len();
        let gate = run.result.gate.selected;
        let context = format!("{name}: {gate:?} {output:?}");
        assert_eq!(findings, places.len(), "{context}");
        match reading {
            Located(expected) => {
                // Next reports one finding per location; two warnings on one line are one here.
                let mut expected: Vec<(String, u32)> = expected
                    .iter()
                    .map(|(path, line)| ((*path).to_owned(), *line))
                    .collect();
                expected.dedup();
                places.dedup();
                assert_eq!(places, expected, "{context}");
                assert_eq!(gate, GateVerdict::Fail, "{context}");
                assert_eq!(
                    output
                        .observation
                        .limitations
                        .iter()
                        .any(|limit| limit == UNATTRIBUTED),
                    expected.iter().any(|(path, _)| path == "[external]"),
                    "{context}"
                );
            }
            Unlocated => {
                assert_eq!(places, [("tests".to_owned(), 1)], "{context}");
                assert_eq!(gate, GateVerdict::Fail, "{context}");
                assert!(
                    output
                        .observation
                        .limitations
                        .iter()
                        .any(|limit| limit == UNLOCATED),
                    "{context}"
                );
            }
            ToolFailure | NotExecuted => {
                assert_eq!(findings, 0, "{context}");
                assert_eq!(gate, GateVerdict::Incomplete, "{context}");
            }
            Clean => {
                assert_eq!(findings, 0, "{context}");
                assert_eq!(gate, GateVerdict::Pass, "{context}");
            }
        }
        assert!(!format!("{output:?}").contains(&outside), "{context}");
    }
}

/// Frozen's only reading of an executed test (`_pytest_has_executed_result`, legacy
/// SanitizeEngine): a suite that only errored in setup ran nothing, so frozen legacy reports the
/// missing scope (optional: SKIP, exit 2; required: tool error ERROR, exit 1), while a test that
/// passed and whose teardown raised ran and failed without a ResourceWarning (ERROR, exit 1, in
/// both). Next reads the same: the setup-only scope is missing and the teardown one is the tool
/// failure; both leave the check incomplete, so a required scope never passes silently.
#[test]
#[ignore = "explicit actual pytest interpreter (ICI_TEST_PYTHON)"]
fn next_python_sanitize_counts_only_tests_that_ran() {
    let fixture = "import pytest\n\n@pytest.fixture\ndef broken():\n";
    for (name, body, optional_gate, required_gate) in [
        (
            "setup-only",
            "    raise RuntimeError('setup broke')\n",
            GateVerdict::Pass,
            GateVerdict::Incomplete,
        ),
        (
            "teardown-only",
            "    yield\n    raise RuntimeError('teardown broke')\n",
            GateVerdict::Pass,
            GateVerdict::Incomplete,
        ),
    ] {
        let tree = serde_json::json!({
            "src/m.py": "X = 1\n",
            "tests/test_a.py": format!("{fixture}{body}\ndef test_a(broken):\n    assert True\n"),
        });
        for (required, gate) in [(false, optional_gate), (true, required_gate)] {
            let (run, _) = next_verify_scope(&Value::Null, &tree, name, required);
            let output = &run.outputs["project.python.sanitize"];
            let context = format!("{name} required={required}: {output:?}");
            assert_eq!(run.result.gate.selected, gate, "{context}");
            assert!(output.observation.findings.is_empty(), "{context}");
            // Next has no skipped scope of its own (`sanitize_scope_optional` is legacy): the
            // check is incomplete either way and `required` decides the gate. What differs is the
            // reading: setup errors only are frozen's missing scope, a teardown error after a pass
            // is the run's tool failure.
            assert_eq!(
                format!("{:?}", output.observation.state),
                "Failed",
                "{context}"
            );
            let message = format!("{:?}", output.targets);
            if name == "setup-only" {
                assert!(
                    message.contains("without parseable test results"),
                    "{context}"
                );
            } else {
                assert!(
                    message.contains("Pytest ResourceWarning check failed with exit code 1"),
                    "{context}"
                );
            }
        }
    }
}
