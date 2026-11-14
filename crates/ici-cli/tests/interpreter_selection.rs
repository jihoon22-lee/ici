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
/// `sys.executable`, which under the pyz is the launcher's choice of `$ICI_PYTHON`,
/// python3.14 ... python3.10 or python3 (`legacy_launcher_interpreter.rs` replays that rule).
/// A PATH that offers none of those names, only `python`, leaves the legacy test and coverage
/// checks blocked (incomplete, exit 3) with a hint naming `engines.test.python` and `ICI_PYTHON`;
/// declaring the key runs the suite.
#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn legacy_project_without_a_launcher_candidate_is_blocked_with_the_legacy_hint() {
    let work = tempfile::tempdir().unwrap();
    let bin = work.path().join("path-bin");
    fs::create_dir_all(&bin).unwrap();
    std::os::unix::fs::symlink(python(), bin.join("python")).unwrap();
    let path = bin.display().to_string();
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
                && summary.contains("ICI_PYTHON (unset), python3.14")
                && summary.ends_with("set engines.test.python, provide a .venv in the project, or set ICI_PYTHON to a Python 3.10+ interpreter (legacy pyz launcher rule)"),
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

/// Frozen next checks a declared bare name as a file and blocks it with the resolver's text
/// ('no such file: python3; set components[0].python.executable to change it'), even when the
/// component has a `.venv`: a declared interpreter is never replaced by the convention. The
/// sparse v2 schema has no frozen counterpart (frozen v0.13.0 rejects `schema_version = 2`); it
/// uses the same form with its own key.
#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn declared_bare_interpreter_name_blocks_on_the_next_schema_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["declared_bare"];
    let work = tempfile::tempdir().unwrap();
    let bin = work.path().join("path-bin");
    fs::create_dir_all(&bin).unwrap();
    std::os::unix::fs::symlink(python(), bin.join("python3")).unwrap();
    let path = format!("{}:/usr/bin:/bin", bin.display());
    for venv in [false, true] {
        let work = tempfile::tempdir().unwrap();
        let root = project(work.path(), frozen["config"].as_str().unwrap(), venv);
        let (checks, edges) = shaped(&plan(&root, &path), &root);
        assert_eq!(checks, frozen["checks"], "venv={venv}");
        assert_eq!(edges, frozen["edges"], "venv={venv}");
    }
    let work = tempfile::tempdir().unwrap();
    let root = project(
        work.path(),
        "schema_version=2\nprofile='deep'\n[python]\nexecutable='python3'\n",
        false,
    );
    let (checks, _) = shaped(&plan(&root, &path), &root);
    for check in CHECKS {
        assert_eq!(
            checks[check],
            json!({"kind": "blocked", "blocked": "no project interpreter \u{2014} no such file: python3; set python.executable to change it"}),
            "{check}"
        );
    }
}

/// Frozen legacy `_resolve_python` returns the declared `engines.test.python` name as argv and the
/// subprocess finds it on the run's PATH, so a legacy bare name runs that interpreter.
#[test]
#[ignore = "explicit project CPython (ICI_TEST_PYTHON)"]
fn declared_bare_interpreter_name_is_a_path_lookup_for_legacy_commands() {
    let work = tempfile::tempdir().unwrap();
    let bin = Path::new(&python()).parent().unwrap().to_owned();
    assert!(bin.join("python3").is_file(), "the venv offers python3");
    let root = project(
        work.path(),
        "[project]\ntype='python'\nsource_dirs=['.']\n[engines.test]\npython='python3'\n",
        true,
    );
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["test", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("PATH", bin.display().to_string())
        .output()
        .unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join("test_report.json")).unwrap()).unwrap();
    let test = report["extra"]["native_checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .unwrap();
    assert_eq!(test["state"], "SUCCEEDED", "{test:#}");
    assert_eq!(
        report["targets"][0]["status"],
        "PASS",
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    // The declared name wins over the project .venv and is not a launcher fallback.
    let result: Value =
        serde_json::from_slice(&fs::read(root.join(".ici/rust-v1/result.json")).unwrap()).unwrap();
    assert!(
        !result["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item
                .as_str()
                .unwrap()
                .starts_with("legacy pyz launcher rule")),
        "{:#}",
        result["limitations"]
    );
}
