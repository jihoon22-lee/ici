//! Frozen `next verify` pytest provider identities replayed through the native application.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const ORACLE: &str = include_str!("../../../tests/rust-parity/validation/pytest-next-oracle.json");

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn run(scenario: &Value, config: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("app.py"), "X = 1\n").unwrap();
    for (path, text) in scenario["files"].as_object().unwrap() {
        fs::write(root.path().join(path), text.as_str().unwrap()).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        config.replace("\"<PYTHON>\"", &serde_json::to_string(&python()).unwrap()),
    )
    .unwrap();
    commands::dispatch(
        &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
        .unwrap()
}
fn pytest_findings(result: &Value) -> Vec<Value> {
    result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["provider"] == "pytest")
        .cloned()
        .collect()
}
fn state(result: &Value, check: &str) -> Value {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["check_id"] == check)
        .map(|item| item["state"].clone())
        .unwrap()
}

#[test]
#[ignore = "explicit project pytest provider identity acceptance"]
fn failed_case_identity_matches_frozen_and_the_spurious_summary_collection_error_is_gone() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["failing"];
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    let failed = &frozen["findings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|finding| finding["rule_id"] == "pytest.failed")
        .unwrap();
    assert_eq!(native.len(), 1, "{native:?}");
    for key in ["rule_id", "message", "fingerprint"] {
        assert_eq!(native[0][key], failed[key], "{key}");
    }
    assert_eq!(native[0]["primary_location"]["path"], failed["path"]);
    // Frozen `(?:collecting\s+)?` also matched the short-summary `FAILED tests/x.py::t` line.
    assert!(
        frozen["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["rule_id"] == "pytest.collection-error")
    );
    assert_eq!(state(&result, "python.test"), "SUCCEEDED");
    let cases = result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|metric| metric["name"] == "pytest.cases")
        .unwrap();
    assert_eq!(cases["numerator"], frozen["metrics"][0]["numerator"]);
    assert_eq!(cases["denominator"], frozen["metrics"][0]["denominator"]);
}

#[test]
#[ignore = "explicit project pytest collection-error identity acceptance"]
fn collection_error_identity_matches_frozen_but_the_suite_stays_incomplete() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["collection"];
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    assert_eq!(native.len(), 1, "{native:?}");
    for key in ["rule_id", "message", "fingerprint"] {
        assert_eq!(native[0][key], frozen["findings"][0][key], "{key}");
    }
    // Frozen marked the interrupted suite SUCCEEDED; its scope is not complete evidence.
    assert_eq!(frozen["checks"]["python.test"], "SUCCEEDED");
    assert_ne!(state(&result, "python.test"), "SUCCEEDED");
}

/// The python.test facts of one run: state, evidence completeness, the test-related run
/// limitations, the pytest findings' rule ids and the pytest.cases measurement.
fn test_facts(result: &Value) -> Value {
    let check = result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["check_id"] == "python.test")
        .unwrap();
    serde_json::json!({
        "gate": result["gate"]["selected"],
        "state": check["state"],
        "evidence_complete": check["evidence_complete"],
        "limitations": result["limitations"].as_array().unwrap().iter()
            .filter(|text| text.as_str().is_some_and(|text| text.contains("pytest") || text.contains("test")))
            .cloned().collect::<Vec<_>>(),
        "findings": pytest_findings(result).iter().map(|finding| finding["rule_id"].clone()).collect::<Vec<_>>(),
        "cases": result["metrics"].as_array().unwrap().iter()
            .filter(|metric| metric["name"] == "pytest.cases")
            .map(|metric| serde_json::json!([metric["numerator"], metric["denominator"]]))
            .collect::<Vec<_>>(),
    })
}
fn frozen_facts(frozen: &Value) -> Value {
    serde_json::json!({
        "gate": frozen["gate"],
        "state": frozen["test_check"]["state"],
        "evidence_complete": frozen["test_check"]["evidence_complete"],
        "limitations": frozen["limitations"],
        "findings": frozen["findings"].as_array().unwrap().iter().map(|finding| finding["rule_id"].clone()).collect::<Vec<_>>(),
        "cases": frozen["metrics"].as_array().unwrap().iter()
            .map(|metric| serde_json::json!([metric["numerator"], metric["denominator"]]))
            .collect::<Vec<_>>(),
    })
}

/// Green, all-skipped, nothing-collected and `pytest.exit` runs (frozen
/// adapters/providers/pytest.py exit contract and limitations) against frozen `next verify`.
#[test]
#[ignore = "explicit project pytest provider contract acceptance"]
fn green_skipped_empty_and_exited_suites_keep_the_frozen_provider_contract() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    // Native-only plan notes on every native run (the visible coverage/unittest branch policy).
    const NOTES: [&str; 2] = [
        "coverage capability selects one visible suite branch",
        "only an exact missing-pytest diagnostic activates",
    ];
    // Reviewed differences: (scenario, fact, native value, reason).
    let corrections: [(&str, &str, Value, &str); 4] = [
        (
            "no-tests",
            "limitations",
            serde_json::json!([
                "pytest did not report a completed nonempty suite (exit 5)",
                "prerequisite project.python.test has no complete evidence"
            ]),
            "Same FAILED state and exit 5; native words the refusal and the blocked coverage prerequisite (the exit was Rust Debug `Some(5)` before this sweep).",
        ),
        (
            "interrupted",
            "state",
            "FAILED".into(),
            "Frozen defect not reproduced: pytest.exit(returncode=2) aborted a collected test and frozen reported SUCCEEDED with 'pytest collected no tests'; native refuses the aborted run as incomplete.",
        ),
        (
            "interrupted",
            "evidence_complete",
            false.into(),
            "Follows the FAILED state above.",
        ),
        (
            "interrupted",
            "limitations",
            serde_json::json!([
                "pytest did not report a completed nonempty suite (exit 2)",
                "prerequisite project.python.test has no complete evidence"
            ]),
            "Native refusal names the real exit instead of frozen's wrong 'collected no tests'.",
        ),
    ];
    let mut wrong = vec![];
    for name in ["green", "all-skipped", "no-tests", "interrupted"] {
        let frozen = &oracle["runs"][name];
        let mut native = test_facts(&run(frozen, oracle["config"].as_str().unwrap()));
        native["limitations"]
            .as_array_mut()
            .unwrap()
            .retain(|text| {
                !NOTES
                    .iter()
                    .any(|note| text.as_str().unwrap().starts_with(note))
            });
        let mut expected = frozen_facts(frozen);
        for (_, fact, value, reason) in corrections.iter().filter(|(case, ..)| *case == name) {
            assert!(!reason.is_empty());
            expected[*fact] = value.clone();
        }
        if native != expected {
            wrong.push(format!("{name}\nnative {native}\nfrozen {expected}"));
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

#[test]
#[ignore = "explicit project pytest provider identity acceptance"]
fn an_escaping_node_id_keeps_its_verdict_at_the_tests_scope_where_frozen_crashed() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["escaping-node"];
    // Frozen `_node_location` meant to keep the verdict at `tests`, but its short-summary
    // collection match produced a `../outside` span and the run ended in an internal error.
    assert!(
        frozen["limitations"][0]
            .as_str()
            .unwrap()
            .contains("span path must be relative and contained")
    );
    assert_eq!(frozen["checks"]["python.test"], "FAILED");
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    assert_eq!(state(&result, "python.test"), "SUCCEEDED", "{result:#}");
    assert_eq!(native.len(), 1, "{native:?}");
    assert_eq!(native[0]["rule_id"], "pytest.failed");
    assert_eq!(
        native[0]["message"],
        "../outside/test_g.py::test_bad failed"
    );
    assert_eq!(native[0]["primary_location"]["path"], "tests");
    assert_eq!(native[0]["primary_location"]["start_line"], 1);
}

/// decisions.md "per-test 판정 없는 next pytest 실행": a run that prints only a summary count.
/// Frozen `_parse` (capture `summary-only`) has no finding, measurement or limitation and keeps
/// the check complete; native keeps it incomplete, because a count names no node or location.
#[test]
#[ignore = "explicit project pytest provider contract acceptance"]
fn a_summary_only_run_is_incomplete_where_frozen_called_it_complete() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["summary-only"];
    assert_eq!(frozen["test_check"]["state"], "SUCCEEDED");
    assert_eq!(frozen["test_check"]["evidence_complete"], true);
    assert_eq!(frozen["findings"], serde_json::json!([]));
    assert_eq!(frozen["metrics"], serde_json::json!([]));
    assert_eq!(frozen["limitations"], serde_json::json!([]));
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let facts = test_facts(&result);
    assert_eq!(facts["evidence_complete"], false, "{facts}");
    assert_ne!(facts["state"], "SUCCEEDED", "{facts}");
    assert_eq!(facts["findings"], serde_json::json!([]));
    assert_eq!(facts["cases"], serde_json::json!([]));
    assert!(
        facts["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .any(|text| text.as_str().unwrap().contains("without per-test verdicts")),
        "{facts}"
    );
}

/// One finding's public identity, without its fingerprint.
fn sub_root_identity(item: &Value, path: &Value, message: &Value) -> Value {
    serde_json::json!({
        "provider": item["provider"], "component": item["component_id"],
        "rule": item["rule_id"], "path": path, "message": message,
    })
}

/// decisions.md "하위 root component의 next finding 경로": frozen reports a finding relative to
/// its component (`tests/test_a.py`, `m.py`), for pytest and ruff alike; native reports it
/// relative to the workspace. Only the path (and the `pytest.collection-error` message built
/// from it) differ, and the attributed fingerprint differs because it hashes both
/// (`application/finding_identity.py` `_fingerprint`). Frozen's summary-line collection error
/// stays the `check:python.test` difference. Two components with the same relative file are
/// both `m.py` in frozen.
#[test]
#[ignore = "explicit project pytest and ruff provider identity acceptance (ICI_TEST_RUFF)"]
fn sub_root_findings_are_workspace_relative_where_frozen_was_component_relative() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let ruff = std::env::var("ICI_TEST_RUFF").expect("ICI_TEST_RUFF");
    let ruff_directory = std::path::Path::new(&ruff).parent().unwrap();
    for (name, scenario) in oracle["sub_root"].as_object().unwrap() {
        let root = tempfile::tempdir().unwrap();
        for (path, text) in scenario["files"].as_object().unwrap() {
            let file = root.path().join(path);
            fs::create_dir_all(file.parent().unwrap()).unwrap();
            fs::write(file, text.as_str().unwrap()).unwrap();
        }
        let mut config =
            String::from("schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n");
        for (id, component_root) in scenario["roots"].as_object().unwrap() {
            config.push_str(&format!(
                "[[components]]\nid='{id}'\nroot='{}'\nlanguages=['python']\n\
                 [components.python]\nexecutable={}\n",
                component_root.as_str().unwrap(),
                serde_json::to_string(&python()).unwrap(),
            ));
        }
        fs::write(root.path().join("ici.toml"), config).unwrap();
        commands::dispatch(
            &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
            root.path(),
            &BTreeMap::from([(
                "PATH".into(),
                format!("{}:/usr/bin:/bin", ruff_directory.display()),
            )]),
        )
        .unwrap();
        let result: Value = serde_json::from_slice(
            &fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap(),
        )
        .unwrap();
        let mut native: Vec<&Value> = result["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|item| matches!(item["provider"].as_str(), Some("pytest" | "ruff")))
            .collect();
        let key = |identity: &Value| identity.to_string();
        native.sort_by_key(|item| {
            key(&sub_root_identity(
                item,
                &item["primary_location"]["path"],
                &item["message"],
            ))
        });
        let frozen = scenario["findings"].as_array().unwrap();
        let mut expected: Vec<(Value, &Value)> = frozen
            .iter()
            // The `check:python.test` difference: frozen reads the summary's `FAILED` line too.
            .filter(|item| {
                name != "subroot-failing" || item["rule_id"] != "pytest.collection-error"
            })
            .map(|item| {
                let component = item["component_id"].as_str().unwrap();
                let prefix = scenario["roots"][component].as_str().unwrap();
                let path = format!("{prefix}/{}", item["path"].as_str().unwrap());
                let message = if item["rule_id"] == "pytest.collection-error" {
                    Value::from(format!("collection error in {path}"))
                } else {
                    item["message"].clone()
                };
                (sub_root_identity(item, &path.into(), &message), item)
            })
            .collect();
        expected.sort_by_key(|(identity, _)| key(identity));
        assert_eq!(native.len(), expected.len(), "{name}: {native:?}");
        for (native, (identity, frozen)) in native.iter().zip(&expected) {
            assert_eq!(
                sub_root_identity(
                    native,
                    &native["primary_location"]["path"],
                    &native["message"]
                ),
                *identity,
                "{name}"
            );
            assert_ne!(native["fingerprint"], frozen["fingerprint"], "{name}");
            // Frozen's own path and message under native's attribution give frozen's fingerprint:
            // nothing but those two inputs differs.
            let raw = match frozen["rule_id"].as_str().unwrap() {
                "pytest.failed" => Some(format!(
                    "pytest\0{}\0FAILED",
                    frozen["message"]
                        .as_str()
                        .unwrap()
                        .trim_end_matches(" failed")
                )),
                "pytest.collection-error" => Some(format!(
                    "pytest\0collect:{}\0error",
                    frozen["path"].as_str().unwrap()
                )),
                _ => None,
            };
            if let Some(raw) = raw {
                let mut finding: ici_domain::Finding =
                    serde_json::from_value((*native).clone()).unwrap();
                finding.fingerprint = ici_domain::sha256(raw);
                finding.message = frozen["message"].as_str().unwrap().to_owned();
                finding.primary_location.path = frozen["path"].as_str().unwrap().to_owned();
                assert_eq!(
                    ici_domain::identity::attributed_fingerprint(&finding, &[]).unwrap(),
                    frozen["fingerprint"].as_str().unwrap(),
                    "{name}"
                );
            }
        }
    }
    // Both components print `m.py` in frozen; only the component id tells them apart.
    let twins = oracle["sub_root"]["subroot-two-components"]["findings"]
        .as_array()
        .unwrap();
    assert!(twins.iter().all(|item| item["path"] == "m.py"), "{twins:?}");
    assert_eq!(twins.len(), 2);
}
