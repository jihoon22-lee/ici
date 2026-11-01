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

/// Replace each side's remote path and viewer URL (the native branch layout is
/// `ici/<label>/<ref>/<revision>/index.html`, frozen's `[pr/<n>/]index.html`).
fn normalize(text: &str, path: &str, viewer: &str) -> String {
    text.replace(viewer, "<viewer>").replace(path, "<path>")
}

fn frozen_path(name: &str) -> &'static str {
    if name.contains("comment") {
        "pr/7/index.html"
    } else {
        "index.html"
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
        let record = base.join(".ici/rust-v1/publish.json");
        let native_path = if record.exists() {
            let value: Value = serde_json::from_slice(&fs::read(&record).unwrap()).unwrap();
            value["reports"][0]["remote_path"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_default()
        } else {
            String::new()
        };
        let native_path = if native_path.is_empty() && name == "upload_failure" {
            // The branch failed before any upload: the message still names the planned path.
            output.stdout[output.stdout.find("upload ").unwrap() + 7..]
                .split(' ')
                .next()
                .unwrap()
                .to_owned()
        } else {
            native_path
        };
        let native = if native_path.is_empty() {
            output.stdout.clone()
        } else {
            normalize(
                &output.stdout,
                &native_path,
                &format!("{SITE}/{native_path}"),
            )
        };
        let expected = normalize(
            frozen["stdout"].as_str().unwrap(),
            frozen_path(name),
            &format!("{SITE}/pr/7/"),
        );
        assert_eq!(native, expected, "{name}");
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
