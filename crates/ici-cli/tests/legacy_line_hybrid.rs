//! Legacy `ici line` on `type='hybrid'` projects against the frozen CLI
//! (`tests/rust-parity/cpp/capture_line_hybrid.py`): frozen `LineEngine` is one engine over every
//! language, so a hybrid project without C++ (or without Python) sources counts the other
//! language and is not an incomplete run.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];
const COUNTS: [&str; 6] = ["code", "comment", "blank", "total", "all", "files_data"];

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}",
            oracle["base"].as_str().unwrap(),
            case["config"].as_str().unwrap()
        ),
    )
    .unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let invocation = registry::parse(["ici", "line", "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join("line_report.json")).unwrap()).unwrap();
    json!({
        "exit_code": output.exit_code,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": report["targets"].as_array().unwrap().iter().map(|target| {
            Value::Object(FIELDS.iter().map(|key| ((*key).to_owned(), target[*key].clone())).collect())
        }).collect::<Vec<_>>(),
        "counts": Value::Object(COUNTS.iter().map(|key| ((*key).to_owned(), report["extra"][*key].clone())).collect()),
    })
}

/// Positive: Python only and C++ only hybrid projects count their sources (exit 0, MEASURED).
/// Negative: both languages (the previously working shape) are unchanged.
/// Failure: a Python only hybrid project over its fail limit fails (exit 1) on the counted file
/// and a C++ only one over its warning limit warns on its C++ target.
#[test]
fn legacy_hybrid_line_counts_the_present_language_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/line-hybrid-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = run(&oracle, case);
        if actual != case["frozen"] {
            eprintln!("== {name}\nactual: {actual}\nfrozen: {}", case["frozen"]);
            mismatches.push(name.clone());
        }
    }
    assert_eq!(
        cases["hybrid-python-only-over-limit"]["frozen"]["exit_code"],
        1
    );
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
