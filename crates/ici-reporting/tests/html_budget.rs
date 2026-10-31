//! R8 report-size budget on the measured one-file Python project, with information checks.
mod common;
use common::embedded;
use ici_reporting::{RenderOptions, StoredResult, render_html};
use serde_json::Value;

const DETAILS: &[u8] =
    include_bytes!("../../../tests/rust-parity/reporting/budget-one-file/details.json");
const RESULT: &[u8] =
    include_bytes!("../../../tests/rust-parity/reporting/budget-one-file/result.json");
const ORACLE: &str =
    include_str!("../../../tests/rust-parity/reporting/budget-one-file/oracle-report.html");

/// Text a reader sees without opening anything: markup before the embedded data.
fn visible(html: &str) -> &str {
    html.split("<script id=").next().unwrap()
}

#[test]
fn one_file_python_report_fits_the_budget_and_keeps_the_complete_saved_result() {
    let details: Value = serde_json::from_slice(DETAILS).unwrap();
    let run: Value = serde_json::from_slice(RESULT).unwrap();
    // The frozen presentation archive belongs to exactly this native run.
    assert_eq!(details["native_run"], run);
    let stored = StoredResult::from_bytes(DETAILS).unwrap();
    let html = render_html(&stored, &RenderOptions::default()).unwrap();
    let budget = (ORACLE.len() * 2).max(64 * 1024);
    println!(
        "one-file report: native_html_bytes={} oracle_html_bytes={} budget_bytes={budget} details_json_bytes={}",
        html.len(),
        ORACLE.len(),
        DETAILS.len()
    );
    assert!(html.len() <= budget, "{} > {budget}", html.len());
    assert_eq!(embedded(&html), details);
    // Zero-CDN: no external resource, network connection or dynamic code evaluation.
    for forbidden in ["<script src=", "<link ", "fetch(", "innerHTML", "eval("] {
        assert!(!html.contains(forbidden), "{forbidden}");
    }
    assert!(html.contains("connect-src 'none'"));
}

#[test]
fn one_file_python_report_shows_every_oracle_fact_without_opening_sections() {
    let run: Value = serde_json::from_slice(RESULT).unwrap();
    let html = render_html(
        &StoredResult::from_bytes(DETAILS).unwrap(),
        &RenderOptions::default(),
    )
    .unwrap();
    let shown = visible(&html);
    // Facts the frozen offline report shows for this run, read from the native run itself.
    let mut facts = vec![
        run["run_id"].as_str().unwrap().to_string(),
        run["producer"]["ici_version"].as_str().unwrap().to_string(),
        run["gate"]["selected"].as_str().unwrap().to_string(),
        run["scope"]["kind"].as_str().unwrap().to_string(),
        "<dt>exit_code</dt><dd>3</dd>".into(),
        "<dt>required_complete</dt><dd>false</dd>".into(),
        "<dt>has_violations</dt><dd>false</dd>".into(),
    ];
    for check in run["checks"].as_array().unwrap() {
        facts.push(format!(
            "<td>{}</td><td>{}</td><td>{}</td><td>{}</td>",
            check["task_id"].as_str().unwrap(),
            check["component_id"].as_str().unwrap(),
            check["state"].as_str().unwrap(),
            check["evidence_complete"]
        ));
    }
    for id in run["execution"]["blocked_task_ids"].as_array().unwrap() {
        facts.push(id.as_str().unwrap().to_string());
    }
    for component in run["scope"]["selected_components"].as_array().unwrap() {
        facts.push(component.as_str().unwrap().to_string());
    }
    for reason in run["gate"]["reasons"].as_array().unwrap() {
        facts.push(reason.as_str().unwrap().to_string());
    }
    for limitation in run["limitations"].as_array().unwrap() {
        facts.push(limitation.as_str().unwrap().to_string());
    }
    for metric in run["metrics"].as_array().unwrap() {
        facts.push(metric["name"].as_str().unwrap().to_string());
    }
    for language in run["scope"]["selected_languages"]
        .as_array()
        .into_iter()
        .flatten()
    {
        facts.push(language.as_str().unwrap().to_string());
    }
    for fact in facts {
        assert!(shown.contains(&fact), "visible report omits {fact}");
    }
    assert!(shown.contains("No findings recorded") || shown.contains("Issues (0)"));
    // Every family keeps its summary, state and curated facet in visible markup.
    let details: Value = serde_json::from_slice(DETAILS).unwrap();
    for engine in details["results"].as_array().unwrap() {
        let name = engine["engine_name"].as_str().unwrap();
        assert!(shown.contains(&format!("data-family=\"{name}\"")), "{name}");
        let summary = engine["summary"].as_str().unwrap().replace('<', "&lt;");
        assert!(shown.contains(&summary), "{name}: {summary}");
    }
    assert!(shown.contains("main.py"));
    assert!(shown.contains("<meter"));
}

#[test]
fn every_saved_record_formerly_dumped_in_markup_has_an_on_demand_section() {
    let details: Value = serde_json::from_slice(DETAILS).unwrap();
    let html = render_html(
        &StoredResult::from_bytes(DETAILS).unwrap(),
        &RenderOptions::default(),
    )
    .unwrap();
    let lazy = regex::Regex::new(r#"<details data-lazy="([^"]*)"(?: data-indices="([0-9 ]*)")?>"#)
        .unwrap();
    let mut pointers = std::collections::BTreeMap::<String, Vec<usize>>::new();
    for captures in lazy.captures_iter(&html) {
        let pointer = captures[1].to_string();
        assert!(details.pointer(&pointer).is_some(), "{pointer}");
        let entry = pointers.entry(pointer).or_default();
        if let Some(indices) = captures.get(2) {
            entry.extend(
                indices
                    .as_str()
                    .split(' ')
                    .map(|index| index.parse::<usize>().unwrap()),
            );
        }
    }
    assert!(pointers.contains_key("/native_run/checks"));
    for (index, engine) in details["results"].as_array().unwrap().iter().enumerate() {
        let base = format!("/results/{index}");
        assert!(pointers.contains_key(&format!("{base}/extra")), "{base}");
        assert!(
            pointers.contains_key(&format!("{base}/tool_evidence")),
            "{base}"
        );
        for position in 0..engine["extra"]["native_checks"].as_array().unwrap().len() {
            assert!(
                pointers.contains_key(&format!("{base}/extra/native_checks/{position}")),
                "{base}"
            );
        }
        // Every normal target is reachable exactly once; non-normal targets stay visible.
        let targets = engine["targets"].as_array().unwrap();
        let normal: Vec<usize> = (0..targets.len())
            .filter(|index| matches!(targets[*index]["status"].as_str(), Some("PASS" | "SKIP")))
            .collect();
        let mut listed = pointers
            .get(&format!("{base}/targets"))
            .cloned()
            .unwrap_or_default();
        listed.sort_unstable();
        assert_eq!(listed, normal, "{base}");
    }
    // Informational inventory cards are built from the saved findings.
    let cards = regex::Regex::new(r#"data-lazy-findings="([^"]*)""#).unwrap();
    let mut listed = 0;
    for captures in cards.captures_iter(&html) {
        let pairs: Value = serde_json::from_str(&captures[1].replace("&quot;", "\"")).unwrap();
        for pair in pairs.as_array().unwrap() {
            assert!(details.pointer(pair[0].as_str().unwrap()).is_some());
            listed += 1;
        }
    }
    let findings: usize = details["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| engine["findings"].as_array().unwrap().len())
        .sum();
    assert_eq!(listed, findings);
}
