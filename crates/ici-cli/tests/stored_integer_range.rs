//! Root ruling 2026-11-12 (reader facet, (b)): a stored report carrying an integer outside
//! -9223372036854775808..18446744073709551615 is refused as an invalid stored report (exit 2)
//! before any output, publish or baseline use. Earlier native rounded such integers through `f64`
//! into the HTML payload (ld 99999999999999999999 became 1e+20; before-evidence
//! /tmp/ici-rust-replacement-20261028/w-cpp27/reader-bigint/check.txt). The report is the frozen
//! v0.13.0 capture `tests/rust-parity/reporting/bigint-verify-report.json` (w-cpp27/frozen-bigint),
//! whose version strings also carry the same digits inside quotes.
use ici_cli::{commands, publish, registry};
use std::{collections::BTreeMap, fs, path::Path};

const REPORT: &str = include_str!("../../../tests/rust-parity/reporting/bigint-verify-report.json");
const RANGE: &str = "outside the supported range -9223372036854775808..18446744073709551615";

fn workspace() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join(".git")).unwrap();
    fs::write(root.path().join("main.py"), "def useful():\n    return 1\n").unwrap();
    root
}
fn refusal(args: &[&str], root: &Path, environment: &BTreeMap<String, String>) -> String {
    // A refusal after the root and scope announcement is an exit 2 output, before that an error.
    let line = match commands::dispatch(
        &registry::parse(args.iter().copied()).unwrap(),
        root,
        environment,
    ) {
        Err(error) => commands::diagnostic(&error),
        Ok(output) => {
            assert_eq!(output.exit_code, 2, "{args:?}: {}", output.stdout);
            output.stderr
        }
    };
    assert!(line.contains(RANGE), "{args:?}: {line}");
    assert!(line.contains("byte offset "), "{line}");
    assert!(
        !line.contains("99999999999999999999") && !line.contains("18446744073709551616"),
        "{line}"
    );
    line
}

#[test]
fn report_re_output_refuses_first_and_leaves_sources_and_outputs_untouched() {
    let root = workspace();
    fs::write(root.path().join("report.json"), REPORT).unwrap();
    for (args, output) in [
        (
            vec![
                "ici",
                "report",
                "--result",
                "report.json",
                "--out",
                "page.html",
            ],
            "page.html",
        ),
        (
            vec![
                "ici",
                "next",
                "report",
                "--result",
                "report.json",
                "--sarif",
                "run.sarif",
            ],
            "run.sarif",
        ),
    ] {
        fs::write(root.path().join(output), "sentinel").unwrap();
        let line = refusal(&args, root.path(), &BTreeMap::new());
        assert!(
            line.contains("is not a result this version can read"),
            "{line}"
        );
        assert_eq!(
            fs::read_to_string(root.path().join(output)).unwrap(),
            "sentinel"
        );
        assert_eq!(
            fs::read_to_string(root.path().join("report.json")).unwrap(),
            REPORT
        );
    }
    assert!(!root.path().join(ici_cli::DEFAULT_PAGE).exists());
}

#[test]
fn a_baseline_with_an_unrepresentable_integer_is_a_refusal_never_no_baseline() {
    let root = workspace();
    let mut config = "schema_version=2\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            check.id == "python.line"
        ));
    }
    config.push_str(
        "[[components]]\nid='app'\nroot='.'\nlanguages=['python']\nsources=['main.py']\n",
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    fs::write(root.path().join("base.json"), REPORT).unwrap();
    let line = refusal(
        &["ici", "next", "verify", "--baseline", "base.json"],
        root.path(),
        &BTreeMap::new(),
    );
    assert!(line.contains("base.json"), "{line}");
    assert!(!root.path().join(".ici/rust-v1/result.json").exists());
    assert_eq!(
        fs::read_to_string(root.path().join("base.json")).unwrap(),
        REPORT
    );
}

#[test]
fn publish_refuses_before_any_request_or_write() {
    // next publish: the stored result is read before the page and the environment.
    let root = workspace();
    fs::create_dir_all(root.path().join(".ici/rust-v1")).unwrap();
    fs::write(root.path().join(".ici/rust-v1/result.json"), REPORT).unwrap();
    fs::write(root.path().join(".ici/rust-v1/result.html"), "page").unwrap();
    let line = refusal(&["ici", "next", "publish"], root.path(), &BTreeMap::new());
    assert!(line.starts_with("publish: "), "{line}");
    // Legacy `ici publish` inside Actions, through a local transport stub: no request is made.
    let root = workspace();
    let base = root.path().canonicalize().unwrap();
    fs::write(base.join("verify_report.json"), REPORT).unwrap();
    fs::write(base.join("verify_report.html"), "<!doctype html>saved").unwrap();
    let environment = BTreeMap::from([
        ("GITHUB_ACTIONS".to_owned(), "true".to_owned()),
        ("GITHUB_REPOSITORY".to_owned(), "team/repo".to_owned()),
        ("GITHUB_TOKEN".to_owned(), "t".to_owned()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let mut requests = 0;
    let outcome = publish::command_with_transport(
        &registry::parse(["ici", "publish"]).unwrap(),
        &base,
        &environment,
        &mut |_| {
            requests += 1;
            Err(ici_distribution::transport::TransportError::Transport)
        },
    );
    let text = match outcome {
        Err(error) => commands::diagnostic(&error),
        Ok(output) => output.stdout + &output.stderr,
    };
    assert!(text.contains(RANGE), "{text}");
    assert_eq!(requests, 0, "{text}");
    assert_eq!(
        fs::read_to_string(base.join("verify_report.json")).unwrap(),
        REPORT
    );
}

const SUITE: &str = r#"{"schema_version":"ici.result/v3","suite_status":"PASS","tem_score":null,"max_tem_score":5.0,"results":[{"engine_name":"line","status":"PASS","summary":"ok"}],"baseline_comparison":null}"#;

/// Legacy `ici publish --report-dir`: a row without a page reads its suite only to enrich the
/// comment. A suite with an integer the reader cannot keep is refused before any request; a
/// malformed suite stays frozen's empty row and publication goes on (control).
#[test]
fn a_page_less_report_dir_suite_is_refused_for_range_but_stays_an_empty_row_when_malformed() {
    let environment = BTreeMap::from([
        ("GITHUB_ACTIONS".to_owned(), "true".to_owned()),
        ("GITHUB_REPOSITORY".to_owned(), "team/repo".to_owned()),
        ("GITHUB_TOKEN".to_owned(), "t".to_owned()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    for (suite, refused) in [(REPORT, true), ("{bad", false)] {
        let root = workspace();
        let base = root.path().canonicalize().unwrap();
        fs::create_dir_all(base.join("a")).unwrap();
        fs::create_dir_all(base.join("b")).unwrap();
        fs::write(base.join("a/verify_report.json"), suite).unwrap();
        fs::write(base.join("b/verify_report.json"), SUITE).unwrap();
        fs::write(base.join("b/verify_report.html"), "<!doctype html>saved").unwrap();
        let mut requests = 0;
        let outcome = publish::command_with_transport(
            &registry::parse(["ici", "publish", "--report-dir", "a", "--report-dir", "b"]).unwrap(),
            &base,
            &environment,
            &mut |_| {
                requests += 1;
                Err(ici_distribution::transport::TransportError::Transport)
            },
        );
        let text = match outcome {
            Err(error) => commands::diagnostic(&error),
            Ok(output) => output.stdout + &output.stderr,
        };
        assert_eq!(text.contains(RANGE), refused, "{text}");
        if refused {
            assert_eq!(requests, 0, "{text}");
            assert!(text.contains("a/verify_report.json"), "{text}");
        } else {
            assert!(requests > 0, "{text}");
        }
        assert_eq!(
            fs::read_to_string(base.join("a/verify_report.json")).unwrap(),
            suite
        );
        assert_eq!(
            fs::read_to_string(base.join("b/verify_report.json")).unwrap(),
            SUITE
        );
        assert_eq!(
            fs::read_to_string(base.join("b/verify_report.html")).unwrap(),
            "<!doctype html>saved"
        );
    }
}

/// The loader seam: what is checked is what is parsed. `load_bytes` judges the bytes it is given,
/// not a later read of the file, and the report-dir row reads its suite once.
#[test]
fn the_checked_bytes_are_the_parsed_bytes() {
    let root = workspace();
    let path = root.path().join("verify_report.json");
    fs::write(&path, SUITE).unwrap();
    assert!(matches!(
        ici_cli::report::load_bytes(root.path(), &path, REPORT.as_bytes()),
        Err(ici_cli::report::LoadFailure::Range(_))
    ));
    fs::write(&path, REPORT).unwrap();
    assert!(ici_cli::report::load_bytes(root.path(), &path, SUITE.as_bytes()).is_ok());
    assert!(matches!(
        ici_cli::report::load_bytes(root.path(), &path, b"{bad"),
        Err(ici_cli::report::LoadFailure::Other(_))
    ));
    assert!(publish::suite_row(root.path(), &path).is_err());
    fs::write(&path, "{bad").unwrap();
    assert!(publish::suite_row(root.path(), &path).unwrap().is_none());
    fs::remove_file(&path).unwrap();
    assert!(publish::suite_row(root.path(), &path).unwrap().is_none());
}
