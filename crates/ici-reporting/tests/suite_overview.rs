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
