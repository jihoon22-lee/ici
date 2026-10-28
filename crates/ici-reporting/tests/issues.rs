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
