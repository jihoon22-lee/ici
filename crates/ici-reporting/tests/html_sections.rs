//! Frozen HTML section facts the native page keeps (reporters/html/sections audit): issue axis
//! filters and sort (sections/issues), collapsed not-applicable engines (summary +
//! static_analysis), and the line ratio card (sections/line).
use ici_reporting::{RenderOptions, StoredResult, render_html};
use serde_json::Value;
use std::path::Path;

fn rich() -> Value {
    serde_json::from_slice(include_bytes!(
        "../../../tests/rust-parity/reporting/rich-19-families.json"
    ))
    .unwrap()
}

fn page(value: &Value) -> String {
    let stored = StoredResult::from_bytes(&serde_json::to_vec(value).unwrap()).unwrap();
    render_html(&stored, &RenderOptions::default()).unwrap()
}

fn engine<'a>(value: &'a mut Value, name: &str) -> &'a mut Value {
    value["results"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|engine| engine["engine_name"] == name)
        .unwrap()
}

#[test]
fn issue_axis_filters_offer_only_present_values_and_tag_every_issue() {
    let html = page(&rich());
    assert!(html.contains("<div class=filters data-issue-filters>"));
    for axis in ["engine", "severity", "category", "confidence", "rule"] {
        assert!(
            html.contains(&format!(
                "<select data-issue-axis={axis}><option value=\"\">All</option><option>"
            )),
            "{axis}"
        );
    }
    assert!(html.contains("<option value=order>Report order</option><option value=severity>Severity</option><option value=engine>Engine</option><option value=file>File</option>"));
    let issues = html.matches("<div data-issue data-order=").count();
    assert!(issues > 0);
    // Every wrapper opens directly onto its issue card.
    assert_eq!(
        html.matches("\"><article class=finding data-search-row")
            .count(),
        issues
    );
    // No option is empty: frozen offers no filter that matches nothing.
    assert!(!html.contains("<option></option>"));
}

#[test]
fn not_applicable_engines_stay_collapsed_with_their_reason() {
    let mut value = rich();
    let skipped = engine(&mut value, "binary_compat");
    skipped["status"] = Value::from("SKIP");
    skipped["summary"] = Value::from("No published <artifact> to compare");
    let html = page(&value);
    let start = html.find("data-family=\"binary_compat\"").unwrap();
    let section = &html[start..start + html[start..].find("</section>").unwrap()];
    assert!(section.contains("class=muted><details><summary>Binary compatibility · <span class=badge>N/A</span> Not applicable — why?</summary><p>No published &lt;artifact&gt; to compare</p>"), "{section}");
    assert!(
        !section.contains("<h2>"),
        "a skipped family has no open heading"
    );
    assert!(section.ends_with("</details>"));
    // A family that ran keeps its open section heading.
    assert!(html.contains("data-family=\"line\"><h2>Line analysis</h2>"));
}

#[test]
fn line_ratio_card_matches_the_frozen_shares_and_handles_an_empty_inventory() {
    let html = page(&rich());
    // Frozen _render_ratio_card over code=40 comment=5 blank=2 total=47 and the all-files totals.
    assert!(html.contains(
        "<p data-line-ratio>Source files: Code (85.1%) · Comments (10.6%) · Blanks (4.3%)</p>"
    ));
    assert!(html.contains(
        "<p data-line-ratio>All project files: Code (84.6%) · Comments (9.6%) · Blanks (5.8%)</p>"
    ));
    let mut value = rich();
    let line = engine(&mut value, "line");
    line["extra"] = serde_json::json!({"code":0,"comment":0,"blank":0,"total":0});
    // max(1, total): an empty project shows 0.0% shares instead of dividing by zero.
    assert!(page(&value).contains("Source files: Code (0.0%) · Comments (0.0%) · Blanks (0.0%)"));
    line_without_counts();
}

fn line_without_counts() {
    let mut value = rich();
    engine(&mut value, "line")["extra"] = serde_json::json!({"preserved_extension": "x"});
    assert!(!page(&value).contains("data-line-ratio"));
}

fn harness(page: &str, controls: &str) -> Value {
    let node = std::env::var("ICI_TEST_NODE")
        .expect("ICI_TEST_NODE must name the Node.js used for the DOM harness");
    let directory = tempfile::tempdir().unwrap();
    let file = directory.path().join("report.html");
    std::fs::write(&file, page).unwrap();
    let script = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/reporting/lazy-dom-check.cjs");
    let output = std::process::Command::new(node)
        .arg(script)
        .arg(&file)
        .arg("")
        .arg(controls)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice::<Value>(&output.stdout).unwrap()["issues"].clone()
}

#[test]
#[ignore = "needs ICI_TEST_NODE; run with --include-ignored"]
fn issue_filters_and_sort_run_in_the_page_script() {
    let html = page(&rich());
    let all = harness(&html, "{}");
    let total = all["order"].as_array().unwrap().len();
    assert_eq!(all["count"], format!("{total} of {total} issues shown"));
    let orders: Vec<u64> = all["order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["order"].as_u64().unwrap())
        .collect();
    assert!(
        orders.windows(2).all(|pair| pair[0] < pair[1]),
        "report order"
    );
    // A file filter hides every issue outside the path; sort by file orders the shown ones.
    let filtered = harness(&html, r#"{"file":"src/line.py","sort":"file"}"#);
    let items = filtered["order"].as_array().unwrap();
    let shown: Vec<&Value> = items
        .iter()
        .filter(|item| item["hidden"] == false)
        .collect();
    assert!(!shown.is_empty());
    assert!(
        shown
            .iter()
            .all(|item| item["file"].as_str().unwrap().contains("src/line.py"))
    );
    assert_eq!(
        filtered["count"],
        format!("{} of {total} issues shown", shown.len())
    );
    let files: Vec<&str> = items
        .iter()
        .map(|item| item["file"].as_str().unwrap())
        .collect();
    assert!(files.windows(2).all(|pair| pair[0] <= pair[1]), "{files:?}");
    // An axis value that no issue carries shows nothing (failure mode: never a silent match).
    let none = harness(&html, r#"{"severity":"not-a-severity"}"#);
    assert_eq!(none["count"], format!("0 of {total} issues shown"));
}
