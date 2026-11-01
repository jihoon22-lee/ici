//! Frozen legacy complexity/cycle engine reports through the binary (F6):
//! `tests/rust-parity/python/legacy-inventory-oracle.json` (`capture_legacy_inventory.py`).
//! One target per Python function in frozen `ast.walk` (breadth-first) order without columns,
//! no per-file `Python source`/`PythonImportGraph` PASS rows, only the `Cycle:N` finding for an
//! import cycle, `top_complex_funcs` and the empty C++ extras of a Python-only project.
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
