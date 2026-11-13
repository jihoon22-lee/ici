//! Real g++ sanitizer runs parsed by the frozen next `SanitizeProvider.parse`
//! (`tests/rust-parity/cpp/capture_sanitizer_kinds.py`: UBSan runtime error, LSan leak, a report
//! without a project frame; `capture_cpptest_parsers.py`: heap use-after-free, plain non-zero
//! exit) replayed through the native frozen-next sanitizer step (`frozen_next_sanitizer`).
use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const STEP: &str = "app.cpp.sanitize.unit";

fn oracle(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/cpp")
        .join(name);
    let oracle: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn replay(name: &str, case: &Value, source_text: &str) {
    let kind = if name.starts_with("thread-sanitize-") {
        ValidationKind::ThreadSanitizer
    } else {
        ValidationKind::Sanitizer
    };
    let root = tempfile::tempdir().unwrap();
    let source = case["source"].as_str().unwrap();
    fs::create_dir_all(root.path().join(source).parent().unwrap()).unwrap();
    fs::write(root.path().join(source), source_text).unwrap();
    let binary = root.path().join("build").join(name);
    let request = ValidationRequest {
        kind,
        task_id: STEP.into(),
        component_id: "app".into(),
        root: root.path().into(),
        cwd: root.path().into(),
        owned_run_dir: root.path().join("run"),
        tool: ResolvedTool {
            id: "cpp-suite".into(),
            path: binary.clone(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec![format!("build/{name}")],
        source_files: vec![source.into()],
        functions: vec![],
        options: ValidationOptions {
            build_dir: Some(root.path().join("build")),
            suite_step_id: Some(STEP.into()),
            frozen_next_sanitizer: true,
            argv: vec![binary.display().to_string()],
            ..Default::default()
        },
    };
    let planned = plan(&request).unwrap();
    assert_eq!(planned.tasks.len(), 1, "{name}");
    let replace = |text: &Value| {
        text.as_str()
            .unwrap()
            .replace("<ROOT>", &root.path().display().to_string())
    };
    let mut suite = TaskOutcome::empty(&planned.tasks[0].id);
    suite.state = TaskState::Succeeded;
    suite.exit_code = case["exit_code"].as_i64().map(|code| code as i32);
    suite.stdout = replace(&case["stdout"]).into_bytes();
    suite.stderr = replace(&case["stderr"]).into_bytes();
    let output = observe(
        &planned,
        &BTreeMap::from([(suite.task_id.clone(), suite)]),
        &CapturedArtifacts::default(),
    );
    let frozen = &case["frozen"];
    assert_eq!(frozen["failed_to_parse"], "", "{name}");
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{name}: {output:?}"
    );
    let findings: Vec<Value> = output
        .observation
        .findings
        .iter()
        .map(|item| {
            json!({"rule_id":item.rule_id,"message":item.message,"native_rule_id":item.native_rule_id,
                "path":item.primary_location.path,"start_line":item.primary_location.start_line,
                "start_column":item.primary_location.start_column,"label":item.primary_location.label,
                "related":item.related_locations.iter().map(|span|json!([span.path,span.start_line,span.label])).collect::<Vec<_>>(),
                "severity":item.severity,"confidence":item.confidence,"limitations":item.limitations})
        })
        .collect();
    assert_eq!(Value::Array(findings), frozen["findings"], "{name}");
    // Root ruling 2026-11-14 (b5): a suite failure carries no `<variant>.diagnostics` count.
    let counts: Vec<Value> = output
        .observation
        .measurements
        .iter()
        .map(|item| json!({"name":item.name,"value":item.value}))
        .collect();
    assert_eq!(Value::Array(counts), frozen["measurements"], "{name}");
}

#[test]
fn actual_ubsan_lsan_and_unlocated_reports_match_the_frozen_next_sanitize_provider() {
    let oracle = oracle("sanitizer-kinds-oracle.json");
    let cases = oracle["cases"].as_object().unwrap();
    for (name, case) in cases {
        replay(name, case, case["source_text"].as_str().unwrap());
    }
    assert_eq!(cases.len(), 3);
}

#[test]
fn actual_use_after_free_and_suite_failure_match_the_frozen_next_sanitize_provider() {
    let oracle = oracle("cpptest-parser-oracle.json");
    let uaf = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/test-cmake/sanitize/tests/test_uaf.cpp"),
    )
    .unwrap();
    let mut seen = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if !name.starts_with("sanitize-") && !name.starts_with("thread-sanitize-") {
            continue;
        }
        let text = if name.ends_with("-uaf") {
            uaf.as_str()
        } else {
            "int main() { return 3; }\n"
        };
        replay(name, case, text);
        seen += 1;
    }
    assert_eq!(seen, 4);
}
