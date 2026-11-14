//! Approved difference (decisions.md "하위 root component의 next finding 경로"), C++ facet: a
//! component with `root = "src"` reports workspace-relative paths where frozen reported
//! component-relative ones, so the message and the fingerprint follow
//! (`tests/rust-parity/cpp/next-root-relative-oracle.json`, `capture_next_root_relative.py`).
//! Upgrade impact: frozen's own `result.json` as a native `--baseline` is `incompatible` and pairs
//! nothing.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn environment(home: &Path, ceiling: &Path) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            ceiling.display().to_string(),
        ),
    ])
}

fn result(base: &Path, environment: &BTreeMap<String, String>, extra: &[&str]) -> Value {
    let mut argv = vec!["ici", "next", "verify", "--no-cache"];
    argv.extend_from_slice(extra);
    let invocation = registry::parse(argv).unwrap();
    commands::dispatch(&invocation, base, environment).unwrap();
    serde_json::from_slice(&fs::read(base.join(".ici/rust-v1/result.json")).unwrap()).unwrap()
}

#[test]
fn a_sub_root_cpp_component_reports_workspace_relative_paths() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-root-relative-oracle.json"
    ))
    .unwrap();
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    for (path, text) in oracle["tree"].as_object().unwrap() {
        let file = base.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(base.join(".hg")).unwrap();
    let environment = environment(home.path(), directory.path());
    let native = result(&base, &environment, &[]);
    let cycle = |document: &Value| {
        document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .find(|finding| finding["rule_id"] == "cycle.include")
            .expect("one include cycle")
            .clone()
    };
    let (ours, frozen) = (cycle(&native), cycle(&oracle["result"]));
    assert_eq!(ours["component_id"], "lib");
    assert_eq!(ours["component_id"], frozen["component_id"]);
    let path = |finding: &Value| {
        finding["primary_location"]["path"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    let related = |finding: &Value| -> Vec<String> {
        finding["related_locations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["path"].as_str().unwrap().to_owned())
            .collect()
    };
    assert_eq!(path(&ours), format!("src/{}", path(&frozen)));
    assert_eq!(
        related(&ours),
        related(&frozen)
            .iter()
            .map(|item| format!("src/{item}"))
            .collect::<Vec<_>>()
    );
    assert_eq!(
        ours["message"],
        "include cycle: src/b.hpp -> src/a.hpp -> src/b.hpp"
    );
    assert_eq!(frozen["message"], "include cycle: b.hpp -> a.hpp -> b.hpp");
    assert_ne!(ours["fingerprint"], frozen["fingerprint"]);

    // Upgrade impact: the frozen result is an incompatible baseline, nothing pairs with it.
    let baseline = directory.path().join("frozen-result.json");
    fs::write(&baseline, oracle["result"].to_string()).unwrap();
    let compared = result(
        &base,
        &environment,
        &["--baseline", baseline.to_str().unwrap()],
    );
    let state = &compared["baseline"];
    assert_eq!(state["state"], "incompatible", "{state}");
    for key in ["new", "carried", "unchanged", "resolved"] {
        assert_eq!(state[key], serde_json::json!([]), "{key}: {state}");
    }
}
