//! Frozen `project.compile_database` load checks on a legacy C++ project through the actual
//! native `ici export-compilation-context` (no `--database`): the setting the configuration
//! accepts is the database the export reads. Golden:
//! tests/rust-parity/cpp/compile-database-setting-oracle.json (capture_compile_database_setting.py).
use serde_json::Value;
use std::{fs, path::Path, process::Command};

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

#[test]
fn legacy_compile_database_setting_is_refused_or_read_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compile-database-setting-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let database = oracle["files"]["build/compile_commands.json"]
        .as_str()
        .unwrap();
    let mut failures = vec![];
    let cases = oracle["cases"].as_object().unwrap();
    for (name, frozen) in cases {
        let work = tempfile::tempdir().unwrap();
        let project = work.path().join("project");
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
            &project,
        );
        fs::create_dir_all(project.join("build")).unwrap();
        fs::write(project.join("build/compile_commands.json"), database).unwrap();
        let outside = work.path().join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("compile_commands.json"), database).unwrap();
        std::os::unix::fs::symlink(&outside, project.join("outside-link")).unwrap();
        let value = frozen["value"]
            .as_str()
            .unwrap()
            .replace("<root>", &project.display().to_string());
        fs::write(
            project.join("ici.toml"),
            format!(
                "{}compile_database = {value}\n",
                oracle["base"].as_str().unwrap()
            ),
        )
        .unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["export-compilation-context", "--output", "-"])
            .current_dir(&project)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", work.path())
            .env("XDG_CONFIG_HOME", work.path())
            .output()
            .unwrap();
        let code = output.status.code();
        let stderr = String::from_utf8_lossy(&output.stderr)
            .replace(&project.display().to_string(), "<root>")
            .replace(&work.path().display().to_string(), "<work>");
        let stderr = stderr.trim();
        if code != frozen["exit_code"].as_i64().map(|code| code as i32) {
            failures.push(format!(
                "{name}: exit {code:?} vs {}: {stderr}",
                frozen["exit_code"]
            ));
            continue;
        }
        let expected = frozen["stderr"].as_str().unwrap();
        if expected.starts_with("Configuration error: ") {
            // Configuration refusals keep native's located wording (ruling e: same failure
            // class, exit 2, nothing read or written); they name the key and the file.
            if !stderr.contains("project.compile_database") || !stderr.contains("ici.toml") {
                failures.push(format!(
                    "{name}: refusal does not name the key/file: {stderr}"
                ));
            }
            if !output.stdout.is_empty() {
                failures.push(format!("{name}: a refusal wrote a payload"));
            }
            continue;
        }
        if stderr != expected {
            failures.push(format!("{name}: stderr {stderr:?} vs {expected:?}"));
        }
        if frozen["payload"].is_null() {
            if !output.stdout.is_empty() {
                failures.push(format!("{name}: unexpected payload"));
            }
            continue;
        }
        let mut payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        // The producer names its own release; every compilation fact and digest is frozen's.
        payload["producer"]["version"] = frozen["payload"]["producer"]["version"].clone();
        if payload != frozen["payload"] {
            failures.push(format!("{name}: payload differs: {payload}"));
        }
    }
    assert_eq!(cases.len(), 10);
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
