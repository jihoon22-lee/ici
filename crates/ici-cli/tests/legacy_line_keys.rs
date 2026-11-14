//! Legacy `[engines.line]` keys on a C++ project against the frozen CLI oracle
//! (`tests/rust-parity/cpp/capture_line_keys.py`): `include_dirs`, `exclude_dirs`, `gate_dirs`,
//! `mode`, `enabled` and `required` through `ici line --report` and `ici verify --report`.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];
const ROW: [&str; 8] = [
    "path", "scope", "lang", "code", "comment", "blank", "total", "status",
];

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    fs::create_dir(&root).unwrap();
    let command = case["command"].as_str().unwrap();
    let config = case["config"].as_str().unwrap();
    let prefix = match command {
        "verify" if config.contains("enabled=false") => oracle["with_dup"].as_str().unwrap(),
        "verify" => oracle["only_line"].as_str().unwrap(),
        _ => "",
    };
    fs::write(
        root.join("ici.toml"),
        format!("{}{prefix}{config}", oracle["base"].as_str().unwrap()),
    )
    .unwrap();
    for (name, text) in oracle["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let environment = BTreeMap::from([
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
    ]);
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let output = match commands::dispatch(&invocation, &root, &environment) {
        Ok(output) => output,
        Err(error) => return json!({"error": error.to_string()}),
    };
    let name = if command == "verify" {
        "verify_report.json"
    } else {
        "line_report.json"
    };
    let mut captured = json!({"exit_code": output.exit_code});
    let Ok(bytes) = fs::read(root.join(name)) else {
        return captured;
    };
    let mut report: Value = serde_json::from_slice(&bytes).unwrap();
    if command == "verify" {
        let results = report["results"].as_array().unwrap();
        captured["engines"] = results
            .iter()
            .map(|engine| engine["engine_name"].clone())
            .collect();
        let Some(line) = results
            .iter()
            .find(|engine| engine["engine_name"] == "line")
            .cloned()
        else {
            return captured;
        };
        report = line;
        captured["required"] = report["required"].clone();
    }
    for key in ["status", "summary", "evidence"] {
        captured[key] = report[key].clone();
    }
    captured["targets"] = report["targets"]
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
    captured["files"] = report["extra"]["files_data"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| ROW.iter().map(|key| row[*key].clone()).collect::<Value>())
        .collect();
    captured["all"] = report["extra"]["all"].clone();
    captured
}

/// Positive: default scope (src/include/lib/app plus `project.source_dirs`, gated with strict
/// cutoffs), nested `include_dirs` counted as source but gated by top-level name only, and every
/// mode's verdict and exit code.
/// Negative: a nested `gate_dirs` entry gates nothing, a gated directory outside the scope or
/// excluded is not gated, a nested `exclude_dirs` entry keeps its files in the source pass while
/// a top-level one drops them from both passes, and `required=false` keeps a failing line engine
/// out of the verify gate (exit 0).
/// Failure: a required failing line engine fails verify (exit 1); `enabled=false` drops the engine
/// from verify while `ici line` still runs it; a line path list or `project.cpp_external_build_dirs`
/// entry outside the project root is refused before any engine runs.
#[test]
fn legacy_line_keys_on_a_cpp_project_match_frozen_scope_gates_modes_and_gate_policy() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/line-keys-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 17);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = run(&oracle, case);
        if let Some(output) = case["frozen"]["output"].as_str() {
            // Refused by frozen path validation before any engine ran: exit 2 naming the key;
            // native refuses the same key as a configuration error (the config row's wording).
            let key = output
                .strip_prefix("Configuration error: ")
                .and_then(|rest| rest.split(['[', ':']).next())
                .unwrap();
            assert_eq!(case["frozen"]["exit_code"], 2, "{name}");
            let error = actual["error"].as_str().unwrap_or_default();
            if !(error.starts_with("Configuration error: ") && error.contains(key)) {
                eprintln!("== {name}\nactual: {actual}\nfrozen: {output}");
                mismatches.push(name.clone());
            }
            continue;
        }
        if actual != case["frozen"] {
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&case["frozen"]).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Defect correction (docs/design/rust-replacement/decisions.md row "읽을 수 없는
/// production source"): frozen LineCountEngine counts an unreadable production source as 0
/// lines and its JSON reporter then crashes on the 0-line target (`ValueError: legacy target
/// 'src/b.cpp' end_line must be a 1-indexed integer: 0`, exit 1, no report;
/// /tmp/ici-rust-replacement-20261028/w-cpp-unreadable-frozen-line.log). Native refuses the
/// whole run at its input snapshot, names the file and the reason, never reports a line PASS
/// and exits non-zero.
#[test]
fn legacy_line_refuses_an_unreadable_production_source_at_the_input_snapshot() {
    use std::os::unix::fs::PermissionsExt;
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(
        root.join("ici.toml"),
        "[project]\ntype = \"cpp\"\nsource_dirs = [\"src\"]\n",
    )
    .unwrap();
    fs::write(root.join("src/a.cpp"), "int a() { return 1; }\n").unwrap();
    fs::write(root.join("src/b.cpp"), "int b() { return 1; }\n").unwrap();
    let unreadable = root.join("src/b.cpp");
    fs::set_permissions(&unreadable, fs::Permissions::from_mode(0o000)).unwrap();
    assert!(
        fs::read(&unreadable).is_err(),
        "the fixture needs a non-root user: mode 000 must make src/b.cpp unreadable"
    );
    let environment = BTreeMap::from([
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
    ]);
    let invocation = registry::parse(["ici", "line", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    fs::set_permissions(&unreadable, fs::Permissions::from_mode(0o644)).unwrap();
    assert_ne!(output.exit_code, 0, "{}", output.stdout);
    let report: Value =
        serde_json::from_slice(&fs::read(root.join("line_report.json")).unwrap()).unwrap();
    assert_eq!(report["evidence"], "NOT_RUN", "{report}");
    assert!(
        report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .all(|target| target["status"] != "PASS"),
        "{report}"
    );
    let native = &report["native_run"];
    assert_eq!(native["gate"]["selected"], "INCOMPLETE", "{native}");
    assert_eq!(native["checks"][0]["state"], "BLOCKED", "{native}");
    assert!(
        native["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .any(|limitation| limitation == "snapshot src/b.cpp: Permission denied (os error 13)"),
        "{native}"
    );
}
