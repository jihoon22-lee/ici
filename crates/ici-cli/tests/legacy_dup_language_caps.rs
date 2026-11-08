//! Approved resource-policy difference (decisions.md "hybrid dup 언어별 한도"): a hybrid legacy
//! `ici dup` keeps its source-file cap (2048) per language where frozen bounded Python and C++
//! together in one intake (`tests/rust-parity/cpp/dup-language-caps-oracle.json`,
//! `capture_dup_language_caps.py`). Within both per-language caps but over the old shared total,
//! native completes the scan; over one language's cap it stops like frozen, incomplete and never
//! a 0 % or PASS result.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

fn run(case: &Value, config: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap().join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("ici.toml"), config).unwrap();
    for index in 0..case["python_sources"].as_u64().unwrap() {
        fs::write(
            root.join(format!("src/p{index:04}.py")),
            format!("x{index} = {index}\n"),
        )
        .unwrap();
    }
    for index in 0..case["cpp_sources"].as_u64().unwrap() {
        fs::write(
            root.join(format!("src/c{index:04}.cpp")),
            format!("int c{index};\n"),
        )
        .unwrap();
    }
    fs::create_dir(root.join(".git")).unwrap();
    let home = work.path().join("home");
    fs::create_dir(&home).unwrap();
    let environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            work.path().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "dup", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
    let report: Value =
        serde_json::from_str(&fs::read_to_string(root.join("dup_report.json")).unwrap()).unwrap();
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!({"target_name": target["target_name"], "status": target["status"],
                "message": target["message"], "file_path": target["file_path"]})
        })
        .collect();
    json!({"exit_code": output.exit_code, "status": report["status"],
        "evidence": report["evidence"], "summary": report["summary"], "targets": targets})
}

#[test]
fn hybrid_dup_keeps_the_source_file_cap_per_language() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/dup-language-caps-oracle.json"
    ))
    .unwrap();
    let config = oracle["config"].as_str().unwrap();
    // 1100 + 1100 sources: frozen's shared intake stops at 2048; native scans both languages.
    let within = &oracle["cases"]["within-language-caps"];
    assert_eq!(within["frozen"]["status"], "ERROR");
    assert_eq!(
        within["frozen"]["targets"][0]["target_name"],
        "SourceInputError"
    );
    let native = run(within, config);
    assert_eq!(native["exit_code"], 0, "{native}");
    assert_eq!(native["status"], "PASS", "{native}");
    assert!(!native.to_string().contains("SourceInputError"), "{native}");
    // 2049 C++ sources: over the C++ cap natively too, the same result as frozen's intake stop
    // (exit, status, evidence, summary and targets): incomplete, never 0 % or PASS.
    let over = &oracle["cases"]["cpp-over-cap"];
    let native = run(over, config);
    assert_eq!(native["status"], "ERROR", "{native}");
    assert_eq!(native, over["frozen"]);
}
