//! Frozen pyz launcher rule as the legacy project interpreter
//! (`tests/rust-parity/validation/capture_legacy_launcher_interpreter.py`, a private copy of the
//! released `dist/ici.pyz` whose 283 `ici/` sources equal the frozen tree). After
//! `engines.test.python` and the project `.venv`, frozen legacy test/coverage/sanitize ran under
//! `sys.executable`; `python_compat` without `interpreters` and integration `{python:current}`
//! used it directly. Under the pyz that is the first of `$ICI_PYTHON`, python3.14 ... python3.10,
//! python3 on PATH that passes a Python >= 3.10 check. Native applies the same rule, for
//! legacy-schema historical commands only, and records the choice.
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-launcher-interpreter-oracle.json");

fn venv_python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("explicit ICI_TEST_PYTHON (venv CPython with pytest)")
}
fn bare_python() -> String {
    std::env::var("ICI_TEST_PYTHON_BARE").expect("explicit ICI_TEST_PYTHON_BARE (CPython 3.12)")
}

struct Paths {
    _work: tempfile::TempDir,
    values: BTreeMap<&'static str, String>,
    home: std::path::PathBuf,
}
impl Paths {
    fn new(oracle: &Value) -> Self {
        let work = tempfile::tempdir().unwrap();
        let fake = work.path().join("fake");
        fs::create_dir_all(&fake).unwrap();
        for name in ["python3.14", "python3"] {
            fs::write(fake.join(name), oracle["fake"].as_str().unwrap()).unwrap();
            fs::set_permissions(
                fake.join(name),
                std::os::unix::fs::PermissionsExt::from_mode(0o755),
            )
            .unwrap();
        }
        let home = work.path().join("home");
        fs::create_dir_all(&home).unwrap();
        let venv = Path::new(&venv_python()).parent().unwrap().to_owned();
        let bare = Path::new(&bare_python()).parent().unwrap().to_owned();
        // The capture relied on these PATH listings; the replay needs the same names.
        for (key, dir) in [("<VENV_BIN>", &venv), ("<BARE_BIN>", &bare)] {
            let mut names: Vec<_> = fs::read_dir(dir)
                .unwrap()
                .map(|entry| entry.unwrap().file_name().into_string().unwrap())
                .filter(|name| name.starts_with("python"))
                .collect();
            names.sort();
            assert_eq!(json!(names), oracle["path_listing"][key], "{key}");
        }
        let values = BTreeMap::from([
            ("<VENV_BIN>", venv.display().to_string()),
            ("<BARE_BIN>", bare.display().to_string()),
            ("<FAKE>", fake.display().to_string()),
        ]);
        Self {
            _work: work,
            values,
            home,
        }
    }
    fn expand(&self, text: &str) -> String {
        self.values
            .iter()
            .fold(text.to_owned(), |text, (key, value)| {
                text.replace(key, value)
            })
    }
}

struct Run {
    code: i32,
    stdout: String,
    root: tempfile::TempDir,
}
impl Run {
    fn report(&self, name: &str) -> Value {
        serde_json::from_slice(&fs::read(self.root.path().join(name)).unwrap()).unwrap()
    }
    fn limitations(&self) -> Vec<String> {
        let result: Value = serde_json::from_slice(
            &fs::read(self.root.path().join(".ici/rust-v1/result.json")).unwrap(),
        )
        .unwrap();
        result["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect()
    }
    fn launcher_notes(&self) -> Vec<String> {
        self.limitations()
            .into_iter()
            .filter(|item| item.starts_with("legacy pyz launcher rule"))
            .collect()
    }
}

fn run(oracle: &Value, paths: &Paths, scenario: &str) -> Run {
    let frozen = &oracle["runs"][scenario];
    let root = tempfile::tempdir().unwrap();
    let dir = root.path();
    fs::create_dir_all(dir.join("tests")).unwrap();
    fs::write(dir.join("ici.toml"), frozen["config"].as_str().unwrap()).unwrap();
    fs::write(dir.join("app.py"), oracle["app"].as_str().unwrap()).unwrap();
    fs::write(
        dir.join("contract.py"),
        oracle["contract"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        dir.join("tests/test_app.py"),
        oracle["test"].as_str().unwrap(),
    )
    .unwrap();
    if frozen["venv"] == true {
        fs::create_dir_all(dir.join(".venv/bin")).unwrap();
        let wrapper = format!(
            "#!/bin/sh\nexec {}/python \"$@\"\n",
            paths.values["<VENV_BIN>"]
        );
        fs::write(dir.join(".venv/bin/python"), wrapper).unwrap();
        fs::set_permissions(
            dir.join(".venv/bin/python"),
            std::os::unix::fs::PermissionsExt::from_mode(0o755),
        )
        .unwrap();
    }
    let path = frozen["path"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| paths.expand(item.as_str().unwrap()))
        .collect::<Vec<_>>()
        .join(":");
    let command = frozen["command"].as_str().unwrap();
    let mut argv = vec![command, "--report"];
    if command == "verify" {
        argv.extend(["--profile", "deep", "--no-cache"]);
    }
    let mut process = Command::new(env!("CARGO_BIN_EXE_ici"));
    process
        .args(&argv)
        .current_dir(dir)
        .env_clear()
        .env("HOME", &paths.home)
        .env("PATH", path);
    if let Some(value) = frozen["ici_python"].as_str() {
        process.env("ICI_PYTHON", paths.expand(value));
    }
    let output = process.output().unwrap();
    Run {
        code: output.status.code().unwrap(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned()
            + &String::from_utf8_lossy(&output.stderr),
        root,
    }
}

/// Frozen targets by (path, name, status, message). Accepted differences elsewhere in the
/// fragment: native locates a pytest case at its definition line and adds threshold targets.
fn target_keys(engine: &Value) -> Vec<Value> {
    let mut keys: Vec<_> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!([
                target["file_path"],
                target["target_name"],
                target["status"],
                target["message"]
            ])
        })
        .collect();
    keys.sort_by_key(Value::to_string);
    keys
}

fn assert_engine(frozen: &Value, native: &Value, scenario: &str) {
    for key in ["status", "evidence", "required"] {
        assert_eq!(native[key], frozen[key], "{scenario}: {key}: {native:#}");
    }
    let native_keys = target_keys(native);
    for key in target_keys(frozen) {
        assert!(
            native_keys.contains(&key),
            "{scenario}: missing {key}: {native_keys:#?}"
        );
    }
}

fn expected_interpreter(frozen: &Value, paths: &Paths) -> String {
    paths.expand(frozen["engine"]["interpreters"][0].as_str().unwrap())
}

#[test]
#[ignore = "explicit venv CPython with pytest/coverage and bare CPython 3.12"]
fn legacy_test_without_venv_or_declaration_runs_the_launcher_selected_interpreter() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let paths = Paths::new(&oracle);
    // PATH python3.10, ICI_PYTHON before PATH, and a fake python3.14 skipped by the 3.10 check.
    for (scenario, candidate) in [
        ("test_path", "python3.10"),
        ("test_ici_python", "<VENV_BIN>/python"),
        ("test_version_skip", "python3.10"),
    ] {
        let frozen = &oracle["runs"][scenario];
        let native = run(&oracle, &paths, scenario);
        assert_eq!(
            i64::from(native.code),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}: {}",
            native.stdout
        );
        let report = native.report("test_report.json");
        assert_engine(&frozen["engine"], &report, scenario);
        let notes = native.launcher_notes();
        let interpreter = expected_interpreter(frozen, &paths);
        // The test and coverage plans record the same (deduplicated) selection.
        assert_eq!(notes.len(), 1, "{scenario}: {notes:#?}");
        for note in &notes {
            assert!(
                note.starts_with(&format!(
                    "legacy pyz launcher rule selected {interpreter} ({}, Python 3.10)",
                    paths.expand(candidate)
                )) && note
                    .ends_with("neither engines.test.python nor a project .venv was available"),
                "{scenario}: {note}"
            );
        }
        if scenario == "test_version_skip" {
            assert!(
                notes[0].contains(&format!(
                    "skipped {}/python3.14 (version probe exit 1)",
                    paths.values["<FAKE>"]
                )),
                "{}",
                notes[0]
            );
        }
    }
}

#[test]
#[ignore = "explicit venv CPython with pytest/coverage and bare CPython 3.12"]
fn venv_and_declared_bare_name_come_before_the_launcher_rule() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let paths = Paths::new(&oracle);
    for scenario in ["test_venv_first", "test_declared_bare"] {
        let frozen = &oracle["runs"][scenario];
        let native = run(&oracle, &paths, scenario);
        assert_eq!(
            i64::from(native.code),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}: {}",
            native.stdout
        );
        assert_engine(
            &frozen["engine"],
            &native.report("test_report.json"),
            scenario,
        );
        // Neither is a launcher fallback: no selection note.
        assert!(
            native.launcher_notes().is_empty(),
            "{scenario}: {:#?}",
            native.limitations()
        );
    }
}

#[test]
#[ignore = "explicit venv CPython with pytest/coverage and bare CPython 3.12"]
fn no_qualifying_launcher_candidate_blocks_with_a_hint_where_the_pyz_could_not_start() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let paths = Paths::new(&oracle);
    let frozen = &oracle["runs"]["test_none"];
    // Intended difference: the frozen launcher itself exits 1 before ici runs (no report).
    assert_eq!(frozen["exit_code"], 1);
    assert!(frozen["engine"].is_null());
    assert!(frozen["stderr"].as_str().unwrap().contains("ICI_PYTHON"));
    let native = run(&oracle, &paths, "test_none");
    assert_eq!(native.code, 3, "{}", native.stdout);
    let report = native.report("test_report.json");
    assert_eq!(report["evidence"], "NOT_RUN");
    let fake = &paths.values["<FAKE>"];
    for check in report["extra"]["native_checks"].as_array().unwrap() {
        assert_eq!(check["state"], "BLOCKED", "{check:#}");
        let summary = check["summary"].as_str().unwrap();
        assert!(
            summary.starts_with("no project interpreter \u{2014} none of 9 candidate(s) could be used; looked at ")
                && summary.contains("ICI_PYTHON (unset), python3.14, python3.13")
                && summary.contains(&format!("skipped {fake}/python3.14 (version probe exit 1)"))
                && summary.contains(&format!("skipped {fake}/python3 (version probe exit 1)"))
                && summary.ends_with("set engines.test.python, provide a .venv in the project, or set ICI_PYTHON to a Python 3.10+ interpreter (legacy pyz launcher rule)"),
            "{summary}"
        );
    }
}

#[test]
#[ignore = "explicit venv CPython with pytest/coverage and bare CPython 3.12"]
fn legacy_sanitize_compat_and_current_integration_use_the_launcher_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let paths = Paths::new(&oracle);
    // Python sanitize: the same order as the test engine.
    let frozen = &oracle["runs"]["sanitize_path"];
    let native = run(&oracle, &paths, "sanitize_path");
    assert_eq!(
        i64::from(native.code),
        frozen["exit_code"].as_i64().unwrap(),
        "{}",
        native.stdout
    );
    assert_engine(
        &frozen["engine"],
        &native.report("sanitize_report.json"),
        "sanitize_path",
    );
    let notes = native.launcher_notes();
    assert_eq!(notes.len(), 1, "{notes:#?}");
    assert!(notes[0].starts_with(&format!(
        "legacy pyz launcher rule selected {} (python3.10, Python 3.10)",
        expected_interpreter(frozen, &paths)
    )));
    // python_compat without interpreters: the launcher alone, ignoring the project .venv.
    let frozen = &oracle["runs"]["compat_path"];
    assert_eq!(frozen["venv"], true);
    let native = run(&oracle, &paths, "compat_path");
    assert_eq!(
        i64::from(native.code),
        frozen["exit_code"].as_i64().unwrap(),
        "{}",
        native.stdout
    );
    let notes = native.launcher_notes();
    assert_eq!(notes.len(), 1, "{notes:#?}");
    let bare = Path::new(&bare_python()).canonicalize().unwrap();
    assert_eq!(
        frozen["engine"]["interpreters"][0], "<BARE_REAL>",
        "frozen ran the launcher's python3.12"
    );
    assert_eq!(
        notes[0],
        format!(
            "legacy pyz launcher rule selected {}/python3.12 (python3.12, Python 3.12): frozen legacy python_compat checks ici's own launcher-selected interpreter when engines.python_compat.interpreters is empty",
            paths.values["<BARE_BIN>"]
        )
    );
    assert_eq!(
        Path::new(&format!("{}/python3.12", paths.values["<BARE_BIN>"]))
            .canonicalize()
            .unwrap(),
        bare
    );
    // Integration {python:current}: the launcher's interpreter with symlinks resolved.
    let frozen = &oracle["runs"]["integration_current"];
    let native = run(&oracle, &paths, "integration_current");
    assert_eq!(
        i64::from(native.code),
        frozen["exit_code"].as_i64().unwrap(),
        "{}",
        native.stdout
    );
    let report = native.report("verify_report.json");
    assert_eq!(report["suite_status"], frozen["suite_status"]);
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "integration")
        .unwrap();
    assert_engine(&frozen["engine"], engine, "integration_current");
    let cases: Vec<_> = engine["extra"]["integration"]["cases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| {
            json!({"name": case["name"], "assertions": case["assertions"], "returncode": case["returncode"], "status": case["status"]})
        })
        .collect();
    assert_eq!(json!(cases), frozen["engine"]["cases"]);
    let notes = native.launcher_notes();
    assert_eq!(notes.len(), 1, "{notes:#?}");
    assert!(
        notes[0].ends_with(
            "frozen legacy integration {python:current} is ici's own launcher-selected interpreter, symlinks resolved"
        ),
        "{}",
        notes[0]
    );
}

/// Modern commands and schemas never use the launcher: `ici next plan` on the same legacy
/// project stays blocked and process-free (frozen next refuses a legacy file outright: "none
/// declares a [workspace]"), and a next-schema `ici test` keeps the strict next order.
#[test]
#[ignore = "explicit venv CPython with pytest/coverage and bare CPython 3.12"]
fn modern_commands_and_schemas_never_fall_back_to_the_launcher() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let paths = Paths::new(&oracle);
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("ici.toml"),
        oracle["runs"]["test_path"]["config"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(root.path().join("app.py"), oracle["app"].as_str().unwrap()).unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        oracle["test"].as_str().unwrap(),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "plan", "--json", "--profile", "deep"])
        .current_dir(root.path())
        .env_clear()
        .env("HOME", &paths.home)
        .env("PATH", &paths.values["<VENV_BIN>"])
        .output()
        .unwrap();
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    let mut seen = 0;
    for check in plan["plans"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|plan| plan["checks"].as_array().unwrap())
        .filter(|check| {
            matches!(
                check["check"].as_str(),
                Some("python.test" | "python.coverage")
            )
        })
    {
        seen += 1;
        assert_eq!(check["kind"], "blocked", "{check:#}");
        assert!(
            check["blocked"]
                .as_str()
                .unwrap()
                .ends_with("declare [python] executable or provide a .venv inside the component"),
            "{check:#}"
        );
    }
    assert_eq!(seen, 2);
    // A next-schema legacy command: no launcher either.
    fs::write(
        root.path().join("ici.toml"),
        "schema_version=1\n[workspace]\nname='w'\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["test", "--report"])
        .current_dir(root.path())
        .env_clear()
        .env("HOME", &paths.home)
        .env("PATH", &paths.values["<VENV_BIN>"])
        .output()
        .unwrap();
    assert_eq!(
        output.status.code(),
        Some(3),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    for check in report["extra"]["native_checks"].as_array().unwrap() {
        assert_eq!(check["state"], "BLOCKED", "{check:#}");
        assert!(
            check["summary"]
                .as_str()
                .unwrap()
                .ends_with("declare [python] executable or provide a .venv inside the component"),
            "{check:#}"
        );
    }
}
