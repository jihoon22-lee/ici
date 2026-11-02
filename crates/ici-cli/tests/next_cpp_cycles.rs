//! `ici analyze --json` C++ include-cycle check against frozen v0.13.0 captures
//! (`tests/rust-parity/cpp/cycle-next-oracle.json`, `capture_cycle_next.py`) on the
//! `parity-fixtures/cpp/cycle-next` trees: a resolved acyclic include chain and quoted includes
//! with no project-file match. Needs only PATH=/usr/bin:/bin (no tool runs for `cpp.cycle`).
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

fn analyze(name: &str) -> Value {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("parity-fixtures/cpp/cycle-next");
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    copy(&fixtures.join(name), &base);
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    json!({
        "exit_code": output.exit_code,
        "checks": document["checks"].as_array().unwrap().iter()
            .filter(|check| check["check_id"] == "cpp.cycle")
            .map(|check| json!({
                "task_id": check["task_id"], "check_id": check["check_id"],
                "state": check["state"], "evidence_complete": check["evidence_complete"],
            }))
            .collect::<Vec<_>>(),
        "findings": document["findings"].as_array().unwrap().iter()
            .filter(|finding| finding["rule_id"].as_str().unwrap().starts_with("cycle."))
            .cloned()
            .collect::<Vec<_>>(),
        "metrics": document["metrics"].as_array().unwrap().iter()
            .filter(|metric| metric["name"] == "cycles_found")
            .map(|metric| json!({
                "name": metric["name"], "unit": metric["unit"],
                "evidence": metric["evidence"], "value": metric["value"],
            }))
            .collect::<Vec<_>>(),
        "limitations": document["limitations"].as_array().unwrap().iter()
            .filter(|item| item.as_str().unwrap().to_lowercase().contains("include"))
            .cloned()
            .collect::<Vec<_>>(),
    })
}

/// Reviewed wording difference: native states the include-resolution limit and each unresolved
/// quoted include in its own words (`crates/ici-cpp/src/cycles.rs`); the facts (heuristic
/// resolution, which include at which path:line is unresolved, nothing reported as a cycle) are
/// the frozen ones.
fn frozen_wording(native: &str) -> String {
    const HEURISTIC: &str = "Include resolution is a path-suffix heuristic; compiler search order, \
        conditional preprocessing and generated headers are not established";
    if native == HEURISTIC {
        return "include resolution is a path-suffix heuristic — compiler search order and \
            generated headers are not modelled"
            .into();
    }
    let (site, include) = native
        .split_once(": unresolved local include ")
        .unwrap_or_else(|| panic!("unreviewed native include limitation: {native}"));
    format!("unresolved include '{include}' at {site}")
}

fn compare(name: &str) {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cycle-next-oracle.json"
    ))
    .unwrap();
    let sorted = |items: &Value, map: &dyn Fn(&str) -> String| {
        let mut items: Vec<String> = items
            .as_array()
            .unwrap()
            .iter()
            .map(|item| map(item.as_str().unwrap()))
            .collect();
        items.sort();
        json!(items)
    };
    let mut native = analyze(name);
    native["limitations"] = sorted(&native["limitations"], &frozen_wording);
    let mut expected = oracle["cases"][name].clone();
    expected["limitations"] = sorted(&expected["limitations"], &str::to_owned);
    assert_eq!(native, expected, "{name}");
}

#[test]
fn analyze_cpp_cycle_check_reports_no_cycle_on_an_acyclic_include_chain_like_frozen() {
    compare("acyclic");
}

#[test]
fn analyze_cpp_cycle_check_records_unresolved_quoted_includes_like_frozen() {
    compare("unresolved");
}
