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

#[test]
fn issue_cards_show_the_frozen_location_extent_only_for_a_range() {
    let base: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/run-code-fail.json"
    ))
    .unwrap();
    let with = |span: Value| {
        let mut value = base.clone();
        value["findings"][0]["primary_location"] = span;
        page(&value)
    };
    let path = "python/tool-a/demo.py";
    // A single point (end_line == start_line, no end column) adds nothing.
    assert!(!page(&base).contains("<span class=muted>L12"));
    let lines = with(serde_json::json!({"path":path,"start_line":13,"end_line":31}));
    assert!(
        lines.contains("</button> <span class=muted>L13-L31</span> "),
        "{lines}"
    );
    let columns = with(
        serde_json::json!({"path":path,"start_line":4,"end_line":4,"start_column":5,"end_column":10}),
    );
    assert!(columns.contains("<span class=muted>L4:C5-C10</span>"));
    let both = with(
        serde_json::json!({"path":path,"start_line":4,"end_line":6,"start_column":5,"end_column":2}),
    );
    assert!(both.contains("<span class=muted>L4:C5-L6:C2</span>"));
}

#[test]
fn test_suites_keep_failures_visible_and_fold_passing_cases() {
    let mut value = rich();
    engine(&mut value, "test")["extra"]["test_suites"] = serde_json::json!([
        {"file":"tests/test_a.py","passed":2,"failed":1,"skipped":1,"total":4,"tests":[
            {"name":"ok_one","status":"PASS"},{"name":"broken","status":"FAIL","message":"expected <1>"},
            {"name":"later","status":"SKIP"},{"name":"ok_two","status":"PASS"}]},
        {"file":"tests/test_b.py","passed":3,"failed":0,"skipped":0,"total":3,"tests":[
            {"name":"b1","status":"PASS"},{"name":"b2","status":"PASS"},{"name":"b3","status":"PASS"}]},
        {"file":"tests/test_c.py","passed":0,"failed":0,"skipped":0,"total":0,"tests":[]}
    ]);
    let html = page(&value);
    let start = html.find("<h4>tests/test_a.py").unwrap();
    let suite_a = &html[start..html.find("<h4>tests/test_b.py").unwrap()];
    assert!(
        suite_a.starts_with(
            "<h4>tests/test_a.py <span class=badge>2/4 Passed · 1 Skipped</span></h4>"
        )
    );
    let folded = suite_a
        .find("<details><summary>Show 2 passed cases</summary>")
        .unwrap();
    // Failure (escaped) and skip precede the fold; passing cases are only inside it.
    assert!(
        suite_a[..folded].contains("expected &lt;1&gt;") && suite_a[..folded].contains("later")
    );
    assert!(!suite_a[..folded].contains("ok_one") && suite_a[folded..].contains("ok_one"));
    let suite_b =
        &html[html.find("<h4>tests/test_b.py").unwrap()..html.find("<h4>tests/test_c.py").unwrap()];
    assert!(
        suite_b.contains(
            "<p>PASS — all 3 cases passed</p><details><summary>Show all 3 cases</summary>"
        )
    );
    // Empty suite: the summary line, no empty disclosure.
    let suite_c = &html[html.find("<h4>tests/test_c.py").unwrap()..];
    assert!(suite_c.contains("<p>PASS — all 0 cases passed</p>"));
    assert!(!suite_c[..suite_c.find("</p>").unwrap() + 30].contains("Show all 0 cases"));
}

#[test]
fn support_entries_needing_attention_are_listed_first_with_a_count() {
    let mut value = rich();
    let entry = value["support_matrix"]["entries"][0].clone();
    let mut healthy = entry.clone();
    healthy["engine_name"] = Value::from("line");
    healthy["evidence"] = Value::from("MEASURED");
    healthy["enabled"] = Value::from(true);
    healthy["applicable"] = Value::from(true);
    let mut inapplicable = healthy.clone();
    inapplicable["engine_name"] = Value::from("dead");
    inapplicable["applicable"] = Value::from(false);
    inapplicable["evidence"] = Value::from("NOT_RUN");
    let mut disabled = healthy.clone();
    disabled["engine_name"] = Value::from("security");
    disabled["enabled"] = Value::from(false);
    value["support_matrix"]["entries"] =
        serde_json::json!([healthy, inapplicable, disabled, entry]);
    let html = page(&value);
    assert!(html.contains("<p data-support-attention>2 item(s) need attention</p>"));
    let section = &html[html.find("<h2>support_matrix</h2>").unwrap()..];
    let at = |name: &str| section.find(&format!(">{name}<")).unwrap();
    // Disabled and ESTIMATED applicable entries first (stable), then healthy and inapplicable.
    assert!(
        at("security") < at("complexity")
            && at("complexity") < at("line")
            && at("line") < at("dead")
    );
    let mut calm = rich();
    calm["support_matrix"]["entries"] = serde_json::json!([]);
    assert!(page(&calm).contains("<p data-support-attention>No active capability caveats</p>"));
}
