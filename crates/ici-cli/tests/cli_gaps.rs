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
        Value::Object(rule) if rule.contains_key("json") => {
            let native: Value = serde_json::from_str(native).unwrap();
            let frozen: Value = serde_json::from_str(frozen).unwrap();
            for pointer in rule["json"].as_array().unwrap() {
                let pointer = pointer.as_str().unwrap();
                assert!(
                    frozen.pointer(pointer).is_some(),
                    "{name}: frozen lacks {pointer}"
                );
                assert_eq!(
                    native.pointer(pointer),
                    frozen.pointer(pointer),
                    "{name}: {stream} {pointer}"
                );
            }
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

const GROUPS: [(&str, &[&str]); 7] = [
    ("workspace", &["workspace_"]),
    (
        "refusal",
        &["next_invalid_", "unparseable_", "legacy_invalid_", "scope_"],
    ),
    (
        "stored",
        &[
            "report_without_result",
            "publish_without_result",
            "diff_without_baseline",
        ],
    ),
    (
        "rooted",
        &[
            "configless_subdir_line",
            "configless_root_line",
            "legacy_root_",
        ],
    ),
    (
        "pyproject",
        &[
            "legacy_pyproject_",
            "next_pyproject_",
            "configless_pyproject_",
        ],
    ),
    ("interpreter", &["bare_local_interpreter_"]),
    ("subdirectory", &["subdir_"]),
];

fn group_of(name: &str) -> Option<&'static str> {
    GROUPS
        .iter()
        .find(|(_, prefixes)| prefixes.iter().any(|prefix| name.starts_with(prefix)))
        .map(|(group, _)| *group)
}

/// Replays every case of one group; returns how many ran.
fn replay(group: &str) -> usize {
    let oracle = fixture("cli-gaps-oracle.json");
    let differences = fixture("cli-gaps-differences.json");
    let recorded = differences["cases"].as_object().unwrap();
    let mut count = 0;
    for (name, frozen) in oracle["cases"].as_object().unwrap() {
        if group_of(name) != Some(group) {
            continue;
        }
        count += 1;
        let native = run(frozen);
        let frozen_exit = frozen["exit_code"].as_i64().unwrap();
        let expected_exit = match recorded
            .get(name)
            .and_then(|difference| difference.get("exit"))
        {
            Some(exit) => {
                let exit = exit.as_i64().unwrap();
                assert_ne!(exit, frozen_exit, "{name}: exit difference is stale");
                exit
            }
            None => frozen_exit,
        };
        assert_eq!(
            native.exit, expected_exit,
            "{name}: {}{}",
            native.stdout, native.stderr
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
    assert!(count > 0, "{group}");
    count
}

#[test]
fn every_frozen_case_and_recorded_difference_belongs_to_a_replayed_group() {
    let oracle = fixture("cli-gaps-oracle.json");
    let differences = fixture("cli-gaps-differences.json");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 81);
    for name in cases.keys() {
        assert!(group_of(name).is_some(), "{name}: not replayed");
    }
    for (name, difference) in differences["cases"].as_object().unwrap() {
        assert!(cases.contains_key(name), "{name}: difference names no case");
        assert!(!difference["reason"].as_str().unwrap().is_empty(), "{name}");
    }
}

/// Frozen `main_callback`: historical subcommands on a `[workspace]` root refuse with the
/// frozen message and write nothing; from a subdirectory without its own file they run.
#[test]
fn workspace_roots_refuse_stable_subcommands_like_frozen() {
    assert_eq!(replay("workspace"), 24);
}

/// `config: ` (next path), `Configuration error: ` (stable loader), exit 2, nothing written.
#[test]
fn configuration_and_scope_refusals_carry_the_frozen_class_prefix() {
    assert_eq!(replay("refusal"), 39);
}

#[test]
fn stored_result_refusals_name_the_missing_file() {
    assert_eq!(replay("stored"), 3);
}

/// Frozen `load_config(base=cwd)`: the working directory is the root; no walk up.
#[test]
fn historical_commands_are_rooted_at_the_working_directory() {
    assert_eq!(replay("rooted"), 5);
}

#[test]
fn unparseable_pyproject_is_never_a_configuration_refusal() {
    assert_eq!(replay("pyproject"), 3);
}

#[test]
fn bare_local_interpreter_blocks_with_the_frozen_resolver_text() {
    assert_eq!(replay("interpreter"), 1);
}

const HISTORICAL: [&str; 20] = [
    "python-compat",
    "resource",
    "security",
    "cognitive",
    "cycle",
    "line",
    "lint",
    "test",
    "type",
    "complexity",
    "sanitize",
    "thread-sanitize",
    "dead",
    "dup",
    "exception",
    "build",
    "verify",
    "doctor",
    "publish",
    "export-compilation-context",
];

fn gate(config: &str, cwd: &str, command: &str) -> Result<Option<String>, String> {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    fs::create_dir_all(base.join("pkg")).unwrap();
    fs::write(base.join("ici.toml"), config).unwrap();
    fs::write(base.join("main.py"), "X = 1\n").unwrap();
    let invocation = ici_cli::registry::parse(["ici", command]).unwrap();
    let environment = std::collections::BTreeMap::from([(
        "XDG_CONFIG_HOME".to_string(),
        base.join("xdg").display().to_string(),
    )]);
    ici_cli::cutover::stable_refusal(&invocation, &base.join(cwd), &environment)
        .map(|refusal| refusal.map(|output| output.stderr))
        .map_err(|error| error.to_string())
}

/// The gate refuses only what frozen's stable loader could not read: a native
/// `schema_version = 2` file (even with a `[workspace]` table) and a legacy file are read by
/// every historical subcommand, `env`/`cache`/`setup` never consult it, and a subdirectory
/// below a next-v1 root without its own file runs with the defaults.
#[test]
fn the_stable_loader_gate_lets_readable_configurations_and_exempt_commands_through() {
    let v1 = "schema_version = 1\n[workspace]\nname = \"demo\"\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\n";
    let v2 = "schema_version = 2\n[workspace]\nname = \"demo\"\n";
    let legacy = "[project]\ntype = \"python\"\nsource_dirs = [\".\"]\n";
    for command in HISTORICAL {
        assert_eq!(gate(v2, ".", command), Ok(None), "{command}: v2");
        assert_eq!(gate(legacy, ".", command), Ok(None), "{command}: legacy");
        assert_eq!(
            gate(v1, "pkg", command),
            Ok(None),
            "{command}: v1 subdirectory"
        );
        let refused = gate(v1, ".", command);
        if command == "verify" {
            assert_eq!(refused, Ok(None), "verify on a next root is the cutover");
        } else {
            assert!(
                refused
                    .unwrap()
                    .unwrap()
                    .contains("this workspace declares a [workspace] (ici-next) config"),
                "{command}"
            );
        }
        let invalid = gate("[engines.line]\nwarn_limit = \"many\"\n", ".", command).unwrap_err();
        assert!(
            invalid.starts_with("Configuration error: ")
                && invalid.contains("engines.line.warn_limit"),
            "{command}: {invalid}"
        );
    }
    for command in [
        "env",
        "cache",
        "init",
        "analyze",
        "report",
        "next plan",
        "next verify",
    ] {
        let argv: Vec<&str> = command.split(' ').collect();
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("ici.toml"), "[[\n").unwrap();
        let invocation = ici_cli::registry::parse(std::iter::once("ici").chain(argv)).unwrap();
        assert!(
            ici_cli::cutover::stable_refusal(&invocation, directory.path(), &Default::default())
                .unwrap()
                .is_none(),
            "{command}: not a stable-loader subcommand"
        );
    }
}

/// Frozen `doctor` (`collect_diagnostics(Path.cwd())`), `export-compilation-context`
/// (`root = Path.cwd()`) and `publish` (cwd-relative report paths) read only the working
/// directory's configuration: a root file above it, valid or invalid, is not consulted.
#[test]
fn doctor_export_and_publish_read_only_the_working_directory_configuration() {
    assert_eq!(replay("subdirectory"), 6);
}
