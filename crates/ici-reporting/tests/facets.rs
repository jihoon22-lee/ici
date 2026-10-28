use ici_reporting::{
    RenderOptions, StoredResult, render_console, render_html, render_markdown, sarif_document,
};
use serde_json::Value;
fn embedded(html: &str) -> &str {
    html.split("<script id=ici-result type=application/json>")
        .nth(1)
        .unwrap()
        .split("</script>")
        .next()
        .unwrap()
}
#[test]
fn all_historical_families_render_their_rich_saved_payload_and_collapsed_normal_inventory() {
    let bytes = include_bytes!("../../../tests/rust-parity/reporting/rich-19-families.json");
    let value: Value = serde_json::from_slice(bytes).unwrap();
    let stored = StoredResult::from_bytes(bytes).unwrap();
    let options = RenderOptions::default();
    let html = render_html(&stored, &options).unwrap();
    for name in ici_domain::builtin_catalog()
        .legacy
        .iter()
        .map(|descriptor| &descriptor.name)
    {
        assert!(html.contains(&format!("data-family=\"{name}\"")), "{name}");
    }
    for sentinel in [
        "data-line-scope",
        "data-scope-toggle",
        "docs/config.toml",
        "data-source-scope=\"extra\" hidden",
        "<meter",
        "gcov",
        "Module coverage",
        "Function coverage",
        "used_function",
        "unused_function",
        "failing_case",
        "expected &lt;value&gt;",
        "Test quality",
        "complex_function",
        "nested_function",
        "Clone groups and instances",
        "python-ast-semantic-shape",
        "return x &lt; 3",
        "pkg.a",
        "moc_widget.cpp",
        "unsafe_access",
        "old_symbol",
        "request_roundtrip",
        "demo-1.0-py3-none-any.whl",
        "Source estimate preserves compiler boundary uncertainty",
        "keep rich extension",
    ] {
        assert!(html.contains(sentinel), "{sentinel}");
    }
    assert!(html.contains("informational inventory records"));
    assert!(html.contains("<summary>PASS · src"));
    assert!(!html.contains("<script>alert(1)</script>"));
    assert_eq!(
        serde_json::from_str::<Value>(embedded(&html)).unwrap(),
        value
    );
    let markdown = render_markdown(&stored, &options).unwrap();
    assert!(markdown.contains("Canonical issue groups"));
    assert!(markdown.contains("38") || markdown.contains("19"));
    assert!(
        render_console(&stored, &options)
            .unwrap()
            .contains("line summary")
    );
    let sarif = sarif_document(&stored, &options).unwrap();
    assert_eq!(sarif["runs"][0]["results"].as_array().unwrap().len(), 38);
    assert_eq!(sarif["runs"][0]["columnKind"], "unicodeCodePoints");
}
#[test]
fn tiny_saved_success_html_stays_within_frozen_64kib_budget() {
    let stored = StoredResult::from_bytes(include_bytes!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap();
    let html = render_html(&stored, &RenderOptions::default()).unwrap();
    assert!(html.len() <= 65536, "{} bytes", html.len());
    assert!(!html.contains("<script src="));
    assert!(!html.contains("<link "));
    println!("tiny HTML bytes={}", html.len());
}
