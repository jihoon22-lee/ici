//! `ici analyze --json` C++ exception-scope ownership against a frozen v0.13.0 capture
//! (`tests/rust-parity/cpp/exception-next-scope-oracle.json`, `capture_next_exception_scope.py`)
//! on `parity-fixtures/cpp/exception-next-scope`: frozen `measure_hygiene` reads the scope through
//! `read_analysis_sources`, so `generated`/`*_autogen` directories (case-folded), `moc_` sources
//! and vendor directories (`third_party`, `extern`, ...) are excluded and reported as
//! `excluded <path>: <reason>` limitations, while `*.pb.cc` is analyzed. Compared: the
//! `cpp.exception` check, its `exception.*` findings and every distinct `excluded` limitation of
//! the run.
//! Needs only PATH=/usr/bin:/bin (no tool runs for `cpp.exception`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

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

#[test]
fn next_cpp_exception_scope_excludes_generated_and_vendor_sources_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/exception-next-scope-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/exception-next-scope");
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    copy(&fixture, &base);
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    let mut excluded: Vec<Value> = document["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| item.as_str().unwrap().starts_with("excluded "))
        .cloned()
        .collect();
    excluded.sort_by_key(|item| item.as_str().unwrap().to_owned());
    let actual = json!({
        "checks": document["checks"].as_array().unwrap().iter()
            .filter(|check| check["check_id"] == "cpp.exception")
            .map(|check| json!({
                "task_id": check["task_id"], "check_id": check["check_id"],
                "state": check["state"], "evidence_complete": check["evidence_complete"],
            }))
            .collect::<Vec<_>>(),
        "findings": document["findings"].as_array().unwrap().iter()
            .filter(|finding| finding["rule_id"].as_str().unwrap().starts_with("exception."))
            .map(|finding| json!({
                "rule_id": finding["rule_id"], "message": finding["message"],
                "severity": finding["severity"], "confidence": finding["confidence"],
                "category": finding["category"],
                "path": finding["primary_location"]["path"],
                "line": finding["primary_location"]["start_line"],
            }))
            .collect::<Vec<_>>(),
        "excluded": excluded,
    });
    // The native run envelope lists each distinct limitation once; frozen repeats the identical
    // `excluded` line for each check that read the scope (`cpp.dup` and `cpp.exception`).
    let mut wanted = oracle["case"].clone();
    let lines = wanted["excluded"].as_array_mut().unwrap();
    assert!(
        lines
            .chunks(2)
            .all(|pair| pair.len() == 2 && pair[0] == pair[1])
    );
    lines.dedup();
    assert_eq!(actual, wanted, "native: {actual:#}\nfrozen: {wanted:#}");
}
