//! Frozen next refusals planned by `application/testing.py`, and the schema 1 rule that naming a
//! check without `required` leaves it not required (`config/composition.py:74`,
//! `application/selection.py:252`). `tests/rust-parity/validation/next-refusals-oracle.json`.
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/next-refusals-oracle.json");

/// decisions.md "next plan 거부 문구": frozen's text and the more specific native text.
const RULED: [(&str, &str); 2] = [
    (
        "declared test_paths matched no files",
        "test target tests/none matched no existing input",
    ),
    (
        "python.coverage reads the test run's data — enable python.test",
        "needs app.python-test-evidence, which no selected check produces — python.test produces \
         it and is disabled (enabled = false)",
    ),
];
const COMPAT: &str = "app.python.compat: python metadata unreadable: ";

fn code(message: &str) -> &'static str {
    if message.starts_with("project.requires-python") {
        "invalid-requires-python"
    } else {
        "invalid-metadata"
    }
}

/// What native must print where frozen printed `text`.
fn ruled(text: &str) -> String {
    if let Some(message) = text.strip_prefix(COMPAT) {
        return format!(
            "app.python.compat: pyproject.toml:1:1: {}: {message}",
            code(message)
        );
    }
    RULED
        .iter()
        .fold(text.to_owned(), |text, (frozen, native)| {
            text.replace(frozen, native)
        })
}

struct Project {
    _temporary: tempfile::TempDir,
    root: std::path::PathBuf,
    log: std::path::PathBuf,
}

fn project(config: &str, files: &serde_json::Map<String, Value>) -> Project {
    let python = std::env::var("ICI_TEST_PYTHON").expect("set ICI_TEST_PYTHON");
    let temporary = tempfile::tempdir().unwrap();
    let log = temporary.path().join("interpreter.log");
    let script = temporary.path().join("project-python");
    fs::write(
        &script,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log:?}\nexec {python:?} \"$@\"\n",
            log = log.display().to_string()
        ),
    )
    .unwrap();
    fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
    let root = temporary.path().join("project");
    for (path, content) in files {
        let target = root.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        match content {
            Value::String(text) => fs::write(&target, text).unwrap(),
            link => std::os::unix::fs::symlink(link["link"].as_str().unwrap(), &target).unwrap(),
        }
    }
    let quoted = serde_json::to_string(&script.display().to_string()).unwrap();
    fs::create_dir_all(&root).unwrap();
    fs::write(
        root.join("ici.toml"),
        config.replace("<PYTHON_JSON>", &quoted),
    )
    .unwrap();
    Project {
        _temporary: temporary,
        root,
        log,
    }
}

fn ici(root: &Path, arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(arguments)
        .current_dir(root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", root)
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap()
}

fn observed(project: &Project, check: &str) -> Value {
    let plan = ici(&project.root, &["next", "plan"]);
    let stdout = String::from_utf8_lossy(&plan.stdout).into_owned();
    let plan_line = stdout
        .lines()
        .find(|line| line.contains(&format!(".{check}:")))
        .filter(|line| line.contains("blocked"))
        .map(str::trim);
    let _ = fs::remove_file(&project.log);
    let verify = ici(
        &project.root,
        &[
            "next",
            "verify",
            "--no-cache",
            "--result",
            "out/result.json",
        ],
    );
    let result: Value =
        serde_json::from_slice(&fs::read(project.root.join("out/result.json")).unwrap()).unwrap();
    let mut states = BTreeMap::<String, Vec<String>>::new();
    for item in result["checks"].as_array().unwrap() {
        let entry = states
            .entry(item["check_id"].as_str().unwrap().to_owned())
            .or_default();
        let state = item["state"].as_str().unwrap().to_owned();
        if !entry.contains(&state) {
            entry.push(state);
            entry.sort();
        }
    }
    let ran = fs::read_to_string(&project.log).unwrap_or_default();
    json!({
        "exit_code": verify.status.code(),
        "plan_exit_code": plan.status.code(),
        "plan_line": plan_line,
        "gate": result["gate"],
        "states": states,
        "blocked": result["execution"]["blocked_task_ids"],
        "failed": result["execution"]["failed_task_ids"],
        "required_complete": result["execution"]["required_complete"],
        "compileall_ran": ran.contains("compileall"),
    })
}

fn expected(frozen: &Value) -> Value {
    let mut expected = frozen.clone();
    if let Some(line) = frozen["plan_line"].as_str() {
        expected["plan_line"] = json!(ruled(line));
    }
    expected["gate"]["reasons"] = frozen["gate"]["reasons"]
        .as_array()
        .unwrap()
        .iter()
        .map(|text| json!(ruled(text.as_str().unwrap())))
        .collect();
    expected
}

fn compare(prefix: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let runs = oracle["runs"].as_object().unwrap();
    let selected: Vec<_> = runs.iter().filter(|(n, _)| n.starts_with(prefix)).collect();
    assert!(!selected.is_empty(), "{prefix}");
    for (name, run) in selected {
        let project = project(
            run["config"].as_str().unwrap(),
            run["files"].as_object().unwrap(),
        );
        let native = observed(&project, run["check"].as_str().unwrap());
        assert_eq!(native, expected(&run["frozen"]), "{name}");
    }
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn a_check_named_without_required_is_not_required_in_schema_one() {
    compare("finding-");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn declared_test_paths_that_match_nothing_block_the_test_check() {
    compare("test-paths-");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn coverage_without_the_test_check_is_blocked() {
    compare("coverage-without-test");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn unreadable_metadata_blocks_compat_runtime_before_any_interpreter_runs() {
    compare("metadata-");
}
