use ici_cli::{commands, planning, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

#[test]
fn legacy_test_report_counts_shared_pytest_coverage_suite_once() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("app.py"), "VALUE = 1\n").unwrap();
    fs::write(root.path().join("tests/test_app.py"), "from app import VALUE\ndef test_one(): assert VALUE == 1\ndef test_two(): assert VALUE > 0\n").unwrap();
    let interpreter = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .join(".venv/bin/python");
    let interpreter = serde_json::to_string(&interpreter.to_string_lossy().to_string()).unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        let enabled = matches!(check.id.as_str(), "python.test" | "python.coverage");
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={enabled}\nrequired={enabled}\n",
            check.id
        ));
    }
    config.push_str(&format!("[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n[components.python]\nexecutable={interpreter}\ntest_paths=['tests']\n"));
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let env = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ]);
    let plan = planning::prepare(
        &registry::parse(["ici", "test"]).unwrap(),
        root.path(),
        &env,
        false,
    )
    .unwrap();
    assert_eq!(
        plan.document()["graph"]["shared"]["project.python.test"],
        serde_json::json!(["project.python.coverage", "project.python.test"])
    );
    let preview = commands::dispatch(
        &registry::parse(["ici", "next", "plan", "--json"]).unwrap(),
        root.path(),
        &env,
    )
    .unwrap();
    let preview: Value = serde_json::from_str(&preview.stdout).unwrap();
    assert_eq!(
        preview["graph"]["shared"]["project.python.test"],
        plan.document()["graph"]["shared"]["project.python.test"]
    );
    assert!(!root.path().join(".ici").exists());
    let result = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &env,
    )
    .unwrap();
    assert_eq!(result.exit_code, 0, "{} {}", result.stdout, result.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    assert_eq!(report["engine_name"], "test");
    assert_eq!(report["extra"]["passed_tests"], 2, "{report:#}");
    assert_eq!(report["extra"]["total_tests"], 2);
    assert!(
        report["extra"]["coverage_totals"]["stmts"]
            .as_u64()
            .is_some()
    );
    assert_eq!(
        report["extra"]["test_execution"]["physical_suites"]
            .as_object()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        report["extra"]["native_checks"].as_array().unwrap().len(),
        2
    );
}

#[test]
fn report_mode_quality_keeps_finding_but_does_not_fail_legacy_test_command() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("app.py"), "VALUE = 1\n").unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        "import time\nfrom app import VALUE\ndef test_slow():\n    time.sleep(0.02)\n    assert VALUE == 1\n",
    )
    .unwrap();
    let interpreter = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .join(".venv/bin/python");
    let interpreter = serde_json::to_string(&interpreter.to_string_lossy().to_string()).unwrap();
    let mut config = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        let enabled = matches!(check.id.as_str(), "python.test" | "python.coverage");
        config.push_str(&format!(
            "[checks.\"{}\"]\nenabled={enabled}\nrequired={enabled}\n",
            check.id
        ));
    }
    config.push_str("[engines.test]\nmode='pass_fail'\n[engines.test.quality]\nenabled=true\nmode='report'\nslow_test_threshold=0.001\n");
    config.push_str(&format!("[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n[components.python]\nexecutable={interpreter}\ntest_paths=['tests']\n"));
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let env = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ]);
    let result = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &env,
    )
    .unwrap();
    assert_eq!(result.exit_code, 0, "{} {}", result.stdout, result.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    assert_eq!(report["status"], "PASS", "{report:#}");
    assert!(
        report["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["rule_id"] == "ici.test.slow-test")
    );
}
