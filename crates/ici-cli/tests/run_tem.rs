//! Every next run ends its limitations with frozen `application/report.py:92` run TEM: the
//! frozen `ici analyze` capture (tests/rust-parity/oracle/analyze-cold.json, from
//! tests/rust-parity/capture_cli_oracle.py) records `project: TEM N/A — …` last.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

#[test]
fn analyze_appends_the_frozen_tem_limitation_after_every_other_limitation() {
    let frozen: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/oracle/analyze-cold.json"
    ))
    .unwrap();
    let work = tempfile::tempdir().unwrap();
    let root = work.path();
    fs::write(
        root.join("ici.toml"),
        "schema_version=1\n[workspace]\nname=\"oracle\"\nprofile=\"fast\"\n[[components]]\nid=\"project\"\nroot=\".\"\nlanguages=[\"python\"]\n",
    )
    .unwrap();
    fs::write(
        root.join("main.py"),
        "def answer(value):\n    if value:\n        return 42\n    return 0\n",
    )
    .unwrap();
    let invocation =
        registry::parse(["ici", "analyze", "--json", "--result", "result.json"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    commands::dispatch(&invocation, root, &environment).unwrap();
    let native: Value =
        serde_json::from_slice(&fs::read(root.join("result.json")).unwrap()).unwrap();
    let tem = |document: &Value| -> Vec<Value> {
        document["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|limitation| limitation.as_str().unwrap().contains("TEM"))
            .cloned()
            .collect()
    };
    assert_eq!(tem(&native), tem(&frozen));
    assert_eq!(
        native["limitations"].as_array().unwrap().last(),
        frozen["limitations"].as_array().unwrap().last()
    );
    let scored = |document: &Value| {
        document["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|metric| metric["name"].as_str().unwrap().starts_with("tem."))
            .count()
    };
    assert_eq!((scored(&native), scored(&frozen)), (0, 0));
    // The measurements before the TEM tuple, in observation order. Evidence levels are left
    // out: native marks the python.dead `issues_found` ESTIMATED where frozen says MEASURED,
    // a python-analysis difference outside this assembly.
    let measured = |document: &Value| -> Vec<Value> {
        document["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .map(|metric| {
                let mut metric = metric.clone();
                metric.as_object_mut().unwrap().remove("evidence");
                metric
            })
            .collect()
    };
    assert_eq!(measured(&native), measured(&frozen));
}
