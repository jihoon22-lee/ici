//! Legacy `ici verify` compile_db policy against the frozen v0.13.0 runs in
//! `tests/rust-parity/cpp/compile-db-policy-oracle.json` (`capture_compile_db_policy.py`):
//! `engines.compile_db.database_required` without a database (unset, true, false) and with one,
//! and a recorded row whose response file frozen `core/compile_db` expands (plain, nested) or
//! refuses (missing, cycle, outside the project, a directory, unreadable, unterminated quote,
//! past the depth bound). Only the compile_db engine runs; no translation unit is compiled.
#[path = "support/host_tools.rs"]
mod host_tools;

use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt};

const FIELDS: [&str; 7] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
    "metrics",
];

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

fn targets(engine: &Value, fields: &[&str]) -> Vec<Value> {
    engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| pick(target, fields))
        .collect()
}

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    fs::create_dir_all(&root).unwrap();
    fs::write(
        base.join("outside.rsp"),
        oracle["outside_rsp"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}{}",
            oracle["head"].as_str().unwrap(),
            case["engine"].as_str().unwrap(),
            oracle["off"].as_str().unwrap()
        ),
    )
    .unwrap();
    for (name, content) in oracle["tree"]
        .as_object()
        .unwrap()
        .iter()
        .chain(case["files"].as_object().unwrap())
    {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        if let Some(text) = content.as_str() {
            fs::write(&path, text.replace("{root}", &root.display().to_string())).unwrap();
        } else if let Some(hex) = content["hex"].as_str() {
            let bytes = (0..hex.len())
                .step_by(2)
                .map(|index| u8::from_str_radix(&hex[index..index + 2], 16).unwrap())
                .collect::<Vec<_>>();
            fs::write(&path, bytes).unwrap();
        } else if let Some(link) = content["symlink"].as_str() {
            std::os::unix::fs::symlink(link, &path).unwrap();
        } else {
            fs::create_dir(&path).unwrap();
        }
    }
    for (name, mode) in case["modes"].as_object().unwrap() {
        fs::set_permissions(
            root.join(name),
            fs::Permissions::from_mode(mode.as_u64().unwrap() as u32),
        )
        .unwrap();
    }
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    // The frozen capture host had no clang-tidy or Clazy; keep them off the PATH here too.
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    let environment = BTreeMap::from([
        ("PATH".to_owned(), host.path.clone()),
        ("HOME".to_owned(), home.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "{root}");
    let report: Value = serde_json::from_str(&text).unwrap();
    let engine = |name: &str| {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == name)
            .cloned()
            .unwrap()
    };
    let database = engine("compile_db");
    json!({
        "exit_code": output.exit_code,
        "suite_status": report["suite_status"],
        "required_complete": report["native_run"]["execution"]["required_complete"],
        "compile_db": {
            "status": database["status"],
            "evidence": database["evidence"],
            "summary": database["summary"],
            "score": database["score"],
            "extra": pick(&database["extra"], &["configurations", "coverage_percent", "covered_units", "issues_count", "production_units"]),
            "targets": targets(&database, &FIELDS),
        },
    })
}

/// Failure: database_required without a database is frozen's coverage FAIL per production unit
/// (ESTIMATED, exit 1), and each refused response file is its `ici.compile-db.response-file-*`
/// FAIL at the unit. Negative: unset or false keeps the WARN; an expanded response file adds no
/// target. Positive: a present database covers every unit with its configuration rows.
#[test]
fn legacy_compile_db_database_policy_and_response_files_match_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compile-db-policy-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 13);
    for (name, rule) in [
        ("rsp-missing", "ici.compile-db.response-file-missing"),
        ("rsp-depth", "ici.compile-db.response-file-depth"),
    ] {
        assert!(
            cases[name]["frozen"]["compile_db"]["targets"]
                .as_array()
                .unwrap()
                .iter()
                .any(|target| target["target_name"] == rule),
            "the frozen capture lost {name}"
        );
    }
    for (name, case) in cases {
        let mut actual = run(&oracle, case);
        let frozen = &case["frozen"];
        let object = actual.as_object_mut().unwrap();
        let complete = object.remove("required_complete").unwrap();
        if object["exit_code"] != frozen["exit_code"] {
            // Accepted policy (decisions.md "legacy 종료 코드 3의 범위"): a run whose required
            // evidence stays incomplete exits 3 where frozen exited 1.
            assert_eq!(complete, false, "{name}: complete run differs from frozen");
            assert_eq!(
                (&object["exit_code"], &frozen["exit_code"]),
                (&json!(3), &json!(1)),
                "{name}: exit"
            );
            object.insert("exit_code".into(), frozen["exit_code"].clone());
        }
        assert_eq!(
            actual, case["frozen"],
            "{name}\nnative: {actual:#}\nfrozen: {:#}",
            case["frozen"]
        );
    }
}
