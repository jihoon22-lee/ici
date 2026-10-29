//! Legacy `ici complexity` / `ici cognitive` C++ thresholds against the frozen CLI oracle.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];

fn run(config: &str, command: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(
        root.path().join("src/main.cpp"),
        include_str!("../../../tests/rust-parity/cpp/metric-cli-fixture.cpp"),
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
    .unwrap();
    let targets: Vec<Value> = report["targets"]
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
    serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets})
}

#[test]
fn legacy_cpp_complexity_and_cognitive_match_frozen_thresholds_messages_and_exit_codes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/metric-cli-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, expected) in cases {
        let (command, config) = name.split_once(':').unwrap();
        let actual = run(oracle["configs"][config].as_str().unwrap(), command);
        assert_eq!(&actual, expected, "{name}");
    }
}

#[test]
fn inverted_or_invalid_cpp_metric_thresholds_stop_before_analysis() {
    for (command, config) in [
        ("complexity", "[engines.complexity]\nwarn_cc=9\nfail_cc=8\n"),
        ("cognitive", "[engines.cognitive]\nwarn=9\nfail=8\n"),
        ("complexity", "[engines.complexity]\nwarn_nesting='deep'\n"),
    ] {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        fs::write(root.path().join("src/main.cpp"), "int main(){return 0;}\n").unwrap();
        fs::write(
            root.path().join("ici.toml"),
            format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
        )
        .unwrap();
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        assert!(
            commands::dispatch(&invocation, root.path(), &environment).is_err(),
            "{config}"
        );
        assert!(!root.path().join(format!("{command}_report.json")).exists());
    }
}

#[test]
fn legacy_cpp_metric_and_exception_scope_excludes_headers_and_matches_frozen_targets() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/legacy-scope-oracle.json"
    ))
    .unwrap();
    let files = [
        (
            "a.hpp",
            include_str!("../../../tests/rust-parity/cpp/legacy-scope-fixture/src/a.hpp"),
        ),
        (
            "b.hpp",
            include_str!("../../../tests/rust-parity/cpp/legacy-scope-fixture/src/b.hpp"),
        ),
        (
            "extra.cc",
            include_str!("../../../tests/rust-parity/cpp/legacy-scope-fixture/src/extra.cc"),
        ),
        (
            "main.cpp",
            include_str!("../../../tests/rust-parity/cpp/legacy-scope-fixture/src/main.cpp"),
        ),
        (
            "util.cpp",
            include_str!("../../../tests/rust-parity/cpp/legacy-scope-fixture/src/util.cpp"),
        ),
    ];
    for (command, expected) in oracle["cases"].as_object().unwrap() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        for (name, text) in files {
            fs::write(root.path().join("src").join(name), text).unwrap();
        }
        fs::write(
            root.path().join("ici.toml"),
            "[project]\ntype='cpp'\nsource_dirs=['src']\n",
        )
        .unwrap();
        let invocation = registry::parse(["ici", command.as_str(), "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value = serde_json::from_slice(
            &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let targets: Vec<Value> = report["targets"]
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
        let actual = serde_json::json!({"exit_code":output.exit_code,"status":report["status"],"targets":targets});
        assert_eq!(&actual, expected, "{command}");
        // Header functions and the header destructor throw stay outside these frozen scopes.
        assert!(
            targets
                .iter()
                .all(|target| !target["file_path"].as_str().unwrap().ends_with(".hpp"))
        );
    }
}
