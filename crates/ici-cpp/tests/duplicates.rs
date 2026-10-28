use ici_cpp::{CppAnalysisSession, cpp_directive_lines, tokenize_cpp_lines};
use ici_domain::{AnalysisRequest, SourceInput, TaskState};
use serde_json::{Value, json};
#[test]
fn frozen_cpp_lexical_corpus_preserves_physical_lines_literals_qt_anchors_and_splices() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lexical-oracle.json"
    ))
    .unwrap();
    for (i, case) in oracle["cases"].as_array().unwrap().iter().enumerate() {
        let source = case["source"].as_str().unwrap();
        assert_eq!(
            serde_json::to_value(tokenize_cpp_lines(source).unwrap()).unwrap(),
            case["indexed"],
            "case {i}: {source:?}"
        );
        assert_eq!(
            json!(cpp_directive_lines(source).unwrap()),
            case["directive_lines"],
            "barriers case {i}"
        );
    }
}
fn request(files: Vec<(&str, &str)>) -> AnalysisRequest {
    AnalysisRequest {
        task_id: "cpp.dup".into(),
        check_id: "cpp.dup".into(),
        component_id: "native".into(),
        language: "cpp".into(),
        inputs: files
            .into_iter()
            .map(|(path, text)| SourceInput {
                path: path.into(),
                bytes: text.as_bytes().to_vec(),
            })
            .collect(),
        options: json!({}),
        target_version: "c++20".into(),
    }
}
#[test]
fn duplicate_matches_share_core_regions_locations_and_preserve_exclusions() {
    let left = "int first(int x) {\n int sum=x;\n sum+=1;\n sum*=2;\n sum-=3;\n sum/=4;\n if(sum)sum+=x;\n return sum;\n}\n";
    let right = "int second(int y) {\n int answer=y;\n answer+=9;\n answer*=8;\n answer-=7;\n answer/=6;\n if(answer)answer+=y;\n return answer;\n}\n";
    let output = CppAnalysisSession::new().analyze(&request(vec![
        ("left.cpp", left),
        ("right.cpp", right),
        ("generated/moc_item.cpp", left),
    ]));
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{:?}",
        output.observation.limitations
    );
    assert_eq!(output.observation.findings.len(), 2, "{output:?}");
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "generated/moc_item.cpp" && target.status == "SKIP")
    );
    let mut bounded = request(vec![("left.cpp", left), ("right.cpp", right)]);
    bounded.options = json!({"max_cross_file_seed_pairs":0});
    assert_eq!(
        CppAnalysisSession::new()
            .analyze(&bounded)
            .observation
            .state,
        TaskState::Failed
    );
}

#[test]
fn generated_and_vendor_opt_ins_are_independent_and_keep_both_exclusion_reasons() {
    let code = "int f(int x) {\n int a=x;\n a+=1;\n a*=2;\n a-=3;\n a/=4;\n return a;\n}\n";
    let mut input = request(vec![
        ("owned.cpp", code),
        ("Vendor/Generated/moc_item.cpp", code),
        ("Deps/helper.cpp", code),
        ("app_autogen/item.cpp", code),
        ("build/ordinary.cpp", code),
    ]);
    for (generated, vendor, expected, excluded) in [
        (false, false, 2, json!({"generated":2,"vendor":2})),
        (true, false, 3, json!({"vendor":2})),
        (false, true, 3, json!({"generated":2})),
        (true, true, 5, json!({})),
    ] {
        input.options = json!({"include_generated":generated,"include_vendor":vendor});
        let output = CppAnalysisSession::new().analyze(&input);
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(output.extra["excluded_source_counts"], excluded);
        assert_eq!(output.observation.findings.len(), expected, "{output:?}");
        assert_eq!(
            output
                .targets
                .iter()
                .filter(|target| target.status == "SKIP")
                .count(),
            5 - expected
        );
        assert!(
            output
                .observation
                .findings
                .iter()
                .any(|finding| finding.primary_location.path == "build/ordinary.cpp")
        );
    }
}

#[test]
fn excluded_only_cpp_duplicates_have_no_measured_zero_or_clean_source_verdict() {
    let input = request(vec![(
        "Vendor/Generated/moc_item.cpp",
        "int f(){return 1;}\n",
    )]);
    let mut session = CppAnalysisSession::new();
    let output = session.analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.status, "SKIP");
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.targets[0].file_path, "Vendor/Generated/moc_item.cpp");
    assert_eq!(output.targets[0].status, "SKIP");
    assert_eq!(
        output.extra["excluded_source_counts"],
        json!({"vendor":1,"generated":1})
    );
    assert!(
        output
            .observation
            .measurements
            .iter()
            .all(|metric| metric.evidence == ici_domain::EvidenceLevel::NotApplicable)
    );
    assert_eq!(session.parser_invocations(), 0);
}

#[test]
fn normalization_does_not_search_later_numeric_literals_at_each_identifier() {
    let source = "value += other;\n".repeat(10_000);
    let started = std::time::Instant::now();
    let lines = tokenize_cpp_lines(&source).unwrap();
    assert_eq!(lines.len(), 10_000);
    assert!(
        started.elapsed() < std::time::Duration::from_secs(2),
        "bounded source took {:?}",
        started.elapsed()
    );
}

#[test]
fn active_duplicate_normalization_cancellation_checks_the_cached_source_path() {
    use std::{
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        },
        time::{Duration, Instant},
    };
    let text = format!(
        "int work(int value){{\n{}return value;\n}}\n",
        "value += other;\n".repeat(15_000)
    );
    let input = request(vec![("large.cpp", &text)]);
    let mut session = CppAnalysisSession::new();
    let mut warm = input.clone();
    warm.check_id = "cpp.complexity".into();
    session.analyze(&warm);
    assert_eq!(session.parser_invocations(), 1);
    let flag = Arc::new(AtomicBool::new(false));
    let worker_flag = Arc::clone(&flag);
    let (sender, receiver) = std::sync::mpsc::channel();
    let worker = std::thread::spawn(move || {
        sender.send(()).unwrap();
        session.analyze_cancellable(&input, &worker_flag)
    });
    receiver.recv().unwrap();
    std::thread::sleep(Duration::from_millis(25));
    let start = Instant::now();
    flag.store(true, Ordering::Relaxed);
    let output = worker.join().unwrap();
    assert!(start.elapsed() < Duration::from_secs(2));
    assert_eq!(output.observation.state, TaskState::Cancelled);
}
