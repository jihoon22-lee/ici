use ici_cli::{
    commands,
    publish::{self, Page, Publication, api::Target},
    registry,
};
use ici_distribution::transport::{Request, Response, TransportError};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

fn publication() -> Publication {
    Publication {
        destination: Target {
            api_url: "https://git.example/api/v3".into(),
            repo: "team/repo".into(),
            branch: "report/branch".into(),
            token: "private-token-value".into(),
        },
        comments: None,
        pages: vec![Page {
            label: "core project".into(),
            bytes: b"<!doctype html>report".to_vec(),
            result: Some(
                ici_reporting::StoredResult::from_bytes(include_bytes!(
                    "../../../tests/fixtures/ici-next/run-code-fail.json"
                ))
                .unwrap(),
            ),
            result_digest: Some(ici_domain::sha256("stored input")),
        }],
        head: "a".repeat(40),
        pr: Some(7),
        run: 9,
        attempt: 1,
        reference: "main".into(),
        marker: "<!-- ici-next:core -->".into(),
        run_url: None,
    }
}
fn response(status: u16, value: Value) -> std::result::Result<Response, TransportError> {
    Ok(Response {
        status,
        headers: BTreeMap::new(),
        bytes: serde_json::to_vec(&value).unwrap(),
    })
}

#[test]
fn publication_retries_blob_conflict_bounds_large_summary_and_keeps_result_on_its_own_axis() {
    let mut publication = publication();
    let mut large = publication.pages[0]
        .result
        .as_ref()
        .unwrap()
        .to_value()
        .unwrap();
    large["findings"][0]["message"] = json!("界".repeat(40_000));
    publication.pages[0].result = Some(ici_reporting::StoredResult::from_value(large).unwrap());
    let before = publication.pages[0]
        .result
        .as_ref()
        .unwrap()
        .to_value()
        .unwrap();
    let mut puts = 0;
    let mut comments = 0;
    let mut heads = 0;
    let mut seen = vec![];
    let record = publish::publish_with_transport(&publication, &mut |request: &Request| {
        seen.push((request.method.clone(), request.url.clone()));
        assert_eq!(
            request.headers["Authorization"],
            "Bearer private-token-value"
        );
        assert!(
            request
                .url
                .starts_with("https://git.example/api/v3/repos/team/repo")
        );
        if request.url.contains("/git/ref/") {
            return response(200, json!({}));
        }
        if request.url.ends_with("/pages") {
            return response(404, json!({}));
        }
        if request.url.contains("/contents/") {
            if request.method == "GET" {
                assert!(request.url.ends_with("?ref=report%2Fbranch"));
                return response(200, json!({"sha":format!("blob{puts}")}));
            }
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            assert_eq!(body["sha"], format!("blob{puts}"));
            puts += 1;
            return response(if puts == 1 { 409 } else { 201 }, json!({}));
        }
        if request.url.ends_with("/pulls/7") {
            heads += 1;
            return response(200, json!({"head":{"sha":"a".repeat(40)}}));
        }
        if request.method == "GET" && request.url.contains("/comments?") {
            return response(
                200,
                json!([{"id":12,"body":"<!-- ici-next:core -->\n<!-- head:aaaaaaa run:8.1 -->"}]),
            );
        }
        if request.method == "PATCH" && request.url.ends_with("/issues/comments/12") {
            comments += 1;
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            assert!(body["body"].as_str().unwrap().contains("FAIL"));
            assert!(body["body"].as_str().unwrap().contains("run:9.1"));
            assert!(body["body"].as_str().unwrap().len() < 65536);
            assert!(body["body"].as_str().unwrap().contains("Summary truncated"));
            return response(
                200,
                json!({"html_url":"https://git.example/team/repo/issues/7#comment-12"}),
            );
        }
        panic!("unexpected {} {}", request.method, request.url)
    });
    assert_eq!(record["state"], "SUCCESS", "{record}");
    assert_eq!((puts, comments, heads), (2, 1, 2));
    assert_eq!(
        publication.pages[0]
            .result
            .as_ref()
            .unwrap()
            .to_value()
            .unwrap(),
        before
    );
    assert!(!record.to_string().contains("private-token-value"));
    assert!(
        record["remote_path"]
            .as_str()
            .unwrap()
            .contains("/aaaaaaaaaaaa/")
    );
}

#[test]
fn stale_newer_unknown_heads_and_failed_comment_reads_never_overwrite_current_comment() {
    for scenario in [
        "stale",
        "newer",
        "unknown",
        "comments-error",
        "head-changed",
    ] {
        let publication = publication();
        let mut heads = 0;
        let record = publish::publish_with_transport(&publication, &mut |request: &Request| {
            if request.url.contains("/git/ref/") {
                return response(200, json!({}));
            }
            if request.url.ends_with("/pages") {
                return response(200, json!({"html_url":"https://pages.example/repo"}));
            }
            if request.url.contains("/contents/") {
                return response(if request.method == "GET" { 404 } else { 201 }, json!({}));
            }
            if request.url.ends_with("/pulls/7") {
                heads += 1;
                if scenario == "unknown" {
                    return response(403, json!({"message":"sensitive response not echoed"}));
                }
                return response(
                    200,
                    json!({"head":{"sha":if scenario=="stale"||scenario=="head-changed"&&heads==2{"b".repeat(40)}else{"a".repeat(40)}}}),
                );
            }
            if request.method == "GET" && request.url.contains("/comments?") {
                if scenario == "comments-error" {
                    return response(500, json!({}));
                }
                return response(
                    200,
                    if scenario == "newer" {
                        json!([{"id":4,"body":"<!-- ici-next:core -->\n<!-- head:aaaaaaa run:10.1 -->"}])
                    } else {
                        json!([])
                    },
                );
            }
            panic!("must not write comment for {scenario}")
        });
        assert_eq!(
            record["state"],
            if matches!(scenario, "unknown" | "comments-error") {
                "FAILED"
            } else {
                "SUCCESS"
            },
            "{scenario}: {record}"
        );
        assert_eq!(record["reports"][0]["uploaded"], true);
        assert!(record["comment_url"].is_null());
    }
}

#[test]
fn invalid_destination_and_colliding_labels_make_no_requests() {
    for change in ["http", "credentials", "labels"] {
        let mut publication = publication();
        match change {
            "http" => publication.destination.api_url = "http://git.example".into(),
            "credentials" => publication.destination.api_url = "https://secret@git.example".into(),
            _ => publication.pages.push(Page {
                label: "core-project".into(),
                bytes: vec![],
                result: None,
                result_digest: None,
            }),
        }
        let record = publish::publish_with_transport(&publication, &mut |_| {
            panic!("invalid inputs must not make network requests")
        });
        assert_eq!(record["state"], "FAILED");
    }
}

#[test]
fn hub_multi_report_uses_hub_base_commit_and_one_source_repository_comment() {
    let mut publication = publication();
    publication.comments = Some(Target {
        api_url: publication.destination.api_url.clone(),
        repo: "team/source".into(),
        branch: "ignored".into(),
        token: "source-comment-token".into(),
    });
    publication.pages.push(Page {
        label: "second".into(),
        bytes: b"second report".to_vec(),
        result: None,
        result_digest: None,
    });
    let mut uploads = 0;
    let mut comments = 0;
    let record = publish::publish_with_transport(&publication, &mut |request: &Request| {
        let source = request.url.contains("/repos/team/source");
        assert_eq!(
            request.headers["Authorization"],
            if source {
                "Bearer source-comment-token"
            } else {
                "Bearer private-token-value"
            }
        );
        if source {
            if request.url.ends_with("/pulls/7") {
                return response(200, json!({"head":{"sha":"a".repeat(40)}}));
            }
            if request.method == "GET" {
                return response(200, json!([]));
            }
            comments += 1;
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            assert!(body["body"].as_str().unwrap().contains("core project"));
            assert!(body["body"].as_str().unwrap().contains("second"));
            return response(201, json!({"html_url":"https://git.example/comment"}));
        }
        if request.url.ends_with("/repos/team/repo") {
            return response(200, json!({"default_branch":"main"}));
        }
        if request.url.ends_with("/git/ref/heads%2Fmain") {
            return response(200, json!({"object":{"sha":"b".repeat(40)}}));
        }
        if request.url.contains("/git/ref/") {
            return response(404, json!({}));
        }
        if request.url.ends_with("/git/refs") {
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            assert_eq!(body["sha"], "b".repeat(40));
            return response(201, json!({}));
        }
        if request.url.ends_with("/pages") {
            return response(404, json!({}));
        }
        if request.url.contains("/contents/") {
            if request.method == "GET" {
                return response(404, json!({}));
            }
            uploads += 1;
            return response(201, json!({}));
        }
        panic!("unexpected endpoint")
    });
    assert_eq!(record["state"], "SUCCESS", "{record}");
    assert_eq!((uploads, comments), (2, 1));
    assert_eq!(record["reports"].as_array().unwrap().len(), 2);
}

#[test]
fn saved_publish_can_inspect_configuration_after_sources_are_deleted_and_preserves_result_bytes() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "schema_version=2\n[[components]]\nid='deleted'\nroot='removed'\nlanguages=['python']\n",
    )
    .unwrap();
    fs::write(
        root.path().join("saved.json"),
        include_bytes!("../../../tests/fixtures/ici-next/run-code-fail.json"),
    )
    .unwrap();
    fs::write(root.path().join("saved.html"), "<!doctype html>saved").unwrap();
    let before = fs::read(root.path().join("saved.json")).unwrap();
    let invocation = registry::parse([
        "ici",
        "next",
        "publish",
        "--result",
        "saved.json",
        "--out",
        "saved.html",
    ])
    .unwrap();
    let output = commands::dispatch(&invocation, root.path(), &BTreeMap::new()).unwrap();
    assert_eq!(output.exit_code, 2);
    let record: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/publish.json")).unwrap())
            .unwrap();
    assert_eq!(record["state"], "NOT_CONFIGURED");
    assert_eq!(fs::read(root.path().join("saved.json")).unwrap(), before);
    assert!(!root.path().join("removed").exists());
}
