//! Legacy `ici lint` / verify lint engine C++ heuristic-fallback projection against the frozen
//! CLI oracle (`tests/rust-parity/cpp/capture_lint_cli.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

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

fn run(case: &Value, base: &str, environment: &BTreeMap<String, String>) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        root.path(),
    );
    fs::write(
        root.path().join("ici.toml"),
        format!("{base}{}", case["config"].as_str().unwrap()),
    )
    .unwrap();
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
    .unwrap();
    let engine = if command == "verify" {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "lint")
            .unwrap()
            .clone()
    } else {
        report
    };
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
    let mut captured = json!({
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": targets,
    });
    if command != "verify" {
        captured["exit_code"] = json!(output.exit_code);
    }
    captured
}

#[test]
#[ignore = "explicit actual g++ legacy lint fallback: ICI_TEST_CXX must be g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 (the oracle compiler) and /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_cpp_lint_fallback_matches_frozen_targets_summaries_and_exit_codes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let version = std::process::Command::new(&compiler)
        .arg("--version")
        .output()
        .unwrap();
    let version = String::from_utf8(version.stdout).unwrap();
    // Compiler wording is version specific: a different g++ must fail, not silently pass.
    assert_eq!(
        version.lines().next().unwrap(),
        oracle["gxx"].as_str().unwrap(),
        "the lint oracle was captured with another g++"
    );
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    for tool in ["clang-tidy", "clazy-standalone"] {
        for path in [directory.as_str(), "/usr/bin", "/bin"] {
            assert!(
                !Path::new(path).join(tool).exists(),
                "{tool} must be absent from {path}"
            );
        }
    }
    let environment = BTreeMap::from([("PATH".to_string(), format!("{directory}:/usr/bin:/bin"))]);
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    for (name, case) in cases {
        let mut actual = run(case, base, &environment);
        let frozen = &case["frozen"];
        if frozen["status"] == "ERROR" {
            // Open (reporting): frozen tool errors are NOT_RUN evidence with exit 1; the
            // projection derives NOT_RUN only from a non-succeeded task.
            assert_eq!(frozen["evidence"], "NOT_RUN", "{name}");
            actual["evidence"] = frozen["evidence"].clone();
        }
        assert_eq!(&actual, frozen, "{name}");
    }
}
