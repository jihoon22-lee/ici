//! Frozen compile-database metadata warnings (`core/_compile_db_metadata.py`) through the legacy
//! `ici verify` compile_db, lint and cycle engines, against the frozen v0.13.0 run in
//! `tests/rust-parity/cpp/compile-metadata-oracle.json` (`capture_compile_metadata.py`) over
//! parity-fixtures/cpp/compile-metadata (decisions.md "compile DB metadata 경고"). Every
//! warning keeps the context complete, so each engine still replays every unit frozen replays.
//! The sources include nothing, so the host g++ version does not change the traces.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[path = "support/lint_extra.rs"]
mod lint_extra;

const TARGET_KEYS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "file_path",
    "start_line",
    "end_line",
];
const FINDING_KEYS: [&str; 5] = ["rule_id", "severity", "confidence", "category", "message"];

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

fn engine_view(engine: &Value) -> Value {
    let mut view = json!({
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "score": engine["score"],
        "targets": engine["targets"].as_array().unwrap().iter()
            .map(|target| pick(target, &TARGET_KEYS)).collect::<Vec<_>>(),
        "findings": engine["findings"].as_array().unwrap().iter()
            .map(|finding| {
                let mut item = pick(finding, &FINDING_KEYS);
                item["path"] = finding["primary_location"]["path"].clone();
                item["start_line"] = finding["primary_location"]["start_line"].clone();
                item
            })
            .collect::<Vec<_>>(),
        "extra": engine["extra"],
        "tool_evidence": engine["tool_evidence"],
    });
    sort_findings(&mut view);
    view
}

/// Findings in (path, message) order: their report order follows fingerprints, not targets.
fn sort_findings(view: &mut Value) {
    view["findings"]
        .as_array_mut()
        .unwrap()
        .sort_by_key(|item| (item["path"].to_string(), item["message"].to_string()));
}

/// Positive: the clean row and the row whose `output` agrees with `-o` only from the database
/// directory carry no diagnostic and pass every engine.
/// Negative: unknown `-x` language, invalid `-D` name, `output` outside the project, not a
/// string, naming a directory or disagreeing with `-o` are warnings: compile_db WARN targets,
/// lint/cycle WARN context diagnostics, and the unit is still replayed and traced.
/// Failure: `-o` without a value fails the compile_db engine (frozen `_FAIL_DIAGNOSTICS`) and its
/// replay is refused like frozen; the unknown language and invalid definition replays fail
/// like frozen.
#[test]
fn compile_database_metadata_warnings_match_frozen_engine_results() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compile-metadata-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let root = base.join("project");
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/compile-metadata"),
        &root,
    );
    let home = base.join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse([
        "ici",
        "verify",
        "--profile",
        "deep",
        "--no-cache",
        "--report",
    ])
    .unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let text = fs::read_to_string(root.join("verify_report.json"))
        .unwrap()
        .replace(&root.display().to_string(), "{root}");
    let report: Value = serde_json::from_str(&text).unwrap();
    let expected = &oracle["case"];
    let mut mismatches = vec![];
    if output.exit_code != expected["exit_code"]
        || report["suite_status"] != expected["suite_status"]
    {
        mismatches.push(format!(
            "exit/suite: native {} {} frozen {} {}",
            output.exit_code,
            report["suite_status"],
            expected["exit_code"],
            expected["suite_status"]
        ));
    }
    for (name, frozen) in expected["engines"].as_object().unwrap() {
        let engine = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == name.as_str())
            .unwrap();
        let mut actual = engine_view(engine);
        let mut frozen = frozen.clone();
        sort_findings(&mut frozen);
        for difference in lint_extra::split(&mut actual, &mut frozen) {
            mismatches.push(format!("{name}: {difference}"));
        }
        if actual != frozen {
            mismatches.push(format!("{name}:\nnative {actual}\nfrozen {frozen}"));
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
