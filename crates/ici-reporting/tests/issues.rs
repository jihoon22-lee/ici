use ici_reporting::{StoredResult, project_issue_groups};
use serde_json::{Value, json};
#[test]
fn display_groups_match_frozen_identity_overlap_context_and_clone_contracts() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/issue-groups.json"
    ))
    .unwrap();
    for case in corpus["cases"].as_array().unwrap() {
        let stored = StoredResult::from_value(case["input"].clone()).unwrap();
        let before = stored.to_value().unwrap();
        let projection = project_issue_groups(&stored).unwrap();
        assert_eq!(
            json!(
                projection
                    .groups
                    .iter()
                    .map(|group| group.view.clone())
                    .collect::<Vec<_>>()
            ),
            case["expected"],
            "{}",
            case["name"]
        );
        assert_eq!(
            projection.total_findings,
            case["total_findings"].as_u64().unwrap() as usize
        );
        assert_eq!(stored.to_value().unwrap(), before);
    }
}

/// Frozen `issue_view._canonical_occurrence` and `_clone_groups` edges
/// (`tests/rust-parity/reporting/capture_issue_edges.py` -> `issue-edges.json`): an occurrence
/// whose end precedes its start is dropped, a missing end is the start line, a non-integer
/// `lines_count` reads "matched code", and an empty id is not a clone group.
#[test]
fn clone_occurrence_edges_match_frozen() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/issue-edges.json"
    ))
    .unwrap();
    let cases = corpus["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 4);
    for case in cases {
        let stored = StoredResult::from_value(case["input"].clone()).unwrap();
        let projection = project_issue_groups(&stored).unwrap();
        let native: Vec<Value> = projection
            .groups
            .iter()
            .map(|group| {
                let view = &group.view;
                json!({
                    "message": view["message"],
                    "fingerprints": view["fingerprints"],
                    "locations": view["locations"].as_array().unwrap().iter()
                        .map(|location| json!([location["path"], location["start_line"], location["end_line"]]))
                        .collect::<Vec<_>>(),
                    "original_finding_count": view["original_finding_count"],
                    "clone_group_id": view.get("clone_group_id").cloned().unwrap_or(json!("")),
                })
            })
            .collect();
        assert_eq!(json!(native), case["groups"], "{}", case["name"]);
        assert_eq!(
            projection.total_findings,
            case["total"].as_u64().unwrap() as usize
        );
    }
}
