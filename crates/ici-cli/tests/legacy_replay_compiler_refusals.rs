//! Frozen `build_replay_command` approves only a compiler a successful capability probe resolved
//! to, for lint, cycle and dead alike (`capture_replay_compiler_refusals.py`): an unlisted driver,
//! a non-executable file, and a `g++` whose `--version` fails or prints an unknown banner.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt};

const ENGINES: [&str; 3] = ["lint", "cycle", "dead"];
const FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap();
    let (base, tools) = (root.join("project"), root.join("tools"));
    fs::create_dir_all(base.join("src")).unwrap();
    fs::create_dir(&tools).unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    fs::write(base.join("src/a.cpp"), oracle["source"].as_str().unwrap()).unwrap();
    fs::write(base.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    let row = json!([{
        "directory": ".",
        "command": format!("{} -std=c++17 -c src/a.cpp", case["compiler"].as_str().unwrap()),
        "file": "src/a.cpp",
    }]);
    fs::write(base.join("compile_commands.json"), row.to_string()).unwrap();
    for (name, text) in case["tools"].as_object().unwrap() {
        fs::write(tools.join(name), text.as_str().unwrap()).unwrap();
        fs::set_permissions(tools.join(name), fs::Permissions::from_mode(0o755)).unwrap();
    }
    if let Some(outside) = case.get("outside").and_then(Value::as_object) {
        for (name, text) in outside {
            fs::write(root.join(name), text.as_str().unwrap()).unwrap();
        }
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), root.display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            root.display().to_string(),
        ),
    ]);
    let invocation = registry::parse([
        "ici",
        "verify",
        "--profile",
        "deep",
        "--no-cache",
        "--report",
    ])
    .unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let text = fs::read_to_string(base.join("verify_report.json"))
        .unwrap()
        .replace(&tools.display().to_string(), "<tools>")
        .replace(&base.display().to_string(), "<root>");
    let report: Value = serde_json::from_str(&text).unwrap();
    let mut result = json!({"exit_code": output.exit_code});
    for engine in report["results"].as_array().unwrap() {
        let name = engine["engine_name"].as_str().unwrap();
        if ENGINES.contains(&name) {
            let targets: Vec<Value> = engine["targets"]
                .as_array()
                .unwrap()
                .iter()
                .map(|target| {
                    Value::Object(
                        FIELDS
                            .iter()
                            .map(|key| ((*key).to_owned(), target[*key].clone()))
                            .collect(),
                    )
                })
                .collect();
            result[name] = json!({
                "status": engine["status"],
                "summary": engine["summary"],
                "evidence": engine["evidence"],
                "targets": targets,
            });
        }
    }
    result
}

#[test]
#[ignore = "explicit actual /usr/bin/g++ gate for recorded compiler refusals"]
fn recorded_compiler_refusals_equal_frozen_in_lint_cycle_and_dead() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/replay-compiler-refusals-oracle.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let mut actual = run(&oracle, case);
        // decisions.md "legacy 종료 코드 3의 범위": native ends 3 while a required engine's
        // evidence is NOT_RUN, as lint's is in every refused case; frozen ended 1.
        if name != "accepted" && actual["lint"]["evidence"] == "NOT_RUN" && actual["exit_code"] == 3
        {
            actual["exit_code"] = case["frozen"]["exit_code"].clone();
        }
        if actual != case["frozen"] {
            differences.push(format!(
                "== {name}\n native {actual}\n frozen {}",
                case["frozen"]
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
