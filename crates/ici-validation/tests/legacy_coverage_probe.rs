//! Frozen legacy `engines/test_interpreter.py::_find_coverage_cmd` (F1): under the legacy
//! option the coverage probe runs with the frozen 300 s / 1,000,000-character bounds, every
//! non-success falls through to the plain suite and names the frozen tool error, and the
//! frozen `coverage_support.module_unavailable` rule decides a missing module. Native runs keep
//! refusing a broken probe (`optional_coverage.rs`).
use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::python_coverage::{Capability, LEGACY_PROBE_ERRORS, legacy_module_unavailable};
use ici_validation::*;
use std::{collections::BTreeMap, path::Path};

fn request(root: &Path, kind: ValidationKind, legacy: bool) -> ValidationRequest {
    ValidationRequest {
        kind,
        task_id: format!(
            "project.{}",
            if kind == ValidationKind::PythonCoverage {
                "python.coverage"
            } else {
                "python.test"
            }
        ),
        component_id: "project".into(),
        root: root.into(),
        cwd: root.into(),
        owned_run_dir: root.join("run"),
        tool: ResolvedTool {
            id: "python".into(),
            path: "/tools/python".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["tests".into()],
        source_files: vec!["app.py".into()],
        functions: vec![],
        options: ValidationOptions {
            collect_coverage: kind == ValidationKind::PythonTest,
            coverage_module_probe: true,
            legacy_coverage_probe: legacy,
            ..Default::default()
        },
    }
}
fn outcome(id: &str, stdout: &str, stderr: &str, exit: Option<i32>) -> TaskOutcome {
    let mut output = TaskOutcome::empty(id);
    output.state = TaskState::Succeeded;
    output.exit_code = exit;
    output.stdout = stdout.as_bytes().to_vec();
    output.stderr = stderr.as_bytes().to_vec();
    output
}

#[test]
fn every_failed_legacy_probe_runs_the_plain_suite_and_names_the_frozen_tool_error() {
    let root = tempfile::tempdir().unwrap();
    let plan = plan(&request(root.path(), ValidationKind::PythonTest, true)).unwrap();
    let probe = python_coverage::probe_id(&plan).unwrap();
    let task = plan.tasks.iter().find(|task| task.id == probe).unwrap();
    assert_eq!(task.timeout_seconds, Some(300.0));
    assert_eq!(task.output_limit_bytes, Some(1_000_000));
    let fallback = python_coverage::fallback_id(&plan);
    let exit = outcome(&probe, "", "broken\n", Some(2));
    let mut timed_out = outcome(&probe, "", "", None);
    timed_out.timed_out = true;
    let mut truncated = outcome(&probe, &"a".repeat(64), "", Some(0));
    truncated.truncated = true;
    let mut signalled = outcome(&probe, "", "", None);
    signalled.signal = Some(9);
    let mut unavailable = outcome(&probe, "", "", None);
    unavailable.state = TaskState::Failed;
    for (probe_outcome, expected) in [
        (exit, "Coverage module probe failed with exit code 2"),
        (timed_out, LEGACY_PROBE_ERRORS[0]),
        (truncated, LEGACY_PROBE_ERRORS[1]),
        (signalled, LEGACY_PROBE_ERRORS[2]),
        (unavailable, LEGACY_PROBE_ERRORS[2]),
    ] {
        let outcomes = BTreeMap::from([(probe.clone(), probe_outcome)]);
        assert!(
            matches!(
                python_coverage::capability(&plan, &outcomes).unwrap(),
                Some(Capability::Failed(ref reason)) if reason == expected
            ),
            "{expected}"
        );
        assert_eq!(
            ready_steps(&plan, &outcomes).unwrap(),
            vec![fallback.clone()],
            "{expected}"
        );
        assert_eq!(
            python_coverage::actual_suite_id(&plan, &outcomes).unwrap(),
            fallback
        );
    }
    // Frozen accepts any exit 0 as an available module; the version is kept only when shown.
    let bare = BTreeMap::from([(probe.clone(), outcome(&probe, "ok\n", "", Some(0)))]);
    assert!(matches!(
        python_coverage::capability(&plan, &bare).unwrap(),
        Some(Capability::Available(ref version)) if version.is_empty()
    ));
    assert_eq!(
        ready_steps(&plan, &bare).unwrap(),
        vec![python_coverage::suite_id(&plan)]
    );
}

#[test]
fn the_frozen_missing_module_rule_accepts_only_one_interpreter_level_line() {
    let root = tempfile::tempdir().unwrap();
    let plan = plan(&request(root.path(), ValidationKind::PythonTest, true)).unwrap();
    let probe = python_coverage::probe_id(&plan).unwrap();
    for (stdout, stderr, exit) in [
        ("No module named coverage\n", "", 2),
        ("", "/usr/bin/python3.12: No module named 'coverage'\n", 1),
        ("", "python3: No module named \"coverage\"\n", 1),
    ] {
        let missing = outcome(&probe, stdout, stderr, Some(exit));
        assert!(legacy_module_unavailable(&missing, "coverage"), "{stderr}");
        let outcomes = BTreeMap::from([(probe.clone(), missing)]);
        assert!(matches!(
            python_coverage::capability(&plan, &outcomes).unwrap(),
            Some(Capability::Missing)
        ));
        assert_eq!(
            ready_steps(&plan, &outcomes).unwrap(),
            vec![python_coverage::fallback_id(&plan)]
        );
    }
    for (stdout, stderr, exit) in [
        (
            "",
            "Traceback (most recent call last):\nModuleNotFoundError: No module named 'coverage'\n",
            1,
        ),
        ("", "/tools/python: No module named coveragex\n", 1),
        ("", "/tools/python: No module named coverage\n", 0),
        ("", "No module named coverage\n", -1),
    ] {
        let probe_outcome = outcome(&probe, stdout, stderr, Some(exit));
        assert!(
            !legacy_module_unavailable(&probe_outcome, "coverage"),
            "{stderr}"
        );
    }
}

#[test]
fn native_plans_keep_the_bounded_probe_and_refuse_a_failed_one() {
    let root = tempfile::tempdir().unwrap();
    let plan = plan(&request(root.path(), ValidationKind::PythonTest, false)).unwrap();
    let probe = python_coverage::probe_id(&plan).unwrap();
    let task = plan.tasks.iter().find(|task| task.id == probe).unwrap();
    assert_eq!(task.timeout_seconds, Some(30.0));
    assert_eq!(task.output_limit_bytes, Some(8192));
    let outcomes = BTreeMap::from([(probe.clone(), outcome(&probe, "", "broken\n", Some(2)))]);
    assert!(python_coverage::capability(&plan, &outcomes).is_err());
    assert!(ready_steps(&plan, &outcomes).is_err());
    // The legacy coverage reader fails with the frozen text instead of measuring anything.
    let coverage = plan_coverage(root.path());
    let probe = python_coverage::probe_id(&coverage).unwrap();
    let outcomes = BTreeMap::from([(probe.clone(), outcome(&probe, "", "broken\n", Some(3)))]);
    let result = observe(&coverage, &outcomes, &CapturedArtifacts::default());
    assert_eq!(result.observation.state, TaskState::Failed, "{result:?}");
    assert_eq!(
        result.summary,
        "Coverage module probe failed with exit code 3"
    );
}

fn plan_coverage(root: &Path) -> ValidationPlan {
    let mut request = request(root, ValidationKind::PythonCoverage, true);
    request.options.shared_test_task_id = Some("project.python.test.suite".into());
    plan(&request).unwrap()
}
