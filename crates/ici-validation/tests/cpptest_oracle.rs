//! Real ctest/QTest transcripts parsed by the frozen `ici next` cpptest provider
//! (`tests/rust-parity/cpp/capture_cpptest_parsers.py`) replayed through the native observers.
use ici_domain::{AnalysisOutput, TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn sources(root: &Path, fixture: &Path, relative: &Path, found: &mut Vec<String>) {
    for entry in fs::read_dir(fixture.join(relative)).unwrap() {
        let entry = entry.unwrap();
        let path = relative.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            fs::create_dir_all(root.join(&path)).unwrap();
            sources(root, fixture, &path, found);
        } else {
            fs::copy(entry.path(), root.join(&path)).unwrap();
            if path.extension().is_some_and(|extension| extension == "cpp") {
                found.push(path.to_string_lossy().into_owned());
            }
        }
    }
}

fn observed(case: &Value, kind: ValidationKind, legacy: bool) -> (AnalysisOutput, String) {
    let root = tempfile::tempdir().unwrap();
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp")
        .join(case["fixture"].as_str().unwrap());
    let mut files = vec![];
    sources(root.path(), &fixture, Path::new(""), &mut files);
    files.sort();
    let mut options = ValidationOptions {
        build_dir: Some(root.path().join("build")),
        ..Default::default()
    };
    options.ctest_not_run_measured = legacy;
    let request = ValidationRequest {
        kind,
        task_id: "app.cpp.test".into(),
        component_id: "app".into(),
        root: root.path().into(),
        cwd: root.path().into(),
        owned_run_dir: root.path().join("run"),
        tool: ResolvedTool {
            id: kind.provider().into(),
            path: "/usr/bin/ctest".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["build/CTestTestfile.cmake".into()],
        source_files: files,
        functions: vec![],
        options,
    };
    let planned = plan(&request).unwrap();
    let id = planned.tasks[0].id.clone();
    let text = case["transcript"]
        .as_str()
        .unwrap()
        .replace("<ROOT>", &root.path().display().to_string());
    let mut outcome = TaskOutcome::empty(&id);
    outcome.state = TaskState::Succeeded;
    outcome.exit_code = Some(if kind == ValidationKind::Ctest { 8 } else { 2 });
    outcome.stdout = text.into_bytes();
    let output = observe(
        &planned,
        &BTreeMap::from([(id.clone(), outcome)]),
        &CapturedArtifacts::default(),
    );
    (output, root.path().display().to_string())
}

fn findings(output: &AnalysisOutput) -> Vec<Value> {
    output
        .observation
        .findings
        .iter()
        .map(|finding| {
            json!({"rule_id":finding.rule_id,"message":finding.message,
                "path":finding.primary_location.path,"start_line":finding.primary_location.start_line,
                "severity":finding.severity,"confidence":finding.confidence})
        })
        .collect()
}

fn counts(output: &AnalysisOutput) -> Vec<Value> {
    output
        .observation
        .measurements
        .iter()
        .filter(|item| item.name.ends_with(".cases"))
        .map(|item| {
            json!({"name":item.name,"value":item.value,"numerator":item.numerator,"denominator":item.denominator})
        })
        .collect()
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cpptest-parser-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

#[test]
fn actual_ctest_transcript_findings_and_counts_match_the_frozen_cpptest_parser() {
    let case = &oracle()["cases"]["ctest-suite"];
    let frozen = &case["frozen"];
    // Frozen next keeps not-run cases as a limitation of a measured run, as legacy Rust does.
    let (output, _) = observed(case, ValidationKind::Ctest, true);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert_eq!(Value::Array(findings(&output)), frozen["findings"]);
    assert_eq!(Value::Array(counts(&output)), frozen["measurements"]);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|item| item == "2 CTest suites were not run")
    );
    // Intended (R05): native refuses a suite whose cases did not all run; the failures found
    // are still the frozen ones.
    let (native, _) = observed(case, ValidationKind::Ctest, false);
    assert_eq!(native.observation.state, TaskState::Failed);
    assert_eq!(Value::Array(findings(&native)), frozen["findings"]);
}

#[test]
fn actual_qtest_transcript_findings_and_counts_match_the_frozen_cpptest_parser() {
    let oracle = oracle();
    let case = &oracle["cases"]["qtest-in-root-locations"];
    let frozen = &case["frozen"];
    let (output, _) = observed(case, ValidationKind::Qtest, false);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let mut expected = frozen["findings"].as_array().unwrap().clone();
    // Known frozen defect: the SKIP row's own `Loc:` line (12) overwrites the preceding XPASS
    // finding's location (10); Rust keeps each Loc with the verdict it follows.
    assert_eq!(expected[1]["start_line"], 12);
    expected[1]["start_line"] = json!(10);
    assert_eq!(Value::Array(findings(&output)), Value::Array(expected));
    assert_eq!(Value::Array(counts(&output)), frozen["measurements"]);
    // Frozen raises "span path must be relative and contained" on an out-of-source build's
    // `Loc: [../../tests/...]` (an internal error); Rust refuses the escaping location as
    // incomplete evidence with a stated limitation.
    let case = &oracle["cases"]["qtest-out-of-source"];
    assert!(
        case["frozen"]["exception"]
            .as_str()
            .unwrap()
            .contains("span path must be relative and contained")
    );
    let (output, _) = observed(case, ValidationKind::Qtest, false);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|item| item.contains("external validation location ../../tests/test_unit.cpp")),
        "{:?}",
        output.observation.limitations
    );
}

#[test]
fn actual_sanitizer_runs_match_the_frozen_next_sanitize_provider_findings() {
    let oracle = oracle();
    let mut seen = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let kind = if name.starts_with("sanitize-") {
            ValidationKind::Sanitizer
        } else if name.starts_with("thread-sanitize-") {
            ValidationKind::ThreadSanitizer
        } else {
            continue;
        };
        seen += 1;
        let root = tempfile::tempdir().unwrap();
        let replace = |text: &Value| {
            text.as_str()
                .unwrap()
                .replace("<ROOT>", &root.path().display().to_string())
        };
        let source = case["source"].as_str().unwrap();
        let bytes = if source.ends_with("test_uaf.cpp") {
            fs::read(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("../../parity-fixtures/cpp/test-cmake/sanitize")
                    .join(source),
            )
            .unwrap()
        } else {
            b"int main() { return 3; }\n".to_vec()
        };
        let binary = format!("build/{name}");
        let request = ValidationRequest {
            kind,
            task_id: "app.cpp.sanitize.unit".into(),
            component_id: "app".into(),
            root: root.path().into(),
            cwd: root.path().into(),
            owned_run_dir: root.path().join("run"),
            tool: ResolvedTool {
                id: "cpp-suite".into(),
                path: root.path().join(&binary),
                source: ToolSource::Explicit,
                digest: sha256("fixture"),
                version: None,
                configuration: vec![],
            },
            // An explicit suite argv names its binary as the target (cpp_validation).
            targets: vec![binary.clone()],
            source_files: vec![source.into()],
            functions: vec![],
            options: ValidationOptions {
                build_dir: Some(root.path().join("build")),
                ..Default::default()
            },
        };
        let mut request = request;
        let readelf = ResolvedTool {
            id: "readelf".into(),
            path: "/usr/bin/readelf".into(),
            source: ToolSource::Explicit,
            digest: sha256("readelf"),
            version: None,
            configuration: vec![],
        };
        request
            .options
            .auxiliary_tools
            .insert("readelf".into(), readelf);
        let mut planned = plan(&request).unwrap();
        attach_sources(
            &mut planned,
            ici_providers::source_coordinates(&[ici_domain::SourceInput {
                path: source.into(),
                bytes,
            }]),
        );
        let marker = if kind == ValidationKind::Sanitizer {
            "__asan_init"
        } else {
            "__tsan_init"
        };
        let mut probe = TaskOutcome::empty(&planned.tasks[0].id);
        probe.state = TaskState::Succeeded;
        probe.exit_code = Some(0);
        probe.stdout =
            format!("Symbol table '.dynsym' contains 1 entry:\n 0 {marker}\n").into_bytes();
        let mut suite = TaskOutcome::empty(&planned.tasks[1].id);
        suite.state = TaskState::Succeeded;
        suite.exit_code = case["exit_code"].as_i64().map(|code| code as i32);
        suite.stdout = replace(&case["stdout"]).into_bytes();
        suite.stderr = replace(&case["stderr"]).into_bytes();
        let output = observe(
            &planned,
            &BTreeMap::from([
                (probe.task_id.clone(), probe),
                (suite.task_id.clone(), suite),
            ]),
            &CapturedArtifacts::default(),
        );
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{name}: {output:?}"
        );
        let actual: Vec<Value> = output
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
        assert_eq!(Value::Array(actual), case["frozen"]["findings"], "{name}");
    }
    assert_eq!(seen, 4);
}
