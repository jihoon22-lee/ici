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
        // Next metrics scan with the frozen brace heuristic itself: every case, including the
        // non-ASCII `café` that frozen's ASCII name pattern does not list, is frozen's.
        let expected = case["functions"].as_array().unwrap();
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
/// Next metrics are frozen `_scan_cpp`: code-point columns after a non-BMP comment, explicit
/// thresholds as ESTIMATED findings, no syntax-tree parse (metric checks never warm the parser
/// cache), cancellation, and a non-UTF-8 source read with replacement characters like frozen's
/// `errors="replace"` (it still completes).
#[test]
fn cache_identity_cancellation_unicode_and_estimated_thresholds_are_explicit() {
    let mut session = CppAnalysisSession::new();
    let mut input = request(
        "complexity",
        vec![(
            "case.cpp",
            "// 😀\nint cafe(int x){if(x)return 1;return 0;}\n",
        )],
    );
    input.options = json!({"warn_limit":1,"fail_limit":10});
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    let target = &output.targets[0];
    assert_eq!(target.target_name, "cafe");
    assert_eq!(target.start_line, 2);
    assert_eq!(target.start_column, Some(1));
    assert_eq!(target.end_column, Some(40));
    assert_eq!(
        output.observation.findings[0].evidence,
        EvidenceLevel::Estimated
    );
    assert_eq!(output.observation.findings[0].confidence, "low");
    input.check_id = "cpp.cognitive".into();
    session.analyze(&input);
    assert_eq!(session.parser_invocations(), 0);
    let cancelled = session.analyze_cancellable(&input, &AtomicBool::new(true));
    assert_eq!(cancelled.observation.state, TaskState::Cancelled);
    input.inputs[0].bytes = vec![0xff];
    assert_eq!(
        session.analyze(&input).observation.state,
        TaskState::Succeeded
    );
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
fn next_exception_rules_use_the_frozen_line_heuristic_like_the_legacy_engine() {
    // The same corpus as the frozen legacy oracle (exception-oracle.json): frozen next
    // `measure_hygiene` calls the same `analyze_cpp_exceptions`.
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&request(
        "exception",
        vec![(
            "dtor.cpp",
            include_str!("../../../parity-fixtures/cpp/exception-fixture/src/dtor.cpp"),
        )],
    ));
    assert_eq!(output.observation.state, TaskState::Succeeded);
    let lines = output
        .targets
        .iter()
        .filter(|target| target.target_name == "DestructorThrow")
        .map(|target| target.start_line)
        .collect::<Vec<_>>();
    assert_eq!(lines, [3, 4, 10, 17, 22, 25, 27, 32, 37]);
    for finding in &output.observation.findings {
        assert!(finding.fingerprint.starts_with("hygiene-"));
        assert_eq!(finding.primary_location.start_column, None);
        assert_eq!(finding.primary_location.label, "");
    }
    let catch_all = output
        .observation
        .findings
        .iter()
        .position(|finding| finding.rule_id == "exception.catchallswallowed");
    // Every destructor throw precedes every catch-all, as frozen orders its line rules.
    assert!(catch_all.is_none_or(|index| {
        output.observation.findings[index..]
            .iter()
            .all(|finding| finding.rule_id == "exception.catchallswallowed")
    }));
}
#[test]
fn include_cycles_use_the_frozen_suffix_graph_without_self_edges_or_relative_resolution() {
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&request(
        "cycle",
        vec![
            ("a.h", "#include \"sub/b.h\"\n"),
            ("sub/b.h", "#include \"a.h\"\n"),
            ("rel/c.h", "#include \"../rel/d.h\"\n"),
            ("rel/d.h", "#include \"rel/c.h\"\n"),
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
    // Frozen `_measure_cpp`: only the a.h/sub/b.h pair closes; a dotted include is never
    // normalized and a file including itself has no edge. Members are in stack-pop order.
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].message,
        "include cycle: sub/b.h -> a.h -> sub/b.h"
    );
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|limit| limit == "ambiguous include 'x.h' at top.cpp:1")
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
fn cycle_report_limit_caps_groups_and_diagnostics_without_changing_graph_counts() {
    let mut input = request(
        "cycle",
        vec![
            ("a.h", "#include \"b.h\"\n"),
            ("b.h", "#include \"a.h\"\n"),
            ("c.h", "#include \"d.h\"\n"),
            ("d.h", "#include \"c.h\"\n"),
            ("e.h", "#include \"f.h\"\n"),
            ("f.h", "#include \"e.h\"\n"),
            (
                "missing.cpp",
                "#include \"missing.h\"\n#include \"also-missing.h\"\n",
            ),
        ],
    );
    let mut session = CppAnalysisSession::new();
    input.options = json!({"max_reported":1, "legacy_projection":true});
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    let named = |name: &str| {
        output
            .targets
            .iter()
            .filter(|target| target.target_name.starts_with(name))
            .count()
    };
    assert_eq!(named("CppCycle:"), 1);
    assert_eq!(named("CppIncludeUnresolved"), 1);
    assert_eq!(
        output
            .observation
            .measurements
            .iter()
            .find(|metric| metric.name == "cycles_found")
            .unwrap()
            .value,
        3.0
    );
    // Frozen next `_measure_cpp` has no report limit; the key stays validated.
    input.options = json!({"max_reported":1});
    let output = session.analyze(&input);
    assert_eq!(output.observation.findings.len(), 3);
    assert_eq!(
        output
            .observation
            .limitations
            .iter()
            .filter(|limit| limit.starts_with("unresolved include "))
            .count(),
        2
    );
    for invalid in [json!(0), json!(-1), json!("1")] {
        input.options["max_reported"] = invalid;
        assert_eq!(session.analyze(&input).observation.state, TaskState::Failed);
    }
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
/// Qt access-specifier extensions and meta-object markers parse like their expansion: the
/// qmake QTest fixture and a Qt class using every form have no recovery region, so exception
/// completes; complexity and cognitive read frozen's brace heuristic, which lists only the
/// namespace-scope `use` (frozen `_scan_cpp` on the same two files: in-class definitions are
/// never rows, the QTest file has none).
#[test]
fn qt_access_specifier_extensions_and_markers_do_not_make_source_rules_error() {
    let qtest = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../parity-fixtures/cpp/test-qmake/tests/test_unit.cpp"
    ))
    .unwrap();
    let widget = "#include <QObject>\nclass W : public QObject {\n    Q_OBJECT\n    Q_PROPERTY(int value READ value NOTIFY changed)\npublic:\n    Q_INVOKABLE int value() const { return v ? 1 : 0; }\npublic slots:\n    void set(int x) { if (x) v = x; }\nprotected Q_SLOTS:\n    void reset() {}\nsignals:\n    void changed();\nQ_SIGNALS:\n    void other();\nprivate:\n    int v = 0;\n};\nQ_DECLARE_METATYPE(W*)\nint use(W& w) { emit w.changed(); return w.value(); }\n";
    let mut session = CppAnalysisSession::new();
    for kind in ["complexity", "cognitive", "exception"] {
        let output = session.analyze(&request(
            kind,
            vec![("tests/test_unit.cpp", &qtest), ("src/w.cpp", widget)],
        ));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{kind}: {:?} {:?}",
            output.observation.limitations,
            output.targets
        );
        assert_ne!(output.status, "ERROR", "{kind}: {:?}", output.targets);
        assert!(
            output
                .targets
                .iter()
                .all(|target| target.target_name != "source-syntax"),
            "{kind}: {:?}",
            output.targets
        );
        if kind == "complexity" {
            let names: Vec<_> = output
                .targets
                .iter()
                .map(|target| (target.target_name.as_str(), target.start_line))
                .collect();
            assert_eq!(
                names,
                [("NoFunctionDefinitions", 1), ("use", 19)],
                "{names:?}"
            );
        }
    }
}
/// A real syntax error is still a recovery region: the Qt normalization does not hide it.
#[test]
fn syntax_errors_do_not_refuse_the_exception_line_heuristic() {
    // Frozen `analyze_cpp_exceptions` reads masked lines in both paths; no syntax tree refuses.
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&request(
        "exception",
        vec![(
            "src/broken.cpp",
            "class W : public QObject {\n    Q_OBJECT\npublic slots:\n    void f( { }\n};\n",
        )],
    ));
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert!(
        output
            .targets
            .iter()
            .all(|target| target.target_name != "source-syntax")
    );
}
/// `cpp.exception` is a masked-text line rule in both paths: it never parses, so a text far past
/// what the syntax tree finishes in 5 seconds is still frozen's SUCCEEDED, and the rule's
/// verdicts, locations and refusals are those of the text.
#[test]
fn exception_rule_reads_text_without_a_parse_in_both_paths() {
    let large = "x ".repeat(500_001);
    let sources = vec![
        (
            "src/dtor.cpp",
            "struct D {\n  ~D() {\n    throw 1;\n  }\n};\n",
        ),
        (
            "src/catch.cpp",
            "void f() {\n  try { g(); } catch (...) { }\n}\n",
        ),
        (
            "src/clean.cpp",
            "struct C { ~C() {} };\nvoid f() { try { g(); } catch (...) { throw; } }\n",
        ),
        (
            "src/broken.cpp",
            "void f( {\n  try { g(); } catch (...) {}\n",
        ),
        ("src/large.cpp", large.as_str()),
    ];
    for legacy in [false, true] {
        let mut session = CppAnalysisSession::new();
        let mut input = request("exception", sources.clone());
        input.options = json!({"legacy_projection": legacy});
        let output = session.analyze(&input);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{legacy}: {:?}",
            output.targets
        );
        assert_eq!(session.parser_invocations(), 0, "{legacy}");
        assert_eq!(
            output.extra["parser_identity"],
            "ici-v0.13.0-cpp-exception-masked-text"
        );
        let targets: Vec<(&str, &str, u32, &str)> = output
            .targets
            .iter()
            .map(|target| {
                (
                    target.file_path.as_str(),
                    target.target_name.as_str(),
                    target.start_line,
                    target.status.as_str(),
                )
            })
            .collect();
        assert_eq!(
            targets,
            [
                ("src/dtor.cpp", "DestructorThrow", 3, "FAIL"),
                ("src/dtor.cpp", "CppExceptionSafety", 1, "PASS"),
                ("src/catch.cpp", "CatchAllSwallowed", 2, "FAIL"),
                ("src/catch.cpp", "CppExceptionSafety", 1, "PASS"),
                ("src/clean.cpp", "CppExceptionSafety", 1, "PASS"),
                ("src/broken.cpp", "CatchAllSwallowed", 2, "FAIL"),
                ("src/broken.cpp", "CppExceptionSafety", 1, "PASS"),
                ("src/large.cpp", "CppExceptionSafety", 1, "PASS"),
            ],
            "{legacy}"
        );
        assert_eq!(output.observation.findings.len(), 3, "{legacy}");
    }
}
/// The text path keeps the refusals of before, each before the inspected sources' rows: next
/// `measure_hygiene` stops at frozen's bounded intake, the legacy engine reports frozen's
/// `ReadError` with CPython's decode text and `source-null` per source, and nothing is parsed.
#[test]
fn exception_rule_keeps_the_utf8_and_null_refusals_without_a_parse() {
    let with = |extra: (&str, &[u8]), legacy: bool| {
        let mut input = request("exception", vec![("src/ok.cpp", "int a;\n")]);
        input.inputs.push(SourceInput {
            path: extra.0.into(),
            bytes: extra.1.to_vec(),
        });
        input.options = json!({"legacy_projection": legacy});
        let mut session = CppAnalysisSession::new();
        let output = session.analyze(&input);
        assert_eq!(output.observation.state, TaskState::Failed, "{legacy}");
        assert_eq!(session.parser_invocations(), 0, "{legacy}");
        output
            .targets
            .iter()
            .map(|target| {
                format!(
                    "{} {} {} {}",
                    target.file_path, target.target_name, target.status, target.message
                )
            })
            .collect::<Vec<_>>()
    };
    let latin: (&str, &[u8]) = ("src/latin.cpp", b"int \xe9;\n");
    let nul: (&str, &[u8]) = ("src/nul.cpp", b"int a;\0\n");
    assert_eq!(
        with(latin, true),
        [
            "src/latin.cpp ReadError ERROR Could not read C++ source: 'utf-8' codec can't decode byte 0xe9 in position 4: invalid continuation byte",
            "src/ok.cpp CppExceptionSafety PASS C++ exception constructs were inspected",
        ]
    );
    assert_eq!(
        with(nul, true),
        [
            "src/nul.cpp source-null ERROR C++ source contains a null byte",
            "src/ok.cpp CppExceptionSafety PASS C++ exception constructs were inspected",
        ]
    );
    assert_eq!(
        with(latin, false),
        [
            "src/latin.cpp AnalysisSourceError ERROR invalid-utf8: Analysis source is not valid UTF-8"
        ]
    );
    assert_eq!(
        with(nul, false),
        [
            "src/nul.cpp AnalysisSourceError ERROR invalid-text: Analysis source contains a NUL character"
        ]
    );
}
