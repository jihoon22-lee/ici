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

fn cpp_snapshot(plan: &mut ProviderPlan, text: &str) {
    ici_providers::attach_sources(
        plan,
        ici_providers::source_coordinates(&[ici_domain::SourceInput {
            path: "main.py".into(),
            bytes: text.as_bytes().to_vec(),
        }]),
    );
}
#[test]
fn cpp_text_fixits_preserve_grouped_exclusive_unicode_edits_and_original_metadata() {
    let mut plan = fixture(ProviderKind::Compiler);
    plan.context.compiler_columns = Some("gnu-byte".into());
    cpp_snapshot(&mut plan, "λ😀abc\n");
    let raw = "main.py:1:7: warning: replace token [-Wexample]\nfix-it:\"main.py\":{1:7-1:10}:\"xyz\"\nmain.py:1:8: note: companion\nfix-it:\"main.py\":{1:8-1:8}:\";\"\n";
    let output = observe_full(&plan, &outcome(&plan, "", raw, 0));
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let finding = &output.observation.findings[0];
    assert_eq!(finding.primary_location.start_column, Some(3));
    assert_eq!(finding.related_locations[0].start_column, Some(4));
    let detail = &output.extra["finding_details"][&finding.fingerprint];
    assert_eq!(detail["legacy_rule_id"], "ici.legacy.lint.target");
    assert_eq!(detail["legacy_category"], "correctness");
    assert_eq!(detail["legacy_label"], "Compiler:-Wexample");
    assert_eq!(detail["legacy_message"], "warning: replace token");
    assert_eq!(detail["related_locations"][0]["label"], "note: companion");
    let fixes = detail["fixes"].as_array().unwrap();
    assert_eq!(fixes.len(), 1);
    let edits = fixes[0]["replacements"].as_array().unwrap();
    assert_eq!(edits.len(), 2);
    assert_eq!(
        edits[0],
        json!({"path":"main.py","start_line":1,"start_column":3,"end_line":1,"end_column":6,"replacement":"xyz","column_kind":"unicodeCodePoints","end_exclusive":true})
    );
    assert_eq!(edits[1]["start_column"], 4);
    assert_eq!(edits[1]["end_column"], 4);
    assert_eq!(finding.rule_id, "compiler.-Wexample");
}
#[test]
fn cpp_unknown_columns_and_json_unknown_end_remain_explicitly_unconfirmed() {
    let mut plan = fixture(ProviderKind::Compiler);
    plan.context.compiler_columns = None;
    let output = observe_full(
        &plan,
        &outcome(
            &plan,
            "",
            "main.py:1:7: warning: edit [-Wexample]\nfix-it:\"main.py\":{1:7-1:10}:\"x\"",
            0,
        ),
    );
    let finding = &output.observation.findings[0];
    let edit =
        &output.extra["finding_details"][&finding.fingerprint]["fixes"][0]["replacements"][0];
    assert_eq!(edit["column_kind"], "unknown");
    assert_eq!(edit["start_column"], 7);
    assert_eq!(finding.primary_location.start_column, None);
    plan.context.compiler_columns = Some("gnu-byte".into());
    cpp_snapshot(&mut plan, "λ😀abc\n");
    let position = |column| json!({"file":"main.py","line":1,"column":column});
    for (end_key, expected) in [("next", json!(true)), ("end", Value::Null)] {
        let mut fix = json!({"start":position(7),"string":"x"});
        fix[end_key] = position(10);
        let raw=json!([{"kind":"warning","message":"edit","option":"-Wexample","locations":[{"caret":position(7)}],"fixits":[fix],"children":[]}]).to_string();
        let output = observe_full(&plan, &outcome(&plan, &raw, "", 0));
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        let finding = &output.observation.findings[0];
        let edit =
            &output.extra["finding_details"][&finding.fingerprint]["fixes"][0]["replacements"][0];
        assert_eq!(edit["end_exclusive"], expected);
        assert_eq!(edit["column_kind"], "unicodeCodePoints");
        assert_eq!(edit["start_column"], 3);
        assert_eq!(edit["end_column"], 6);
    }
}
#[test]
fn cpp_invalid_fixits_fail_atomically_and_external_fixits_cannot_bind_to_previous_issue() {
    let mut plan = fixture(ProviderKind::Compiler);
    plan.context.compiler_columns = Some("gnu-byte".into());
    cpp_snapshot(&mut plan, "λ😀abc\n");
    for fix in [
        "fix-it:\"main.py\":{1:8-1:7}:\"x\"",
        "fix-it:\"main.py\":{1:4-1:7}:\"x\"",
        "fix-it:\"main.py\":{1:7-1:99}:\"x\"",
        "fix-it:\"main.py\":{0:7-1:8}:\"x\"",
        "fix-it:\"main.py\":{1:7-1:8}:\"\\q\"",
    ] {
        let raw = format!("main.py:1:7: warning: edit [-Wexample]\n{fix}\n");
        let output = observe_full(&plan, &outcome(&plan, "", &raw, 0));
        assert_eq!(
            output.observation.state,
            TaskState::Failed,
            "{fix}: {output:?}"
        );
        assert!(output.observation.findings.is_empty());
        assert!(output.extra.get("finding_details").is_none());
    }
    let raw = "main.py:1:7: warning: primary [-Wexample]\n/private/source.cpp:1:2: warning: external [-Wother]\nfix-it:\"/private/source.cpp\":{1:2-1:3}:\"x\"\n";
    let output = observe_full(&plan, &outcome(&plan, "", raw, 0));
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let finding = &output.observation.findings[0];
    assert_eq!(
        output.extra["finding_details"][&finding.fingerprint]["fixes"],
        json!([])
    );
    assert!(
        !serde_json::to_string(&output)
            .unwrap()
            .contains("/private/")
    );
}
#[test]
fn cpp_rich_details_obey_existing_report_redaction_boundary() {
    let mut plan = fixture(ProviderKind::Compiler);
    plan.context.compiler_columns = Some("gnu-byte".into());
    cpp_snapshot(&mut plan, "abcdef\n");
    let raw = "main.py:1:1: warning: edit [-Wexample]\nfix-it:\"main.py\":{1:1-1:2}:\"token=example-secret\"\n";
    let output = observe_full(&plan, &outcome(&plan, "", raw, 0));
    let safe = ici_execution::redaction::redact_value(&serde_json::to_value(output).unwrap());
    let safe = serde_json::to_string(&safe).unwrap();
    assert!(!safe.contains("example-secret"));
    assert!(safe.contains("REDACTED"));
}
#[test]
fn cpp_original_category_policy_retains_security_resource_and_compatibility() {
    for (kind, rule, category) in [
        (
            ProviderKind::ClangTidy,
            "clang-analyzer-security.insecureAPI.strcpy",
            "security",
        ),
        (
            ProviderKind::ClangTidy,
            "clang-analyzer-cplusplus.NewDelete",
            "resource",
        ),
        (
            ProviderKind::ClangTidy,
            "bugprone-use-after-move",
            "resource",
        ),
        (
            ProviderKind::ClangTidy,
            "modernize-deprecated-headers",
            "compatibility",
        ),
        (ProviderKind::Clazy, "clazy-qt6-foo", "compatibility"),
        (ProviderKind::Clazy, "clazy-qdeleteall", "resource"),
    ] {
        let plan = fixture(kind);
        let raw = format!("main.py:1:1: warning: sample [{rule}]\n");
        let output = observe_full(&plan, &outcome(&plan, "", &raw, 0));
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        let finding = &output.observation.findings[0];
        assert_eq!(
            output.extra["finding_details"][&finding.fingerprint]["legacy_category"],
            category
        );
    }
}

#[test]
fn rich_cpp_metadata_matches_the_original_python_producer_oracle() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/providers/cpp-rich-oracle.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let kind = match case["kind"].as_str().unwrap() {
            "compiler" => ProviderKind::Compiler,
            "clang-tidy" => ProviderKind::ClangTidy,
            "clazy" => ProviderKind::Clazy,
            _ => unreachable!(),
        };
        let mut plan = crate::fixture(kind);
        plan.context.compiler_columns = Some(
            if kind == ProviderKind::Compiler {
                "gnu-byte"
            } else {
                "clang-byte"
            }
            .into(),
        );
        plan.context.tool_name = case["tool_name"].as_str().unwrap().into();
        plan.context.tool_version = case["tool_version"].as_str().unwrap().into();
        cpp_snapshot(&mut plan, case["source"].as_str().unwrap());
        let output = observe_full(
            &plan,
            &outcome(&plan, "", case["stderr"].as_str().unwrap(), 0),
        );
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {output:?}",
            case["id"]
        );
        let expected = case["findings"].as_array().unwrap();
        assert_eq!(
            output.observation.findings.len(),
            expected.len(),
            "{}",
            case["id"]
        );
        for (finding, expected) in output.observation.findings.iter().zip(expected) {
            let detail = &output.extra["finding_details"][&finding.fingerprint];
            for (key, native_key) in [
                ("rule_id", "legacy_rule_id"),
                ("category", "legacy_category"),
                ("severity", "legacy_severity"),
                ("confidence", "legacy_confidence"),
                ("message", "legacy_message"),
                ("explanation", "explanation"),
                ("remediation", "remediation"),
                ("tool_name", "tool_name"),
                ("tool_version", "tool_version"),
                ("tool_rule_id", "tool_rule_id"),
                ("snippet", "snippet"),
                ("metrics", "metrics"),
                ("related_locations", "related_locations"),
            ] {
                assert_eq!(
                    detail[native_key], expected[key],
                    "{} field {key}",
                    case["id"]
                );
            }
            assert_eq!(
                detail["legacy_label"], expected["primary_location"]["label"],
                "{} label",
                case["id"]
            );
            let mut fixes = detail["fixes"].clone();
            for fix in fixes.as_array_mut().unwrap() {
                for edit in fix["replacements"].as_array_mut().unwrap() {
                    let object = edit.as_object_mut().unwrap();
                    object.remove("column_kind");
                    object.remove("end_exclusive");
                }
            }
            assert_eq!(fixes, expected["fixes"], "{} fixes", case["id"]);
        }
    }
}

#[test]
fn cpp_multifile_fixits_require_each_source_boundary_and_external_json_errors_remain_errors() {
    let mut plan = fixture(ProviderKind::Compiler);
    plan.context.compiler_columns = Some("gnu-byte".into());
    let raw = "main.py:1:7: warning: edit [-Wexample]\nfix-it:\"main.py\":{1:7-1:7}:\"x\"\npkg/λ.py:1:3: note: declaration\nfix-it:\"pkg/λ.py\":{1:3-1:3}:\"y\"\n";
    cpp_snapshot(&mut plan, "λ😀abc\n");
    let partial = observe_full(&plan, &outcome(&plan, "", raw, 0));
    let finding = &partial.observation.findings[0];
    let edits = &partial.extra["finding_details"][&finding.fingerprint]["fixes"][0]["replacements"];
    assert_eq!(edits[0]["column_kind"], "unicodeCodePoints");
    assert_eq!(edits[1]["column_kind"], "unknown");
    ici_providers::attach_sources(
        &mut plan,
        ici_providers::source_coordinates(&[
            ici_domain::SourceInput {
                path: "main.py".into(),
                bytes: "λ😀abc\n".as_bytes().to_vec(),
            },
            ici_domain::SourceInput {
                path: "pkg/λ.py".into(),
                bytes: "λabc\n".as_bytes().to_vec(),
            },
        ]),
    );
    let complete = observe_full(&plan, &outcome(&plan, "", raw, 0));
    let finding = &complete.observation.findings[0];
    let edits =
        &complete.extra["finding_details"][&finding.fingerprint]["fixes"][0]["replacements"];
    assert_eq!(edits[0]["column_kind"], "unicodeCodePoints");
    assert_eq!(edits[1]["column_kind"], "unicodeCodePoints");
    assert_eq!(edits[1]["start_column"], 2);
    assert_eq!(finding.related_locations[0].path, "pkg/λ.py");
    let position = |path| json!({"file":path,"line":1,"column":1});
    let child = json!({"kind":"error","message":"bad system context","locations":[{"caret":position("/outside/system.hpp")}]});
    let raw=json!([{"kind":"warning","message":"local","locations":[{"caret":position("main.py")}]},{"kind":"warning","message":"external","locations":[{"caret":position("/outside/system.hpp")}],"children":[child]}]).to_string();
    let failed = observe_full(&plan, &outcome(&plan, &raw, "", 1));
    assert_eq!(failed.observation.state, TaskState::Failed);
    assert!(failed.observation.findings.is_empty());
    assert!(failed.extra.get("finding_details").is_none());
}

#[test]
fn cpp_related_note_count_obeys_the_diagnostic_bound() {
    let plan = fixture(ProviderKind::Compiler);
    let raw = format!(
        "main.py:1:1: warning: primary [-Wexample]\n{}",
        "main.py:1:1: note: context\n".repeat(10_000)
    );
    let output = observe_full(&plan, &outcome(&plan, "", &raw, 0));
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(output.observation.findings.is_empty());
    assert!(output.extra.get("finding_details").is_none());
}

#[test]
fn gcc_context_headers_required_from_and_columnless_diagnostics_follow_frozen_text_grammar() {
    // Frozen _TEXT_CONTEXT_HEADER_RE / _TEXT_REQUIRED_FROM_RE accept these ancillary lines;
    // a column-less diagnostic keeps its line with an unknown column.
    let raw = "main.py: In destructor 'Guard::~Guard()':\nmain.py:3:20: warning: 'throw' will always call 'terminate' [-Wterminate]\nmain.py:3:20: note: in C++11 destructors default to 'noexcept'\nmain.py: At global scope:\nmain.py: In constructor 'A::A()':\nmain.py: In lambda function:\nmain.py: In instantiation of 'void f() [with T = int]':\nmain.py:9:5:   required from here\nmain.py:7: error: column-less diagnostic\n";
    let plan = fixture(ProviderKind::Compiler);
    let observation = observe(&plan, &outcome(&plan, "", raw, 1));
    assert_eq!(observation.state, TaskState::Succeeded, "{observation:?}");
    assert_eq!(observation.findings.len(), 2, "{:?}", observation.findings);
    let columnless = observation
        .findings
        .iter()
        .find(|finding| finding.message.contains("column-less"))
        .unwrap();
    assert_eq!(columnless.primary_location.start_line, 7);
    assert_eq!(columnless.primary_location.start_column, None);
    // Lines outside the frozen grammar still fail closed.
    for unknown in [
        "main.py: In static member function 'int f()':\n",
        "cc1plus: some warnings being treated as errors\n",
        "main.py:3:20: frobnicated: odd\n",
    ] {
        assert_eq!(
            observe(&plan, &outcome(&plan, "", unknown, 1)).state,
            TaskState::Failed,
            "{unknown}"
        );
    }
}
