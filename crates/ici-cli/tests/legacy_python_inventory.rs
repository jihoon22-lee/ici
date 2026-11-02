//! Frozen legacy engine reports of the Python inventory through the binary (F5, F6):
//! `tests/rust-parity/python/legacy-inventory-oracle.json` (`capture_legacy_inventory.py`).
//! Complexity/cycle: one target per Python function in frozen `ast.walk` (breadth-first) order
//! without columns, no per-file `Python source`/`PythonImportGraph` PASS rows, only the
//! `Cycle:N` finding for an import cycle, `top_complex_funcs` and the empty C++ extras of a
//! Python-only project. Hybrid lint/test/resource/security: the Python facet beside C++.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const ORACLE: &str = include_str!("../../../tests/rust-parity/python/legacy-inventory-oracle.json");
/// Native extras the legacy view keeps beside the frozen ones (evidence of the native checks).
const NATIVE_EXTRAS: [&str; 10] = [
    "excluded_source_counts",
    "finding_identity_map",
    "heuristic_edges",
    "import_edges",
    "module_index",
    "native_checks",
    "parser_identity",
    "reported_cycles",
    "source_encodings",
    "target_scopes",
];

fn run(engine: &str, tree: &Value) -> (i32, Value) {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("project");
    for (name, text) in tree.as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir_all(root.join(".git")).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        (
            "XDG_CONFIG_HOME".to_owned(),
            home.path().display().to_string(),
        ),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let output = commands::dispatch(
        &registry::parse(["ici", engine, "--report"]).unwrap(),
        &root,
        &environment,
    )
    .unwrap();
    let text = fs::read_to_string(root.join(format!("{engine}_report.json"))).unwrap();
    (
        output.exit_code,
        serde_json::from_str(&text.replace(&root.display().to_string(), "<root>")).unwrap(),
    )
}

fn compare(engine: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["cases"][engine];
    let (code, native) = run(engine, &oracle["tree"]);
    assert_eq!(code, frozen["exit_code"], "{engine} exit");
    let report = &frozen["report"];
    for key in [
        "status",
        "summary",
        "evidence",
        "required",
        "score",
        "max_score",
        "targets",
        "findings",
    ] {
        assert_eq!(native[key], report[key], "{engine} {key}");
    }
    for (key, value) in report["extra"].as_object().unwrap() {
        assert_eq!(&native["extra"][key], value, "{engine} extra.{key}");
    }
    for key in native["extra"].as_object().unwrap().keys() {
        assert!(
            report["extra"].get(key).is_some() || NATIVE_EXTRAS.contains(&key.as_str()),
            "{engine}: unreviewed native extra {key}"
        );
    }
}

#[test]
fn legacy_complexity_lists_python_functions_in_frozen_walk_order_without_inventory_rows() {
    compare("complexity");
}

#[test]
fn legacy_cycle_reports_only_the_frozen_cycle_target_and_finding() {
    compare("cycle");
}

/// One hybrid engine (Python plus one C++ source; no Ruff, no tests, no compilation database).
fn hybrid(engine: &str) -> (Value, Value, i32) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = oracle["hybrid"][engine].clone();
    let (code, native) = run(engine, &oracle["hybrid_tree"]);
    assert_eq!(code, frozen["exit_code"], "{engine} exit");
    for key in ["status", "required", "score", "max_score", "targets"] {
        if engine == "test" && matches!(key, "score" | "max_score") {
            continue;
        }
        assert_eq!(native[key], frozen["report"][key], "{engine} {key}");
    }
    let identity = |report: &Value| -> Vec<Value> {
        let mut findings: Vec<Value> = report["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                serde_json::json!([
                    finding["fingerprint"],
                    finding["rule_id"],
                    finding["primary_location"],
                    finding["message"],
                    finding["severity"]
                ])
            })
            .collect();
        findings.sort_by_key(Value::to_string);
        findings
    };
    assert_eq!(
        identity(&native),
        identity(&frozen["report"]),
        "{engine} findings"
    );
    (frozen["report"].clone(), native, code)
}

#[test]
fn hybrid_lint_without_ruff_keeps_the_frozen_ast_fallback_beside_the_cpp_targets() {
    let (frozen, native, _) = hybrid("lint");
    for key in ["summary", "evidence"] {
        assert_eq!(native[key], frozen[key], "lint {key}");
    }
    assert_eq!(
        native["extra"]["python_files_parsed"],
        frozen["extra"]["python_files_parsed"]
    );
    let names = |report: &Value| -> Vec<Value> {
        report["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .map(|tool| tool["name"].clone())
            .collect()
    };
    assert_eq!(names(&native), names(&frozen));
    assert_eq!(native["tool_evidence"][0], frozen["tool_evidence"][0]);
}

#[test]
fn hybrid_test_without_tests_reports_both_frozen_no_tests_collected_targets() {
    let (frozen, native, _) = hybrid("test");
    assert_eq!(frozen["status"], "FAIL");
    // Accepted no-fabrication difference: frozen invents an estimated coverage (45%/50%) and a
    // TEM for an empty suite; the native legacy view states the measured empty suite.
    assert_eq!(native["summary"], "0/0 Tests Passed; No tests collected");
    // Frozen's TEM score of that estimate (0.0 of 5.0) is not computed either.
    assert_eq!(
        (&frozen["score"], &frozen["max_score"]),
        (&serde_json::json!(0.0), &serde_json::json!(5.0))
    );
    assert_eq!(
        (&native["score"], &native["max_score"]),
        (&Value::Null, &Value::Null)
    );
    assert_eq!(native["evidence"], "MEASURED");
}

#[test]
fn hybrid_resource_and_security_have_no_cpp_side_not_applicable_target() {
    for engine in ["resource", "security"] {
        let (frozen, native, _) = hybrid(engine);
        for key in ["summary", "evidence"] {
            assert_eq!(native[key], frozen[key], "{engine} {key}");
        }
    }
}
