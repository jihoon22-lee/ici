//! Historical `ici publish` sticky comment against the frozen `engines/publish.py` bodies
//! captured by `tests/rust-parity/reporting/capture_publish_comment.py`.
use ici_cli::publish::{
    self, LegacyComment, Page, Publication,
    api::Target,
    legacy_comment::{self, Report},
};
use ici_distribution::transport::{Request, Response, TransportError};
use serde_json::{Value, json};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/publish-comment-oracle.json"
    ))
    .unwrap()
}

#[test]
fn legacy_comment_bodies_equal_the_frozen_single_and_multi_project_layouts() {
    let oracle = oracle();
    let suite = |name: &str| legacy_comment::suite(&oracle["suites"][name]);
    let run_url = Some("https://github.example/team/repo/actions/runs/9");
    let single = &oracle["single"];
    assert_eq!(
        legacy_comment::single(
            suite("warn-tem").as_ref(),
            Some("https://pages.example/a/index.html"),
            true,
            "self",
            "pr/7/index.html",
            run_url,
        ),
        single["viewer"].as_str().unwrap()
    );
    assert_eq!(
        legacy_comment::single(
            suite("pass-no-tem").as_ref(),
            None,
            false,
            "hub",
            "proj/pr/7/index.html",
            None,
        ),
        single["no-pages"].as_str().unwrap()
    );
    assert_eq!(
        legacy_comment::single(
            None,
            Some("https://pages.example/b/index.html"),
            true,
            "self",
            "index.html",
            None,
        ),
        single["no-suite"].as_str().unwrap()
    );
    let reports = || {
        vec![
            Report {
                label: "api",
                suite: suite("warn-tem"),
                remote_path: "api/pr/7/index.html".into(),
                viewer: Some("https://pages.example/api".into()),
            },
            Report {
                label: "web",
                suite: suite("pass-no-tem"),
                remote_path: "web/pr/7/index.html".into(),
                viewer: None,
            },
            Report {
                label: "docs",
                suite: None,
                remote_path: "docs/pr/7/index.html".into(),
                viewer: None,
            },
        ]
    };
    assert_eq!(
        legacy_comment::multi(&reports(), "self", run_url, true),
        oracle["multi"]["pages"].as_str().unwrap()
    );
    assert_eq!(
        legacy_comment::multi(&reports()[1..], "hub", None, false),
        oracle["multi"]["no-pages"].as_str().unwrap()
    );
}

#[test]
fn unreadable_suites_and_inconsistent_baselines_render_like_the_frozen_loader() {
    let oracle = oracle();
    let cases = oracle["negative"].as_object().unwrap();
    // 7 loader cases plus 16 frozen parse_baseline_summary faults (15 dropped, 1 escaped).
    assert_eq!(cases.len(), 23);
    for (name, expected) in cases
        .iter()
        .filter(|(name, _)| name.starts_with("baseline-"))
    {
        let kept = expected.as_str().unwrap().contains("Baseline delta");
        assert_eq!(
            kept,
            name.as_str() == "baseline-multiline-warning",
            "{name}"
        );
    }
    for (name, expected) in cases {
        let suite = legacy_comment::suite(&oracle["negative_suites"][name]);
        assert_eq!(
            legacy_comment::single(suite.as_ref(), None, true, "self", "index.html", None),
            expected.as_str().unwrap(),
            "{name}"
        );
    }
    // Refused documents produce no suite at all, never a favourable default.
    for name in [
        "unknown-engine-status",
        "null-engine-status",
        "unknown-schema",
        "bad-duration",
    ] {
        assert!(
            legacy_comment::suite(&oracle["negative_suites"][name]).is_none(),
            "{name}"
        );
    }
}

fn response(status: u16, value: Value) -> std::result::Result<Response, TransportError> {
    Ok(Response {
        status,
        headers: Default::default(),
        bytes: serde_json::to_vec(&value).unwrap(),
    })
}

#[test]
fn historical_publish_posts_the_frozen_comment_with_the_native_run_marker() {
    let oracle = oracle();
    let document = serde_json::to_vec(&oracle["suites"]["warn-tem"]).unwrap();
    let publication = Publication {
        destination: Target {
            api_url: "https://git.example/api/v3".into(),
            repo: "team/repo".into(),
            branch: "gh-pages".into(),
            token: "private-token-value".into(),
        },
        comments: None,
        pages: vec![Page {
            label: "workspace".into(),
            bytes: b"<!doctype html>report".to_vec(),
            result: Some(ici_reporting::StoredResult::from_bytes(&document).unwrap()),
            result_digest: Some(ici_domain::sha256(&document)),
        }],
        head: "a".repeat(40),
        pr: Some(7),
        run: 9,
        attempt: 1,
        reference: "main".into(),
        marker: legacy_comment::MARKER.into(),
        run_url: Some("https://github.example/team/repo/actions/runs/9".into()),
        legacy: Some(LegacyComment {
            unlabeled: true,
            mode: "self".into(),
            missing: vec![],
        }),
    };
    let mut posted = None;
    let record = publish::publish_with_transport(&publication, &mut |request: &Request| {
        let url = &request.url;
        if url.ends_with("/pulls/7") {
            return response(200, json!({"head":{"sha":"a".repeat(40)}}));
        }
        if url.contains("/issues/7/comments") {
            if request.method == "GET" {
                return response(200, json!([]));
            }
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            posted = body["body"].as_str().map(str::to_owned);
            return response(201, json!({"html_url":"https://git.example/comment"}));
        }
        if url.ends_with("/repos/team/repo") {
            return response(200, json!({"default_branch":"main"}));
        }
        if url.contains("/git/ref/") {
            return response(200, json!({"object":{"sha":"a".repeat(40)}}));
        }
        if url.ends_with("/pages") {
            return response(404, json!({}));
        }
        if url.contains("/contents/") {
            return if request.method == "GET" {
                response(404, json!({}))
            } else {
                response(201, json!({}))
            };
        }
        panic!("unexpected endpoint {} {url}", request.method)
    });
    assert_eq!(record["state"], "SUCCESS", "{record}");
    let path = "ici/workspace/pr-7/aaaaaaaaaaaa/index.html";
    assert_eq!(record["remote_path"], path);
    let expected = legacy_comment::single(
        legacy_comment::suite(&oracle["suites"]["warn-tem"]).as_ref(),
        None,
        false,
        "self",
        path,
        Some("https://github.example/team/repo/actions/runs/9"),
    );
    let posted = posted.expect("comment posted");
    assert_eq!(
        posted,
        format!("{expected}\n<!-- head:{} run:9.1 -->", "a".repeat(40))
    );
    // The frozen gh-pages notice, TEM header and stats table, not the native summary.
    assert!(posted.starts_with(
        "<!-- ici-report -->\n## ⚠️ ici Quality Gate — **`WARN`** · TEM **`3.46/5.0`**\n\n> 📦"
    ));
    assert!(posted.contains("| 1 | 1 | 1 | 1 | 1 | `3.46/5.0` |"));
    assert!(!posted.contains("## ici verification report"));
}
