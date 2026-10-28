use ici_reporting::{
    RenderOptions, StoredResult, render_html, render_json, render_markdown, source_link,
};
use serde_json::{Value, json};
#[test]
fn source_links_encode_each_segment_and_require_explicit_safe_repository_context() {
    let options = RenderOptions {
        repository_url: Some("https://github.example/org/repo.git/".into()),
        commit_ref: Some("feature/fix".into()),
        ..Default::default()
    };
    assert_eq!(
        source_link("src/café's [x] %path.py", 3, Some(7), &options)
            .unwrap()
            .unwrap(),
        "https://github.example/org/repo/blob/feature%2Ffix/src/caf%C3%A9%27s%20%5Bx%5D%20%25path.py#L3-L7"
    );
    for path in [
        "../escape.py",
        "/outside.py",
        "C:/outside.py",
        "[external]",
        "x\\y.py",
        "x\ny.py",
    ] {
        assert!(source_link(path, 1, None, &options).unwrap().is_none());
    }
    for url in [
        "javascript:alert(1)",
        "https://user:pass@example.com/repo",
        "https:///repo",
        "https://example.com/repo?q=x",
        "https://example.com/repo#fragment",
        "https://example.com/a/../b",
    ] {
        assert!(
            source_link(
                "src/a.py",
                1,
                None,
                &RenderOptions {
                    repository_url: Some(url.into()),
                    commit_ref: Some("main".into()),
                    ..Default::default()
                }
            )
            .is_err(),
            "{url}"
        );
    }
    assert!(
        source_link(
            "src/a.py",
            1,
            None,
            &RenderOptions {
                repository_url: Some("https://example.com/repo".into()),
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        source_link(
            "src/a.py",
            1,
            None,
            &RenderOptions {
                repository_url: Some("https://example.com/team/repo)[x]".into()),
                commit_ref: Some("main".into()),
                ..Default::default()
            }
        )
        .unwrap()
        .unwrap()
        .contains("repo%29%5Bx%5D/blob/")
    );
}
#[test]
fn json_output_redaction_does_not_modify_lossless_archive_reader() {
    let mut value: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    value["producer_extension"] =
        json!({"api_key":"never-output-this","nested":"password=also-private"});
    let stored = StoredResult::from_value(value.clone()).unwrap();
    let output = render_json(&stored).unwrap();
    assert!(!output.contains("never-output-this"));
    assert!(!output.contains("also-private"));
    assert!(output.contains("***REDACTED***"));
    assert_eq!(stored.to_value().unwrap(), value);
}
#[test]
fn html_editor_context_is_explicit_escaped_and_kept_out_of_the_saved_result() {
    let mut value: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    value["results"][0]["findings"][0]["primary_location"]["path"] = json!("src/café's [x].py");
    let stored = StoredResult::from_value(value.clone()).unwrap();
    let options = RenderOptions {
        source_root: Some("/checkout/quoted '</script> folder".into()),
        repository_url: Some("https://example.com/team/repo".into()),
        commit_ref: Some("abc123".into()),
        ..Default::default()
    };
    let html = render_html(&stored, &options).unwrap();
    assert!(html.contains("id=editor-select"));
    assert!(html.contains("id=ici-source-navigation"));
    assert!(html.contains("vscode"));
    assert!(html.contains("idea://open?file="));
    assert!(html.contains("subl://"));
    assert!(html.contains("data-path=\"src/café&#39;s [x].py\""));
    let config = html
        .split("<script id=ici-source-navigation type=application/json>")
        .nth(1)
        .unwrap()
        .split("</script>")
        .next()
        .unwrap();
    let config: Value = serde_json::from_str(config).unwrap();
    assert_eq!(config["root"], options.source_root.clone().unwrap());
    assert!(!html.contains("/checkout/quoted '</script>"));
    assert_eq!(stored.to_value().unwrap(), value);
    let markdown = render_markdown(&stored, &options).unwrap();
    assert!(
        markdown.contains(
            "https://example.com/team/repo/blob/abc123/src/caf%C3%A9%27s%20%5Bx%5D.py#L12"
        )
    );
    assert!(
        render_html(
            &stored,
            &RenderOptions {
                source_root: Some("relative/root".into()),
                ..Default::default()
            }
        )
        .is_err()
    );
}
