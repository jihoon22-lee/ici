//! Which mypy a legacy-schema historical command runs, replayed against frozen v0.13.0
//! (`tests/rust-parity/config/legacy-type-interpreter-oracle.json`, captured through a private
//! copy of the released pyz by `capture_legacy_type_interpreter.py`). Standalone `ici type` uses
//! PATH mypy, then the project `.venv` mypy; `ici verify` runs `<interpreter> -m mypy` with the
//! project `.venv` python or else the pyz launcher rule's interpreter (frozen `sys.executable`,
//! type_check.py:176 / toolchain.py:449), and falls back to the estimated AST scan when that
//! module is not importable. The engine verdict matches frozen and the selected executable is
//! recorded in the run's limitations.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/config/legacy-type-interpreter-oracle.json");

fn directory(variable: &str) -> String {
    let python = std::env::var(variable).unwrap_or_else(|_| panic!("explicit {variable}"));
    Path::new(&python).parent().unwrap().display().to_string()
}

#[test]
#[ignore = "explicit ICI_TEST_PYTHON (venv with mypy) and ICI_TEST_PYTHON_BARE (no mypy)"]
fn legacy_type_and_verify_select_mypy_like_the_frozen_engine() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let values = [
        ("<VENV_BIN>", directory("ICI_TEST_PYTHON")),
        ("<BARE_BIN>", directory("ICI_TEST_PYTHON_BARE")),
    ];
    let expand = |text: &str| {
        values.iter().fold(text.to_string(), |text, (key, value)| {
            text.replace(key, value)
        })
    };
    let runs = oracle["runs"].as_object().unwrap();
    assert_eq!(runs.len(), 7);
    for (name, frozen) in runs {
        let work = tempfile::tempdir().unwrap();
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let root = work.path().join("project");
        fs::create_dir(&root).unwrap();
        let root = root.canonicalize().unwrap();
        fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        fs::write(root.join("app.py"), oracle["app"].as_str().unwrap()).unwrap();
        for entry in frozen["venv"].as_array().unwrap() {
            let entry = entry.as_str().unwrap();
            let target = root
                .join(".venv/bin")
                .join(entry.trim_end_matches("-broken"));
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            let text = if entry.ends_with("-broken") {
                "#!/bin/sh\nexit 3\n".to_string()
            } else {
                format!("#!/bin/sh\nexec {}/{entry} \"$@\"\n", values[0].1)
            };
            fs::write(&target, text).unwrap();
            fs::set_permissions(&target, std::os::unix::fs::PermissionsExt::from_mode(0o755))
                .unwrap();
        }
        let command = frozen["command"].as_str().unwrap();
        let path: Vec<String> = frozen["path"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| expand(entry.as_str().unwrap()))
            .collect();
        let mut argv = vec![command, "--report"];
        if command == "verify" {
            argv.push("--no-cache");
        }
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(&argv)
            .current_dir(&root)
            .env_clear()
            .env("HOME", &home)
            .env("PATH", path.join(":"))
            .output()
            .unwrap();
        let text = String::from_utf8_lossy(&output.stderr);
        assert_eq!(
            i64::from(output.status.code().unwrap()),
            frozen["exit_code"].as_i64().unwrap(),
            "{name}: {text}"
        );
        let report: Value = serde_json::from_str(
            &fs::read_to_string(root.join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let engine = if command == "verify" {
            report["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == "type")
                .unwrap()
                .clone()
        } else {
            report
        };
        let expected = &frozen["engine"];
        for key in ["status", "summary", "evidence"] {
            assert_eq!(engine[key], expected[key], "{name}: {key}");
        }
        let mut targets: Vec<&str> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| target["target_name"].as_str().unwrap())
            .collect();
        targets.sort();
        targets.dedup();
        assert_eq!(
            serde_json::json!(targets),
            expected["targets"],
            "{name}: targets"
        );
        // The executable frozen ran (`tool_evidence[].path`) is the one native records.
        let result = fs::read_to_string(root.join(".ici/rust-v1/result.json")).unwrap();
        let selected = expected["tool_evidence"][0]["path"].as_str().unwrap();
        if selected.is_empty() {
            assert!(
                !result.contains("runs mypy as") && !result.contains("selected PATH mypy"),
                "{name}"
            );
        } else {
            let selected = expand(selected).replace("<ROOT>", &root.display().to_string());
            let notes = [
                format!("legacy `ici type` selected PATH mypy {selected}\""),
                format!("legacy `ici type` selected the project mypy {selected}\""),
                format!("module of the project interpreter {selected}\""),
                format!("legacy pyz launcher rule selected {selected} ("),
            ];
            assert!(
                notes.iter().any(|note| result.contains(note.as_str())),
                "{name}: {selected} not recorded"
            );
        }
    }
}
