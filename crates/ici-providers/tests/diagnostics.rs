use ici_domain::{EvidenceLevel, TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest, observe, observe_full, plan,
    resolver::{ResolvedTool, ToolSource},
};
use serde_json::{Value, json};
use std::path::PathBuf;
fn fixture(kind: ProviderKind) -> ProviderPlan {
    let tool = ResolvedTool {
        id: kind.name().into(),
        path: PathBuf::from("/usr/bin/true"),
        source: ToolSource::Explicit,
        digest: sha256("replay executable"),
        version: None,
        configuration: vec![],
    };
    let mut request = ProviderRequest::new(
        kind,
        tool,
        "/workspace",
        "/workspace",
        vec!["main.py".into(), "pkg/λ.py".into()],
        "component.check",
    );
    request.component_id = Some("component".into());
    request.analysis_unit_ids = vec!["unit".into()];
    request.variant = Some("debug".into());
    if matches!(kind, ProviderKind::Compiler) {
        request.options.compiler_argv = vec!["g++".into(), "-c".into(), "main.py".into()];
        request.options.source_token = Some("main.py".into());
    }
    if matches!(kind, ProviderKind::ClangTidy | ProviderKind::Clazy) {
        request.options.database_dir = Some("build".into());
    }
    plan(&request).unwrap()
}
fn outcome(plan: &ProviderPlan, stdout: &str, stderr: &str, exit: i32) -> TaskOutcome {
    let mut result = TaskOutcome::empty(&plan.task.id);
    result.state = TaskState::Succeeded;
    result.exit_code = Some(exit);
    result.stdout = stdout.as_bytes().to_vec();
    result.stderr = stderr.as_bytes().to_vec();
    result
}
fn ruff_item() -> Value {
    json!({"filename":"/workspace/pkg/λ.py", "code":"F821", "message":"Undefined name `missing`", "location":{"row":2,"column":3}, "end_location":{"row":2,"column":10}})
}
#[test]
fn ruff_preserves_unicode_location_inclusive_end_and_frozen_fingerprint() {
    let plan = fixture(ProviderKind::RuffLint);
    let raw = json!([ruff_item()]).to_string();
    let result = observe_full(&plan, &outcome(&plan, &raw, "", 1));
    assert_eq!(result.observation.state, TaskState::Succeeded);
    let finding = &result.observation.findings[0];
    assert_eq!(finding.primary_location.path, "pkg/λ.py");
    assert_eq!(finding.primary_location.start_line, 2);
    assert_eq!(finding.primary_location.start_column, Some(3));
    assert_eq!(finding.primary_location.end_column, Some(9));
    assert_eq!(
        finding.fingerprint,
        "sha256:e88263387a510086abe812c49961dcce4781c0f36522af8e7bf77a8f78e21ecf"
    );
    assert_eq!(finding.analysis_unit_id.as_deref(), Some("unit"));
    assert_eq!(finding.component_id.as_deref(), Some("component"));
    assert_eq!(
        result
            .targets
            .iter()
            .find(|target| target.file_path == "main.py")
            .unwrap()
            .status,
        "SKIP"
    );
}
#[test]
fn malformed_missing_and_truncated_native_output_never_becomes_empty_success() {
    let plan = fixture(ProviderKind::RuffLint);
    for text in ["{}", "[", "[{\"filename\":\"main.py\"}]", "[null]"] {
        let observation = observe(&plan, &outcome(&plan, text, "", 0));
        assert_eq!(observation.state, TaskState::Failed, "{text}");
    }
    let mut bad = ruff_item();
    bad["location"]["row"] = json!(true);
    assert_eq!(
        observe(&plan, &outcome(&plan, &json!([bad]).to_string(), "", 1)).state,
        TaskState::Failed
    );
    assert_eq!(
        observe(&plan, &outcome(&plan, "[]", "", 1)).state,
        TaskState::Failed
    );
    let mut raw = outcome(&plan, &json!([ruff_item()]).to_string(), "", 1);
    raw.truncated = true;
    let observation = observe(&plan, &raw);
    assert!(observation.findings.is_empty());
    assert!(!observation.evidence_is_complete());
    raw.truncated = false;
    raw.task_id = "wrong".into();
    assert_eq!(observe(&plan, &raw).state, TaskState::Failed);
}
#[test]
fn ruff_formats_both_dialects_and_refuses_unattributed_summary() {
    let plan = fixture(ProviderKind::RuffFormat);
    for text in [
        "Would reformat: main.py\n1 file would be reformatted\n",
        "unformatted: File would be reformatted\n  --> main.py:2:3\n   |\n 1 - old\n 2 + new\n1 file would be reformatted\n",
    ] {
        let observation = observe(&plan, &outcome(&plan, text, "", 1));
        assert_eq!(observation.state, TaskState::Succeeded, "{observation:?}");
        assert_eq!(observation.findings.len(), 1);
    }
    assert_eq!(
        observe(
            &plan,
            &outcome(&plan, "1 file would be reformatted\n", "", 1)
        )
        .state,
        TaskState::Failed
    );
    assert_eq!(
        observe(&plan, &outcome(&plan, "1 file already formatted\n", "", 0)).state,
        TaskState::Succeeded
    );
}
#[test]
fn ty_sarif_preserves_native_severity_and_rejects_unmapped_uris() {
    let plan = fixture(ProviderKind::Ty);
    let mut item = json!({"ruleId":"invalid-assignment", "level":"error", "message":{"text":"not assignable"}, "locations":[{"physicalLocation":{"artifactLocation":{"uri":"file:///workspace/pkg/%CE%BB.py"},"region":{"startLine":2,"startColumn":3,"endColumn":8}}}]});
    let document = |item: Value| {
        json!({"version":"2.1.0", "runs":[{"tool":{"driver":{"name":"ty"}},"results":[item]}]})
            .to_string()
    };
    let observation = observe(&plan, &outcome(&plan, &document(item.clone()), "", 1));
    assert_eq!(observation.state, TaskState::Succeeded);
    assert_eq!(observation.findings[0].primary_location.path, "pkg/λ.py");
    assert!(
        observation.findings[0]
            .tags
            .contains(&"native-severity:error".into())
    );
    assert_eq!(observation.findings[0].evidence, EvidenceLevel::Measured);
    item["locations"][0]["physicalLocation"]["artifactLocation"]["uriBaseId"] = json!("UNKNOWN");
    assert_eq!(
        observe(&plan, &outcome(&plan, &document(item), "", 1)).state,
        TaskState::Failed
    );
}
#[test]
fn mypy_notes_are_preserved_and_unknown_context_is_failed() {
    let plan = fixture(ProviderKind::Mypy);
    let observation = observe(
        &plan,
        &outcome(
            &plan,
            "main.py:1:4: error: bad type [assignment]\nmain.py:2: note: expected int\nFound 1 error in 1 file (checked 1 source file)\n",
            "",
            1,
        ),
    );
    assert_eq!(observation.state, TaskState::Succeeded, "{observation:?}");
    assert_eq!(observation.findings.len(), 2);
    assert_eq!(observation.findings[0].native_rule_id, "assignment");
    assert_eq!(observation.findings[1].severity, "low");
    assert_eq!(
        observe(&plan, &outcome(&plan, "unexpected plugin output", "", 0)).state,
        TaskState::Failed
    );
}
#[test]
fn eslint_enumerates_clean_files_and_native_ignore_is_not_an_inspection_pass() {
    let plan = fixture(ProviderKind::Eslint);
    let raw = json!([{"filePath":"/workspace/main.py", "messages":[]}, {"filePath":"/workspace/pkg/λ.py","messages":[{"severity":1,"ruleId":null,"message":"File ignored because of a matching ignore pattern. Use --no-ignore to override."}]}]).to_string();
    let result = observe_full(&plan, &outcome(&plan, &raw, "", 0));
    assert_eq!(result.observation.state, TaskState::Succeeded);
    assert_eq!(
        result
            .targets
            .iter()
            .find(|item| item.file_path == "main.py")
            .unwrap()
            .status,
        "PASS"
    );
    assert_eq!(
        result
            .targets
            .iter()
            .find(|item| item.file_path == "pkg/λ.py")
            .unwrap()
            .status,
        "SKIP"
    );
    let raw = json!([{"filePath":"/workspace/main.py","messages":[{"severity":1,"message":"File ignored because no matching configuration was supplied."}]}]).to_string();
    assert_eq!(
        observe(&plan, &outcome(&plan, &raw, "", 0)).state,
        TaskState::Failed
    );
}
#[test]
fn cargo_requires_terminal_record_and_retains_related_spans() {
    let plan = fixture(ProviderKind::Clippy);
    let record = json!({"reason":"compiler-message", "message":{"level":"warning","message":"unused variable", "code":{"code":"unused_variables"},"spans":[{"is_primary":true,"file_name":"main.py","line_start":1,"column_start":4},{"is_primary":false,"file_name":"pkg/λ.py","line_start":2,"column_start":1,"label":"related source"}]}}).to_string();
    let raw = format!("{record}\n{{\"reason\":\"build-finished\",\"success\":true}}\n");
    let observation = observe(&plan, &outcome(&plan, &raw, "", 0));
    assert_eq!(observation.state, TaskState::Succeeded);
    assert_eq!(observation.findings[0].related_locations.len(), 1);
    assert_eq!(
        observe(&plan, &outcome(&plan, &record, "", 0)).state,
        TaskState::Failed
    );
}
#[test]
fn typescript_global_errors_and_external_diagnostics_are_not_dropped_into_success() {
    let plan = fixture(ProviderKind::Tsc);
    let observation = observe(
        &plan,
        &outcome(
            &plan,
            "main.py(1,7): error TS2322: Type 'string' is not assignable to type 'number'.\n",
            "",
            2,
        ),
    );
    assert_eq!(observation.state, TaskState::Succeeded);
    assert_eq!(observation.findings[0].native_rule_id, "2322");
    assert_eq!(
        observe(
            &plan,
            &outcome(&plan, "error TS5058: config missing\n", "", 1)
        )
        .state,
        TaskState::Failed
    );
    assert_eq!(
        observe(
            &plan,
            &outcome(&plan, "../outside.py(1,1): error TS2322: bad\n", "", 1)
        )
        .state,
        TaskState::Failed
    );
}
#[test]
fn compiler_and_tidy_keep_source_notes_and_obey_exit_contracts() {
    let raw = "main.py: In function 'int answer()':\nmain.py:1:12: warning: unused variable [-Wunused-variable]\n 1 | int unused;\n   |     ^~~~~~\npkg/λ.py:2:3: note: previous declaration\n";
    for kind in [
        ProviderKind::Compiler,
        ProviderKind::ClangTidy,
        ProviderKind::Clazy,
    ] {
        let plan = fixture(kind);
        let observation = observe(&plan, &outcome(&plan, "", raw, 0));
        assert_eq!(observation.state, TaskState::Succeeded, "{observation:?}");
        assert_eq!(observation.findings[0].related_locations.len(), 1);
        if kind != ProviderKind::Compiler {
            assert_eq!(
                observe(&plan, &outcome(&plan, "", raw, 1)).state,
                TaskState::Failed
            );
        }
    }
}

#[test]
fn utf16_surrogates_and_missing_snapshot_columns_are_not_guessed() {
    let mut plan = fixture(ProviderKind::Eslint);
    let raw = |column, end| {
        serde_json::json!([{"filePath":"/workspace/main.py","messages":[{"ruleId":"no-undef","severity":2,"message":"undefined","line":1,"column":column,"endLine":1,"endColumn":end}]}]).to_string()
    };
    let unknown = observe(&plan, &outcome(&plan, &raw(17, 24), "", 1));
    assert_eq!(unknown.findings[0].primary_location.start_column, None);
    assert!(!unknown.findings[0].limitations.is_empty());
    let inputs = [ici_domain::SourceInput {
        path: "main.py".into(),
        bytes: "const s = '😀'; missing();\n".as_bytes().to_vec(),
    }];
    ici_providers::attach_sources(&mut plan, ici_providers::source_coordinates(&inputs));
    let mapped = observe(&plan, &outcome(&plan, &raw(17, 24), "", 1));
    assert_eq!(mapped.findings[0].primary_location.start_column, Some(16));
    assert_eq!(mapped.findings[0].primary_location.end_column, Some(22));
    let split_surrogate = observe(&plan, &outcome(&plan, &raw(13, 14), "", 1));
    assert_eq!(split_surrogate.state, TaskState::Failed);
    let document = serde_json::to_string(&plan).unwrap();
    assert!(!document.contains("const s"));
}
