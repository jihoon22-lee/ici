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
