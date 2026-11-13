//! Frozen legacy lint for each `build_replay_command` refusal code of the C++ syntax replay
//! (`_cpp_lint.py:372`, `core/cpp_replay.py`), through the real CLI's saved report
//! (`tests/rust-parity/cpp/lint-replay-codes-oracle.json`, `capture_lint_replay_codes.py`): one
//! refused src/a.cpp beside a src/b.cpp that replays cleanly. Reached at replay: a repeated
//! source (`invalid-source-operands`), stdin and a second `--` (`extra-compiler-operand`), a
//! compiler missing from PATH or not resolvable (`compiler-unavailable`), no direct driver
//! (`unsupported-compiler-driver`) and a project-contained compiler
//! (`project-compiler-rejected`), and 32,768 arguments that pass ingestion but not the sanitized
//! replay bound (`command-too-large`). Stopped earlier by ingestion, so the replay code is
//! unreachable there: an empty argument and 32,769 arguments (`invalid-arguments`, frozen's
//! `invalid-command-argument`/`command-too-large`), a bare `@` (`response-file-invalid`, frozen's
//! `unexpanded-response-file`), a directory that is a file (the unit is dropped, frozen's
//! `unsafe-working-directory`). Compared in full except the run's own timing and cache identity,
//! the native evidence keys (decisions.md "legacy `extra` 안 native 증거 키") and the compiler
//! records (decisions.md "compiler·linker·binutils 실제 실행 증거"); exit 3 for the required lint
//! engine left NOT_RUN is decisions.md "legacy 종료 코드 3의 범위".
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/lint-replay-codes-oracle.json");

/// Frozen keeps `-Werror` options in the syntax replay, so GCC's "warnings being treated as
/// errors" trailer makes the output unparseable (`C++SyntaxExecutionError`); native demotes them
/// like frozen's unused-function and clang-tool replays. Needs a root ruling; these cases are
/// pinned to frozen's literal only.
const NEEDS_RULING: [&str; 3] = ["warning-policy-linker", "werror-kept", "werror-plain-kept"];

/// Cases ingestion refuses before any replay code is reached (compared in their own test).
const INGESTION: [&str; 4] = [
    "empty-argument",
    "too-many-arguments",
    "bare-response-token",
    "directory-is-file",
];

fn comparable(lint: &mut Value) {
    let engine = lint.as_object_mut().unwrap();
    for key in ["duration", "cache_key", "cache_hit", "tool_evidence"] {
        engine.remove(key);
    }
    let extra = engine["extra"].as_object_mut().unwrap();
    for key in ["native_checks", "target_scopes", "finding_identity_map"] {
        extra.remove(key);
    }
}

fn expanded(unit: &Value) -> Value {
    let mut unit = unit.clone();
    let arguments: Vec<Value> = unit["arguments"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|argument| match argument.get("repeat") {
            Some(repeat) => vec![repeat[0].clone(); repeat[1].as_u64().unwrap() as usize],
            None => vec![argument.clone()],
        })
        .collect();
    unit["arguments"] = json!(arguments);
    unit
}

fn verify(oracle: &Value, case: &Value) -> (i64, Value) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    let mut files = oracle["sources"].as_object().unwrap().clone();
    if let Some(extra) = case["files"].as_object() {
        files.extend(extra.clone());
    }
    for (name, text) in &files {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, text.as_str().unwrap()).unwrap();
        if name.starts_with("bin/") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        }
    }
    let mut a = expanded(&case["a"]);
    a["file"] = json!("src/a.cpp");
    let mut b = oracle["clean_b"].clone();
    b["file"] = json!("src/b.cpp");
    let text = serde_json::to_string(&json!([a, b]))
        .unwrap()
        .replace("<root>", root.to_str().unwrap());
    fs::write(root.join("compile_commands.json"), text).unwrap();
    // The capture's PATH had no clang-tidy or clazy; a host that has them reports other modes.
    let tools = base.join("tools");
    fs::create_dir(&tools).unwrap();
    for tool in ["g++", "as", "ld"] {
        std::os::unix::fs::symlink(format!("/usr/bin/{tool}"), tools.join(tool)).unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["verify", "--report", "--no-cache"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &base)
        .env("PATH", &tools)
        .env("GIT_CEILING_DIRECTORIES", &base)
        .output()
        .unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap_or_else(|error| panic!("{error}: {}", String::from_utf8_lossy(&output.stderr)))
        .replace(root.to_str().unwrap(), "<root>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let lint = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "lint")
        .unwrap()
        .clone();
    (i64::from(output.status.code().unwrap()), lint)
}

fn replay_error(lint: &Value) -> Option<String> {
    lint["targets"]
        .as_array()
        .unwrap()
        .iter()
        .find(|target| target["target_name"] == "C++SyntaxReplayError")
        .map(|target| target["message"].as_str().unwrap().to_owned())
}

#[test]
fn each_refused_syntax_replay_code_reports_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    // Independent literals: frozen's `<code>: <message>` for each code reached at replay.
    for (case, message) in [
        (
            "source-twice",
            "invalid-source-operands: The translation unit command must identify its canonical source exactly once.",
        ),
        (
            "stdin-operand",
            "extra-compiler-operand: Compiler stdin cannot be used as an additional replay input.",
        ),
        (
            "second-separator",
            "extra-compiler-operand: The translation unit command contains an extra or mismatched input operand.",
        ),
        (
            "replay-too-large",
            "command-too-large: The sanitized translation unit command exceeds the bounded replay size.",
        ),
        (
            "compiler-path-miss",
            "compiler-unavailable: The translation unit compiler is not available on PATH.",
        ),
        (
            "compiler-unresolved",
            "compiler-unavailable: The translation unit compiler could not be resolved.",
        ),
        (
            "unsupported-driver",
            "unsupported-compiler-driver: The translation unit does not use a direct GCC/Clang compiler driver.",
        ),
        (
            "project-compiler",
            "project-compiler-rejected: Project-contained compiler programs are not executed during analysis.",
        ),
    ] {
        let frozen = &oracle["runs"][case]["lint"];
        assert_eq!(replay_error(frozen).as_deref(), Some(message), "{case}");
    }
    for case in NEEDS_RULING {
        assert_eq!(
            oracle["runs"][case]["lint"]["summary"], "C++ syntax output was not parseable: a.cpp",
            "{case}"
        );
    }
    let mut compared = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if NEEDS_RULING.contains(&name.as_str()) || INGESTION.contains(&name.as_str()) {
            continue;
        }
        let frozen = &oracle["runs"][name];
        let (exit, mut native) = verify(&oracle, case);
        let mut expected = frozen["lint"].clone();
        comparable(&mut native);
        comparable(&mut expected);
        assert_eq!(native, expected, "{name}");
        assert_eq!(frozen["exit_code"], 1, "{name}");
        assert_eq!(frozen["lint"]["evidence"], "NOT_RUN", "{name}");
        assert_eq!(exit, 3, "{name}: the required engine stayed NOT_RUN");
        compared += 1;
    }
    assert_eq!(compared, 8);
}

/// The codes frozen never reaches at replay because ingestion stops the unit first: an empty
/// argument and 32,769 arguments (`invalid-arguments`), a bare `@` (`response-file-invalid`) and
/// a directory that is a file (the unit is dropped, `C++SyntaxContextMissing`). Native behaviour
/// for these four was not reviewed by cpp30.
#[test]
fn replay_codes_ingestion_refuses_first_report_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for case in INGESTION {
        assert_eq!(replay_error(&oracle["runs"][case]["lint"]), None, "{case}");
    }
    for case in INGESTION {
        let frozen = &oracle["runs"][case];
        let (exit, mut native) = verify(&oracle, &oracle["cases"][case]);
        let mut expected = frozen["lint"].clone();
        comparable(&mut native);
        comparable(&mut expected);
        assert_eq!(native, expected, "{case}");
        assert_eq!(frozen["exit_code"], 1, "{case}");
        assert_eq!(exit, 3, "{case}: the required engine stayed NOT_RUN");
    }
}
