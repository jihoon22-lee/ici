//! Legacy `ici <engine> --report` Python scaffolding targets (security, resource, dup, dead,
//! cognitive, exception; no-source verdicts also for lint, cycle, type and complexity) against the frozen CLI oracle
//! (`tests/rust-parity/python/capture_legacy_engines.py`, CPython 3.10.21 venv).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

/// Reviewed difference: frozen embedded the running CPython's `SyntaxError.msg`, which differs
/// between interpreters (3.10.21 "invalid syntax" vs 3.14.7 "expected expression after 'else',
/// but statement is given" for one input), so the text after "SyntaxError: " is the pinned ruff
/// parser's wording. Name, status, file, line, summary shape and fingerprint stay frozen.
const SYNTAX_WORDING: [(&str, &str); 2] = [
    (
        "invalid syntax",
        "Expected a parameter or the end of the parameter list",
    ),
    ("'(' was never closed", "unexpected EOF while parsing"),
];

fn corrected(frozen: &Value) -> Value {
    let mut text = serde_json::to_string(frozen).unwrap();
    for (cpython, ruff) in SYNTAX_WORDING {
        text = text.replace(
            &format!("SyntaxError: {cpython}"),
            &format!("SyntaxError: {ruff}"),
        );
    }
    let mut value: Value = serde_json::from_str(&text).unwrap();
    // Accepted exit-code policy: an incomplete legacy run exits 3 where frozen exited 1.
    if value["exit_code"] == 1 && value["status"] == "ERROR" {
        value["exit_code"] = json!(3);
    }
    value
}

fn run(case: &Value, base: &str) -> Option<Value> {
    run_with_path(case, base, "/usr/bin:/bin")
}

fn run_with_path(case: &Value, base: &str, path: &str) -> Option<Value> {
    let root = tempfile::tempdir().unwrap();
    // A null configuration means the project has no ici.toml at all.
    if !case["config"].is_null() || case.get("config").is_none() {
        let config = format!("{base}{}", case["config"].as_str().unwrap_or(""));
        fs::write(root.path().join("ici.toml"), config).unwrap();
    }
    for (name, content) in case["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        match content {
            Value::String(text) => fs::write(path, text).unwrap(),
            other => fs::write(
                path,
                (0..other["hex"].as_str().unwrap().len())
                    .step_by(2)
                    .map(|index| {
                        u8::from_str_radix(&other["hex"].as_str().unwrap()[index..index + 2], 16)
                            .unwrap()
                    })
                    .collect::<Vec<_>>(),
            )
            .unwrap(),
        }
    }
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), path.to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join(format!("{command}_report.json"))).ok()?)
            .unwrap();
    let pick = |items: &Value, keys: &[&str]| -> Vec<Value> {
        items
            .as_array()
            .unwrap()
            .iter()
            .map(|item| {
                Value::Object(
                    keys.iter()
                        .map(|key| ((*key).to_owned(), item[*key].clone()))
                        .collect(),
                )
            })
            .collect()
    };
    let mut extra = json!({});
    if command == "line" {
        for key in [
            "all",
            "files_data",
            "top_files",
            "top_files_all",
            "code",
            "total",
        ] {
            extra[key] = report["extra"][key].clone();
        }
    }
    let mut actual = json!({
        "exit_code": output.exit_code,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": pick(&report["targets"], &FIELDS),
        "findings": pick(&report["findings"], &["rule_id", "fingerprint", "message", "tool_rule_id"]),
    });
    if command == "line" {
        actual["extra"] = extra;
    }
    Some(actual)
}

#[test]
fn legacy_python_engine_targets_match_frozen_intake_syntax_and_scope_shapes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-engines.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 65);
    let mut compared = 0;
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let actual = run(case, base);
        if frozen.get("crash").is_some() {
            // Frozen CPython 3.10 raised an uncaught ValueError for a NUL source and wrote no
            // report: there is no frozen shape, and the native command must still report.
            assert!(actual.is_some(), "{name}: native wrote no report");
            continue;
        }
        assert_eq!(actual.unwrap(), corrected(frozen), "{name}");
        compared += 1;
    }
    assert_eq!(compared, 63);
}

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}

/// Legacy `ici type` (frozen mypy) and `ici lint` (frozen ruff) with the project tools linked into
/// one PATH directory, exactly as the capture linked the same files for the frozen CLI.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn legacy_python_tool_engines_match_frozen_mypy_and_ruff_views() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-engines.json"
    ))
    .unwrap();
    let tools = tempfile::tempdir().unwrap();
    for (name, variable) in [("ruff", "ICI_TEST_RUFF"), ("mypy", "ICI_TEST_MYPY")] {
        let target = required(variable);
        let version = std::process::Command::new(&target)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&version.stdout).trim(),
            oracle["tool_versions"][name],
            "{variable} is not the captured {name}"
        );
        std::os::unix::fs::symlink(target, tools.path().join(name)).unwrap();
    }
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["tool_cases"].as_object().unwrap();
    assert_eq!(cases.len(), 27);
    for (name, case) in cases {
        let path = if case["tools"] == true {
            with_tools.as_str()
        } else {
            "/usr/bin:/bin"
        };
        let actual = run_with_path(case, base, path).unwrap_or_else(|| panic!("{name}: no report"));
        assert_eq!(actual, corrected(&case["frozen"]), "{name}");
    }
}
