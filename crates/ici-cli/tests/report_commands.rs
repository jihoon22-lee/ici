use ici_cli::{commands, execute, registry, report};
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs};

fn call(args: &[&str]) -> registry::Invocation {
    registry::parse(args.iter().copied()).unwrap()
}
fn fixture() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
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
    let details = report::details_path(&run.result_path, &run.result.run_id).unwrap();
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
    let details = report::details_path(&run.result_path, &run.result.run_id).unwrap();
    let original = fs::read(&details).unwrap();
    let mut value: serde_json::Value = serde_json::from_slice(&original).unwrap();
    value["native_run"]["run_id"] = "another-run".into();
    fs::write(&details, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(report::load(&run.result_path).is_err());
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
    assert!(output.stdout.contains("HTML:"));
    assert!(output.stdout.contains("Result:"));
    assert!(dir.path().join(ici_cli::DEFAULT_PAGE).is_file());
    let output = commands::dispatch(
        &call(&["ici", "analyze", "--json"]),
        dir.path(),
        &BTreeMap::new(),
    )
    .unwrap();
    let json: serde_json::Value = serde_json::from_str(&output.stdout).unwrap();
    assert_eq!(json["schema_id"], "ici.next.run");
}

#[test]
fn historical_v2_reports_render_without_overwriting_or_inventing_native_evidence() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("old.json");
    let payload = serde_json::json!({
        "schema_version":"ici.result/v2", "suite_status":"FAIL", "results":[{
            "engine_name":"lint", "status":"FAIL", "summary":"historical failure",
            "targets":[{"file_path":"main.py","line_number":4,"status":"FAIL","message":"old diagnostic"}]
        }]
    });
    let original = serde_json::to_vec(&payload).unwrap();
    fs::write(&path, &original).unwrap();
    let stored = report::load(&path).unwrap();
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
        assert!(report::load(&path).is_err(), "{invalid}");
    }
    fs::write(&path, br#"{"schema_version":"ici.result/v2","engine_name":"lint","status":"PASS","status":"FAIL"}"#).unwrap();
    assert!(report::load(&path).is_err());
}
