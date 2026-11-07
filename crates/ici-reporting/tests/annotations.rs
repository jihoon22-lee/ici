//! Historical GitHub workflow annotations against frozen v0.13.0
//! (`tests/rust-parity/reporting/legacy-annotations-oracle.json`, capture_legacy_annotations.py).
use ici_reporting::{RenderOptions, StoredResult, legacy_github_annotations};
use serde_json::{Value, json};

fn stored(value: &Value) -> StoredResult {
    StoredResult::from_bytes(&serde_json::to_vec(value).unwrap()).unwrap()
}

#[test]
fn frozen_suite_targets_become_the_frozen_annotation_lines() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-annotations-oracle.json"
    ))
    .unwrap();
    let lines =
        legacy_github_annotations(&stored(&oracle["suite"]), &RenderOptions::default()).unwrap();
    let expected: Vec<String> = serde_json::from_value(oracle["annotations"].clone()).unwrap();
    assert_eq!(lines, expected);
}

fn target(path: &str, line: u64, status: &str, message: &str) -> Value {
    json!({"file_path":path,"start_line":line,"end_line":line,"start_column":null,"end_column":null,
        "target_name":"t","status":status,"message":message,"snippet":"","metrics":{}})
}

#[test]
fn bounded_changed_first_escaped_and_pass_targets_never_annotate() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-annotations-oracle.json"
    ))
    .unwrap();
    let mut suite = oracle["suite"].clone();
    let mut targets: Vec<Value> = (1..=60)
        .map(|line| target("src/many.py", line, "WARN", "plain"))
        .collect();
    targets.push(target("src/a,b:c.py", 7, "WARN", "100% sure\r\nnext"));
    targets.push(target("src/ok.py", 1, "PASS", "fine"));
    let engine = &mut suite["results"][0];
    engine["targets"] = Value::Array(targets);
    let name = engine["engine_name"].as_str().unwrap().to_owned();
    for other in suite["results"].as_array_mut().unwrap().iter_mut().skip(1) {
        other["targets"] = json!([]);
    }
    // The last WARN target is new against the baseline, so it takes the budget first.
    suite["baseline_comparison"] = json!({"entries":[{"state":"new","regressed":false,
        "current_location":{"path":"src/a,b:c.py","start_line":7}}]});
    let lines = legacy_github_annotations(&stored(&suite), &RenderOptions::default()).unwrap();
    assert_eq!(lines.len(), 51);
    assert_eq!(
        lines[0],
        format!("::warning file=src/a%2Cb%3Ac.py,line=7::[{name}] 100%25 sure%0D%0Anext")
    );
    assert_eq!(
        lines[1],
        format!("::warning file=src/many.py,line=1::[{name}] plain")
    );
    assert_eq!(
        lines[50],
        "::notice::11 additional ici annotation(s) omitted from this bounded workflow view; the JSON and HTML reports retain the full inventory"
    );
    assert!(!lines.iter().any(|line| line.contains("src/ok.py")));
}
