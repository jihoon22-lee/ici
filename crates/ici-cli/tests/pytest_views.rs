//! Root decisions E3/E5: frozen pytest case order and counts in both views, and the three
//! legacy `_parse_pytest_result` ERROR summaries, replayed natively against
//! `tests/rust-parity/validation/pytest-views-oracle.json` (`capture_pytest_views.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str = include_str!("../../../tests/rust-parity/validation/pytest-views-oracle.json");

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ])
}
fn project(oracle: &Value, scenario: &str, config: String) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let files = oracle["source_file"]
        .as_object()
        .unwrap()
        .iter()
        .chain(oracle["scenarios"][scenario].as_object().unwrap());
    for (path, text) in files {
        let path = root.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::write(root.path().join("ici.toml"), config).unwrap();
    root
}
/// Legacy `ici test --report`: the facts the capture recorded.
fn legacy(oracle: &Value, scenario: &str) -> Value {
    let config = oracle["legacy_config"]
        .as_str()
        .unwrap()
        .replace("<PYTHON>", &python());
    let root = project(oracle, scenario, config);
    let output = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &environment(),
    )
    .unwrap();
    let engine: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    let cases: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            let name = target["target_name"].as_str().unwrap();
            name.contains("::") || name.starts_with("[Python]")
        })
        .map(|target| json!([target["target_name"], target["status"], target["message"]]))
        .collect();
    json!({"exit_code":output.exit_code,"status":engine["status"],"summary":engine["summary"],
        "evidence":engine["evidence"],"cases":cases,
        "extra":{"passed_tests":engine["extra"]["passed_tests"],
            "total_tests":engine["extra"]["total_tests"],"pass_rate":engine["extra"]["pass_rate"]}})
}
/// `ici next verify --python --no-cache`: the pytest metric, findings and check facts.
fn next(oracle: &Value, scenario: &str) -> Value {
    let config = oracle["next_config"]
        .as_str()
        .unwrap()
        .replace("<PYTHON_JSON>", &serde_json::to_string(&python()).unwrap());
    let root = project(oracle, scenario, config);
    let output = commands::dispatch(
        &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
        root.path(),
        &environment(),
    )
    .unwrap();
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    let prefix = root.path().display().to_string();
    let metrics: Vec<Value> = result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|metric| metric["name"] == "pytest.cases")
        .map(|metric| {
            json!({"name":metric["name"],"numerator":metric["numerator"],
                "denominator":metric["denominator"],"value":metric["value"]})
        })
        .collect();
    let findings: Vec<Value> = result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["provider"] == "pytest")
        .map(|finding| {
            json!({"rule_id":finding["rule_id"],
                "message":finding["message"].as_str().unwrap().replace(&prefix, "<root>"),
                "fingerprint":finding["fingerprint"],
                "path":finding["primary_location"]["path"].as_str().unwrap().replace(&prefix, "<root>"),
                "line":finding["primary_location"]["start_line"]})
        })
        .collect();
    let check = result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .unwrap();
    assert!(Path::new(&prefix).is_absolute());
    json!({"exit_code":output.exit_code,"metrics":metrics,"findings":findings,
        "state":check["state"],"root":prefix})
}
fn frozen(scenario: &str) -> (Value, Value) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let run = oracle["runs"][scenario].clone();
    (oracle, run)
}
/// The frozen metric with only the fields both products write.
fn frozen_metrics(run: &Value) -> Value {
    json!(
        run["next"]["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .map(
                |metric| json!({"name":metric["name"],"numerator":metric["numerator"],
                "denominator":metric["denominator"],"value":metric["value"]})
            )
            .collect::<Vec<_>>()
    )
}

#[test]
#[ignore = "explicit project pytest legacy/next view acceptance"]
fn legacy_cases_keep_execution_order_and_next_counts_only_passed_nodes() {
    for scenario in ["order", "repeated-line"] {
        let (oracle, run) = frozen(scenario);
        let native = legacy(&oracle, scenario);
        let expected = &run["legacy"];
        // Execution order with the xfail passing, the repeated line counted again (6/6).
        assert_eq!(native["cases"], expected["cases"], "{scenario}");
        assert_eq!(native["extra"], expected["extra"], "{scenario}");
        assert_eq!(native["status"], expected["status"], "{scenario}");
        assert_eq!(native["exit_code"], expected["exit_code"], "{scenario}");
        let summary = expected["summary"].as_str().unwrap();
        let counts = summary.split(" | ").next().unwrap();
        assert!(
            native["summary"].as_str().unwrap().starts_with(counts),
            "{scenario}: {native:#}"
        );
        // The next metric counts unique nodes, PASSED only (the xfail is not a pass): 4/5.
        let native = next(&oracle, scenario);
        assert_eq!(native["metrics"], frozen_metrics(&run), "{scenario}");
        assert_eq!(native["findings"], run["next"]["findings"], "{scenario}");
    }
}

#[test]
#[ignore = "explicit project pytest collection-path acceptance"]
fn an_absolute_collection_path_inside_the_project_is_canonicalized_and_one_outside_is_skipped() {
    let (oracle, run) = frozen("absolute-collection");
    let native = next(&oracle, "absolute-collection");
    // Inside: `tests/test_x.py` with frozen's rule and message. The identity hashes the printed
    // absolute path (frozen sha256("pytest\0collect:<printed>\0error")), so it names each
    // run's own root in both products and is not compared across runs.
    let strip = |findings: &Value| {
        json!(
            findings
                .as_array()
                .unwrap()
                .iter()
                .map(|finding| {
                    let mut finding = finding.clone();
                    finding.as_object_mut().unwrap().remove("fingerprint");
                    finding
                })
                .collect::<Vec<_>>()
        )
    };
    assert_eq!(strip(&native["findings"]), strip(&run["next"]["findings"]));
    // Outside: `/nonexistent-elsewhere/test_y.py` names no finding (and is no parse failure).
    assert_eq!(native["findings"].as_array().unwrap().len(), 1);
    assert_eq!(native["metrics"], frozen_metrics(&run));
    // A collection banner still means the collected scope is not complete evidence (accepted
    // on pytest.py:138:12), where frozen relied on the exit code alone.
    assert_eq!(run["next"]["check"]["state"], "SUCCEEDED");
    assert_ne!(native["state"], "SUCCEEDED");
}

#[test]
#[ignore = "explicit project pytest legacy ERROR summary acceptance"]
fn legacy_pytest_error_branches_keep_the_frozen_summaries() {
    for scenario in [
        "success-unparseable",
        "exit-outside-contract",
        "exit-one-no-failure",
    ] {
        let (oracle, run) = frozen(scenario);
        let native = legacy(&oracle, scenario);
        let expected = &run["legacy"];
        for key in ["status", "summary", "evidence"] {
            assert_eq!(native[key], expected[key], "{scenario} {key}: {native:#}");
        }
        // Accepted incomplete-exit-3 policy (root decision): an ERROR built from incomplete
        // native evidence exits 3 where frozen exited 1.
        assert_eq!(expected["exit_code"], 1, "{scenario}");
        assert_eq!(native["exit_code"], 3, "{scenario}");
    }
}
