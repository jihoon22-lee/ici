//! Native's input limits (decisions.md "`cpp.line` 입력 상한") through real `ici next verify --json`
//! on sparse trees: at a limit `cpp.line` equals the frozen capture
//! (`tests/rust-parity/cpp/cpp-line-bound-oracle.json`); one byte past it frozen still measures, and
//! native refuses the run naming the limit, with no measured line count and no complete check.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, fs::File, io::Write};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/cpp-line-bound-oracle.json");

fn verify(oracle: &Value, case: &Value) -> (i32, Value) {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = base.join("project");
    let home = base.join("home");
    fs::create_dir_all(project.join("src")).unwrap();
    fs::create_dir_all(&home).unwrap();
    fs::create_dir(base.join(".hg")).unwrap();
    fs::write(project.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    for (index, size) in case["sizes"].as_array().unwrap().iter().enumerate() {
        let mut file = File::create(project.join(format!("src/f{index:02}.cpp"))).unwrap();
        file.write_all(b"int a;\n").unwrap();
        file.set_len(size.as_u64().unwrap()).unwrap();
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "next", "verify", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &project, &environment).unwrap();
    (
        output.exit_code,
        serde_json::from_str(&output.stdout).unwrap(),
    )
}

fn checks(document: &Value) -> Vec<Value> {
    document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            json!({
                "check_id": check["check_id"],
                "state": check["state"],
                "evidence_complete": check["evidence_complete"],
            })
        })
        .collect()
}

fn lines(document: &Value) -> Vec<Value> {
    document["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|metric| metric["name"].as_str().unwrap().starts_with("lines_"))
        .map(|metric| {
            json!({"name": metric["name"], "value": metric["value"], "evidence": metric["evidence"]})
        })
        .collect()
}

fn at_limit(name: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let case = &oracle["cases"][name];
    let (exit, native) = verify(&oracle, case);
    assert_eq!(exit, case["exit_code"], "{name}");
    assert_eq!(native["gate"], case["gate"], "{name}");
    assert_eq!(
        checks(&native),
        *case["checks"].as_array().unwrap(),
        "{name}"
    );
    assert_eq!(
        lines(&native),
        *case["metrics"].as_array().unwrap(),
        "{name}"
    );
}

fn past_limit(name: &str, limit: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let case = &oracle["cases"][name];
    // Frozen has no such limit: it measures this tree and passes.
    assert_eq!(case["exit_code"], 0, "{name}");
    assert_eq!(case["checks"][0]["state"], "SUCCEEDED", "{name}");
    assert!(!case["metrics"].as_array().unwrap().is_empty(), "{name}");
    let (exit, native) = verify(&oracle, case);
    assert_eq!(exit, 3, "{name}");
    assert_eq!(native["gate"]["selected"], "INCOMPLETE", "{name}");
    assert_eq!(
        checks(&native),
        [json!({"check_id": "cpp.line", "state": "BLOCKED", "evidence_complete": false})],
        "{name}"
    );
    assert_eq!(
        lines(&native),
        Vec::<Value>::new(),
        "{name}: nothing was measured"
    );
    let limitations = native["limitations"].as_array().unwrap();
    assert!(
        limitations
            .iter()
            .any(|item| item.as_str().unwrap().contains(limit)),
        "{name}: {limitations:?}"
    );
    // The refusal is what happened: no source changed during the run.
    assert!(
        !limitations
            .iter()
            .chain(native["gate"]["reasons"].as_array().unwrap())
            .any(|item| item.as_str().unwrap().contains("inputs changed")),
        "{name}: {native}"
    );
}

#[test]
fn a_source_at_the_per_file_limit_is_measured_like_frozen() {
    at_limit("file-at-limit");
}

#[test]
fn one_byte_past_the_per_file_limit_refuses_the_run_naming_it() {
    past_limit("file-past-limit", "16 MiB per-file limit");
}

#[test]
#[ignore = "reads 512 MiB of sparse sources"]
fn inputs_at_the_aggregate_limit_are_measured_like_frozen() {
    at_limit("aggregate-at-limit");
}

#[test]
#[ignore = "reads 512 MiB of sparse sources"]
fn one_byte_past_the_aggregate_limit_refuses_the_run_naming_it() {
    past_limit("aggregate-past-limit", "512 MiB aggregate input limit");
}
