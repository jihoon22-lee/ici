//! Legacy deep-profile `binary_compat` (and its `build` producer) against the frozen
//! `ici verify --profile deep --report` oracle (`tests/rust-parity/cpp/capture_binary_compat.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn engines(case: &Value, base: &str, environment: &BTreeMap<String, String>) -> Value {
    let root = tempfile::tempdir().unwrap();
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/package-make");
    for name in ["Makefile", "pyproject.toml"] {
        fs::copy(fixture.join(name), root.path().join(name)).unwrap();
    }
    fs::create_dir(root.path().join("src")).unwrap();
    fs::copy(
        fixture.join("src/main.cpp"),
        root.path().join("src/main.cpp"),
    )
    .unwrap();
    fs::write(root.path().join("ici.toml"), base).unwrap();
    for (relative, text) in case["changes"].as_object().unwrap() {
        fs::write(root.path().join(relative), text.as_str().unwrap()).unwrap();
    }
    let invocation = registry::parse(["ici", "verify", "--profile", "deep", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join("verify_report.json"))
            .unwrap_or_else(|_| panic!("no report: {}", output.stdout)),
    )
    .unwrap();
    let mut engines = serde_json::Map::new();
    for engine in report["results"].as_array().unwrap() {
        let name = engine["engine_name"].as_str().unwrap_or_default();
        if !matches!(name, "build" | "binary_compat") {
            continue;
        }
        let targets: Vec<Value> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({
                    "target_name": target["target_name"],
                    "status": target["status"],
                    "message": target["message"],
                    "start_line": target["start_line"],
                    "file_path": target["file_path"],
                })
            })
            .collect();
        engines.insert(
            name.into(),
            json!({"status":engine["status"],"summary":engine["summary"],"targets":targets}),
        );
    }
    Value::Object(engines)
}

#[test]
#[ignore = "explicit actual GNU Make, g++ and readelf deep legacy run (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn legacy_binary_compat_targets_and_summaries_match_the_frozen_deep_pipeline() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/binary-compat-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let path = ["ICI_TEST_MAKE", "ICI_TEST_CXX"]
        .map(|name| std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}")))
        .iter()
        .map(|tool| Path::new(tool).parent().unwrap().display().to_string())
        .chain(["/usr/bin".into(), "/bin".into()])
        .collect::<Vec<_>>()
        .join(":");
    let environment = BTreeMap::from([("PATH".to_string(), path)]);
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 7);
    for (name, case) in cases {
        let actual = engines(case, base, &environment);
        for (engine, frozen) in case["frozen"].as_object().unwrap() {
            let mut actual = actual[engine].clone();
            match (name.as_str(), engine.as_str()) {
                // Open: the Rust build inventory selects only measured ELF outputs, while the
                // frozen manifest also lists MZ/Mach-O files from the shadow.
                ("non-elf-allowed" | "non-elf-rejected", "binary_compat") => {
                    assert_eq!(actual["targets"][0], frozen["targets"][0], "{name}");
                    assert_eq!(actual["targets"].as_array().unwrap().len(), 1, "{name}");
                    continue;
                }
                // Open: an unpublished selection falls back to the python.binary-compat entry,
                // which stays blocked instead of the frozen AnalysisUnavailable ERROR.
                ("unpublished-selection", "binary_compat") => {
                    assert_eq!(frozen["status"], "ERROR");
                    assert_eq!(actual["status"], "SKIP", "{name}");
                    continue;
                }
                // Open: a Make build without linked outputs stops at artifact preparation
                // (SKIP) instead of the frozen BuildAdapter PASS (0) + BuildArtifacts FAIL.
                ("no-artifacts" | "no-artifacts-required", "build") => {
                    assert_eq!(frozen["status"], "FAIL");
                    assert_eq!(actual["status"], "SKIP", "{name}");
                    continue;
                }
                _ => {}
            }
            if frozen["status"] == "ERROR" {
                // Open reporting difference: ERROR summaries carry the check-id prefix.
                assert_eq!(
                    actual["summary"],
                    json!(format!(
                        "cpp.binary-compat: {}",
                        frozen["summary"].as_str().unwrap()
                    )),
                    "{name}"
                );
                actual["summary"] = frozen["summary"].clone();
            }
            assert_eq!(&actual, frozen, "{name} {engine}");
        }
    }
}
