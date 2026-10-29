//! Legacy python-compat runtime/import parity on a src-layout project (actual project Python).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("src/pkg")).unwrap();
    fs::write(
        root.path().join("src/pkg/__init__.py"),
        "import json\n\ndef f():\n    return json.dumps({})\n",
    )
    .unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname = \"demo-pkg\"\nversion = \"1.0\"\nrequires-python = \">=3.11\"\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "[ici]\nprofile = \"standard\"\n[project]\nsource_dirs = [\"src\"]\n[engines.python_compat]\nenabled = true\ninterpreters = [{0}, \"/nonexistent/python3.99\"]\nrequired_interpreters = [{0}]\nimports = [\"pkg\", \"pkg.nothere\"]\n",
            serde_json::to_string(&python()).unwrap()
        ),
    )
    .unwrap();
    root
}
fn report(root: &Path) -> Value {
    commands::dispatch(
        &registry::parse(["ici", "python-compat", "--report"]).unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    serde_json::from_slice(&fs::read(root.join("python_compat_report.json")).unwrap()).unwrap()
}

#[test]
#[ignore = "explicit project Python src-layout import-only and runtime matrix acceptance"]
fn src_layout_imports_resolve_through_declared_source_dirs_and_unavailable_is_reported_once() {
    let root = fixture();
    let engine = report(root.path());
    let messages: Vec<_> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["tool_rule_id"] == "python.compat.import")
        .map(|finding| finding["message"].as_str().unwrap().to_owned())
        .collect();
    // Frozen prepends [root, *source_dirs]; only the genuinely missing module fails.
    assert_eq!(messages.len(), 1, "{messages:?}");
    assert!(
        messages[0].starts_with("import pkg.nothere:"),
        "{messages:?}"
    );
    let unavailable = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .contains("/nonexistent/python3.99")
        })
        .count();
    assert_eq!(unavailable, 1);
    assert!(
        engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["tool_rule_id"] == "python.compat.runtime-version")
    );
    assert!(!root.path().join("src/pkg/__pycache__").exists());
}
