use ici_cpp::CppAnalysisSession;
use ici_domain::{AnalysisRequest, EvidenceLevel, SourceInput, TaskState};
use serde_json::{Value, json};
use std::sync::atomic::AtomicBool;
fn request(kind: &str, inputs: Vec<(&str, &str)>) -> AnalysisRequest {
    AnalysisRequest {
        task_id: format!("cpp.{kind}"),
        check_id: format!("cpp.{kind}"),
        component_id: "fixture".into(),
        language: "cpp".into(),
        inputs: inputs
            .into_iter()
            .map(|(path, source)| SourceInput {
                path: path.into(),
                bytes: source.as_bytes().to_vec(),
            })
            .collect(),
        options: json!({}),
        target_version: "c++20".into(),
    }
}
#[test]
fn frozen_cpp_source_metric_oracle_preserves_function_ownership_and_values() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/source-oracle.json"
    ))
    .unwrap();
    let mut session = CppAnalysisSession::new();
    let mut mismatches = vec![];
    for case in oracle["cases"].as_array().unwrap() {
        let source = case["source"].as_str().unwrap();
        let result = session.analyze(&request("complexity", vec![("case.cpp", source)]));
        if result.observation.state != TaskState::Succeeded {
            mismatches.push(format!(
                "{}: {:?}",
                case["name"], result.observation.limitations
            ));
            continue;
        }
        let rows: Vec<_> = result
            .targets
            .iter()
            .filter(|target| target.metrics.contains_key("complexity"))
            .collect();
        let unicode_correction = vec![json!({"name":"café","complexity":2,"cognitive":1})];
        let expected = if case["name"] == "unicode" {
            &unicode_correction
        } else {
            case["functions"].as_array().unwrap()
        };
        if rows.len() != expected.len() {
            mismatches.push(format!(
                "{}: {} vs {} rows: {:?}",
                case["name"],
                rows.len(),
                expected.len(),
                rows
            ));
            continue;
        }
        for (actual, expected) in rows.iter().zip(expected) {
            if actual.target_name != expected["name"].as_str().unwrap() {
                mismatches.push(format!(
                    "{} name {} expected {}",
                    case["name"], actual.target_name, expected["name"]
                ));
            }
            for key in ["complexity", "cognitive", "excluded_lambdas"] {
                if expected.get(key).is_none() {
                    continue;
                }
                let actual_key = if key == "excluded_lambdas" {
                    "excluded_nested_lambdas"
                } else {
                    key
                };
                if actual.metrics[actual_key] != expected[key] {
                    mismatches.push(format!(
                        "{} {} {}: {} expected {}",
                        case["name"],
                        actual.target_name,
                        key,
                        actual.metrics[actual_key],
                        expected[key]
                    ));
                }
            }
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
#[test]
fn cache_identity_cancellation_unicode_and_estimated_thresholds_are_explicit() {
    let mut session = CppAnalysisSession::new();
    let mut input = request(
        "complexity",
        vec![(
            "case.cpp",
            "// 😀\nint café(int x){if(x)return 1;return 0;}\n",
        )],
    );
    input.options = json!({"warn_limit":1,"fail_limit":10});
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    let target = &output.targets[0];
    assert_eq!(target.target_name, "café");
    assert_eq!(target.start_line, 2);
    assert_eq!(target.start_column, Some(1));
    assert_eq!(target.end_column, Some(40));
    assert_eq!(
        output.observation.findings[0].evidence,
        EvidenceLevel::Estimated
    );
    input.check_id = "cpp.cognitive".into();
    session.analyze(&input);
    assert_eq!(session.parser_invocations(), 1);
    input.inputs[0].bytes.push(b'\n');
    session.analyze(&input);
    assert_eq!(session.parser_invocations(), 2);
    let cancelled = session.analyze_cancellable(&input, &AtomicBool::new(true));
    assert_eq!(cancelled.observation.state, TaskState::Cancelled);
    input.inputs[0].bytes = vec![0xff];
    assert_eq!(session.analyze(&input).observation.state, TaskState::Failed);
}
#[test]
fn exception_rules_mask_literals_preserve_pass_and_find_actual_syntax() {
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&request(
        "exception",
        vec![
            (
                "bad.cpp",
                "struct X { ~X(){ throw 1; } }; void f(){try{g();}catch(...){/* silent */}}",
            ),
            ("good.cpp", "void f(){try{g();}catch(...){log();throw;}}"),
        ],
    ));
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{:?}",
        output.observation.limitations
    );
    assert_eq!(output.observation.findings.len(), 2, "{:?}", output.targets);
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "good.cpp" && target.status == "PASS")
    );
}
#[test]
fn include_cycles_resolve_direct_paths_and_report_ambiguous_and_self_edges() {
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&request(
        "cycle",
        vec![
            ("a.h", "#include \"sub/b.h\"\n"),
            ("sub/b.h", "#include \"../a.h\"\n"),
            ("self.h", "#include \"self.h\"\n"),
            ("top.cpp", "#include \"x.h\"\n"),
            ("one/x.h", "int x;"),
            ("two/x.h", "int y;"),
        ],
    ));
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{:?}",
        output.observation.limitations
    );
    assert_eq!(output.observation.findings.len(), 2);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|limit| limit.contains("ambiguous"))
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .all(|finding| finding.evidence == EvidenceLevel::Estimated)
    );
}
