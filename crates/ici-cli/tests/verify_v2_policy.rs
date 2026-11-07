//! One high security finding under `verify`, `analyze` and `next verify`. A v2 project's policy does
//! not depend on the command name; only a legacy project keeps frozen's optional security engine.
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const SOURCE: &str = "def a(value):\n    return eval(value)\n";
/// What needs external tools, off so the remaining run is complete wherever it runs.
const NEEDS_TOOLS: [&str; 10] = [
    "python.lint",
    "python.format",
    "python.type",
    "python.test",
    "python.coverage",
    "python.compat",
    "python.compat-runtime",
    "python.sanitize",
    "python.tsan",
    "python.dead",
];

fn v2(security: &str, on: &[&str]) -> String {
    let mut text = String::from("schema_version = 2\n");
    for check in NEEDS_TOOLS {
        if !on.contains(&check) {
            text.push_str(&format!("[checks.\"{check}\"]\nenabled = false\n"));
        }
    }
    text.push_str(security);
    text
}

fn project(config: &str) -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join(".hg"), "").unwrap();
    fs::write(root.join("src/a.py"), SOURCE).unwrap();
    fs::write(root.join("ici.toml"), config).unwrap();
    (directory, root)
}

struct Run {
    exit: i32,
    selected: String,
    violations: bool,
    finding: bool,
    stderr: String,
}

fn run(root: &Path, command: &[&str]) -> Run {
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(command)
        .current_dir(root)
        .env_clear()
        .env("HOME", root.parent().unwrap())
        .env("PATH", root.parent().unwrap())
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let saved: Value =
        serde_json::from_slice(&fs::read(root.join(".ici/rust-v1/result.json")).unwrap()).unwrap();
    Run {
        exit: output.status.code().unwrap(),
        selected: saved["gate"]["selected"].as_str().unwrap().to_owned(),
        violations: saved["gate"]["has_violations"] == true,
        finding: saved["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["rule_id"] == "security.evalexec"),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

const COMMANDS: [&[&str]; 3] = [&["verify"], &["analyze"], &["next", "verify"]];

#[test]
fn a_v2_default_counts_the_finding_under_every_command() {
    let (_directory, root) = project(&v2("", &[]));
    for command in COMMANDS {
        let got = run(&root, command);
        assert!(got.finding, "{command:?}: {}", got.stderr);
        assert!(got.violations, "{command:?}: {}", got.stderr);
        assert_eq!(got.selected, "FAIL", "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 1, "{command:?}: {}", got.stderr);
    }
}

#[test]
fn a_v2_explicit_optional_check_is_respected_under_every_command() {
    let (_directory, root) = project(&v2("[checks.\"python.security\"]\nrequired = false\n", &[]));
    for command in COMMANDS {
        let got = run(&root, command);
        assert!(got.finding, "{command:?}: {}", got.stderr);
        assert!(!got.violations, "{command:?}: {}", got.stderr);
        assert_eq!(got.selected, "PASS", "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 0, "{command:?}: {}", got.stderr);
    }
}

#[test]
fn a_v2_explicit_optional_engine_is_respected_under_every_command() {
    let (_directory, root) = project(&v2("[engines.security]\nrequired = false\n", &[]));
    for command in COMMANDS {
        let got = run(&root, command);
        assert!(got.finding, "{command:?}: {}", got.stderr);
        assert!(!got.violations, "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 0, "{command:?}: {}", got.stderr);
    }
}

#[test]
fn a_v2_disabled_check_is_respected_under_every_command() {
    let (_directory, root) = project(&v2("[checks.\"python.security\"]\nenabled = false\n", &[]));
    for command in COMMANDS {
        let got = run(&root, command);
        assert!(!got.finding, "{command:?}: {}", got.stderr);
        assert!(!got.violations, "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 0, "{command:?}: {}", got.stderr);
    }
}

/// A missing required tool is another axis: the run is incomplete and the finding still counts.
#[test]
fn a_v2_missing_required_tool_is_incomplete_under_every_command() {
    let (_directory, root) = project(&v2("", &["python.lint"]));
    for command in COMMANDS {
        let got = run(&root, command);
        assert!(got.violations, "{command:?}: {}", got.stderr);
        assert_eq!(got.selected, "INCOMPLETE", "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 3, "{command:?}: {}", got.stderr);
    }
}

/// Coverage has no test run to read here. Under a v2 project that is incomplete, not a pass.
#[test]
fn a_v2_coverage_that_cannot_run_does_not_pass_verify() {
    let (_directory, root) = project(&v2(
        "[checks.\"python.security\"]\nenabled = false\n",
        &["python.coverage"],
    ));
    for command in [["verify"].as_slice(), ["next", "verify"].as_slice()] {
        let got = run(&root, command);
        assert_eq!(got.selected, "INCOMPLETE", "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 3, "{command:?}: {}", got.stderr);
    }
    let (_directory, root) = project(&v2(
        "[checks.\"python.security\"]\nenabled = false\n[checks.\"python.coverage\"]\nrequired = false\n",
        &["python.coverage"],
    ));
    for command in [["verify"].as_slice(), ["next", "verify"].as_slice()] {
        let got = run(&root, command);
        assert_eq!(got.selected, "PASS", "{command:?}: {}", got.stderr);
        assert_eq!(got.exit, 0, "{command:?}: {}", got.stderr);
    }
}

/// Frozen's security engine reads `cfg.get("required", False)` and WARNs: suite WARN, exit 0. A
/// legacy project and a project without configuration keep that.
#[test]
fn a_legacy_verify_keeps_the_optional_security_engine() {
    let off = NEEDS_TOOLS
        .iter()
        .filter_map(|check| check.strip_prefix("python."))
        .map(|name| match name {
            "lint" | "format" => "lint",
            "coverage" => "test",
            "compat" | "compat-runtime" => "python_compat",
            "tsan" => "thread_sanitize",
            name => name,
        })
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .map(|engine| format!("[engines.{engine}]\nenabled = false\n"))
        .collect::<String>();
    let (_directory, root) = project(&format!(
        "type = \"python\"\n[project]\nname = \"p\"\n{off}"
    ));
    let got = run(&root, &["verify"]);
    assert!(got.finding, "{}", got.stderr);
    assert!(!got.violations, "{}", got.stderr);
    assert_eq!(got.exit, 0, "{}", got.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.join(".ici/rust-v1/result.json")).unwrap()).unwrap();
    assert_eq!(report["gate"]["selected"], "PASS");
}
