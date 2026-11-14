//! Root ruling 2026-11-14 (f1): historical commands run below a `schema_version = 2` root follow
//! frozen v0.13.0 exactly. Frozen reads only the working directory's configuration
//! (`load_config(base=cwd)`), except that its `find_workspace_root` also claims a v2 file that
//! declares `[workspace]`: below one, `ici verify` cuts over and frozen's next loader refuses the
//! schema version, and a configuration error of the working directory's file is the `[workspace]`
//! refusal. Replays `tests/rust-parity/config/v2-subdirectory-oracle.json`
//! (`capture_v2_subdirectory.py`): `line`, `verify --report`, `doctor --json`,
//! `export-compilation-context` and `publish` from `sub/` below a plain, a `[workspace]` and an
//! invalid v2 root, with no `sub/ici.toml`, a valid legacy one and an invalid one. Compared: the
//! exit code, the written files, the configuration in use (the line report's file inventory, the
//! doctor policy) and the key outputs.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/config/v2-subdirectory-oracle.json");
/// Frozen created `$XDG_CONFIG_HOME/ici/ici.toml` on first use and said so; native never writes
/// the user's configuration directory (cli-gaps-differences.json `global.global_default`).
const GLOBAL_DEFAULT: &str = "[ici] 기본 전역 설정을 생성했습니다: <home>/ici/ici.toml\n";
/// cli-gaps-differences.json `subdir_publish_invalid_root`: frozen crashed with an AttributeError
/// traceback (exit 1) because no verify_report.json exists; native applies frozen's
/// ReportPublisher skip rule first (frozen defect not reproduced).
const PUBLISH_SKIP: &str = "[publish] --publish requires a GitHub Actions environment; skipping.\n";
/// cli-gaps-differences.json `legacy_invalid_line`: the same exit 2, `Configuration error: `
/// prefix and key; the native loader names the file:line:column origin and words the problem.
const FROZEN_INVALID: &str = "Configuration error: engines.line.warn_limit must be an integer\n";
const NATIVE_INVALID: &str = "Configuration error: <root>/sub/ici.toml:4:1: engines.line.warn_limit: invalid value type for configuration key\n";
const PUBLISH_CRASH: &str =
    "<traceback> AttributeError: 'NoneType' object has no attribute 'results'\n";

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
    line: Option<Value>,
    verify: Option<Value>,
}

fn run(case: &Value, python: Option<&str>) -> Native {
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
    let cwd = base.join(case["cwd"].as_str().unwrap());
    let mut command = Command::new(env!("CARGO_BIN_EXE_ici"));
    command
        .args(&argv)
        .current_dir(&cwd)
        .env_clear()
        .env("HOME", &home_path)
        .env("XDG_CONFIG_HOME", &home_path)
        .env("PATH", "/usr/bin:/bin");
    // The frozen capture ran under its own interpreter, whose `-m mypy` the legacy type engine
    // used; ICI_PYTHON pins the same runtime for the native legacy launcher rule.
    if let Some(python) = python {
        command.env("ICI_PYTHON", python);
    }
    let output = command.output().unwrap();
    let scrub = |text: &str| {
        text.replace(&base.display().to_string(), "<root>")
            .replace(&home_path.display().to_string(), "<home>")
    };
    let report = |name: &str| -> Option<Value> {
        let text = fs::read_to_string(cwd.join(name)).ok()?;
        Some(serde_json::from_str(&scrub(&text)).unwrap())
    };
    let line = report("line_report.json").map(|report| {
        serde_json::json!({
            "all": report["extra"]["all"],
            "files_data": report["extra"]["files_data"],
        })
    });
    let verify = report("verify_report.json").map(|report| {
        let engines: Vec<Value> = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| {
                serde_json::json!([
                    item["engine_name"],
                    item["status"],
                    item["evidence"],
                    item.get("required").cloned().unwrap_or(Value::Null),
                ])
            })
            .collect();
        serde_json::json!({"suite_status": report["suite_status"], "engines": engines})
    });
    Native {
        exit: i64::from(output.status.code().unwrap()),
        stdout: scrub(&String::from_utf8_lossy(&output.stdout)),
        stderr: scrub(&String::from_utf8_lossy(&output.stderr)),
        // Native evidence namespace (cli-gaps-differences.json `global.evidence_namespace`).
        written: files(&base)
            .into_iter()
            .filter(|path| !tree.contains_key(path) && !path.contains(".ici/rust-v1/"))
            .collect(),
        home: files(&home_path),
        line,
        verify,
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().to_owned())
        .collect()
}

/// A frozen verify suite that ran: a required engine ERROR with NOT_RUN evidence (here sanitize
/// without a tests directory) is native exit 3 where frozen exited 1 (decisions.md "legacy 종료
/// 코드 3의 범위").
fn native_exit(name: &str, frozen: &Value) -> i64 {
    let exit = frozen["exit_code"].as_i64().unwrap();
    let Some(suite) = frozen["reports"].get("verify_report.json") else {
        return exit;
    };
    let incomplete = suite["engines"].as_array().unwrap().iter().any(|engine| {
        engine[1] == "ERROR" && engine[2] == "NOT_RUN" && engine[3] == Value::Bool(true)
    });
    if incomplete {
        assert_eq!(exit, 1, "{name}: frozen exit");
        3
    } else {
        exit
    }
}

fn replay(name: &str, frozen: &Value, python: Option<&str>) {
    let native = run(frozen, python);
    assert_eq!(
        native.exit,
        native_exit(name, frozen),
        "{name}: {}{}",
        native.stdout,
        native.stderr
    );
    // decisions.md "legacy Python 도구의 격리 옵션과 프로젝트에 남는 파일": native mypy writes no
    // `.mypy_cache/` into the project.
    let frozen_written: Vec<String> = strings(&frozen["written"])
        .into_iter()
        .filter(|path| !path.starts_with("sub/.mypy_cache/"))
        .collect();
    assert_eq!(native.written, frozen_written, "{name}: written");
    // Native keeps its cache under the project's .ici/rust-v1 and never writes the user's
    // configuration or cache directories.
    assert_eq!(native.home, Vec::<String>::new(), "{name}: home");
    let frozen_stdout = frozen["stdout"].as_str().unwrap();
    let frozen_stderr = frozen["stderr"]
        .as_str()
        .unwrap()
        .replace(GLOBAL_DEFAULT, "");
    match frozen["argv"][0].as_str().unwrap() {
        "publish" if frozen_stderr == PUBLISH_CRASH => {
            assert_eq!(native.stdout, PUBLISH_SKIP, "{name}: stdout");
            assert_eq!(native.stderr, "", "{name}: stderr");
        }
        // capability:doctor-env-cache: the rest of the diagnosis describes the native runtime.
        "doctor" if frozen.get("doctor").is_some() => {
            let diagnosis: Value = serde_json::from_str(&native.stdout).unwrap();
            for key in ["required_tools", "effective_required_tools"] {
                assert_eq!(diagnosis[key], frozen["doctor"][key], "{name}: {key}");
            }
            assert_eq!(native.stderr, frozen_stderr, "{name}: stderr");
        }
        // The suite frame of a completed verify is compared on its own rows; here the engines.
        "verify" if frozen["reports"].get("verify_report.json").is_some() => {
            assert_eq!(
                native.verify.as_ref(),
                Some(&frozen["reports"]["verify_report.json"]),
                "{name}: verify_report.json"
            );
        }
        _ if frozen_stderr == FROZEN_INVALID => {
            assert_eq!(native.stdout, frozen_stdout, "{name}: stdout");
            assert_eq!(native.stderr, NATIVE_INVALID, "{name}: stderr");
        }
        _ => {
            assert_eq!(native.stdout, frozen_stdout, "{name}: stdout");
            assert_eq!(native.stderr, frozen_stderr, "{name}: stderr");
        }
    }
    assert_eq!(
        native.line.as_ref(),
        frozen["reports"].get("line_report.json"),
        "{name}: line_report.json"
    );
}

/// Every case except a `verify` suite that runs its engines: the configuration each command reads
/// (the working directory's file, or the defaults; never the v2 file above), frozen's refusals
/// (an invalid working-directory file, the `[workspace]` refusal and verify's cutover refusal
/// below a v2 `[workspace]` root), the line report's file inventory and the doctor policy.
#[test]
fn historical_commands_below_a_v2_root_follow_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let mut count = 0;
    for (name, frozen) in oracle["cases"].as_object().unwrap() {
        if frozen["reports"].get("verify_report.json").is_some() {
            continue;
        }
        replay(name, frozen, None);
        count += 1;
    }
    assert_eq!(count, 50);
}

/// The four `verify --report` suites that run (plain and invalid v2 roots, with and without a
/// legacy working-directory file): the same engines with frozen's status, evidence and required
/// flag, and the suite status.
#[test]
#[ignore = "explicit actual frozen capture runtime with mypy (ICI_TEST_PYTHON)"]
fn verify_below_a_v2_root_runs_the_working_directory_suite_like_frozen() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("the frozen capture runtime");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let mut count = 0;
    for (name, frozen) in oracle["cases"].as_object().unwrap() {
        if frozen["reports"].get("verify_report.json").is_none() {
            continue;
        }
        replay(name, frozen, Some(&python));
        count += 1;
    }
    assert_eq!(count, 4);
}
