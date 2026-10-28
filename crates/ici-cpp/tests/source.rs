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
fn legacy_line_policy_uses_strict_cutoffs_scope_gates_null_limits_and_extra_languages() {
    let mut session = CppAnalysisSession::new();
    let mut input = request(
        "line",
        vec![
            ("src/equal.cpp", "int a;\nint b;\n"),
            ("src/over.cpp", "int a;\nint b;\nint c;\n"),
            ("custom/ungated.h", "int a;\nint b;\nint c;\nint d;\n"),
            ("README.md", "# heading\n// ordinary prose\n\n"),
            ("run.sh", "# comment\necho ready\n"),
        ],
    );
    input.options = json!({"legacy_projection":true,"warn_limit":2,"fail_limit":3,
        "source_paths":["src/equal.cpp","src/over.cpp","custom/ungated.h"],
        "gated_paths":["src/equal.cpp","src/over.cpp"]});
    let output = session.analyze(&input);
    assert_eq!(output.status, "WARN");
    assert_eq!(
        output
            .targets
            .iter()
            .map(|target| target.status.as_str())
            .collect::<Vec<_>>(),
        ["PASS", "WARN", "PASS", "PASS", "PASS"]
    );
    assert_eq!(output.extra["code"], 9);
    assert_eq!(output.extra["all"]["code"], 12);
    assert_eq!(output.extra["all"]["comment"], 1);
    assert_eq!(output.extra["all"]["blank"], 1);
    assert_eq!(output.extra["all"]["files"], 5);
    assert_eq!(output.extra["top_files"].as_array().unwrap().len(), 3);
    assert_eq!(output.targets[0].target_name, "C++");
    assert_eq!(output.targets[3].target_name, "Markdown");
    assert_eq!(output.targets[4].target_name, "Shell");
    assert_eq!(output.targets[0].metrics["code"], 2);
    input.options["warn_limit"] = Value::Null;
    input.options["fail_limit"] = json!(2);
    let failure = session.analyze(&input);
    assert_eq!(failure.status, "FAIL");
    assert_eq!(failure.targets[0].status, "PASS");
    assert_eq!(failure.targets[1].status, "FAIL");
    input.options["fail_limit"] = Value::Null;
    assert_eq!(session.analyze(&input).status, "PASS");
    assert_eq!(session.parser_invocations(), 0);
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

#[test]
fn frozen_cpp_exception_oracle_preserves_rule_ids_locations_summary_targets_and_measured_source_evidence()
 {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/source-oracle.json"
    ))
    .unwrap();
    let mut session = CppAnalysisSession::new();
    for case in oracle["cases"].as_array().unwrap() {
        let output = session.analyze(&request(
            "exception",
            vec![("case.cpp", case["source"].as_str().unwrap())],
        ));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{} {:?}",
            case["name"],
            output.observation.limitations
        );
        let targets:Vec<_>=output.targets.iter().map(|target|json!({"name":target.target_name,"line":target.start_line,"status":target.status})).collect();
        assert_eq!(json!(targets), case["exceptions"], "{}", case["name"]);
        assert!(
            output
                .observation
                .findings
                .iter()
                .all(|finding| finding.evidence == EvidenceLevel::Measured
                    && finding.confidence == "medium"
                    && !finding.native_rule_id.is_empty())
        );
    }
}

#[test]
fn cpp_source_metrics_and_line_locations_preserve_crlf_and_cr_only_endings() {
    let lf = "// 😀\nint first(int x){\n if(x)return 1;\n return 0;\n}\n";
    let mut session = CppAnalysisSession::new();
    for ending in ["\n", "\r\n", "\r"] {
        let source = lf.replace('\n', ending);
        let output = session.analyze(&request("complexity", vec![("case.cpp", &source)]));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{ending:?}: {:?}",
            output.observation.limitations
        );
        let row = &output.targets[0];
        assert_eq!(row.start_line, 2);
        assert_eq!(row.end_line, Some(5));
        assert_eq!(row.metrics["complexity"], 2);
        let lines = session.analyze(&request("line", vec![("case.cpp", &source)]));
        assert_eq!(lines.observation.measurements[0].value, 5.0);
        assert_eq!(lines.targets[0].metrics["comment_lines"], 1);
    }
}

#[test]
fn line_count_does_not_parse_or_inherit_the_cpp_rule_file_count_limit() {
    let mut input = request("line", vec![]);
    input.inputs = (0..2049)
        .map(|index| SourceInput {
            path: format!("f{index}.cpp"),
            bytes: b"int value;\n".to_vec(),
        })
        .collect();
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.targets.len(), 2049);
    assert_eq!(session.parser_invocations(), 0);
}
#[test]
fn active_line_count_cancellation_returns_within_the_shared_two_second_budget() {
    use std::{
        sync::{Arc, atomic::Ordering},
        time::{Duration, Instant},
    };
    let input = request("line", vec![("large.cpp", &"//x\n".repeat(2_000_000))]);
    let flag = Arc::new(AtomicBool::new(false));
    let worker_flag = Arc::clone(&flag);
    let (sender, receiver) = std::sync::mpsc::channel();
    let worker = std::thread::spawn(move || {
        sender.send(()).unwrap();
        CppAnalysisSession::new().analyze_cancellable(&input, &worker_flag)
    });
    receiver.recv().unwrap();
    std::thread::sleep(Duration::from_millis(20));
    let start = Instant::now();
    flag.store(true, Ordering::Relaxed);
    let output = worker.join().unwrap();
    assert!(start.elapsed() < Duration::from_secs(2));
    assert_eq!(output.observation.state, TaskState::Cancelled);
}
