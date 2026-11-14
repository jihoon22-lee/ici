//! Approved difference (decisions.md "C++ cycle finding identity"): frozen's `cycle.include`
//! fingerprint hashes absolute checkout paths and its member order follows Python set order, so
//! the same cycle has another identity after a tree move or under another hash seed
//! (`tests/rust-parity/cpp/next-cycle-identity-oracle.json`, `capture_next_cycle_identity.py`).
//! Native hashes workspace-relative members in path order: stable across both, while a real
//! change of the cycle's members is still a different finding.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

fn analyze(tree: &Value, parent: &str, seed: &str) -> Value {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory
        .path()
        .canonicalize()
        .unwrap()
        .join(parent)
        .join("project");
    fs::create_dir_all(base.parent().unwrap().join(".hg")).unwrap();
    for (path, text) in tree.as_object().unwrap() {
        let target = base.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        fs::write(target, text.as_str().unwrap()).unwrap();
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("PYTHONHASHSEED".to_owned(), seed.to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            directory.path().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    let finding = document["findings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["rule_id"] == "cycle.include")
        .expect("one include cycle")
        .clone();
    json!({
        "exit_code": output.exit_code,
        "message": finding["message"],
        "fingerprint": finding["fingerprint"],
        "related": finding["related_locations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["path"].clone())
            .collect::<Vec<_>>(),
    })
}

#[test]
fn a_cycle_keeps_its_identity_across_a_tree_move_and_a_hash_seed() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-cycle-identity-oracle.json"
    ))
    .unwrap();
    // The recorded frozen facts this difference answers: identity and member order vary.
    let frozen = |tree: &str| oracle["frozen"][tree].as_array().unwrap().clone();
    let distinct = |runs: &[Value], key: &str| {
        runs.iter()
            .map(|run| run[key].to_string())
            .collect::<std::collections::BTreeSet<_>>()
            .len()
    };
    assert!(distinct(&frozen("cycle"), "fingerprint") > 1);
    assert!(distinct(&frozen("cycle"), "related") > 1);

    let tree = &oracle["trees"]["cycle"];
    let base = analyze(tree, "one", "0");
    assert_eq!(base, analyze(tree, "one", "1"), "another hash seed");
    assert_eq!(base, analyze(tree, "two/deeper", "0"), "a tree move");

    let changed = analyze(&oracle["trees"]["changed"], "one", "0");
    assert_ne!(base["fingerprint"], changed["fingerprint"], "a real change");
    assert_ne!(base["related"], changed["related"]);
}
