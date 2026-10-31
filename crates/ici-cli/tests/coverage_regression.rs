use ici_application::{
    plan::{Plan, PlannedCheck, Work},
    suppressions::SuppressionPolicy,
};
use ici_cli::coverage_regression;
use ici_domain::{
    AnalysisOutput, BaselineComparison, BaselineState, CheckRecord, Profile, RunResult, TaskState,
};
use ici_reporting::{ProjectionContext, StoredResult, legacy_projection};
use ici_validation::coverage_baseline::{CoveragePolicySnapshot, CoverageRatio, SNAPSHOT_SCHEMA};
use serde_json::json;
use std::{collections::BTreeMap, fs};

fn policy(covered: u64) -> CoveragePolicySnapshot {
    let raw = BTreeMap::from([
        (
            "line".into(),
            CoverageRatio::counts(Some(covered), Some(10)),
        ),
        ("branch".into(), CoverageRatio::counts(Some(0), Some(0))),
        ("function".into(), CoverageRatio::counts(Some(1), Some(1))),
        (
            "changed_line".into(),
            CoverageRatio::counts(Some(0), Some(0)),
        ),
    ]);
    CoveragePolicySnapshot {
        schema_id: SNAPSHOT_SCHEMA.into(),
        schema_version: 1,
        task_id: "project.python.coverage".into(),
        component_id: "project".into(),
        provider: "coverage".into(),
        anchor_path: "app.py".into(),
        metrics: raw
            .iter()
            .filter_map(|(key, ratio): (&String, &CoverageRatio)| {
                ratio.percentage().map(|value| (key.clone(), value))
            })
            .collect(),
        files: BTreeMap::from([("app.py".into(), covered as f64 * 10.0)]),
        regression_tolerance: Some(5.0),
        raw_metrics: raw,
        raw_files: BTreeMap::from([(
            "app.py".into(),
            CoverageRatio::counts(Some(covered), Some(10)),
        )]),
        min_file_statements: 1,
    }
}
fn plan() -> Plan {
    Plan {
        profile: Profile::Standard,
        components: vec!["project".into()],
        languages: vec!["python".into()],
        checks: vec![PlannedCheck {
            component_id: "project".into(),
            task_id: "project.python.coverage".into(),
            check: ici_domain::builtin_catalog()
                .checks
                .into_iter()
                .find(|c| c.id == "python.coverage")
                .unwrap(),
            work: Work::Internal,
        }],
        omitted: vec![],
        coverage_gaps: vec![],
        limitations: vec![],
    }
}
fn output(covered: u64) -> BTreeMap<String, AnalysisOutput> {
    let mut value = AnalysisOutput::new("project.python.coverage", "coverage");
    value.extra = json!({"project.python.coverage":{"coverage_policy":policy(covered)}});
    BTreeMap::from([("project.python.coverage".into(), value)])
}
fn run() -> RunResult {
    let mut run = RunResult::from_json(include_str!(
        "../../../tests/rust-parity/domain/run-v2.json"
    ))
    .unwrap();
    run.findings.clear();
    run.checks = Some(vec![CheckRecord {
        task_id: "project.python.coverage".into(),
        check_id: "python.coverage".into(),
        component_id: "project".into(),
        language: "python".into(),
        scope_digest: ici_domain::sha256("same selected test nodes and settings"),
        state: TaskState::Succeeded,
        evidence_complete: true,
        analysis_unit_id: Some("project.python".into()),
        variant: None,
    }]);
    run.baseline = Some(BaselineComparison {
        state: BaselineState::Comparable,
        ..Default::default()
    });
    run
}
#[test]
fn typed_coverage_drop_only_adds_baseline_findings_and_keeps_ordinary_gate() {
    let baseline = run();
    let document = StoredResult::Legacy(
        legacy_projection(&baseline, &output(10), &ProjectionContext::default()).unwrap(),
    );
    let mut current = run();
    let gate = current.gate.clone();
    let mut outputs = output(7);
    let raw = coverage_regression::apply(
        &mut current,
        &mut outputs,
        &plan(),
        &baseline,
        &document,
        &SuppressionPolicy::new(&[]).unwrap(),
        &std::collections::BTreeSet::from(["project.python.coverage".into()]),
    );
    assert_eq!(current.gate, gate);
    assert_eq!(current.findings.len(), 2);
    assert_eq!(raw.len(), 2);
    assert!(
        current
            .findings
            .iter()
            .all(|finding| finding.tags.contains(&"baseline-only".into())
                && finding.task_id.as_deref() == Some("project.python.coverage"))
    );
    assert_eq!(current.baseline.as_ref().unwrap().new.len(), 2);
    assert!(!coverage_regression::incomplete(&outputs));
    assert_eq!(
        outputs["project.python.coverage"]
            .targets
            .iter()
            .filter(|t| t.status == "WARN")
            .count(),
        2
    );
}
#[test]
fn missing_rich_data_changed_scope_or_incomplete_baseline_never_invents_coverage_drop() {
    for mode in ["missing", "scope", "incomplete", "current_missing"] {
        let mut baseline = run();
        if mode == "incomplete" {
            baseline.checks.as_mut().unwrap()[0].evidence_complete = false;
        }
        let document = if mode == "missing" {
            StoredResult::Run(Box::new(baseline.clone()))
        } else {
            StoredResult::Legacy(
                legacy_projection(&baseline, &output(10), &ProjectionContext::default()).unwrap(),
            )
        };
        let mut current = run();
        if mode == "scope" {
            current.checks.as_mut().unwrap()[0].scope_digest =
                ici_domain::sha256("different tests");
        }
        let mut outputs = output(7);
        if mode == "current_missing" {
            outputs.get_mut("project.python.coverage").unwrap().extra = json!({});
        }
        coverage_regression::apply(
            &mut current,
            &mut outputs,
            &plan(),
            &baseline,
            &document,
            &SuppressionPolicy::new(&[]).unwrap(),
            &std::collections::BTreeSet::from(["project.python.coverage".into()]),
        );
        assert!(current.findings.is_empty(), "{mode}");
        assert!(coverage_regression::incomplete(&outputs), "{mode}");
        assert!(!current.limitations.is_empty());
    }
}

#[test]
#[ignore = "explicit project pytest/coverage baseline regression acceptance"]
fn actual_coverage_drop_gates_fail_on_new_without_changing_selected_gate_or_baseline() {
    let root = tempfile::tempdir().unwrap();
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("set explicit project interpreter with pytest and coverage");
    fs::write(root.path().join("app.py"), "def called():\n    return 1\n").unwrap();
    fs::write(
        root.path().join("test_app.py"),
        "import app\ndef test_called():\n    assert app.called() == 1\n",
    )
    .unwrap();
    let mut config = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|c| c.language == "python")
    {
        config.push_str(&format!(
            "[checks.'{}']\nenabled={}\n",
            check.id,
            matches!(check.id.as_str(), "python.test" | "python.coverage")
        ));
    }
    config.push_str(&format!("[engines.test]\nmin_line_cov=0.0\nmin_branch_cov=0.0\nmin_func_cov=0.0\nmin_tem_score=0.0\nmin_file_statements=1\nmax_coverage_regression=1.0\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n[components.python]\nexecutable={}\ntest_paths=['test_app.py']\n",serde_json::to_string(&python).unwrap()));
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let environment = BTreeMap::from([("PATH".into(), std::env::var("PATH").unwrap_or_default())]);
    let call = |args: &[&str]| {
        ici_cli::commands::dispatch(
            &ici_cli::registry::parse(args.iter().copied()).unwrap(),
            root.path(),
            &environment,
        )
    };
    let first = call(&["ici", "verify", "--write-baseline", "baseline.json"]).unwrap();
    assert_eq!(first.exit_code, 0, "{} {}", first.stdout, first.stderr);
    let original = fs::read(root.path().join("baseline.json")).unwrap();
    fs::write(root.path().join("app.py"),"def called():\n    return 1\ndef not_called():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n").unwrap();
    let second = call(&[
        "ici",
        "verify",
        "--baseline",
        "baseline.json",
        "--fail-on-new",
    ])
    .unwrap();
    assert_eq!(second.exit_code, 1, "{} {}", second.stdout, second.stderr);
    let result =
        ici_execution::storage::read_result(&root.path().join(ici_cli::DEFAULT_RESULT)).unwrap();
    assert_eq!(
        result.gate.selected,
        ici_domain::GateVerdict::Pass,
        "{result:?}"
    );
    assert!(
        result
            .findings
            .iter()
            .any(|finding| finding.rule_id.starts_with("ici.test.coverage-regression."))
    );
    assert_eq!(
        fs::read(root.path().join("baseline.json")).unwrap(),
        original
    );
    let details = ici_cli::report::load(&root.path().join(ici_cli::DEFAULT_RESULT))
        .unwrap()
        .to_value()
        .unwrap();
    assert!(details.to_string().contains("baseline_raw"));
    // Loss of rich metric evidence must not silently approve an explicit regression policy.
    let mut missing: serde_json::Value = serde_json::from_slice(&original).unwrap();
    for engine in missing["results"].as_array_mut().unwrap() {
        engine["extra"] = json!({});
    }
    fs::write(
        root.path().join("missing.json"),
        serde_json::to_vec(&missing).unwrap(),
    )
    .unwrap();
    let failed = call(&[
        "ici",
        "verify",
        "--baseline",
        "missing.json",
        "--fail-on-new",
        "--write-baseline",
        "replacement.json",
    ])
    .unwrap_err();
    assert!(
        failed
            .0
            .contains("coverage regression comparison is incomplete"),
        "{failed:?}"
    );
    assert!(!root.path().join("replacement.json").exists());
}
