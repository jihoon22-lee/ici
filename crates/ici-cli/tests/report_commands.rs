use ici_cli::{commands, execute, registry, report};
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs};

fn call(args: &[&str]) -> registry::Invocation {
    registry::parse(args.iter().copied()).unwrap()
}
fn fixture() -> tempfile::TempDir {
    let dir = bounded_tempdir();
    fs::write(dir.path().join("main.py"), "def useful():\n    return 1\n").unwrap();
    let mut text = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|c| c.language == "python")
    {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\n",
            check.id,
            check.id == "python.line"
        ));
    }
    fs::write(dir.path().join("ici.toml"), text).unwrap();
    dir
}
#[test]
fn saved_rich_report_survives_deleted_sources_and_no_tools() {
    let dir = fixture();
    let run = execute::execute(
        &call(&["ici", "analyze"]),
        dir.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let details = report::details_path(dir.path(), &run.result_path, &run.result.run_id).unwrap();
    let saved = ici_reporting::StoredResult::from_bytes(&fs::read(&details).unwrap()).unwrap();
    assert!(matches!(saved, ici_reporting::StoredResult::Legacy(_)));
    assert!(
        !saved.to_value().unwrap()["results"][0]["targets"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    fs::remove_file(dir.path().join("main.py")).unwrap();
    // Invalid configuration cannot be consulted when rendering a saved artifact.
    fs::write(dir.path().join("ici.toml"), "this is no longer TOML").unwrap();
    let rendered =
        commands::dispatch(&call(&["ici", "report"]), dir.path(), &BTreeMap::new()).unwrap();
    assert_eq!(rendered.exit_code, 0);
    let html = fs::read_to_string(dir.path().join(ici_cli::DEFAULT_PAGE)).unwrap();
    assert!(html.contains("main.py"));
    assert!(html.contains("connect-src 'none'"));
    let second = commands::dispatch(
        &call(&["ici", "next", "report", "--sarif", "findings.sarif"]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(second.exit_code, 0);
    let sarif: serde_json::Value =
        serde_json::from_slice(&fs::read(dir.path().join("findings.sarif")).unwrap()).unwrap();
    assert_eq!(sarif["version"], "2.1.0");
    assert_eq!(
        ici_execution::storage::read_result(&run.result_path).unwrap(),
        run.result
    );
}
#[test]
fn report_rejects_mismatched_details_input_aliases_and_user_files() {
    let dir = fixture();
    let run = execute::execute(
        &call(&["ici", "analyze"]),
        dir.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let details = report::details_path(dir.path(), &run.result_path, &run.result.run_id).unwrap();
    let original = fs::read(&details).unwrap();
    let mut value: serde_json::Value = serde_json::from_slice(&original).unwrap();
    value["native_run"]["run_id"] = "another-run".into();
    fs::write(&details, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(report::load(dir.path(), &run.result_path).is_err());
    fs::write(&details, &original).unwrap();
    fs::hard_link(&run.result_path, dir.path().join("alias.html")).unwrap();
    assert!(
        commands::dispatch(
            &call(&["ici", "report", "--out", "alias.html"]),
            dir.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    fs::write(dir.path().join("user.html"), "user original").unwrap();
    assert!(
        commands::dispatch(
            &call(&["ici", "report", "--out", "user.html"]),
            dir.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    assert_eq!(
        fs::read_to_string(dir.path().join("user.html")).unwrap(),
        "user original"
    );
    assert_eq!(
        ici_execution::storage::read_result(&run.result_path).unwrap(),
        run.result
    );
}
#[test]
fn imported_evidence_is_separate_and_never_changes_gate() {
    let dir = fixture();
    let run = execute::execute(
        &call(&["ici", "analyze"]),
        dir.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let original = fs::read(&run.result_path).unwrap();
    fs::write(dir.path().join("tests.xml"),"<testsuite name='external' tests='1'><testcase name='bad'><failure>foreign failure</failure></testcase></testsuite>").unwrap();
    let output = commands::dispatch(
        &call(&[
            "ici",
            "import-report",
            "tests.xml",
            "--format",
            "junit",
            "--scope",
            "external job",
        ]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert!(output.stdout.contains("gate not evaluated"));
    let imported_path = dir.path().join(".ici/rust-v1/auxiliary/report.json");
    let imported = fs::read(&imported_path).unwrap();
    assert!(
        commands::dispatch(
            &call(&[
                "ici",
                "import-report",
                "tests.xml",
                "--format",
                "junit",
                "--scope",
                "a later job"
            ]),
            dir.path(),
            &BTreeMap::new(),
        )
        .is_err()
    );
    assert_eq!(fs::read(&imported_path).unwrap(), imported);
    commands::dispatch(
        &call(&[
            "ici",
            "report",
            "--auxiliary",
            ".ici/rust-v1/auxiliary/report.json",
        ]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(fs::read(&run.result_path).unwrap(), original);
    assert!(
        fs::read_to_string(dir.path().join(ici_cli::DEFAULT_PAGE))
            .unwrap()
            .contains("foreign failure")
    );
    assert!(
        commands::dispatch(
            &call(&[
                "ici",
                "report",
                "--auxiliary",
                ".ici/rust-v1/auxiliary/report.json",
                "--sarif",
                "combined.sarif"
            ]),
            dir.path(),
            &BTreeMap::new()
        )
        .is_err()
    );
    assert!(!dir.path().join("combined.sarif").exists());
}

#[test]
fn ordinary_analyze_points_to_complete_offline_html_and_json_keeps_machine_output() {
    let dir = fixture();
    let output =
        commands::dispatch(&call(&["ici", "analyze"]), dir.path(), &BTreeMap::new()).unwrap();
    assert_eq!(output.exit_code, 0);
    // Frozen `verify_text` ends at `saved <result>`; the native page location follows it.
    let saved = dir.path().join(ici_cli::DEFAULT_RESULT);
    let page = dir.path().join(ici_cli::DEFAULT_PAGE);
    assert!(
        output.stdout.contains(&format!(
            "\nsaved {}\nHTML: {}\n",
            saved.display(),
            page.display()
        )),
        "{}",
        output.stdout
    );
    assert!(dir.path().join(ici_cli::DEFAULT_PAGE).is_file());
    let output = commands::dispatch(
        &call(&["ici", "analyze", "--json"]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    let json: serde_json::Value = serde_json::from_str(&output.stdout).unwrap();
    assert_eq!(json["schema_id"], "ici.next.run");
    // Under --json the root/scope announcement is a diagnostic on stderr.
    assert!(output.stderr.starts_with("root: "), "{}", output.stderr);
    assert_eq!(output.stderr.lines().count(), 2);
}

/// Root decision D4: frozen `next verify` (cli/next_path.cmd_verify) saves only the result and
/// its text ends at `saved <result>`; the page is `next report`'s job, so the native command
/// creates no file the user did not ask for.
#[test]
fn next_verify_text_writes_only_the_result_and_ends_at_the_saved_line() {
    let dir = fixture();
    let output = commands::dispatch(
        &call(&["ici", "next", "verify", "--result", "out/base.json"]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(output.exit_code, 0, "{}", output.stdout);
    let saved = dir.path().join("out/base.json");
    assert!(saved.is_file());
    assert!(
        output
            .stdout
            .ends_with(&format!("\nsaved {}\n", saved.display())),
        "{}",
        output.stdout
    );
    assert!(!output.stdout.contains("HTML: "), "{}", output.stdout);
    // The native details store (`out/.ici/`, the saved rich evidence `next report` renders) is
    // the R0 result namespace, outside this decision; no page is written anywhere.
    let mut written: Vec<_> = fs::read_dir(dir.path().join("out"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .filter(|name| name != ".ici")
        .collect();
    written.sort();
    assert_eq!(written, ["base.json"]);
    let mut pending = vec![dir.path().to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else {
                assert_ne!(
                    path.extension().and_then(|value| value.to_str()),
                    Some("html"),
                    "{}",
                    path.display()
                );
            }
        }
    }
    assert!(!dir.path().join(ici_cli::DEFAULT_PAGE).exists());
}

#[test]
fn historical_v2_reports_render_without_overwriting_or_inventing_native_evidence() {
    let dir = bounded_tempdir();
    let path = dir.path().join("old.json");
    let payload = serde_json::json!({
        "schema_version":"ici.result/v2", "suite_status":"FAIL", "results":[{
            "engine_name":"lint", "status":"FAIL", "summary":"historical failure",
            "targets":[{"file_path":"main.py","line_number":4,"status":"FAIL","message":"old diagnostic"}]
        }]
    });
    let original = serde_json::to_vec(&payload).unwrap();
    fs::write(&path, &original).unwrap();
    let stored = report::load(dir.path(), &path).unwrap();
    assert_eq!(stored.schema(), "ici.result/v3");
    assert_eq!(stored.to_value().unwrap()["suite_status"], "FAIL");
    assert!(stored.to_value().unwrap().get("native_run").is_none());
    let rendered = commands::dispatch(
        &call(&["ici", "report", "--result", "old.json", "--out", "old.html"]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(rendered.exit_code, 0);
    assert!(
        fs::read_to_string(dir.path().join("old.html"))
            .unwrap()
            .contains("historical failure")
    );
    assert_eq!(fs::read(&path).unwrap(), original);
    assert!(!dir.path().join(".ici").exists());
    for invalid in [
        serde_json::json!({"schema_version":"ici.result/v2","suite_status":"UNKNOWN","results":[]}),
        serde_json::json!({"schema_version":"ici.result/v2","suite_status":"FAIL","results":[null]}),
        serde_json::json!({"schema_version":"ici.result/v2","engine_name":"lint","status":"UNKNOWN"}),
        serde_json::json!({"schema_version":"ici.result/v2","engine_name":"lint","status":"PASS","native_run":{}}),
        serde_json::json!({"schema_version":"ici.result/v9","engine_name":"lint","status":"PASS"}),
    ] {
        fs::write(&path, serde_json::to_vec(&invalid).unwrap()).unwrap();
        assert!(report::load(dir.path(), &path).is_err(), "{invalid}");
    }
    fs::write(&path, br#"{"schema_version":"ici.result/v2","engine_name":"lint","status":"PASS","status":"FAIL"}"#).unwrap();
    assert!(report::load(dir.path(), &path).is_err());
}
/// Root decision E2: frozen wrote only the result beside an explicit `--result`
/// (tests/rust-parity/runtime/result-placement-oracle.json), so native keeps its presentation
/// archive under the project's `.ici/rust-v1/details/` and never creates `.ici` beside a user
/// path, inside or outside the project; `ici report` still finds the archive there.
#[test]
fn explicit_result_paths_gain_no_ici_directory_beside_them() {
    let oracle: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/runtime/result-placement-oracle.json"
    ))
    .unwrap();
    let cases = &oracle["cases"];
    assert_eq!(
        cases["analyze/relative-result"]["project_created"],
        serde_json::json!(["out/r.json"])
    );
    assert_eq!(
        cases["analyze/outside-result"]["outside_created"],
        serde_json::json!(["r.json"])
    );
    assert_eq!(
        cases["analyze/outside-result"]["project_created"],
        serde_json::json!([])
    );
    let dir = fixture();
    let outside = tempfile::tempdir().unwrap();
    let outside_result = outside.path().join("r.json");
    for result in ["out/r.json", outside_result.to_str().unwrap()] {
        let run = execute::execute(
            &call(&["ici", "analyze", "--result", result]),
            dir.path(),
            &BTreeMap::new(),
            true,
            &Cancellation::default(),
        )
        .unwrap();
        let details =
            report::details_path(dir.path(), &run.result_path, &run.result.run_id).unwrap();
        assert!(details.starts_with(dir.path().join(".ici/rust-v1/details/r.json")));
        assert!(details.is_file());
        assert!(matches!(
            report::load(dir.path(), &run.result_path).unwrap(),
            ici_reporting::StoredResult::Legacy(_)
        ));
    }
    assert!(!dir.path().join("out/.ici").exists());
    assert!(!dir.path().join("out/runs").exists());
    let written: Vec<_> = fs::read_dir(outside.path())
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(written, vec![std::ffi::OsString::from("r.json")]);
    // `ici report` over the explicit result renders the saved details (targets), not only the run.
    let rendered = commands::dispatch(
        &call(&[
            "ici",
            "report",
            "--result",
            "out/r.json",
            "--out",
            "out/page.html",
        ]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    assert_eq!(rendered.exit_code, 0, "{}", rendered.stderr);
    assert!(
        fs::read_to_string(dir.path().join("out/page.html"))
            .unwrap()
            .contains("main.py")
    );
    assert!(!dir.path().join("out/.ici").exists());
}
/// Archives that earlier native builds wrote beside an explicit result stay readable (and a
/// mismatched one is still refused); nothing is moved.
#[test]
fn details_written_beside_an_explicit_result_by_earlier_builds_stay_readable() {
    let dir = fixture();
    let run = execute::execute(
        &call(&["ici", "analyze", "--result", "out/r.json"]),
        dir.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let details = report::details_path(dir.path(), &run.result_path, &run.result.run_id).unwrap();
    let beside = dir
        .path()
        .join("out/.ici/rust-v1/details/r.json")
        .join(&run.result.run_id)
        .join("details.json");
    fs::create_dir_all(beside.parent().unwrap()).unwrap();
    fs::rename(&details, &beside).unwrap();
    assert!(matches!(
        report::load(dir.path(), &run.result_path).unwrap(),
        ici_reporting::StoredResult::Legacy(_)
    ));
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&beside).unwrap()).unwrap();
    value["native_run"]["run_id"] = "another-run".into();
    fs::write(&beside, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(report::load(dir.path(), &run.result_path).is_err());
}
#[test]
fn custom_result_details_stay_in_ici_namespace_and_previous_placement_stays_readable() {
    let dir = fixture();
    // A user's own `runs/` directory beside a custom result is never written.
    fs::create_dir_all(dir.path().join("runs/user")).unwrap();
    fs::write(dir.path().join("runs/user/data.txt"), "user data").unwrap();
    let run = execute::execute(
        &call(&["ici", "analyze", "--result", "r.json"]),
        dir.path(),
        &BTreeMap::new(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    let details = report::details_path(dir.path(), &run.result_path, &run.result.run_id).unwrap();
    assert!(details.starts_with(dir.path().join(".ici/rust-v1/details/r.json")));
    assert!(details.is_file());
    let entries: Vec<_> = fs::read_dir(dir.path().join("runs"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(entries, vec![std::ffi::OsString::from("user")]);
    assert_eq!(
        fs::read_to_string(dir.path().join("runs/user/data.txt")).unwrap(),
        "user data"
    );
    // Default results already live in the namespace and do not nest it again.
    let default =
        report::details_path(dir.path(), &dir.path().join(ici_cli::DEFAULT_RESULT), "id").unwrap();
    assert_eq!(
        default,
        dir.path()
            .join(".ici/rust-v1/details/result.json/id/details.json")
    );
    // An archive written with the earlier `<dir>/runs/<run>/details.json` placement loads.
    let previous = dir
        .path()
        .join("runs")
        .join(&run.result.run_id)
        .join("details.json");
    fs::create_dir_all(previous.parent().unwrap()).unwrap();
    fs::rename(&details, &previous).unwrap();
    let loaded = report::load(dir.path(), &run.result_path).unwrap();
    assert!(matches!(loaded, ici_reporting::StoredResult::Legacy(_)));
    // A mismatched archive at the previous placement is still refused, not ignored.
    let mut value: serde_json::Value =
        serde_json::from_slice(&fs::read(&previous).unwrap()).unwrap();
    value["native_run"]["run_id"] = "another-run".into();
    fs::write(&previous, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(report::load(dir.path(), &run.result_path).is_err());
    // Without any archive the native run itself is reported.
    fs::remove_file(&previous).unwrap();
    assert!(matches!(
        report::load(dir.path(), &run.result_path).unwrap(),
        ici_reporting::StoredResult::Run(_)
    ));
}

/// Root decision E2 through the CLI: `ici analyze --result <path>` writes only that file beside
/// the user's path, inside or outside the project, as frozen did
/// (result-placement-oracle.json); the offline page it points to lives with the details under
/// `.ici/rust-v1/details/`, and a user's own `out/a.html` is neither replaced nor a failure.
#[test]
fn analyze_pages_of_explicit_results_stay_in_the_ici_namespace() {
    let dir = fixture();
    let outside = tempfile::tempdir().unwrap();
    let outside_result = outside.path().join("r.json");
    fs::create_dir_all(dir.path().join("out")).unwrap();
    fs::write(dir.path().join("out/a.html"), "<p>mine</p>\n").unwrap();
    let environment = BTreeMap::from([("PATH".to_owned(), "/usr/bin:/bin".to_owned())]);
    for result in ["out/a.json", outside_result.to_str().unwrap()] {
        let output = commands::dispatch(
            &call(&["ici", "analyze", "--result", result]),
            dir.path(),
            &environment,
        )
        .unwrap();
        assert_eq!(output.exit_code, 0, "{}{}", output.stdout, output.stderr);
        let page = output
            .stdout
            .lines()
            .find_map(|line| line.strip_prefix("HTML: "))
            .unwrap_or_else(|| panic!("{}", output.stdout));
        assert!(
            std::path::Path::new(page).starts_with(dir.path().join(".ici/rust-v1/details")),
            "{page}"
        );
        assert!(
            fs::read_to_string(page).unwrap().contains("<html"),
            "{page}"
        );
    }
    assert_eq!(
        fs::read_to_string(dir.path().join("out/a.html")).unwrap(),
        "<p>mine</p>\n"
    );
    let mut beside: Vec<_> = fs::read_dir(dir.path().join("out"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    beside.sort();
    assert_eq!(beside, ["a.html", "a.json"]);
    let written: Vec<_> = fs::read_dir(outside.path())
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(written, vec![std::ffi::OsString::from("r.json")]);
    // The default result keeps its page beside it in the native namespace.
    let output = commands::dispatch(&call(&["ici", "analyze"]), dir.path(), &environment).unwrap();
    assert!(
        output.stdout.contains(&format!(
            "HTML: {}",
            dir.path().join(".ici/rust-v1/result.html").display()
        )),
        "{}",
        output.stdout
    );
}

/// A temporary fixture root that is its own VCS boundary: root discovery never climbs to a
/// repository enclosing the host's temporary directory (frozen and native both stop at `.hg`,
/// and Git source identity ignores it).
fn bounded_tempdir() -> tempfile::TempDir {
    let directory = tempfile::tempdir().unwrap();
    std::fs::create_dir(directory.path().join(".hg")).unwrap();
    directory
}

/// Frozen `_artifact_root` (`find_workspace_root`), replayed through the real binary from the
/// captured cases: a relative `--result` resolves at the nearest `ici.toml` that declares
/// `[workspace]`, never past the top of a checkout, and otherwise at `cwd` — not at a bare
/// `.git`, a `pyproject.toml` or a legacy `ici.toml` (tests/rust-parity/runtime/capture_artifact_root.py).
#[test]
fn next_report_resolves_a_relative_result_where_frozen_does() {
    let oracle: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/runtime/artifact-root-oracle.json"
    ))
    .unwrap();
    for (name, case) in oracle.as_object().unwrap() {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        for (relative, text) in case["tree"].as_object().unwrap() {
            let path = base.join(relative);
            if relative.ends_with('/') {
                fs::create_dir_all(&path).unwrap();
            } else {
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(&path, text.as_str().unwrap()).unwrap();
            }
        }
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["next", "report", "--result", "missing.json"])
            .current_dir(base.join(case["cwd"].as_str().unwrap()))
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", &base)
            .env("GIT_CEILING_DIRECTORIES", &base)
            .output()
            .unwrap();
        let stderr = String::from_utf8_lossy(&output.stderr)
            .trim()
            .replace(&*base.to_string_lossy(), "<base>");
        assert_eq!(
            output.status.code(),
            case["exit_code"].as_i64().map(|code| code as i32),
            "{name}: {stderr}"
        );
        assert_eq!(stderr, case["stderr"].as_str().unwrap(), "{name}");
    }
}
