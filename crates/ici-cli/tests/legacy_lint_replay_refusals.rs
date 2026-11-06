//! Frozen legacy lint on compile databases whose recorded commands are refused before replay,
//! through the real CLI's saved report (`tests/rust-parity/cpp/capture_lint_replay_refusals.py`).
//! Native used to abort the run when no unit replayed ("native steps must not be empty", exit 2,
//! no report), and left a replayed unit's clean target unnamed when a refused and an uncovered
//! unit met. Compared in full except the keys named below; exit 3 for the required lint engine
//! left NOT_RUN is decisions.md "legacy 종료 코드 3의 범위".
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/cpp/lint-replay-refusals-oracle.json");

/// The run's own timing and cache identity, the native evidence keys of decisions.md "legacy
/// `extra` 안 native 증거 키", and the compiler records of "compiler·linker·binutils 실제 실행
/// 증거" (native records the `/usr/bin/g++` it ran where frozen records the resolved driver).
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

fn verify(oracle: &Value, units: &Value) -> (i64, Value) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    for name in oracle["sources"].as_array().unwrap() {
        let name = name.as_str().unwrap();
        fs::write(
            root.join(format!("src/{name}.cpp")),
            format!("int {name}() {{ return 0; }}\n"),
        )
        .unwrap();
    }
    write_database(&root, units);
    // The capture's PATH had no clang-tidy or clazy; a host that has them reports other lint modes.
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
        .unwrap_or_else(|error| panic!("{error}: {}", String::from_utf8_lossy(&output.stderr)));
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

fn write_database(root: &Path, units: &Value) {
    let database: Vec<Value> = units
        .as_array()
        .unwrap()
        .iter()
        .map(|unit| {
            let name = unit[0].as_str().unwrap();
            let mut arguments = vec![Value::from("/usr/bin/g++"), Value::from("-std=c++17")];
            arguments.extend(unit[1].as_array().unwrap().iter().cloned());
            arguments.extend(["-c".into(), format!("src/{name}.cpp").into()]);
            serde_json::json!({"directory": root, "arguments": arguments, "file": format!("src/{name}.cpp")})
        })
        .collect();
    fs::write(
        root.join("compile_commands.json"),
        serde_json::to_vec(&database).unwrap(),
    )
    .unwrap();
}

#[test]
fn refused_replays_report_like_frozen_instead_of_stopping_the_run() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let mut compared = 0;
    for (name, units) in oracle["cases"].as_object().unwrap() {
        let frozen = &oracle["runs"][name];
        let (exit, mut native) = verify(&oracle, units);
        let mut expected = frozen["lint"].clone();
        comparable(&mut native);
        comparable(&mut expected);
        assert_eq!(native, expected, "{name}");
        assert_eq!(frozen["exit_code"], 1, "{name}");
        assert_eq!(exit, 3, "{name}: the required engine stayed NOT_RUN");
        compared += 1;
    }
    assert_eq!(compared, 7);
}
