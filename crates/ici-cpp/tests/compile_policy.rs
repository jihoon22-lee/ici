use ici_cpp::{ContextRequest, analyze_compile_context, load_compilation_context};
use ici_domain::{AnalysisRequest, EvidenceLevel, SourceInput, TaskState};
use serde_json::json;
use std::fs;
fn request(options: serde_json::Value) -> AnalysisRequest {
    AnalysisRequest {
        task_id: "project.cpp.compile".into(),
        check_id: "cpp.compile".into(),
        component_id: "project".into(),
        language: "cpp".into(),
        inputs: vec![SourceInput {
            path: "main.cpp".into(),
            bytes: b"int value;\n".to_vec(),
        }],
        options,
        target_version: "c++17".into(),
    }
}
#[test]
fn optional_missing_database_preserves_estimated_warning_and_required_absence_is_violation() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    );
    let optional = analyze_compile_context(&request(json!({})), &context);
    assert_eq!(optional.status, "WARN");
    assert_eq!(optional.observation.state, TaskState::Succeeded);
    assert_eq!(optional.targets[0].file_path, "main.cpp");
    assert_eq!(
        optional.observation.measurements[0].evidence,
        EvidenceLevel::Estimated
    );
    assert_eq!(
        optional.observation.findings[0].rule_id,
        "ici.compile-db.coverage"
    );
    assert_eq!(
        optional.observation.findings[0].evidence,
        EvidenceLevel::Estimated
    );
    let required = analyze_compile_context(&request(json!({"database_required":true})), &context);
    assert_eq!(required.status, "FAIL");
    assert_eq!(required.observation.state, TaskState::Succeeded);
    assert_eq!(required.observation.findings[0].severity, "high");
    let explicit = load_compilation_context(
        root.path(),
        &ContextRequest {
            database: Some("missing.json".into()),
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    );
    let failed = analyze_compile_context(&request(json!({"database_required":false})), &explicit);
    assert_eq!(failed.observation.state, TaskState::Failed);
    assert!(
        failed
            .observation
            .limitations
            .iter()
            .any(|value| value.contains("database"))
    );
}
#[test]
fn flag_policy_uses_each_ordered_expanded_configuration_and_keeps_distinct_violations() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    fs::write(
        root.path().join("flags.rsp"),
        "-std=c++17 -fPIC -DVALUE=first -DVALUE=last",
    )
    .unwrap();
    let rows = json!([{"directory":root.path(),"file":"main.cpp","arguments":["g++","@flags.rsp","-c","main.cpp","-o","one.o"]},{"directory":root.path(),"file":"main.cpp","arguments":["g++","-std=c++17","-fpermissive","-c","main.cpp","-o","two.o"]}]);
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&rows).unwrap(),
    )
    .unwrap();
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    );
    assert!(context.complete(), "{context:?}");
    let options =
        json!({"required_flags":["-fPIC","-DVALUE=last"],"forbidden_flags":["-fpermissive"]});
    let output = analyze_compile_context(&request(options), &context);
    assert_eq!(output.status, "FAIL");
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.observation.findings.len(), 3);
    assert_eq!(
        output
            .observation
            .findings
            .iter()
            .map(|finding| &finding.fingerprint)
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        3
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .all(|finding| finding.primary_location.path == "main.cpp"
                && finding.analysis_unit_id.is_some())
    );
    let pass = analyze_compile_context(
        &request(json!({"required_flags":["-std=c++17"],"forbidden_flags":["-fno-such-option"]})),
        &context,
    );
    assert_eq!(pass.status, "PASS");
    assert!(pass.observation.findings.is_empty());
}
