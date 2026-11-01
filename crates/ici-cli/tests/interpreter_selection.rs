//! Frozen `application/tooling.py::resolve_python` project-interpreter selection for the deep
//! plan (`tests/rust-parity/validation/capture_interpreter_selection.py`): the declared
//! `[python] executable`, else only the component's `.venv/bin/python`. PATH `python`, `venv/`
//! and ici's own runtime are never the project interpreter.
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/interpreter-selection-oracle.json");
const CHECKS: [&str; 3] = ["python.test", "python.coverage", "python.compat-runtime"];

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("explicit ICI_TEST_PYTHON (project CPython)")
}

/// A PATH whose first entry holds a working `python`, so a PATH fallback would be visible.
fn path_with_python(work: &Path) -> String {
    let bin = work.join("path-bin");
    fs::create_dir_all(&bin).unwrap();
    std::os::unix::fs::symlink(python(), bin.join("python")).unwrap();
    format!("{}:/usr/bin:/bin", bin.display())
}

fn plan(root: &Path, path: &str) -> Value {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "plan", "--json", "--profile", "deep"])
        .current_dir(root)
        .env_clear()
        .env("PATH", path)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

fn project(work: &Path, config: &str, venv: bool) -> std::path::PathBuf {
    let root = work.join("project");
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::write(root.join("ici.toml"), config).unwrap();
    fs::write(root.join("app.py"), "X = 1\n").unwrap();
    fs::write(root.join("tests/test_app.py"), "def test_x():\n    pass\n").unwrap();
    if venv {
        fs::create_dir_all(root.join(".venv/bin")).unwrap();
        std::os::unix::fs::symlink(python(), root.join(".venv/bin/python")).unwrap();
    }
    root.canonicalize().unwrap()
}

fn shaped(document: &Value, root: &Path) -> (Value, Value) {
    let mut checks = serde_json::Map::new();
    for check in document["plans"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|plan| plan["checks"].as_array().unwrap())
    {
        let id = check["check"].as_str().unwrap();
        if !CHECKS.contains(&id) {
            continue;
        }
        let normal = |text: &str| text.replace(&root.display().to_string(), "<ROOT>");
        let mut row = json!({"kind": check["kind"]});
        if let Some(reason) = check["blocked"].as_str() {
            row["blocked"] = json!(normal(reason));
        }
        if let Some(argv) = check["argv"].as_array() {
            row["interpreter"] = json!(normal(argv[0].as_str().unwrap()));
        }
        checks.insert(id.into(), row);
    }
    let edges: BTreeMap<_, _> = document["graph"]["edges"]
        .as_object()
        .unwrap()
        .iter()
        .filter(|(task, _)| CHECKS.iter().any(|check| task.ends_with(check)))
        .map(|(task, value)| (task.clone(), value.clone()))
        .collect();
    (Value::Object(checks), json!(edges))
}

#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn undeclared_interpreter_is_only_the_component_venv_never_path_python() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in ["no_venv", "venv"] {
        let frozen = &oracle["runs"][scenario];
        let work = tempfile::tempdir().unwrap();
        let path = path_with_python(work.path());
        let root = project(
            work.path(),
            frozen["config"].as_str().unwrap(),
            frozen["venv"].as_bool().unwrap(),
        );
        let (checks, edges) = shaped(&plan(&root, &path), &root);
        assert_eq!(checks, frozen["checks"], "{scenario}");
        assert_eq!(edges, frozen["edges"], "{scenario}");
    }
    // `venv/` (not `.venv`) is not a frozen candidate either.
    let work = tempfile::tempdir().unwrap();
    let path = path_with_python(work.path());
    let root = project(
        work.path(),
        oracle["runs"]["no_venv"]["config"].as_str().unwrap(),
        false,
    );
    fs::create_dir_all(root.join("venv/bin")).unwrap();
    std::os::unix::fs::symlink(python(), root.join("venv/bin/python")).unwrap();
    let (checks, _) = shaped(&plan(&root, &path), &root);
    assert_eq!(checks, oracle["runs"]["no_venv"]["checks"]);
}

#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn declared_interpreter_that_is_missing_blocks_without_using_the_venv() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["declared_missing"];
    let work = tempfile::tempdir().unwrap();
    let path = path_with_python(work.path());
    let root = project(work.path(), frozen["config"].as_str().unwrap(), true);
    let (checks, edges) = shaped(&plan(&root, &path), &root);
    assert_eq!(edges, frozen["edges"]);
    for check in CHECKS {
        assert_eq!(
            checks[check]["kind"], frozen["checks"][check]["kind"],
            "{check}"
        );
        // Native wording names the declared value and the failure (intended difference).
        let reason = checks[check]["blocked"].as_str().unwrap();
        assert!(
            reason.starts_with("project Python \"missing/python\"")
                && reason.contains("<ROOT>/missing/python"),
            "{check}: {reason}"
        );
    }
}

/// Frozen legacy engines (`engines/test_interpreter.py`, `_sanitize_python_scope.py`) fell back to
/// `sys.executable`, ici's own interpreter. The native product has no own Python and never uses
/// PATH `python` instead: a legacy project without `.venv` or `engines.test.python` keeps its test
/// and coverage checks blocked (incomplete, exit 3) and names the legacy key; declaring the key runs
/// the suite.
#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn legacy_project_without_venv_is_blocked_instead_of_running_under_path_python() {
    let work = tempfile::tempdir().unwrap();
    let path = path_with_python(work.path());
    let root = project(
        work.path(),
        "[project]\ntype='python'\nsource_dirs=['.']\n",
        false,
    );
    let run = |root: &Path| {
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["test", "--report"])
            .current_dir(root)
            .env_clear()
            .env("PATH", &path)
            .output()
            .unwrap();
        let report: Value =
            serde_json::from_slice(&fs::read(root.join("test_report.json")).unwrap()).unwrap();
        (output.status.code().unwrap(), report)
    };
    let (code, report) = run(&root);
    assert_eq!(code, 3, "{report:#}");
    assert_eq!(report["evidence"], "NOT_RUN");
    for check in report["extra"]["native_checks"].as_array().unwrap() {
        assert_eq!(check["state"], "BLOCKED", "{check:#}");
        let summary = check["summary"].as_str().unwrap();
        assert!(
            summary.starts_with("no project interpreter")
                && summary.ends_with("set engines.test.python or provide a .venv in the project"),
            "{summary}"
        );
    }
    fs::write(
        root.join("ici.toml"),
        format!(
            "[project]\ntype='python'\nsource_dirs=['.']\n[engines.test]\npython={:?}\n",
            python()
        ),
    )
    .unwrap();
    fs::remove_file(root.join("test_report.json")).unwrap();
    let (_, report) = run(&root);
    let test = report["extra"]["native_checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .unwrap();
    assert_eq!(test["state"], "SUCCEEDED", "{test:#}");
}

/// Intended difference (root decision requested): frozen next checks a declared bare name at plan
/// time as a relative file and blocks it ('no such file: python3'), while frozen legacy
/// `_resolve_python` runs the declared `engines.test.python` name through the PATH. Native keeps
/// the documented SPEC-01 §3 `_anchor` semantics for both schemas: a declared bare name is the
/// user's own PATH lookup (never a fallback), so the three checks plan with that interpreter.
#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn declared_bare_interpreter_name_is_the_users_path_lookup() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["declared_bare"];
    for check in CHECKS {
        assert_eq!(frozen["checks"][check]["kind"], "blocked");
    }
    let work = tempfile::tempdir().unwrap();
    let bin = work.path().join("path-bin");
    fs::create_dir_all(&bin).unwrap();
    std::os::unix::fs::symlink(python(), bin.join("python3")).unwrap();
    let path = format!("{}:/usr/bin:/bin", bin.display());
    let root = project(work.path(), frozen["config"].as_str().unwrap(), false);
    let (checks, edges) = shaped(&plan(&root, &path), &root);
    for check in CHECKS {
        assert_eq!(checks[check]["kind"], "process", "{check}: {checks:#}");
        assert_eq!(
            checks[check]["interpreter"],
            json!(bin.join("python3").display().to_string()),
            "{check}"
        );
    }
    assert_eq!(
        edges,
        json!({"project.python.coverage": ["project.python.test"]})
    );
}
