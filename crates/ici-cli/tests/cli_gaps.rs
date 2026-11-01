//! Frozen v0.13.0 CLI behaviour at the config/CLI boundary, replayed through the native binary
//! (`tests/rust-parity/config/cli-gaps-oracle.json`, `capture_cli_gaps.py`): non-engine
//! subcommands on a `[workspace]` root, configuration/scope/report refusal prefixes, historical
//! commands run from a checkout subdirectory, an unparseable `pyproject.toml`, and a bare
//! `--local-config` interpreter. Every case keeps the frozen exit code and written files; stdout
//! and stderr are equal unless `cli-gaps-differences.json` records and asserts the native text.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const GLOBAL_DEFAULT: &str = "[ici] 기본 전역 설정을 생성했습니다: <home>/ici/ici.toml\n";

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/config")
        .join(name);
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

fn files(base: &Path) -> Vec<String> {
    let mut found = vec![];
    let mut pending = vec![base.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries {
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
    found.sort();
    found
}

struct Native {
    exit: i64,
    stdout: String,
    stderr: String,
    written: Vec<String>,
    home: Vec<String>,
}

fn run(case: &Value) -> Native {
    let home = tempfile::tempdir().unwrap();
    let home_path = home.path().canonicalize().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    let tree = case["tree"].as_object().unwrap();
    for (name, text) in tree {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(base.join(".git")).unwrap();
    let argv: Vec<&str> = case["argv"]
        .as_array()
        .unwrap()
        .iter()
        .map(|arg| arg.as_str().unwrap())
        .collect();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(&argv)
        .current_dir(base.join(case["cwd"].as_str().unwrap()))
        .env_clear()
        .env("HOME", &home_path)
        .env("XDG_CONFIG_HOME", &home_path)
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    let scrub = |bytes: &[u8]| {
        String::from_utf8_lossy(bytes)
            .replace(&base.display().to_string(), "<root>")
            .replace(&home_path.display().to_string(), "<home>")
    };
    Native {
        exit: i64::from(output.status.code().unwrap()),
        stdout: scrub(&output.stdout),
        stderr: scrub(&output.stderr),
        // Native evidence namespace (differences `global.evidence_namespace`).
        written: files(&base)
            .into_iter()
            .filter(|path| !tree.contains_key(path) && !path.contains(".ici/rust-v1/"))
            .collect(),
        home: files(&home_path),
    }
}

fn blocked(stdout: &str, name: &str) -> Vec<(String, String)> {
    let plan: Value = serde_json::from_str(stdout).unwrap();
    let mut found: Vec<(String, String)> = plan["plans"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|item| item["checks"].as_array().unwrap())
        .filter_map(|check| {
            let reason = check["blocked"].as_str()?;
            reason
                .contains(name)
                .then(|| (check["task_id"].as_str().unwrap().into(), reason.into()))
        })
        .collect();
    found.sort();
    found
}

fn expect(name: &str, stream: &str, rule: &Value, native: &str, frozen: &str) {
    match rule {
        Value::String(kind) if kind == "equal" => assert_eq!(native, frozen, "{name}: {stream}"),
        Value::Object(rule) if rule.contains_key("exact") => {
            assert_eq!(native, rule["exact"].as_str().unwrap(), "{name}: {stream}");
            assert_ne!(native, frozen, "{name}: {stream} difference is stale");
        }
        Value::Object(rule) if rule.contains_key("lines") => {
            let count = rule["lines"].as_u64().unwrap() as usize;
            let head = |text: &str| text.lines().take(count).collect::<Vec<_>>().join("\n");
            assert_eq!(head(native), head(frozen), "{name}: {stream}");
        }
        Value::Object(rule) if rule.contains_key("contains") => {
            for needle in rule["contains"].as_array().unwrap() {
                assert!(
                    native.contains(needle.as_str().unwrap()),
                    "{name}: {stream} lacks {needle}: {native}"
                );
            }
        }
        other => panic!("{name}: unknown {stream} rule {other}"),
    }
}

#[test]
fn frozen_cli_boundary_cases_replay_natively_or_are_recorded_differences() {
    let oracle = fixture("cli-gaps-oracle.json");
    let differences = fixture("cli-gaps-differences.json");
    let recorded = differences["cases"].as_object().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 45);
    for name in recorded.keys() {
        assert!(cases.contains_key(name), "{name}: difference names no case");
        assert!(
            !recorded[name]["reason"].as_str().unwrap().is_empty(),
            "{name}"
        );
    }
    for (name, frozen) in cases {
        let native = run(frozen);
        assert_eq!(
            native.exit,
            frozen["exit_code"].as_i64().unwrap(),
            "{name}: {}{}",
            native.stdout,
            native.stderr
        );
        let frozen_written: Vec<String> = frozen["written"]
            .as_array()
            .unwrap()
            .iter()
            .map(|path| path.as_str().unwrap().into())
            .collect();
        assert_eq!(native.written, frozen_written, "{name}: written");
        let frozen_home: Vec<&str> = frozen["home_written"]
            .as_array()
            .unwrap()
            .iter()
            .map(|path| path.as_str().unwrap())
            .filter(|path| *path != "ici/ici.toml")
            .collect();
        assert_eq!(
            native.home, frozen_home,
            "{name}: native never writes the user config"
        );
        let frozen_stdout = frozen["stdout"].as_str().unwrap();
        let frozen_stderr = frozen["stderr"]
            .as_str()
            .unwrap()
            .replace(GLOBAL_DEFAULT, "");
        let Some(difference) = recorded.get(name) else {
            assert_eq!(native.stdout, frozen_stdout, "{name}: stdout");
            assert_eq!(native.stderr, frozen_stderr, "{name}: stderr");
            continue;
        };
        if difference["stdout"] == "blocked" {
            let found = blocked(&native.stdout, "ici-parity-missing-python");
            assert!(!found.is_empty(), "{name}");
            assert_eq!(
                found,
                blocked(frozen_stdout, "ici-parity-missing-python"),
                "{name}"
            );
        } else {
            expect(
                name,
                "stdout",
                &difference["stdout"],
                &native.stdout,
                frozen_stdout,
            );
        }
        expect(
            name,
            "stderr",
            &difference["stderr"],
            &native.stderr,
            &frozen_stderr,
        );
    }
}
