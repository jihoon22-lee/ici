//! Frozen `run_cpp_lint` with C++ sources, no build context and no g++
//! (`capture_lint_no_compiler.py`): `ici lint` and the lint engine of `ici verify` are one ERROR,
//! NOT_RUN, with the missing tool recorded and no command run.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const TARGET_FIELDS: [&str; 5] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "file_path",
];
const EVIDENCE_FIELDS: [&str; 4] = ["name", "path", "returncode", "error"];
const EXTRA_FIELDS: [&str; 4] = [
    "cpp_analysis_mode",
    "cpp_configurations_checked",
    "cpp_sources_checked",
    "cpp_missing_context_sources",
];

fn pick(value: &Value, fields: &[&str]) -> Value {
    Value::Object(
        fields
            .iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// One run's projected lint engine and its saved native gate.
fn run(oracle: &Value, arguments: &[&str], report: &str) -> (Value, Value) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap();
    let (base, tools) = (root.join("project"), root.join("bin"));
    fs::create_dir_all(base.join("src")).unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    fs::create_dir(&tools).unwrap();
    fs::write(base.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    fs::write(base.join("src/a.cpp"), oracle["source"].as_str().unwrap()).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), root.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), root.display().to_string()),
        ("PATH".to_owned(), tools.display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            root.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(arguments.iter().copied()).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value =
        serde_json::from_str(&fs::read_to_string(base.join(report)).unwrap()).unwrap();
    let engine = document["results"].as_array().map_or(&document, |engines| {
        engines
            .iter()
            .find(|engine| engine["engine_name"] == "lint")
            .unwrap()
    });
    let list = |key: &str, fields: &[&str]| -> Vec<Value> {
        engine[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| pick(item, fields))
            .collect()
    };
    let projected = json!({
        "exit_code": output.exit_code,
        "status": engine["status"],
        "evidence": engine["evidence"],
        "required": engine["required"],
        "summary": engine["summary"],
        "targets": list("targets", &TARGET_FIELDS),
        "tool_evidence": list("tool_evidence", &EVIDENCE_FIELDS),
        "extra": pick(&engine["extra"], &EXTRA_FIELDS),
    });
    (projected, document["native_run"]["gate"].clone())
}

/// `ici verify` exits 3 where frozen exits 1, within decisions.md "legacy 종료 코드 3의 범위",
/// and this asserts every condition of that allowance before it is applied: frozen ended exactly
/// 1 and the lint engine is required, ERROR and NOT_RUN on both sides. `verify` runs every engine
/// and lint is not the check the gate names: the only INCOMPLETE reason here is the C++ test
/// check, and the same fixture with g++ on PATH keeps that gate and exits 1 like frozen, so the
/// required lint engine's NOT_RUN is what keeps the 3. Any other gate reason fails this test.
fn apply_the_verify_exit_allowance(actual: &mut Value, frozen: &Value, gate: &Value) {
    assert_eq!(frozen["exit_code"], 1, "frozen verify exit");
    for (side, engine) in [("frozen", frozen), ("native", &*actual)] {
        assert_eq!(
            (&engine["required"], &engine["status"], &engine["evidence"]),
            (&json!(true), &json!("ERROR"), &json!("NOT_RUN")),
            "{side} lint engine"
        );
    }
    assert_eq!(
        gate,
        &json!({
            "has_violations": false,
            "reasons": ["project.cpp.test: C++ runtime checks require a declared component build"],
            "selected": "INCOMPLETE",
            "workspace": "NOT_EVALUATED",
        }),
        "saved native gate"
    );
    actual["exit_code"] = frozen["exit_code"].clone();
}

#[test]
fn cpp_lint_without_gxx_equals_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-no-compiler-oracle.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for (name, arguments, report) in [
        ("lint", vec!["ici", "lint", "--report"], "lint_report.json"),
        (
            "verify",
            vec![
                "ici",
                "verify",
                "--profile",
                "deep",
                "--no-cache",
                "--report",
            ],
            "verify_report.json",
        ),
    ] {
        let (mut actual, gate) = run(&oracle, &arguments, report);
        // Only `verify`; standalone `ici lint` has no such allowance and is compared as is.
        if name == "verify" && actual["exit_code"] == 3 {
            apply_the_verify_exit_allowance(&mut actual, &oracle["cases"][name], &gate);
        }
        if actual != oracle["cases"][name] {
            differences.push(format!(
                "{name}:\n native {actual}\n frozen {}",
                oracle["cases"][name]
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
