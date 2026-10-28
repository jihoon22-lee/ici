//! Actual CLI binding acceptance. No network/tool installation or silent absence skips.
use ici_cli::{execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::Cancellation;
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn quoted(value: &str) -> String {
    serde_json::to_string(value).unwrap()
}
fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("PATH".into(), std::env::var("PATH").unwrap_or_default()),
        ("HOME".into(), "/must-not-leak".into()),
        ("PYTHONPATH".into(), "/must-not-leak".into()),
        ("ICI_VISIBLE_MARKER".into(), "declared-marker".into()),
    ])
}
fn config(selected: &[&str], extra: &str, component: &str) -> String {
    let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.{}]\nenabled={}\nrequired={}\n",
            quoted(&check.id),
            selected.contains(&check.id.as_str()),
            selected.contains(&check.id.as_str())
        ));
    }
    text.push_str(extra);
    text.push_str(
        "\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n",
    );
    text.push_str(component);
    text
}
fn prepare(root: &Path, env: &BTreeMap<String, String>) -> planning::PreparedPlan {
    planning::prepare(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root,
        env,
        false,
    )
    .unwrap()
}
fn run(root: &Path, env: &BTreeMap<String, String>) -> execute::ExecutedRun {
    execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root,
        env,
        false,
        &Cancellation::default(),
    )
    .unwrap()
}
fn write(root: &Path, text: &str) {
    fs::write(root.join("ici.toml"), text).unwrap();
}
fn clean_source(root: &Path) {
    fs::write(root.join("app.py"), "VALUE = 1\n").unwrap();
}
fn matrices(output: &ici_domain::AnalysisOutput) -> Vec<&Value> {
    output
        .extra
        .as_object()
        .unwrap()
        .values()
        .filter_map(|extra| extra.get("runtime_matrix"))
        .collect()
}
#[test]
#[ignore = "explicit project runtime matrix and import-only CLI acceptance"]
fn configured_runtime_matrix_is_independent_of_primary_and_preserves_optional_missing() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    let runtime = python();
    let missing = root
        .path()
        .join("missing-optional-python")
        .to_string_lossy()
        .into_owned();
    let text = config(
        &["python.compat-runtime"],
        &format!(
            "[engines.python_compat]\ninterpreters=[{},{}]\nrequired_interpreters=[{}]\nimports=['app']\n",
            quoted(&runtime),
            quoted(&missing),
            quoted(&runtime)
        ),
        "[components.python]\nexecutable='missing-primary-python'\ntest_paths=['missing-tests-do-not-block-runtime']\n",
    );
    write(root.path(), &text);
    let env = environment();
    let planned = prepare(root.path(), &env);
    assert!(!root.path().join(".ici").exists());
    let plans = &planned.validations["project.python.compat-runtime"];
    assert_eq!(plans.len(), 2);
    assert!(plans.iter().all(|plan| plan.runtime_plans.len() == 2));
    assert!(
        plans
            .iter()
            .flat_map(|plan| &plan.tasks)
            .all(|task| task.argv[0] == runtime)
    );
    assert!(
        plans
            .iter()
            .flat_map(|plan| &plan.tasks)
            .any(|task| task.argv.iter().any(|word| word == "compileall"))
    );
    let result = run(root.path(), &env);
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    let output = &result.outputs["project.python.compat-runtime"];
    assert_eq!(output.observation.state, TaskState::Succeeded);
    let matrices = matrices(output);
    assert_eq!(matrices.len(), 2);
    for matrix in matrices {
        assert_eq!(matrix[0]["required"], true);
        assert_eq!(matrix[1]["state"], "unavailable");
        assert_eq!(matrix[1]["required"], false);
    }
    assert_eq!(
        fs::read_to_string(root.path().join("app.py")).unwrap(),
        "VALUE = 1\n"
    );
    assert!(!root.path().join("__pycache__").exists());
}
#[test]
#[ignore = "explicit required/optional runtime failure CLI acceptance"]
fn missing_required_runtime_blocks_and_optional_missing_remains_measured() {
    let runtime = python();
    for required_missing in [true, false] {
        let root = tempfile::tempdir().unwrap();
        clean_source(root.path());
        let missing = root
            .path()
            .join("missing-python")
            .to_string_lossy()
            .into_owned();
        let required = if required_missing {
            vec![runtime.as_str(), missing.as_str()]
        } else {
            vec![runtime.as_str()]
        };
        write(
            root.path(),
            &config(
                &["python.compat-runtime"],
                &format!(
                    "[engines.python_compat]\ninterpreters=[{},{}]\nrequired_interpreters={}\n",
                    quoted(&runtime),
                    quoted(&missing),
                    serde_json::to_string(&required).unwrap()
                ),
                &format!("[components.python]\nexecutable={}\n", quoted(&runtime)),
            ),
        );
        let result = run(root.path(), &environment());
        assert_eq!(
            result.result.gate.selected,
            if required_missing {
                GateVerdict::Incomplete
            } else {
                GateVerdict::Pass
            },
            "{:?}",
            result.outputs
        );
        if required_missing {
            assert_eq!(
                result.outputs["project.python.compat-runtime"]
                    .observation
                    .state,
                TaskState::Failed
            );
        }
    }
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    let missing = root
        .path()
        .join("missing-python")
        .to_string_lossy()
        .into_owned();
    write(
        root.path(),
        &config(
            &["python.compat-runtime"],
            &format!(
                "[engines.python_compat]\ninterpreters=[{}]\n",
                quoted(&missing)
            ),
            &format!("[components.python]\nexecutable={}\n", quoted(&runtime)),
        ),
    );
    let plan = prepare(root.path(), &environment());
    assert!(
        plan.plan.checks[0]
            .blocked()
            .unwrap()
            .contains("no configured project runtime")
    );
    assert!(!root.path().join(".ici").exists());
}
#[test]
#[ignore = "explicit declared integration CLI execution and environment isolation"]
fn integration_case_uses_declared_environment_nonzero_exit_streams_and_fresh_output() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("dist")).unwrap();
    fs::write(root.path().join("dist/payload.txt"), "payload").unwrap();
    let script = "import os, pathlib, sys\nassert 'HOME' not in os.environ\nassert 'PYTHONPATH' not in os.environ\nassert os.environ['ICI_VISIBLE_MARKER'] == 'declared-marker'\nassert os.environ['VALUE'] == 'configured'\nassert pathlib.Path(sys.argv[1]).read_text() == 'payload'\npathlib.Path('result.txt').write_text('fresh-result')\nprint('hello configured')\nprint('notice', file=sys.stderr)\nsys.exit(3)\n";
    fs::write(root.path().join("contract.py"), script).unwrap();
    let extra = "[builds.payload]\ndirectory='dist'\nartifacts=['payload.txt']\n";
    let component = format!(
        "[[components.integrations]]\nname='declared process'\nargv=['{{python:current}}','contract.py','{{artifact:payload/payload.txt}}']\nexpected_exit=3\nstdout_contains=['hello configured']\nstderr_contains=['notice']\nstdout_not_contains=['forbidden']\nstderr_not_contains=['failure']\ninherit_env=['ICI_VISIBLE_MARKER']\noutput_artifacts=[{{path='result.txt',min_size=5}}]\n[components.integrations.env]\nVALUE='configured'\n[components.python]\nexecutable={}\n",
        quoted(&python())
    );
    write(root.path(), &config(&["integration"], extra, &component));
    let planned = prepare(root.path(), &environment());
    assert!(!root.path().join(".ici").exists());
    assert!(!root.path().join("result.txt").exists());
    let case = &planned.validations["project.integration"][0];
    assert_eq!(
        case.tasks[0].env_inherit,
        Some(vec!["ICI_VISIBLE_MARKER".into()])
    );
    assert_eq!(case.tasks[0].timeout_seconds, Some(30.0));
    assert_eq!(case.tasks[0].output_limit_bytes, Some(65536));
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    assert_eq!(
        fs::read_to_string(root.path().join("result.txt")).unwrap(),
        "fresh-result"
    );
    assert_eq!(
        fs::read_to_string(root.path().join("contract.py")).unwrap(),
        script
    );
    let output = &result.outputs["project.integration"];
    assert_eq!(output.targets.len(), 1);
    assert_eq!(
        output.targets[0].target_name,
        "Integration:declared process"
    );
    assert_eq!(output.observation.findings.len(), 0);
}
#[test]
#[ignore = "explicit integration optional failure and preserved stale output CLI acceptance"]
fn optional_integration_failure_keeps_one_rich_case_finding_and_existing_output() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::write(root.path().join("contract.py"), "print('actual')\n").unwrap();
    fs::write(root.path().join("existing.txt"), "user-owned-original").unwrap();
    let component = format!(
        "[[components.integrations]]\nname='optional contract'\nrequired=false\nargv=['{{python:current}}','contract.py']\nstdout_contains=['missing']\noutput_artifacts=[{{path='existing.txt',min_size=1}}]\n[components.python]\nexecutable={}\n",
        quoted(&python())
    );
    write(root.path(), &config(&["integration"], "", &component));
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    assert_eq!(
        fs::read_to_string(root.path().join("existing.txt")).unwrap(),
        "user-owned-original"
    );
    let output = &result.outputs["project.integration"];
    assert_eq!(output.targets[0].status, "WARN");
    assert_eq!(output.observation.findings.len(), 1);
    let finding = &output.observation.findings[0];
    assert!(finding.tags.contains(&"advisory".into()));
    assert_eq!(finding.rule_id, "ici.hybrid.process-contract");
    let raw = output
        .extra
        .as_object()
        .unwrap()
        .values()
        .find_map(|extra| extra.get("finding_details"))
        .unwrap();
    let details = raw.as_object().unwrap().values().next().unwrap();
    assert_eq!(details["legacy_label"], "optional contract");
    assert!(
        finding
            .message
            .contains("stdout_contains, output_artifacts")
    );
}
#[test]
#[ignore = "explicit existing native artifact and unavailable future artifact CLI acceptance"]
fn declared_native_artifact_runs_without_any_project_python_fallback() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("dist")).unwrap();
    fs::copy("/usr/bin/true", root.path().join("dist/app")).unwrap();
    let component = "[[components.integrations]]\nname='native artifact'\nargv=['{artifact:release/app}']\n[components.python]\nexecutable='missing-unused-python'\n";
    write(
        root.path(),
        &config(
            &["integration"],
            "[builds.release]\ndirectory='dist'\nartifacts=['app']\n",
            component,
        ),
    );
    let planned = prepare(root.path(), &environment());
    assert!(!root.path().join(".ici").exists());
    assert!(
        planned.validations["project.integration"][0].tasks[0]
            .tool_digest
            .is_some()
    );
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    fs::remove_file(root.path().join("dist/app")).unwrap();
    let blocked = prepare(root.path(), &environment());
    assert!(
        blocked.plan.checks[0]
            .blocked()
            .unwrap()
            .contains("prepare the declared build")
    );
}

#[test]
#[ignore = "explicit project pytest ResourceWarning CLI acceptance"]
fn python_sanitize_measures_resource_defects_and_keeps_other_pytest_failures_incomplete() {
    for (test, verdict) in [
        ("def test_scope():\n    assert True\n", GateVerdict::Pass),
        (
            "import warnings\ndef test_scope():\n    warnings.warn('unclosed resource', ResourceWarning)\n",
            GateVerdict::Fail,
        ),
        (
            "def test_scope():\n    assert False\n",
            GateVerdict::Incomplete,
        ),
        (
            "import pytest\ndef test_scope():\n    pytest.skip('no executed scope')\n",
            GateVerdict::Incomplete,
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        clean_source(root.path());
        fs::create_dir(root.path().join("tests")).unwrap();
        fs::write(root.path().join("tests/test_scope.py"), test).unwrap();
        fs::write(root.path().join("pyproject.toml"),"[project]\nname='resource-fixture'\nversion='1'\n[tool.pytest.ini_options]\naddopts='--invalid-option-must-be-cleared'\n").unwrap();
        write(
            root.path(),
            &config(
                &[],
                "[checks.\"python.sanitize\"]\nenabled=true\nrequired=true\n",
                &format!(
                    "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
                    quoted(&python())
                ),
            ),
        );
        let planned = prepare(root.path(), &environment());
        assert!(!root.path().join(".ici").exists());
        let tasks = &planned.validations["project.python.sanitize"][0].tasks;
        assert_eq!(tasks.len(), 1);
        assert!(
            tasks[0]
                .argv
                .iter()
                .any(|arg| arg == "error::ResourceWarning")
        );
        assert!(tasks[0].argv.iter().any(|arg| arg == "addopts="));
        let result = run(root.path(), &environment());
        assert_eq!(
            result.result.gate.selected, verdict,
            "{test}: {:?}",
            result.outputs
        );
        let output = &result.outputs["project.python.sanitize"];
        if verdict == GateVerdict::Fail {
            assert!(
                output
                    .targets
                    .iter()
                    .any(|target| target.target_name == "ResourceWarning"
                        && target.file_path == "tests/test_scope.py"
                        && target.start_line == 3),
                "{output:?}"
            );
            assert!(
                output
                    .observation
                    .findings
                    .iter()
                    .any(|finding| finding.rule_id == "sanitize.python-resource-warning")
            );
        }
        if verdict == GateVerdict::Incomplete {
            assert!(output.observation.findings.is_empty());
        }
        assert_eq!(
            fs::read_to_string(root.path().join("tests/test_scope.py")).unwrap(),
            test
        );
        assert!(!root.path().join("tests/__pycache__").exists());
    }
}
#[test]
#[ignore = "explicit two-project-interpreter requires-python matrix CLI acceptance"]
fn optional_runtime_incompatibility_is_advisory_inside_a_required_matrix() {
    let first = python();
    let second = std::env::var("ICI_TEST_SECOND_PYTHON")
        .expect("set ICI_TEST_SECOND_PYTHON to the explicit Python3.14+ interpreter");
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='runtime-fixture'\nversion='1'\nrequires-python='>=3.14'\n",
    )
    .unwrap();
    write(
        root.path(),
        &config(
            &["python.compat-runtime"],
            &format!(
                "[engines.python_compat]\ninterpreters=[{},{}]\nrequired_interpreters=[{}]\n",
                quoted(&second),
                quoted(&first),
                quoted(&second)
            ),
            &format!("[components.python]\nexecutable={}\n", quoted(&first)),
        ),
    );
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    let findings = &result.outputs["project.python.compat-runtime"]
        .observation
        .findings;
    assert!(!findings.is_empty());
    assert!(
        findings
            .iter()
            .all(|finding| finding.tags.contains(&"advisory".into()))
    );
}

fn artifact_fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("src/pkg")).unwrap();
    fs::write(root.path().join("src/pkg/__init__.py"), "").unwrap();
    fs::write(
        root.path().join("src/pkg/cli.py"),
        "def main():\n    print('packaged-call')\n    return 0\n",
    )
    .unwrap();
    fs::write(root.path().join("pyproject.toml"),"[project]\nname='artifact-fixture'\nversion='1.2.3'\n[project.scripts]\ndemo='pkg.cli:main'\n").unwrap();
    let text = config(
        &[],
        "[checks.\"python.artifact\"]\nenabled=true\nrequired=true\n",
        "",
    )
    .replace(
        "sources=['app.py']",
        "source_dirs=['src']\nsources=['src/**/*.py']",
    );
    write(root.path(), &text);
    root
}
#[test]
fn rust_python_artifacts_are_planned_read_only_and_preserve_owned_updates_and_user_edits() {
    use std::os::unix::fs::PermissionsExt;
    let root = artifact_fixture();
    let env = environment();
    let preview = prepare(root.path(), &env);
    assert!(!root.path().join(".ici").exists());
    assert!(!root.path().join("v1.2.3").exists());
    let build = &preview.artifact_builds["project.python.artifact"];
    assert_eq!(build.task.kind, ici_domain::TaskKind::Prepare);
    assert_eq!(build.metadata.version_source, "pyproject.toml:version");
    assert_eq!(build.output_paths.len(), 6);
    assert!(
        build
            .task
            .input_refs
            .iter()
            .any(|path| path.ends_with("src/pkg/cli.py"))
    );
    let result = run(root.path(), &env);
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    let output = &result.outputs["project.python.artifact"];
    assert_eq!(output.targets.len(), 5);
    let frozen: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/python-artifacts-oracle.json"
    ))
    .unwrap();
    for (path, expected) in frozen["content"].as_object().unwrap() {
        assert_eq!(
            fs::read_to_string(root.path().join("v1.2.3/x86_64").join(path)).unwrap(),
            expected.as_str().unwrap(),
            "{path}"
        );
    }
    let copied = root.path().join("v1.2.3/x86_64/lib/pkg/cli.py");
    let source = root.path().join("src/pkg/cli.py");
    assert_eq!(fs::read(&copied).unwrap(), fs::read(&source).unwrap());
    assert_eq!(
        fs::metadata(root.path().join("v1.2.3/x86_64/bin/demo"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o755
    );
    let owned = ici_cli::artifact_build::owned_outputs(root.path()).unwrap();
    assert!(owned.contains("v1.2.3/x86_64/lib/pkg/cli.py"));
    assert_eq!(
        ici_cli::artifact_build::verify_output(build).unwrap().len(),
        6
    );
    let updated = "def main():\n    print('updated-package')\n    return 0\n";
    fs::write(&source, updated).unwrap();
    let second = run(root.path(), &env);
    assert_eq!(
        second.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        second.outputs
    );
    assert_eq!(fs::read_to_string(&copied).unwrap(), updated);
    fs::write(&copied, "user-owned-modification\n").unwrap();
    assert!(ici_cli::artifact_build::owned_outputs(root.path()).is_err());
    let third = run(root.path(), &env);
    assert_eq!(
        third.result.gate.selected,
        GateVerdict::Incomplete,
        "{:?}",
        third.outputs
    );
    assert_eq!(
        fs::read_to_string(&copied).unwrap(),
        "user-owned-modification\n"
    );
    assert_eq!(fs::read_to_string(&source).unwrap(), updated);
}
#[test]
fn rust_python_artifacts_preflight_unknown_data_and_reject_symlink_writes() {
    let root = artifact_fixture();
    let destination = root.path().join("v1.2.3/x86_64/lib/pkg/cli.py");
    fs::create_dir_all(destination.parent().unwrap()).unwrap();
    fs::write(&destination, "existing-user-data").unwrap();
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Incomplete,
        "{:?}",
        result.outputs
    );
    assert_eq!(
        fs::read_to_string(&destination).unwrap(),
        "existing-user-data"
    );
    assert!(!root.path().join("v1.2.3/x86_64/env.sh").exists());
    let root = artifact_fixture();
    let outside = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("v1.2.3/x86_64")).unwrap();
    std::os::unix::fs::symlink(outside.path(), root.path().join("v1.2.3/x86_64/lib")).unwrap();
    let result = execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root.path(),
        &environment(),
        false,
        &Cancellation::default(),
    );
    if let Ok(result) = result {
        assert_eq!(
            result.result.gate.selected,
            GateVerdict::Incomplete,
            "{:?}",
            result.outputs
        );
    }
    assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
}
#[test]
#[ignore = "explicit generated project-callable launcher acceptance"]
fn generated_python_launcher_invokes_the_packaged_callable_using_project_runtime() {
    let root = artifact_fixture();
    fs::create_dir_all(root.path().join(".venv/bin")).unwrap();
    std::os::unix::fs::symlink(python(), root.path().join(".venv/bin/python")).unwrap();
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    fs::write(
        root.path().join("src/pkg/cli.py"),
        "raise RuntimeError('unpackaged source must not execute')\n",
    )
    .unwrap();
    let output = std::process::Command::new(root.path().join("v1.2.3/x86_64/bin/demo"))
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .current_dir(root.path())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout).unwrap(), "packaged-call\n");
}

#[test]
fn broad_python_artifact_scope_excludes_verified_releases_and_preserves_retired_sources() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "def main():\n    return 0\n").unwrap();
    fs::write(root.path().join("retired.py"), "VALUE = 7\n").unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='broad-fixture'\nversion='1'\n[project.scripts]\ndemo='app:main'\n",
    )
    .unwrap();
    write(
        root.path(),
        &config(
            &["python.line"],
            "[checks.\"python.artifact\"]\nenabled=true\nrequired=true\n",
            "",
        )
        .replace(
            "sources=['app.py']",
            "source_dirs=['.']\nsources=['**/*.py']",
        ),
    );
    let first = run(root.path(), &environment());
    assert_eq!(
        first.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        first.outputs
    );
    let preview = prepare(root.path(), &environment());
    assert_eq!(
        preview.project.components[0].files,
        vec!["app.py", "retired.py"]
    );
    fs::remove_file(root.path().join("retired.py")).unwrap();
    let second = run(root.path(), &environment());
    assert_eq!(
        second.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        second.outputs
    );
    assert_eq!(
        fs::read_to_string(root.path().join("v1/x86_64/lib/retired.py")).unwrap(),
        "VALUE = 7\n"
    );
    assert!(
        ici_cli::artifact_build::owned_outputs(root.path())
            .unwrap()
            .contains("v1/x86_64/lib/retired.py")
    );
    assert_eq!(
        prepare(root.path(), &environment()).project.components[0].files,
        vec!["app.py"]
    );
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='broad-fixture'\nversion='2'\n[project.scripts]\ndemo='app:main'\n",
    )
    .unwrap();
    let third = run(root.path(), &environment());
    assert_eq!(
        third.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        third.outputs
    );
    assert!(root.path().join("v1/x86_64/lib/app.py").is_file());
    assert!(root.path().join("v2/x86_64/lib/app.py").is_file());
    let verified = ici_cli::artifact_build::owned_outputs(root.path()).unwrap();
    assert!(verified.contains("v1/x86_64/lib/app.py"));
    assert!(verified.contains("v2/x86_64/lib/app.py"));
    assert_eq!(
        prepare(root.path(), &environment()).project.components[0].files,
        vec!["app.py"]
    );
    let analyzed = execute::execute(
        &registry::parse(["ici", "analyze"]).unwrap(),
        root.path(),
        &environment(),
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        analyzed.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        analyzed.outputs
    );
    assert!(!analyzed.outputs.contains_key("project.python.artifact"));
}

#[test]
fn artifact_version_probe_rejects_changed_tools_and_cancellation_without_writes() {
    let root = artifact_fixture();
    let metadata = root.path().join("pyproject.toml");
    let text = fs::read_to_string(&metadata)
        .unwrap()
        .replace("version='1.2.3'\n", "");
    fs::write(&metadata, text).unwrap();
    let config_path = root.path().join("ici.toml");
    let text = fs::read_to_string(&config_path).unwrap() + "\n[tools.git]\npath='/usr/bin/true'\n";
    fs::write(&config_path, text).unwrap();
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment()).unwrap();
    let plan = ici_cli::artifact_build::describe_with_environment(
        &project,
        &project.components[0],
        "project.python.artifact",
        &environment(),
    )
    .unwrap();
    let probe = plan.version_probe.as_ref().unwrap();
    assert_eq!(plan.metadata.version_source, "planned:git-describe");
    assert!(!root.path().join(".ici").exists());
    let blocked = ici_execution::TaskOutcome::blocked(&probe.id, "selected executable changed");
    assert!(ici_cli::artifact_build::apply_version_probe(&plan, &blocked).is_err());
    let mut outcome = ici_execution::TaskOutcome::empty(&probe.id);
    outcome.state = TaskState::Succeeded;
    outcome.exit_code = Some(0);
    outcome.stdout = b"v2.4.0\n".to_vec();
    let resolved = ici_cli::artifact_build::apply_version_probe(&plan, &outcome).unwrap();
    assert_eq!(resolved.metadata.version, "v2.4.0");
    assert_eq!(resolved.metadata.version_source, "git-describe");
    assert!(
        resolved
            .output_paths
            .iter()
            .any(|path| path == "v2.4.0/x86_64/lib/pkg/cli.py")
    );
    assert!(
        resolved
            .task
            .output_specs
            .iter()
            .all(|path| !path.contains("__git_describe__"))
    );
    outcome.stdout = b"invalid/version\n".to_vec();
    assert!(ici_cli::artifact_build::apply_version_probe(&plan, &outcome).is_err());
    outcome.stdout = b"v2\n".to_vec();
    outcome.cancelled = true;
    assert!(ici_cli::artifact_build::apply_version_probe(&plan, &outcome).is_err());
    outcome.cancelled = false;
    outcome.state = TaskState::Failed;
    outcome.exit_code = None;
    assert!(ici_cli::artifact_build::apply_version_probe(&plan, &outcome).is_err());
    outcome.cleanup.group = Some(1234);
    outcome.timed_out = true;
    let fallback = ici_cli::artifact_build::apply_version_probe(&plan, &outcome).unwrap();
    assert_eq!(fallback.metadata.version_source, "fallback:git-failed");
    assert_eq!(fallback.metadata.version, "v1.0.0");
    assert!(!fallback.limitations.is_empty());
    assert!(!root.path().join("v2.4.0").exists());
    let text = fs::read_to_string(&config_path)
        .unwrap()
        .replace("path='/usr/bin/true'", "path='/missing/explicit/git'");
    fs::write(config_path, text).unwrap();
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment()).unwrap();
    assert!(
        ici_cli::artifact_build::describe_with_environment(
            &project,
            &project.components[0],
            "project.python.artifact",
            &environment()
        )
        .unwrap_err()
        .contains("configured Git executable")
    );
}
#[test]
#[ignore = "explicit native Git version probe and fallback CLI acceptance"]
fn native_git_version_is_visible_and_resolved_before_any_release_write() {
    let git =
        std::env::var("ICI_TEST_GIT").expect("set ICI_TEST_GIT to the explicit Git executable");
    for tagged in [true, false] {
        let root = artifact_fixture();
        let metadata = root.path().join("pyproject.toml");
        let text = fs::read_to_string(&metadata)
            .unwrap()
            .replace("version='1.2.3'\n", "");
        fs::write(metadata, text).unwrap();
        let path = root.path().join("ici.toml");
        fs::write(
            &path,
            fs::read_to_string(&path).unwrap() + &format!("\n[tools.git]\npath={}\n", quoted(&git)),
        )
        .unwrap();
        if tagged {
            let call = |args: &[&str]| {
                let output = std::process::Command::new(&git)
                    .args([
                        "-c",
                        "init.templateDir=",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        "-c",
                        "tag.gpgsign=false",
                    ])
                    .args(args)
                    .env_clear()
                    .env("PATH", "/usr/bin:/bin")
                    .env("GIT_CONFIG_NOSYSTEM", "1")
                    .env("GIT_CONFIG_GLOBAL", "/dev/null")
                    .current_dir(root.path())
                    .output()
                    .unwrap();
                assert!(
                    output.status.success(),
                    "{}",
                    String::from_utf8_lossy(&output.stderr)
                );
            };
            call(&["init", "-q"]);
            call(&["commit", "-q", "--allow-empty", "-m", "fixture"]);
            call(&["tag", "v2.4"]);
        }
        let preview = prepare(root.path(), &environment());
        let planned = &preview.plan.checks[0];
        assert_eq!(planned.tasks().len(), 2);
        assert_eq!(planned.tasks()[0].kind, ici_domain::TaskKind::Probe);
        assert_eq!(
            &planned.tasks()[0].argv[1..],
            ["describe", "--tags", "--always"]
        );
        assert_eq!(planned.tasks()[1].kind, ici_domain::TaskKind::Prepare);
        assert!(!root.path().join(".ici").exists());
        let result = run(root.path(), &environment());
        assert_eq!(
            result.result.gate.selected,
            GateVerdict::Pass,
            "{:?}",
            result.outputs
        );
        let output = &result.outputs["project.python.artifact"];
        let expected = if tagged { "v2.4" } else { "v1.0.0" };
        assert_eq!(output.extra["project_version"], expected);
        assert_eq!(
            output.extra["version_source"],
            if tagged {
                "git-describe"
            } else {
                "fallback:git-failed"
            }
        );
        assert!(
            root.path()
                .join(expected)
                .join("x86_64/lib/pkg/cli.py")
                .is_file()
        );
        assert!(!root.path().join("v__git_describe__").exists());
        assert_eq!(
            output.extra["version_probe"]["exit_code"],
            if tagged { 0 } else { 128 }
        );
    }
}
