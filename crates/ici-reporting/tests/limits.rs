use ici_reporting::{
    ConsoleGroupBy, RenderOptions, StoredResult, bounded_summary, github_annotations,
    render_console, render_html, render_sarif,
};
use serde_json::{Value, json};
#[test]
fn large_html_keeps_100000_records_and_only_fifty_initial_issue_cards() {
    let started = std::time::Instant::now();
    let base: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    let first = base["results"][0]["findings"][0].clone();
    let findings: Vec<_> = (0..100_000)
        .map(|index| {
            let mut finding = first.clone();
            finding["rule_id"] = json!("ici.synthetic.large");
            finding["fingerprint"] = json!(format!("sha256:{index:064x}"));
            finding["message"] = json!(if index == 0 {
                "</script><script>alert('x')</script> &".into()
            } else {
                format!("synthetic finding {index}")
            });
            finding["primary_location"]["path"] = json!(format!("src/file-{index:06}.py"));
            finding["primary_location"]["start_column"] = Value::Null;
            finding["primary_location"]["end_column"] = Value::Null;
            finding
        })
        .collect();
    let payload = json!({"schema_version":"ici.result/v3","suite_status":"WARN","results":[{"engine_name":"synthetic","status":"WARN","evidence":"MEASURED","findings":findings,"targets":[],"extra":{}}]});
    let bytes = serde_json::to_vec(&payload).unwrap();
    assert!(bytes.len() > 32 * 1024 * 1024);
    drop(payload);
    let stored = StoredResult::from_bytes(&bytes).unwrap();
    let html = render_html(&stored, &RenderOptions::default()).unwrap();
    assert_eq!(html.matches("<article class=finding").count(), 50);
    assert!(html.contains("id=ici-groups"));
    assert!(!html.contains("<script>alert('x')</script>"));
    assert!(!html.contains("innerHTML"));
    assert!(!html.contains("fetch("));
    let raw = html
        .split("<script id=ici-result type=application/json>")
        .nth(1)
        .unwrap()
        .split("</script>")
        .next()
        .unwrap();
    let data: Value = serde_json::from_str(raw).unwrap();
    assert_eq!(
        data["results"][0]["findings"].as_array().unwrap().len(),
        100_000
    );
    assert_eq!(
        data["results"][0]["findings"][0]["message"],
        "</script><script>alert('x')</script> &"
    );
    println!(
        "100000 finding HTML: input_bytes={} html_bytes={} elapsed_seconds={:.3}",
        bytes.len(),
        html.len(),
        started.elapsed().as_secs_f64()
    );
    drop(data);
    drop(html);
    drop(bytes);
    let sarif = render_sarif(&stored, &RenderOptions::default()).unwrap();
    assert_eq!(
        sarif.matches("\"ruleId\":\"ici.synthetic.large\"").count(),
        100_000
    );
    println!(
        "100000 finding SARIF: bytes={} cumulative_elapsed_seconds={:.3}",
        sarif.len(),
        started.elapsed().as_secs_f64()
    );
}
#[test]
fn console_caps_per_engine_verbose_and_unicode_width_preserve_original_inventory() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/issue-groups.json"
    ))
    .unwrap();
    let mut value = corpus["cases"][0]["input"].clone();
    let first = value["results"][0]["findings"][0].clone();
    value["results"] = json!([]);
    for engine in ["alpha", "beta"] {
        let findings: Vec<_> = (0..3)
            .map(|index| {
                let mut finding = first.clone();
                finding["message"] = json!(format!(
                    "{engine}-{index} 한글🙂 long-console-marker remains a readable sentence"
                ));
                finding["fingerprint"] = json!(format!("{engine}-{index}"));
                finding["primary_location"]["path"] = json!(format!("src/{engine}.py"));
                finding["primary_location"]["start_line"] = json!(index + 1);
                finding["primary_location"]["end_line"] = json!(index + 1);
                finding
            })
            .collect();
        value["results"].as_array_mut().unwrap().push(json!({"engine_name":engine,"status":"WARN","findings":findings,"targets":[],"extra":{}}));
    }
    let stored = StoredResult::from_value(value.clone()).unwrap();
    let console = render_console(
        &stored,
        &RenderOptions {
            max_findings: 2,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(console.contains("alpha-1"));
    assert!(console.contains("beta-1"));
    assert!(!console.contains("alpha-2"));
    assert!(
        console
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .contains("2 hidden finding(s), 2 hidden group(s)")
    );
    assert!(
        console
            .lines()
            .all(|line| unicode_width::UnicodeWidthStr::width(line) <= 80)
    );
    let verbose = render_console(
        &stored,
        &RenderOptions {
            verbose: true,
            max_findings: 0,
            group_by: ConsoleGroupBy::Category,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(verbose.contains("alpha-2"));
    assert!(verbose.contains("Category:"));
    assert_eq!(stored.to_value().unwrap(), value);
}
#[test]
fn utf8_step_summary_and_annotation_priority_are_deterministic() {
    let text = "결과🙂".repeat(2000);
    let bounded = bounded_summary(&text, 900).unwrap();
    assert!(bounded.len() <= 900);
    assert!(bounded.contains("truncated"));
    assert_eq!(bounded, bounded_summary(&text, 900).unwrap());
    let mut run: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap();
    let mut first = serde_json::from_slice::<Value>(include_bytes!(
        "../../../tests/fixtures/ici-next/run-partial-selection.json"
    ))
    .unwrap()["findings"][0]
        .clone();
    if first.is_null() {
        first = json!({"fingerprint":"old","rule_id":"ici.test.old","message":"old","severity":"high","confidence":"high","provider":"test","evidence":"MEASURED","primary_location":{"path":"old.py","start_line":1}});
    }
    let mut second = first.clone();
    first["fingerprint"] = json!("old");
    first["primary_location"]["path"] = json!("old.py");
    first["severity"] = json!("critical");
    second["fingerprint"] = json!("new");
    second["primary_location"]["path"] = json!("new.py");
    second["severity"] = json!("warning");
    run["findings"] = json!([first, second]);
    run["baseline"] = json!({"state":"comparable","origin":"prior","reason":"","new":["new"],"unchanged":["old"],"resolved":[],"carried":[]});
    let stored = StoredResult::from_value(run).unwrap();
    let annotations = github_annotations(
        &stored,
        &RenderOptions {
            max_annotations: 1,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(annotations[0].contains("new.py"));
}
