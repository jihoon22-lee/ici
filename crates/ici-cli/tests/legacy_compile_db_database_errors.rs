//! Legacy `ici verify` over a compilation database frozen refuses as a whole (frozen
//! `core/compile_db.load_compilation_context` database-level failures) against the frozen v0.13.0
//! runs in `tests/rust-parity/cpp/compile-db-database-errors-oracle.json`
//! (`capture_compile_db_database_errors.py`): the compile_db engine's coverage FAIL per
//! production unit and its `ici.compile-db.database-*` FAIL at the database path, and the lint
//! engine's `C++SyntaxContextDiagnostic` stop. Only the compile_db and lint engines run; no
//! translation unit is compiled, so the test needs no compiler.
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

/// The lint targets are compared without end_line and metrics, like the other lint captures.
const LINT_FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];

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
        base.join("outside.json"),
        oracle["outside"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}{}",
            oracle["head"].as_str().unwrap(),
            case["project"].as_str().unwrap(),
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
            fs::write(&path, text).unwrap();
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
    // The frozen capture host had no clang-tidy or Clazy (the lint targets are their
    // Unavailable WARNs); the GitHub runner has both, so they are left out of the PATH here.
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
    let (database, lint) = (engine("compile_db"), engine("lint"));
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
        "lint": {
            "status": lint["status"],
            "summary": lint["summary"],
            "targets": targets(&lint, &LINT_FIELDS),
        },
    })
}

/// Failure: a database that is not JSON, has a duplicate key, a `NaN`, non-UTF-8 bytes or an
/// object root, is a directory, cannot be read, resolves outside the project through a symlink,
/// or is configured and absent gives frozen's `database-*` sentence at the database path (the
/// compile_db FAIL row and the lint `C++SyntaxContextDiagnostic`, lint stopping before any replay).
/// Negative: no production unit counts as covered (`0/2`, score 0.0) and no configuration row is
/// invented. Positive: every production unit still has its own coverage target.
#[test]
fn legacy_database_level_errors_match_the_frozen_compile_db_and_lint_projection() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compile-db-database-errors-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    for (name, rule, sentence) in [
        (
            "directory",
            "ici.compile-db.database-not-file",
            "The compilation database path is not a regular file.",
        ),
        (
            "unreadable",
            "ici.compile-db.database-unreadable",
            "The compilation database could not be read safely.",
        ),
        (
            "duplicate-key",
            "ici.compile-db.database-malformed",
            "The compilation database could not be decoded as JSON.",
        ),
    ] {
        assert!(
            cases[name]["frozen"]["compile_db"]["targets"]
                .as_array()
                .unwrap()
                .iter()
                .any(|target| target["target_name"] == rule && target["message"] == sentence),
            "the frozen capture lost {name}"
        );
    }
    for (name, case) in cases {
        let mut actual = run(&oracle, case);
        let frozen = &case["frozen"];
        let object = actual.as_object_mut().unwrap();
        let complete = object.remove("required_complete").unwrap();
        if object["exit_code"] != frozen["exit_code"] {
            // Accepted policy (root decision recorded on descriptor:compile_db and command:ici
            // verify, as legacy_verify_matrix.rs asserts): a run whose required evidence stays
            // incomplete (lint ERROR NOT_RUN) keeps the ERROR suite and exits 3 where frozen
            // exited 1.
            assert_eq!(complete, false, "{name}: complete run differs from frozen");
            assert_eq!(
                (
                    &object["exit_code"],
                    &frozen["exit_code"],
                    &frozen["suite_status"]
                ),
                (&json!(3), &json!(1), &json!("ERROR")),
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
