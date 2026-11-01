//! Historical `ici publish` and `ici verify --publish` outcomes against frozen v0.13.0
//! (`tests/rust-parity/reporting/legacy-publish-oracle.json`, `capture_legacy_publish.py`):
//! the Actions and target skips exit 0, a missing page, upload or comment failure exits 1 with
//! the frozen message, and `verify --publish` never changes the verify exit.
use ici_cli::{commands, publish, registry};
use ici_distribution::transport::{Request, Response, TransportError};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const HEAD: &str = "abcdef1234567890";
const SITE: &str = "https://team.github.io/repo";
const COMMENT: &str = "https://github.com/team/repo/pull/7#issuecomment-1";

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-publish-oracle.json"
    ))
    .unwrap()
}

fn response(status: u16, value: Value) -> std::result::Result<Response, TransportError> {
    Ok(Response {
        status,
        headers: BTreeMap::new(),
        bytes: serde_json::to_vec(&value).unwrap(),
    })
}

/// The frozen stubs of each case (capture_legacy_publish.CASES): branch, comment, pages.
fn stubs(name: &str) -> (bool, bool, bool) {
    match name {
        "upload_failure" => (false, true, false),
        "comment_failure" => (true, false, false),
        "published_with_comment" => (true, true, true),
        _ => (true, true, false),
    }
}

fn environment(name: &str, base: &Path) -> BTreeMap<String, String> {
    let mut values = BTreeMap::from([
        ("GITHUB_ACTIONS".to_owned(), "true".to_owned()),
        ("GITHUB_REPOSITORY".to_owned(), "team/repo".to_owned()),
        ("GITHUB_TOKEN".to_owned(), "t".to_owned()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), base.join("home").display().to_string()),
    ]);
    match name {
        "outside_actions" => drop(values.remove("GITHUB_ACTIONS")),
        "no_repository" => drop(values.remove("GITHUB_REPOSITORY")),
        "no_token" => drop(values.remove("GITHUB_TOKEN")),
        "comment_failure" | "published_with_comment" => {
            let event = base.join("event.json");
            fs::write(
                &event,
                json!({"pull_request":{"number":7,"head":{"sha":HEAD}}}).to_string(),
            )
            .unwrap();
            values.insert("GITHUB_EVENT_PATH".into(), event.display().to_string());
        }
        _ => {}
    }
    values
}

fn project(base: &Path, html: bool) {
    fs::create_dir_all(base.join(".git")).unwrap();
    fs::write(base.join("main.py"), "def hello():\n    return 1\n").unwrap();
    let suite = json!({"schema_version":"ici.result/v3","suite_status":"PASS","tem_score":null,
        "max_tem_score":5.0,"results":[{"engine_name":"line","status":"PASS","summary":"ok"}],
        "baseline_comparison":null});
    fs::write(base.join("verify_report.json"), suite.to_string()).unwrap();
    if html {
        fs::write(base.join("verify_report.html"), "<!doctype html>saved").unwrap();
    }
}

#[test]
fn legacy_publish_matches_frozen_skips_failures_and_messages() {
    let oracle = oracle();
    let cases = oracle["publish"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, frozen) in cases {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        project(&base, name != "html_missing");
        let environment = environment(name, &base);
        let (branch_ok, comment_ok, pages) = stubs(name);
        let mut calls = 0;
        let output = publish::command_with_transport(
            &registry::parse(["ici", "publish"]).unwrap(),
            &base,
            &environment,
            &mut |request: &Request| {
                calls += 1;
                let url = request.url.as_str();
                if url.contains("/git/ref/") {
                    return response(if branch_ok { 200 } else { 403 }, json!({}));
                }
                if url.ends_with("/pages") {
                    return if pages {
                        response(200, json!({"html_url": SITE}))
                    } else {
                        response(404, json!({}))
                    };
                }
                if url.contains("/pulls/7") {
                    return response(200, json!({"head":{"sha":HEAD}}));
                }
                if url.contains("/issues/7/comments") && request.method == "GET" {
                    return response(200, json!([]));
                }
                if url.contains("/issues/") {
                    return if comment_ok {
                        response(201, json!({"html_url": COMMENT}))
                    } else {
                        response(403, json!({}))
                    };
                }
                if request.method == "GET" {
                    return response(404, json!({}));
                }
                response(201, json!({}))
            },
        )
        .unwrap();
        assert_eq!(
            output.exit_code,
            frozen["exit_code"].as_i64().unwrap() as i32,
            "{name}: {}",
            output.stdout
        );
        // The frozen gh-pages layout (`index.html`, `pr/7/index.html`) and viewer URL are
        // reproduced exactly, so the frozen stdout is compared byte for byte.
        assert_eq!(output.stdout, frozen["stdout"].as_str().unwrap(), "{name}");
        let record = base.join(".ici/rust-v1/publish.json");
        assert!(output.stderr.is_empty(), "{name}: {}", output.stderr);
        // Skips make no request and write no record, as frozen leaves nothing behind.
        if matches!(
            name.as_str(),
            "outside_actions" | "no_repository" | "no_token" | "html_missing"
        ) {
            assert_eq!(calls, 0, "{name}");
            assert!(!record.exists(), "{name}");
        }
    }
}

/// `verify --publish`: the frozen verify exit is the run's own in every publication outcome.
#[test]
fn legacy_verify_publish_never_changes_the_run_exit() {
    let oracle = oracle();
    for (name, frozen) in oracle["verify"].as_object().unwrap() {
        assert_eq!(frozen["exit_code"], frozen["run_exit_code"], "{name}");
    }
    // Native: the skips and an unreachable API (an upload failure) keep the plain verify exit.
    for (name, expected) in [
        ("outside_actions", "requires a GitHub Actions environment"),
        (
            "no_repository",
            "GITHUB_REPOSITORY or a publish token is unavailable",
        ),
        (
            "no_token",
            "GITHUB_REPOSITORY or a publish token is unavailable",
        ),
        ("upload_failure", "--publish failed to upload "),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        project(&base, false);
        let mut environment = environment(name, &base);
        // Port 9 on loopback refuses the connection; nothing leaves the machine.
        environment.insert("GITHUB_API_URL".into(), "https://127.0.0.1:9".into());
        let plain = commands::dispatch(
            &registry::parse(["ici", "verify", "--html", "verify_report.html"]).unwrap(),
            &base,
            &environment,
        )
        .unwrap();
        let output = commands::dispatch(
            &registry::parse(["ici", "verify", "--html", "verify_report.html", "--publish"])
                .unwrap(),
            &base,
            &environment,
        )
        .unwrap();
        assert_eq!(
            output.exit_code, plain.exit_code,
            "{name}: {}",
            output.stdout
        );
        let line = output
            .stdout
            .lines()
            .find(|line| line.starts_with("[publish] "))
            .unwrap_or_else(|| panic!("{name}: {}", output.stdout));
        assert!(line.contains(expected), "{name}: {line}");
        let frozen_lines = oracle["verify"][name]["publish_lines"].as_array().unwrap();
        if name != "upload_failure" {
            assert_eq!(line, frozen_lines[0].as_str().unwrap(), "{name}");
        }
    }
}

/// `ici next publish` keeps frozen's NOT_CONFIGURED exit 2 (frozen cli/next_path.cmd_publish).
#[test]
fn next_publish_without_destination_keeps_the_frozen_exit() {
    let frozen = &oracle()["next_publish"]["not_configured"];
    assert_eq!(frozen["exit_code"], 2);
    assert_eq!(frozen["record"]["state"], "NOT_CONFIGURED");
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(
        base.join("saved.json"),
        include_bytes!("../../../tests/fixtures/ici-next/run-code-fail.json"),
    )
    .unwrap();
    fs::write(base.join("saved.html"), "<!doctype html>saved").unwrap();
    let output = commands::dispatch(
        &registry::parse([
            "ici",
            "next",
            "publish",
            "--result",
            "saved.json",
            "--out",
            "saved.html",
        ])
        .unwrap(),
        &base,
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(output.exit_code, 2);
}

/// The frozen layout cases (capture_legacy_publish.LAYOUT): self/hub mode, `main`/`master`,
/// `branch/<ref>`, `pr/<n>`, raw `--report-dir` labels, a missing page and a failed branch.
/// Uploads (repository and path), the `[publish]` lines and every sticky comment body are equal.
#[test]
fn legacy_layout_paths_viewer_urls_and_comments_match_frozen() {
    let oracle = oracle();
    let cases = oracle["layout"].as_object().unwrap();
    assert_eq!(cases.len(), 13);
    let mut compared = 0;
    for (name, frozen) in cases {
        let argv: Vec<&str> = frozen["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap())
            .collect();
        if argv[0] != "publish" {
            continue;
        }
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        project(&base, true);
        for label in ["a", "b", "c"] {
            fs::create_dir_all(base.join(label)).unwrap();
            fs::copy(
                base.join("verify_report.json"),
                base.join(label).join("verify_report.json"),
            )
            .unwrap();
            if label != "c" {
                fs::write(
                    base.join(label).join("verify_report.html"),
                    "<!doctype html>saved",
                )
                .unwrap();
            }
        }
        let mut environment = layout_environment(name, &base);
        environment.insert("GITHUB_API_URL".into(), "https://api.fixture".into());
        // capture_legacy_publish.LAYOUT: Pages is off in exactly these cases.
        let pages = !matches!(
            name.as_str(),
            "self_master" | "hub_publish_branch" | "hub_report_dirs"
        );
        let branch_ok = name != "self_pr_upload_failure";
        let mut uploads = vec![];
        let mut comments = vec![];
        let output = publish::command_with_transport(
            &registry::parse(std::iter::once("ici").chain(argv.iter().copied())).unwrap(),
            &base,
            &environment,
            &mut |request: &Request| {
                let url = request.url.strip_prefix("https://api.fixture/repos/").unwrap();
                let (owner, rest) = url.split_once('/').unwrap();
                let (repository, path) = rest.split_once('/').unwrap_or((rest, ""));
                let repo = format!("{owner}/{repository}");
                if path.starts_with("git/ref/") {
                    return response(if branch_ok { 200 } else { 403 }, json!({}));
                }
                if path == "pages" {
                    return if pages {
                        response(200, json!({"html_url": SITE}))
                    } else {
                        response(404, json!({}))
                    };
                }
                if let Some(content) = path.strip_prefix("contents/") {
                    if request.method == "GET" {
                        return response(404, json!({}));
                    }
                    uploads.push(json!([repo, percent_decode(content)]));
                    return response(201, json!({}));
                }
                if path.starts_with("issues/7/comments") && request.method == "GET" {
                    return response(200, json!([]));
                }
                if path == "issues/7/comments" && request.method == "POST" {
                    let body: Value = serde_json::from_slice(&request.body).unwrap();
                    comments.push(json!([repo, 7, body["body"]]));
                    return response(
                        201,
                        json!({"html_url": format!("https://github.com/{repo}/pull/7#issuecomment-1")}),
                    );
                }
                panic!("{name}: unexpected request {} {}", request.method, request.url)
            },
        )
        .unwrap();
        let lines: Vec<&str> = output.stdout.lines().collect();
        let mut expected_lines = frozen["publish_lines"].clone();
        if name == "self_no_ref_pages" {
            // Frozen defect not reproduced: a page at the branch root had the viewer URL
            // `<site>/index.html/` (`rsplit('/', 1)[0]` of a path without `/`), which does not
            // resolve; native names the site root.
            expected_lines = json!([expected_lines[0]
                .as_str()
                .unwrap()
                .replace("/repo/index.html/", "/repo/")]);
        }
        assert_eq!(json!(lines), expected_lines, "{name}: {}", output.stdout);
        assert_eq!(
            output.exit_code,
            frozen["exit_code"].as_i64().unwrap() as i32,
            "{name}"
        );
        let expected_uploads: Vec<Value> = frozen["uploads"]
            .as_array()
            .unwrap()
            .iter()
            .map(|upload| json!([upload["repo"], upload["path"]]))
            .collect();
        assert_eq!(uploads, expected_uploads, "{name}");
        let expected_comments: Vec<Value> = frozen["comments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|comment| json!([comment["repo"], comment["pr"], comment["body"]]))
            .collect();
        assert_eq!(comments, expected_comments, "{name}");
        compared += 1;
    }
    assert_eq!(compared, 11);
}

/// `verify --publish` files a hub report under the analyzed project's name (frozen
/// `ReportPublisher(project_name=...)`), and a self-mode `main` run publishes `main/index.html`.
/// The refused loopback API makes the upload fail, so the frozen message names the path.
#[test]
fn legacy_verify_publish_uses_the_project_name_and_the_frozen_layout() {
    let oracle = oracle();
    for name in ["hub_verify_project_name", "self_verify_main"] {
        let frozen = &oracle["layout"][name];
        let path = frozen["uploads"][0]["path"].as_str().unwrap();
        let repo = frozen["uploads"][0]["repo"].as_str().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        project(&base, false);
        if name == "hub_verify_project_name" {
            fs::write(
                base.join("pyproject.toml"),
                "[project]\nname = 'demo-app'\n",
            )
            .unwrap();
        }
        let mut environment = layout_environment(name, &base);
        environment.insert("GITHUB_API_URL".into(), "https://127.0.0.1:9".into());
        let output = commands::dispatch(
            &registry::parse(["ici", "verify", "--html", "verify_report.html", "--publish"])
                .unwrap(),
            &base,
            &environment,
        )
        .unwrap();
        let line = output
            .stdout
            .lines()
            .find(|line| line.starts_with("[publish] "))
            .unwrap_or_else(|| panic!("{name}: {}", output.stdout));
        assert_eq!(
            line,
            format!(
                "[publish] --publish failed to upload {path} to {repo} (gh-pages). Check token permissions (contents: write) and branch availability."
            ),
            "{name}"
        );
    }
}

fn layout_environment(name: &str, base: &Path) -> BTreeMap<String, String> {
    let frozen = &oracle()["layout"][name];
    let mut values = BTreeMap::from([
        ("GITHUB_ACTIONS".to_owned(), "true".to_owned()),
        ("GITHUB_REPOSITORY".to_owned(), "team/repo".to_owned()),
        ("GITHUB_TOKEN".to_owned(), "t".to_owned()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), base.join("home").display().to_string()),
    ]);
    let hub = name.starts_with("hub_");
    if hub {
        values.insert("ICI_PUBLISH_REPO".into(), "hub/pages".into());
        values.insert("ICI_PUBLISH_TOKEN".into(), "h".into());
    }
    let reference = match name {
        "self_main" | "self_pr_with_ref" | "hub_report_dirs" | "self_verify_main" => Some("main"),
        "self_master" => Some("master"),
        "self_branch" => Some("feature/x-Y"),
        "hub_publish_branch" => Some("release/1.0"),
        _ => None,
    };
    if let Some(reference) = reference {
        values.insert("GITHUB_REF_NAME".into(), reference.into());
    }
    if name == "self_pr_with_ref" {
        values.insert("GITHUB_SERVER_URL".into(), "https://gh.test".into());
        values.insert("GITHUB_RUN_ID".into(), "42".into());
    }
    let pr = frozen["comments"]
        .as_array()
        .is_some_and(|rows| !rows.is_empty())
        || frozen["publish_lines"]
            .as_array()
            .unwrap()
            .iter()
            .any(|line| line.as_str().unwrap().contains("/pr/7"));
    if pr {
        let event = base.join("event.json");
        // Frozen captured the event without a head; the historical caller needs none.
        fs::write(&event, json!({"pull_request":{"number":7}}).to_string()).unwrap();
        values.insert("GITHUB_EVENT_PATH".into(), event.display().to_string());
    }
    values
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = vec![];
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            out.push(u8::from_str_radix(&text[index + 1..index + 3], 16).unwrap());
            index += 3;
        } else {
            out.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(out).unwrap()
}
