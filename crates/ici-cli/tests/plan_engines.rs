//! Frozen v0.13.0 `ici next plan` / `ici analyze --plan` announcements and historical engine
//! subcommands on a `[workspace]` root (`tests/rust-parity/reporting/plan-engines-oracle.json`,
//! `capture_plan_and_engines.py`).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn materialize(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(base.join(".git")).unwrap();
}

fn two_lines(text: &str) -> Vec<&str> {
    text.lines().take(2).collect()
}

#[test]
fn plans_announce_and_workspace_engines_refuse_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/plan-engines-oracle.json"
    ))
    .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 10);
    for (name, frozen) in cases {
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap().join("project");
        materialize(&base, &oracle["tree"]);
        let environment = BTreeMap::from([
            ("HOME".to_owned(), home.path().display().to_string()),
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ]);
        let argv: Vec<&str> = std::iter::once("ici")
            .chain(
                frozen["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|arg| arg.as_str().unwrap()),
            )
            .collect();
        let expected_exit = frozen["exit_code"].as_i64().unwrap() as i32;
        let frozen_out = frozen["stdout"].as_str().unwrap();
        let frozen_err = frozen["stderr"].as_str().unwrap();
        let outcome = commands::dispatch(
            &registry::parse(argv.iter().copied()).unwrap(),
            &base,
            &environment,
        );
        let root = base.display().to_string();
        if name == "plan_nothing_selected" {
            // Frozen `next plan` refuses before announcing (config: … exit 2).
            assert_eq!((expected_exit, frozen_out), (2, ""));
            assert!(frozen_err.starts_with("config: "));
            // The refusal carries the frozen class prefix (`commands::diagnostic` prints it as is).
            let error = outcome.unwrap_err();
            assert_eq!(commands::diagnostic(&error), frozen_err, "{name}");
            continue;
        }
        let output = outcome.unwrap();
        let stdout = output.stdout.replace(&root, "<root>");
        let stderr = output.stderr.replace(&root, "<root>");
        assert_eq!(output.exit_code, expected_exit, "{name}: {stdout}{stderr}");
        if name.starts_with("engine_") {
            // Refused before anything runs: exact frozen message, nothing written.
            assert_eq!(stdout, frozen_out, "{name}");
            assert_eq!(stderr, frozen_err, "{name}");
            let written: Vec<_> = walk(&base)
                .into_iter()
                .filter(|path| !["ici.toml", "main.py"].contains(&path.as_str()))
                .collect();
            assert!(written.is_empty(), "{name}: {written:?}");
            continue;
        }
        if name.ends_with("_json") {
            assert_eq!(two_lines(&stderr), two_lines(frozen_err), "{name}");
            serde_json::from_str::<Value>(&stdout).unwrap();
        } else if name.ends_with("_nothing_selected") {
            // `analyze --plan` announces, then refuses with frozen's `config: ` line.
            assert_eq!(two_lines(&stdout), two_lines(frozen_out), "{name}");
            assert_eq!(stderr, frozen_err, "{name}");
        } else {
            assert_eq!(two_lines(&stdout), two_lines(frozen_out), "{name}");
            assert!(stderr.is_empty(), "{name}: {stderr}");
        }
    }
}

fn walk(base: &Path) -> Vec<String> {
    let mut found = vec![];
    let mut pending = vec![base.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                if path.file_name().is_some_and(|name| name != ".git") {
                    pending.push(path);
                }
            } else {
                found.push(path.strip_prefix(base).unwrap().display().to_string());
            }
        }
    }
    found
}
