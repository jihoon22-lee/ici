//! Historical `ici verify --github-summary` Markdown against frozen v0.13.0 step summaries
//! (`tests/rust-parity/reporting/legacy-summary-oracle.json`, `capture_legacy_summary.py`):
//! the frozen saved suite renders to the exact bytes frozen appended.
use ici_reporting::{StoredResult, bounded_summary_payload, legacy_summary_markdown};
use serde_json::Value;

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-summary-oracle.json"
    ))
    .unwrap()
}

#[test]
fn frozen_suites_render_the_frozen_step_summary_byte_for_byte() {
    let oracle = oracle();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    for (name, case) in cases {
        let stored =
            StoredResult::from_report_bytes(&serde_json::to_vec(&case["suite"]).unwrap()).unwrap();
        let markdown = legacy_summary_markdown(&stored).unwrap().unwrap();
        let existing = case["existing"].as_str().unwrap();
        let appended = bounded_summary_payload(&markdown, 900_000 - existing.len());
        let frozen = case["summary"].as_str().unwrap();
        let (expected_existing, expected) = frozen.split_at(existing.len());
        assert_eq!(expected_existing, existing, "{name}");
        assert_eq!(appended.lines().count(), expected.lines().count(), "{name}");
        let mut inventory = false;
        for (native, frozen) in appended.lines().zip(expected.lines()) {
            inventory = (inventory || native.starts_with("<summary><b>Complete tool inventory"))
                && native != "</details>";
            if inventory && native.starts_with("| <code>") {
                // Saved JSON objects are key-sorted natively (serde_json without
                // preserve_order), so a tool's `details` pairs keep their content, not frozen's
                // probe insertion order.
                let (native_head, native_details) = native.rsplit_once(" | ").unwrap();
                let (frozen_head, frozen_details) = frozen.rsplit_once(" | ").unwrap();
                assert_eq!(native_head, frozen_head, "{name}");
                let sorted = |text: &str| {
                    let mut chars: Vec<char> = text.chars().collect();
                    chars.sort_unstable();
                    chars
                };
                assert_eq!(sorted(native_details), sorted(frozen_details), "{name}");
                continue;
            }
            assert_eq!(native, frozen, "{name}");
        }
        assert_eq!(appended.len(), expected.len(), "{name}");
    }
}

#[test]
fn bounded_targets_baseline_selection_and_payload_limits() {
    let oracle = oracle();
    let bounded = oracle["cases"]["bounded"]["summary"].as_str().unwrap();
    // More than 100 targets in one engine: 100 rows and the omitted-row note.
    assert!(bounded.contains(" target row(s) omitted from this bounded GitHub view."));
    let baseline = oracle["cases"]["baseline"]["summary"].as_str().unwrap();
    assert!(baseline.contains("### Baseline finding delta"));
    assert!(baseline.contains("unchanged row(s) omitted"));
    // A native run result has no historical step summary.
    let native = StoredResult::from_bytes(include_bytes!(
        "../../../tests/fixtures/ici-next/run-code-fail.json"
    ))
    .unwrap();
    assert!(legacy_summary_markdown(&native).unwrap().is_none());
    // The bounded append never exceeds its budget and never splits a UTF-8 character.
    let markdown = "é".repeat(400);
    let cut = bounded_summary_payload(&markdown, 300);
    assert!(cut.len() <= 300 && cut.ends_with("Full JSON and HTML reports retain all details.\n"));
    assert_eq!(bounded_summary_payload(&markdown, 0), "");
    assert!(bounded_summary_payload(&markdown, 50).len() <= 50);
    assert_eq!(bounded_summary_payload("ok", 100), "\nok\n");
}
