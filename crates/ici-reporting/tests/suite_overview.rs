//! Frozen legacy renderer facts: gate reason, score display, tally and baseline delta order.
use ici_reporting::{RenderOptions, StoredResult, render_console, render_html, render_markdown};
use serde_json::Value;

/// Markdown cells escape HTML and table/emphasis characters.
fn md(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('\'', "&#39;")
        .replace('_', "\\_")
        .replace('|', "\\|")
}
fn corpus() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/suite-overview.json"
    ))
    .unwrap()
}

#[test]
fn every_renderer_states_the_frozen_gate_reason_scores_and_tally() {
    let options = RenderOptions {
        console_width: 500,
        ..RenderOptions::default()
    };
    for case in corpus()["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let document = &case["document"];
        let stored = StoredResult::from_value(document.clone()).unwrap();
        let console = render_console(&stored, &options).unwrap();
        let markdown = render_markdown(&stored, &options).unwrap();
        let html = render_html(&stored, &options).unwrap();
        let reason = case["gate_reason"].as_str().unwrap();
        let status = document["suite_status"].as_str().unwrap();
        assert!(
            console.contains(&format!("Suite: {status} — {reason}")),
            "{name}\n{console}"
        );
        assert!(markdown.contains(&md(reason)), "{name}\n{markdown}");
        assert!(html.contains(&reason.replace('\'', "&#39;")), "{name}");
        for (engine, score) in document["results"]
            .as_array()
            .unwrap()
            .iter()
            .zip(case["scores"].as_array().unwrap())
        {
            let score = score.as_str().unwrap();
            let engine = engine["engine_name"].as_str().unwrap();
            assert!(
                console.contains(&format!("{engine}: {engine} summary")),
                "{name}"
            );
            assert!(
                console.contains(&format!("score {score} ·")),
                "{name}: {score}\n{console}"
            );
            assert!(
                markdown.contains(&format!("| {score} |")),
                "{name}: {score}"
            );
        }
        let fail =
            document["failed_count"].as_u64().unwrap() - document["error_count"].as_u64().unwrap();
        assert!(
            console.contains(&format!(
                "Total Engines: {} (Pass: {}, Warn: {}, Fail: {fail}, Error: {}, Skip: {})",
                document["total_count"],
                document["passed_count"],
                document["warned_count"],
                document["error_count"],
                document["skipped_count"]
            )),
            "{name}\n{console}"
        );
        assert!(console.contains("Total Time: 3.14s"), "{name}");
        if name == "pass" {
            assert!(console.contains("TEM Score: 2.35 / 5.0"));
        }
        // Issues-first delta order and severity transitions follow `select_baseline_details`.
        let order: Vec<_> = case["baseline_order"]
            .as_array()
            .unwrap()
            .iter()
            .zip(case["severity_transitions"].as_array().unwrap())
            .collect();
        let mut cursor = 0;
        for (message, transition) in order {
            let message = message
                .as_str()
                .filter(|text| !text.is_empty())
                .unwrap_or("—");
            let needle = format!("— {message} · current");
            let found = console[cursor..]
                .find(&needle)
                .unwrap_or_else(|| panic!("{name}: {needle}\n{console}"));
            cursor += found + needle.len();
            let line_end = console[cursor..].find('\n').unwrap() + cursor;
            assert!(
                console[cursor..line_end]
                    .ends_with(&format!("severity {}", transition.as_str().unwrap())),
                "{name}"
            );
            assert!(
                markdown.contains(&format!("| {} |", transition.as_str().unwrap())),
                "{name}"
            );
        }
    }
}

#[test]
fn resolved_historical_deltas_stay_visible_as_sarif_absent_results() {
    let corpus = corpus();
    let case = corpus["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["name"] == "baseline-gate")
        .unwrap();
    let stored = StoredResult::from_value(case["document"].clone()).unwrap();
    let sarif = ici_reporting::sarif_document(&stored, &RenderOptions::default()).unwrap();
    let results = sarif["runs"][0]["results"].as_array().unwrap();
    // Frozen 6fe3e405 serialize_sarif emitted exactly one result here: the resolved delta.
    let absent: Vec<_> = results
        .iter()
        .filter(|result| result["baselineState"] == "absent")
        .collect();
    assert_eq!(absent.len(), 1);
    let result = absent[0];
    assert_eq!(result["ruleId"], "ici.r");
    assert_eq!(result["level"], "error");
    assert_eq!(result["message"]["text"], "ici.r");
    assert_eq!(
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
        "b.py"
    );
    assert_eq!(
        result["locations"][0]["physicalLocation"]["region"]["startLine"],
        1
    );
    for (key, value) in [
        ("engine", "lint"),
        ("category", "correctness"),
        ("confidence", "low"),
        ("delta_state", "resolved"),
    ] {
        assert_eq!(result["properties"][key], value);
    }
    assert_eq!(result["properties"]["delta_gated"], false);
    // The saved baseline identity is kept so consumers can match the absent result.
    assert_eq!(
        result["partialFingerprints"]["ici.fingerprint"],
        format!("sha256:{}", "b".repeat(64))
    );
    // A delta without a baseline location cannot be placed and is not invented.
    let mut document = case["document"].clone();
    document["baseline_comparison"]["entries"][1]["baseline_location"] = Value::Null;
    document["baseline_comparison"]["entries"][1]["state"] = "new".into();
    let stored = StoredResult::from_value(document);
    if let Ok(stored) = stored {
        let sarif = ici_reporting::sarif_document(&stored, &RenderOptions::default()).unwrap();
        assert!(
            sarif["runs"][0]["results"]
                .as_array()
                .unwrap()
                .iter()
                .all(|result| result["baselineState"] != "absent")
        );
    }
}

/// Frozen `_resolved_finding`: a resolved delta without a baseline location yields no absent
/// result, and one whose baseline path escapes the project fails the serializer
/// (`canonical_project_path` raises "path escapes the project root") instead of being emitted.
#[test]
fn resolved_deltas_without_or_with_escaping_baseline_locations_follow_the_frozen_serializer() {
    let corpus = corpus();
    let case = corpus["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["name"] == "baseline-gate")
        .unwrap();
    let pointer = "/baseline_comparison/entries/1";
    assert_eq!(
        case["document"].pointer(pointer).unwrap()["state"],
        "resolved"
    );
    let absent = |document: Value| -> Option<usize> {
        let stored = StoredResult::from_value(document).ok()?;
        let sarif = ici_reporting::sarif_document(&stored, &RenderOptions::default()).ok()?;
        Some(
            sarif["runs"][0]["results"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|result| result["baselineState"] == "absent")
                .count(),
        )
    };
    let mut unlocated = case["document"].clone();
    unlocated.pointer_mut(pointer).unwrap()["baseline_location"] = Value::Null;
    assert_eq!(absent(unlocated), Some(0));
    for path in ["../outside.py", "/abs/outside.py"] {
        let mut escaping = case["document"].clone();
        escaping.pointer_mut(pointer).unwrap()["baseline_location"]["path"] = Value::from(path);
        assert_eq!(absent(escaping), None, "{path}");
    }
}
