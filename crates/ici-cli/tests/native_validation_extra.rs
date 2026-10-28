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
    let component = "[[components.integrations]]\nname='published launcher'\nargv=['{artifact:release:project:v1.2.3/x86_64/bin/demo}']\nstdout_contains=['packaged-call']\n[components.python]\nexecutable='missing-unused-python'\n";
    write(
        root.path(),
        &config(&["integration"], "", component).replace(
            "sources=['app.py']",
            "source_dirs=['src']\nsources=['src/**/*.py']",
        ),
    );
    let consumed = run(root.path(), &environment());
    assert_eq!(
        consumed.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        consumed.outputs
    );
    assert_eq!(
        consumed.outputs["project.integration"].observation.state,
        TaskState::Succeeded
    );
    fs::write(
        root.path().join("v1.2.3/x86_64/lib/pkg/cli.py"),
        "user changed published bytes\n",
    )
    .unwrap();
    assert!(ici_cli::artifact_build::published_outputs(root.path()).is_err());
    let rejected = planning::prepare(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root.path(),
        &environment(),
        false,
    );
    assert!(rejected.is_err() || rejected.unwrap().plan.checks[0].blocked().is_some());
    assert_eq!(
        fs::read_to_string(root.path().join("v1.2.3/x86_64/lib/pkg/cli.py")).unwrap(),
        "user changed published bytes\n"
    );
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
    let active = ici_cli::artifact_build::published_outputs(root.path()).unwrap();
    assert!(active.contains("v1/x86_64/lib/app.py"));
    assert!(!active.contains("v1/x86_64/lib/retired.py"));
    assert!(active.iter().all(|path| !path.starts_with(".ici/")));
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

#[test]
fn declared_wheels_and_reports_publish_captured_identity_without_ownership() {
    let root = artifact_fixture();
    fs::create_dir(root.path().join("dist")).unwrap();
    fs::write(root.path().join("dist/package.whl"), b"wheel-user-bytes").unwrap();
    fs::write(root.path().join("dist/report.json"), b"{\"measured\":true}").unwrap();
    std::os::unix::fs::symlink("report.json", root.path().join("dist/report-alias.json")).unwrap();
    let path = root.path().join("ici.toml");
    let mut text = fs::read_to_string(&path).unwrap();
    text.push_str("\n[build.artifacts]\npython_wheel=['dist/*.whl']\nreport=['dist/report-alias.json','v1.2.3/x86_64/env.sh']\n");
    fs::write(path, text).unwrap();
    let preview = prepare(root.path(), &environment());
    let plan = &preview.artifact_builds["project.python.artifact"];
    assert!(plan.binary_inputs.contains("dist/package.whl"));
    assert!(plan.binary_inputs.contains("dist/report.json"));
    assert!(!plan.binary_inputs.contains("v1.2.3/x86_64/env.sh"));
    assert!(!root.path().join("v1.2.3").exists());
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    let manifest = result.outputs["project.python.artifact"].extra["artifact_manifests"]
        .as_array()
        .unwrap()
        .iter()
        .find(|manifest| manifest["producer"] == "config.artifacts")
        .unwrap();
    let records = manifest["artifacts"].as_array().unwrap();
    assert_eq!(records.len(), 3);
    let wheel = records
        .iter()
        .find(|record| record["kind"] == "python-wheel")
        .unwrap();
    assert_eq!(wheel["path"], "dist/package.whl");
    assert_eq!(wheel["size"], 16);
    assert_eq!(wheel["sha256"], ici_domain::sha256(b"wheel-user-bytes"));
    assert_eq!(wheel["artifact_id"], "release:project:dist/package.whl");
    assert_eq!(wheel["owned"], false);
    assert!(
        records
            .iter()
            .any(|record| record["path"] == "dist/report.json")
    );
    let owned = ici_cli::artifact_build::owned_outputs(root.path()).unwrap();
    assert!(!owned.contains("dist/package.whl"));
    assert!(!owned.contains("dist/report.json"));
    fs::write(
        root.path().join("dist/package.whl"),
        b"edited-existing-wheel",
    )
    .unwrap();
    let result = run(root.path(), &environment());
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    assert_eq!(
        fs::read(root.path().join("dist/package.whl")).unwrap(),
        b"edited-existing-wheel"
    );
}

#[test]
fn unmatched_and_escaping_declared_artifacts_prevent_preparation() {
    for escaping in [false, true] {
        let root = artifact_fixture();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("outside.whl"), b"private-wheel").unwrap();
        fs::create_dir(root.path().join("dist")).unwrap();
        if escaping {
            std::os::unix::fs::symlink(
                outside.path().join("outside.whl"),
                root.path().join("dist/missing.whl"),
            )
            .unwrap();
        }
        let path = root.path().join("ici.toml");
        let mut text = fs::read_to_string(&path).unwrap();
        text.push_str("\n[build.artifacts]\npython_wheel=['dist/missing.whl']\n");
        fs::write(path, text).unwrap();
        let result = run(root.path(), &environment());
        assert_eq!(
            result.result.gate.selected,
            GateVerdict::Incomplete,
            "{:?}",
            result.outputs
        );
        assert!(!root.path().join("v1.2.3").exists());
        assert_eq!(
            fs::read(outside.path().join("outside.whl")).unwrap(),
            b"private-wheel"
        );
    }
}

#[test]
#[ignore = "explicit compiler shared release packager acceptance"]
fn compiled_release_packager_handles_cpp_only_binary_library_and_soname_alias() {
    use ici_application::snapshot::{InputLimits, InputSnapshot};
    use ici_cli::artifact_build::{self, ArtifactSource};
    use std::{os::unix::fs::PermissionsExt, process::Command};
    let root = artifact_fixture();
    // Metadata has no Python scripts, and the supplied component has no Python source inputs.
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname='cpp-only'\nversion='3'\n",
    )
    .unwrap();
    let preview = prepare(root.path(), &environment());
    let mut component = preview.project.components[0].clone();
    component.files.clear();
    component.languages = vec!["cpp".into()];
    fs::write(
        root.path().join("lib.cpp"),
        "extern \"C\" int answer(){return 42;}\n",
    )
    .unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    let compiler = std::env::var("ICI_TEST_GXX").expect("set ICI_TEST_GXX to explicit compiler");
    for args in [
        vec![
            "-fPIC",
            "-shared",
            "-Wl,-soname,libanswer.so.3",
            "lib.cpp",
            "-o",
            "libanswer.so.3.0",
        ],
        vec!["main.cpp", "-o", "app"],
    ] {
        let result = Command::new(&compiler)
            .args(args)
            .current_dir(root.path())
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
    }
    let mut sources = vec![];
    for (path, kind, alias) in [
        ("app", "executable", None),
        ("libanswer.so.3.0", "shared-library", None),
        ("libanswer.so.3.0", "shared-library", Some("libanswer.so.3")),
    ] {
        let metadata = fs::metadata(root.path().join(path)).unwrap();
        sources.push(ArtifactSource {
            path: path.into(),
            kind: kind.into(),
            sha256: ici_domain::sha256(fs::read(root.path().join(path)).unwrap()),
            size: metadata.len(),
            mode: metadata.permissions().mode() & 0o777,
            destination_name: alias.map(str::to_owned),
        });
    }
    let plan = artifact_build::describe_compiled(
        &preview.project,
        &component,
        "project.cpp.artifact",
        &environment(),
        &sources,
    )
    .unwrap();
    let inputs = plan
        .task
        .input_refs
        .iter()
        .map(|path| {
            Path::new(path)
                .strip_prefix(root.path())
                .unwrap()
                .to_string_lossy()
                .into_owned()
        })
        .collect::<Vec<_>>();
    let captured = InputSnapshot::take(
        root.path(),
        &inputs,
        &[],
        InputLimits {
            file_bytes: 256 * 1024 * 1024,
            total_bytes: 512 * 1024 * 1024,
        },
    )
    .unwrap();
    let result = artifact_build::execute(&plan, &captured, &Cancellation::default());
    assert_eq!(result.observation.state, TaskState::Succeeded, "{result:?}");
    assert_eq!(
        fs::read(root.path().join("v3/x86_64/lib/libanswer.so.3")).unwrap(),
        fs::read(root.path().join("libanswer.so.3.0")).unwrap()
    );
    assert!(
        !fs::symlink_metadata(root.path().join("v3/x86_64/lib/libanswer.so.3"))
            .unwrap()
            .file_type()
            .is_symlink()
    );
    assert!(
        fs::read_to_string(root.path().join("v3/x86_64/env.sh"))
            .unwrap()
            .contains("LD_LIBRARY_PATH")
    );
    let status = Command::new(root.path().join("v3/x86_64/bin/app"))
        .status()
        .unwrap();
    assert!(status.success());
    assert!(
        artifact_build::owned_outputs(root.path())
            .unwrap()
            .contains("v3/x86_64/bin/app")
    );
    let mut changed = sources.clone();
    changed[0].sha256 = ici_domain::sha256(b"wrong producer digest");
    assert!(
        artifact_build::describe_compiled(
            &preview.project,
            &component,
            "project.cpp.artifact",
            &environment(),
            &changed
        )
        .is_err()
    );
    let mut collision = sources.clone();
    collision[2].destination_name = Some("libanswer.so.3.0".into());
    assert!(
        artifact_build::describe_compiled(
            &preview.project,
            &component,
            "project.cpp.artifact",
            &environment(),
            &collision
        )
        .is_err()
    );
}

#[test]
fn git_declared_outputs_resolve_from_frozen_plans_without_filesystem_reads() {
    let root = artifact_fixture();
    let metadata = root.path().join("pyproject.toml");
    fs::write(
        &metadata,
        fs::read_to_string(&metadata)
            .unwrap()
            .replace("version='1.2.3'\n", ""),
    )
    .unwrap();
    let path = root.path().join("ici.toml");
    fs::write(&path, fs::read_to_string(&path).unwrap() + "\n[tools.git]\npath='/usr/bin/true'\n[build.artifacts]\nreport=['v2.4.0/x86_64/env.sh']\n").unwrap();
    let project =
        ici_config::resolve_with_environment(root.path(), None, None, &environment()).unwrap();
    let plan = ici_cli::artifact_build::describe_with_environment(
        &project,
        &project.components[0],
        "project.python.artifact",
        &environment(),
    )
    .unwrap();
    assert_eq!(plan.metadata.version_source, "planned:git-describe");
    fs::remove_dir_all(root.path().join("src")).unwrap();
    fs::remove_file(metadata).unwrap();
    fs::remove_file(path).unwrap();
    let mut outcome = ici_execution::TaskOutcome::empty(&plan.version_probe.as_ref().unwrap().id);
    outcome.state = TaskState::Succeeded;
    outcome.exit_code = Some(0);
    outcome.stdout = b"v2.4.0\n".to_vec();
    let resolved = ici_cli::artifact_build::apply_version_probe(&plan, &outcome).unwrap();
    assert!(
        resolved
            .output_paths
            .contains(&"v2.4.0/x86_64/env.sh".into())
    );
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    outcome.stdout = b"v9\n".to_vec();
    assert!(
        ici_cli::artifact_build::apply_version_probe(&plan, &outcome)
            .unwrap_err()
            .contains("matched nothing")
    );
}

#[test]
fn deep_quality_plan_honors_aliases_explicit_thresholds_and_missing_optional_probe() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        "def test_green():\n    assert True\n",
    )
    .unwrap();
    let text = config(
        &["python.test", "python.coverage"],
        "[engines.test]\nmin_line_cov=55.0\nmin_tem_score=2.5\nchanged_lines=['app.py:1']\n[engines.test.quality]\nrepeat=3\nslow_threshold=0.25\nmutation=true\n",
        "[components.python]\nexecutable='/usr/bin/true'\ntest_paths=['tests']\n",
    );
    write(root.path(), &text);
    let env = BTreeMap::from([("PATH".into(), String::new())]);
    let planned = prepare(root.path(), &env);
    let suite = &planned.validations["project.python.test"][0];
    let options = &suite.context.options;
    assert_eq!(options.repeat_runs, 3);
    assert_eq!(options.slow_threshold_seconds, Some(0.25));
    assert_eq!(options.min_line_cov, Some(55.0));
    assert_eq!(options.min_tem_score, Some(2.5));
    assert_eq!(options.min_func_cov, None);
    assert_eq!(options.changed_lines, vec!["app.py:1"]);
    assert_eq!(options.min_changed_line_cov, Some(100.0));
    assert!(options.mutation_enabled);
    assert!(options.mutation_tool.is_none());
    assert!(
        !suite
            .tasks
            .iter()
            .any(|task| task.id.ends_with(".mutation"))
    );
    assert_eq!(options.environment["PATH"], "/usr/bin");
    assert_eq!(Path::new(&options.environment["PYTHONPATH"]), root.path());
    assert!(!root.path().join(".ici").exists());
}

#[test]
#[ignore = "explicit source-layout project pytest and optional mutation CLI acceptance"]
fn project_source_paths_and_explicit_mutation_probe_preserve_base_test_gate() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("src/app.py"), "ANSWER = 42\n").unwrap();
    fs::write(root.path().join("tests/test_app.py"), "import app, os, pathlib, sys\ndef test_source_layout():\n    assert app.ANSWER == 42\n    assert os.environ['PATH'].split(os.pathsep)[0] == str(pathlib.Path(sys.executable).parent)\n").unwrap();
    fs::write(
        root.path().join("probe.sh"),
        "#!/bin/sh\nprintf '%s' \"$1\" > probe-ran\nexit 7\n",
    )
    .unwrap();
    fs::set_permissions(
        root.path().join("probe.sh"),
        fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    let text = config(&["python.test"], "[engines.test.quality]\nrepeat_runs=2\n[engines.test.quality.mutation]\nenabled=true\ncommand=['./probe.sh','configured-probe']\n", &format!("[components.python]\nexecutable={}\ntest_paths=['tests']\n", quoted(&python()))).replace("sources=['app.py']", "source_dirs=['src']\nsources=['src/**/*.py']");
    write(root.path(), &text);
    let env = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let planned = prepare(root.path(), &env);
    let suite = &planned.validations["project.python.test"][0];
    let probe = suite
        .tasks
        .iter()
        .find(|task| task.id.ends_with(".mutation"))
        .unwrap();
    assert_eq!(Path::new(&probe.argv[0]), root.path().join("probe.sh"));
    assert_eq!(probe.argv[1], "configured-probe");
    assert!(suite.optional_task_ids.contains(&probe.id));
    assert!(!root.path().join("probe-ran").exists());
    let result = run(root.path(), &env);
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        result.outputs
    );
    assert_eq!(
        fs::read_to_string(root.path().join("probe-ran")).unwrap(),
        "configured-probe"
    );
    let output = &result.outputs["project.python.test"];
    assert!(
        output.extra.as_object().unwrap().values().any(|value| value
            .pointer("/mutation/mutation_status")
            .and_then(Value::as_str)
            == Some("unavailable")),
        "{:?}",
        output.extra
    );
    assert!(output.observation.findings.is_empty());
}

#[test]
#[ignore = "explicit project coverage changed-lines policy CLI acceptance"]
fn changed_line_coverage_policy_is_bound_and_keeps_the_uncovered_location() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("src/app.py"),
        "def chosen():\n    return 42\ndef missed():\n    return 0\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        "import app\ndef test_chosen():\n    assert app.chosen() == 42\n",
    )
    .unwrap();
    let text = config(&["python.test", "python.coverage"], "[engines.test]\nchanged_lines=['src/app.py:4']\nmin_changed_line_cov=100.0\nmin_tem_score=5.0\n", &format!("[components.python]\nexecutable={}\ntest_paths=['tests']\n", quoted(&python()))).replace("sources=['app.py']", "source_dirs=['src']\nsources=['src/**/*.py']");
    write(root.path(), &text);
    let env = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let result = run(root.path(), &env);
    assert_eq!(
        result.result.gate.selected,
        GateVerdict::Fail,
        "{:?}",
        result.outputs
    );
    let finding = result
        .result
        .findings
        .iter()
        .find(|finding| finding.rule_id == "ici.test.coverage.changed-lines")
        .unwrap();
    assert_eq!(finding.primary_location.path, "src/app.py");
    assert_eq!(finding.primary_location.start_line, 4);
    assert!(
        result
            .result
            .findings
            .iter()
            .any(|finding| finding.rule_id == "ici.test.coverage.tem")
    );
    assert_eq!(
        result.outputs["project.python.coverage"].observation.state,
        TaskState::Succeeded
    );
}

#[test]
#[ignore = "explicit executed test scope baseline fail/skip/pass CLI acceptance"]
fn skipped_pytest_case_carries_its_baseline_failure_until_it_completes() {
    use ici_execution::storage;
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("tests")).unwrap();
    let test_file = root.path().join("tests/test_app.py");
    fs::write(
        &test_file,
        "import pytest\ndef test_old():\n    assert False\ndef test_green():\n    assert True\n",
    )
    .unwrap();
    write(
        root.path(),
        &config(
            &["python.test"],
            "",
            &format!(
                "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
                quoted(&python())
            ),
        ),
    );
    let env = environment();
    let first = run(root.path(), &env);
    assert_eq!(
        first.result.gate.selected,
        GateVerdict::Fail,
        "{:?}",
        first.outputs
    );
    let old = first
        .result
        .findings
        .iter()
        .find(|finding| finding.rule_id == "pytest.failed")
        .unwrap();
    storage::write_result(
        &root.path().join("baseline.json"),
        &first.result,
        storage::WriteMode::CreateNew,
    )
    .unwrap();
    let compared =
        registry::parse(["ici", "next", "verify", "--baseline", "baseline.json"]).unwrap();
    fs::write(&test_file, "import pytest\ndef test_old():\n    pytest.skip('temporarily unavailable')\ndef test_green():\n    assert True\n").unwrap();
    let second = execute::execute(
        &compared,
        root.path(),
        &env,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        second.outputs["project.python.test"].observation.state,
        TaskState::Succeeded
    );
    let comparison = second.result.baseline.as_ref().unwrap();
    assert!(
        comparison.carried.contains(&old.fingerprint),
        "{comparison:?}"
    );
    assert!(
        !comparison.resolved.contains(&old.fingerprint),
        "{comparison:?}"
    );
    fs::write(
        &test_file,
        "import pytest\ndef test_old():\n    assert True\ndef test_green():\n    assert True\n",
    )
    .unwrap();
    let third = execute::execute(
        &compared,
        root.path(),
        &env,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    let comparison = third.result.baseline.as_ref().unwrap();
    assert!(
        comparison.resolved.contains(&old.fingerprint),
        "{comparison:?}"
    );
}

fn copy_project_tool_tree(source: &Path, destination: &Path) {
    if source.is_dir() {
        fs::create_dir_all(destination).unwrap();
        for entry in fs::read_dir(source).unwrap() {
            let entry = entry.unwrap();
            if entry.file_name() == "__pycache__" {
                continue;
            }
            copy_project_tool_tree(&entry.path(), &destination.join(entry.file_name()));
        }
    } else {
        fs::copy(source, destination).unwrap();
    }
}
fn isolated_pytest_without_coverage(root: &Path) -> String {
    use std::process::Command;
    let selected = python();
    let metadata = Command::new(&selected)
        .args([
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .unwrap();
    assert!(metadata.status.success());
    let purelib = std::path::PathBuf::from(String::from_utf8(metadata.stdout).unwrap().trim());
    let virtualenv = root.join(".venv");
    let created = Command::new(&selected)
        .args(["-m", "venv", "--without-pip"])
        .arg(&virtualenv)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .unwrap();
    assert!(
        created.status.success(),
        "{}",
        String::from_utf8_lossy(&created.stderr)
    );
    let executable = virtualenv.join("bin/python");
    let metadata = Command::new(&executable)
        .args([
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .unwrap();
    assert!(metadata.status.success());
    let destination = std::path::PathBuf::from(String::from_utf8(metadata.stdout).unwrap().trim());
    let packages = [
        "pytest",
        "_pytest",
        "iniconfig",
        "packaging",
        "pluggy",
        "pygments",
        "exceptiongroup",
        "tomli",
        "py.py",
        "typing_extensions.py",
        "typing_extensions",
    ];
    for entry in fs::read_dir(&purelib).unwrap() {
        let entry = entry.unwrap();
        let name = entry.file_name().to_string_lossy().into_owned();
        if packages.iter().any(|package| {
            name == *package
                || name.starts_with(&format!("{package}-")) && name.ends_with(".dist-info")
        }) {
            copy_project_tool_tree(&entry.path(), &destination.join(entry.file_name()));
        }
    }
    let pytest = Command::new(&executable)
        .args(["-m", "pytest", "--version"])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        .output()
        .unwrap();
    assert!(
        pytest.status.success(),
        "{}",
        String::from_utf8_lossy(&pytest.stderr)
    );
    let coverage = Command::new(&executable)
        .args(["-m", "coverage", "--version"])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .unwrap();
    assert!(!coverage.status.success());
    assert!(String::from_utf8_lossy(&coverage.stderr).contains("No module named coverage"));
    executable.to_string_lossy().into_owned()
}
#[test]
#[ignore = "explicit real pytest-only runtime and optional/required coverage CLI acceptance"]
fn missing_coverage_keeps_real_pytest_evidence_and_respects_legacy_and_explicit_required_policy() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(
        root.path().join("src/app.py"),
        "def answer():\n    return 42\n",
    )
    .unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        "import app\ndef test_answer():\n    assert app.answer() == 42\n",
    )
    .unwrap();
    let executable = isolated_pytest_without_coverage(root.path());
    let text = config(
        &["python.test", "python.coverage"],
        "",
        &format!(
            "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
            quoted(&executable)
        ),
    )
    .replace(
        "sources=['app.py']",
        "source_dirs=['src']\nsources=['src/**/*.py']",
    )
    .replace(
        "[checks.\"python.coverage\"]\nenabled=true\nrequired=true\n",
        "[checks.\"python.coverage\"]\nenabled=true\n",
    );
    let environment = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ]);
    for (route, required, explicit) in [
        ("verify", false, false),
        ("verify", true, false),
        ("next", false, false),
        ("verify", false, true),
    ] {
        let mut configured = text.clone();
        if required {
            configured = configured.replace(
                "[[components]]",
                "[engines.test]\ncoverage_required=true\n[[components]]",
            );
        }
        if explicit {
            configured = configured.replace(
                "[checks.\"python.coverage\"]\nenabled=true\n",
                "[checks.\"python.coverage\"]\nenabled=true\nrequired=true\n",
            );
        }
        write(root.path(), &configured);
        let invocation = if route == "next" {
            registry::parse(["ici", "next", "verify"]).unwrap()
        } else {
            registry::parse(["ici", "verify"]).unwrap()
        };
        let planned = planning::prepare(&invocation, root.path(), &environment, false).unwrap();
        let policy = planned
            .plan
            .checks
            .iter()
            .find(|check| check.check.id == "python.coverage")
            .unwrap();
        assert_eq!(
            policy.check.required,
            required || explicit || route == "next"
        );
        let suite = &planned.validations["project.python.test"][0];
        assert_eq!(suite.conditional_steps.len(), 3);
        let run = execute::execute(
            &invocation,
            root.path(),
            &environment,
            false,
            &Cancellation::default(),
        )
        .unwrap();
        assert_eq!(
            run.outputs["project.python.test"].observation.state,
            TaskState::Succeeded,
            "{:?}",
            run.outputs
        );
        assert!(
            run.outputs["project.python.test"]
                .observation
                .measurements
                .iter()
                .any(|metric| metric.name == "pytest.cases"
                    && metric.numerator == Some(1)
                    && metric.denominator == Some(1))
        );
        let coverage = &run.outputs["project.python.coverage"];
        assert_eq!(coverage.observation.state, TaskState::Failed);
        assert!(coverage.score.is_none());
        assert!(coverage.observation.measurements.is_empty());
        assert_eq!(
            run.result.gate.selected,
            if required || explicit || route == "next" {
                GateVerdict::Incomplete
            } else {
                GateVerdict::Pass
            },
            "route={route} required={required} explicit={explicit}: {:?}",
            run.outputs
        );
    }
}

#[test]
fn canonical_declared_integration_artifacts_require_exact_typed_membership_and_containment() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join(".ici")).unwrap();
    fs::write(root.path().join(".ici/report.txt"), "declared report\n").unwrap();
    fs::write(root.path().join("consumer"), "#!/bin/sh\n/bin/cat \"$1\"\n").unwrap();
    fs::set_permissions(
        root.path().join("consumer"),
        fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    let configure = |identity: &str| {
        config(
            &["integration"],
            "[build.artifacts]\nreport=['consumer','**/report.txt']\n",
            &format!(
                "[[components.integrations]]\nname='typed consumer'\nargv=['{{artifact:release:project:consumer}}',{}]\nstdout_contains=['declared report']\n[components.python]\nexecutable='missing-unused-python'\n",
                quoted(&format!("{{artifact:{identity}}}"))
            ),
        )
    };
    for identity in ["release:project:.ici/report.txt", ".ici/report.txt"] {
        write(root.path(), &configure(identity));
        let planned = prepare(root.path(), &environment());
        assert!(
            planned
                .plan
                .checks
                .iter()
                .all(|check| check.blocked().is_none())
        );
        assert!(!root.path().join(".ici/rust-v1").exists());
    }
    let actual = run(root.path(), &environment());
    assert_eq!(
        actual.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        actual.outputs
    );
    fs::write(root.path().join("unpublished.txt"), "user data\n").unwrap();
    for identity in [
        "release:project:unpublished.txt",
        "release:shadow:.ici/report.txt",
        "release:project:../escape",
        "release:project:missing.txt",
    ] {
        write(root.path(), &configure(identity));
        let blocked = prepare(root.path(), &environment());
        assert!(blocked.plan.checks[0].blocked().is_some(), "{identity}");
    }
    fs::remove_file(root.path().join(".ici/report.txt")).unwrap();
    let outside = tempfile::NamedTempFile::new().unwrap();
    std::os::unix::fs::symlink(outside.path(), root.path().join(".ici/report.txt")).unwrap();
    write(root.path(), &configure("release:project:.ici/report.txt"));
    assert!(
        prepare(root.path(), &environment()).plan.checks[0]
            .blocked()
            .is_some()
    );
    assert_eq!(
        fs::read_to_string(root.path().join("unpublished.txt")).unwrap(),
        "user data\n"
    );
}

#[test]
#[ignore = "explicit project pytest repeat timeout and later independent repeat acceptance"]
fn optional_quality_repeat_timeout_preserves_measured_base_and_runs_the_later_attempt() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("tests/test_repeat.py"), "from pathlib import Path\nimport time\ndef test_repeat():\n    counter = Path(__file__).with_name('attempts.txt')\n    attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n    counter.write_text(str(attempt))\n    if attempt == 2:\n        time.sleep(5)\n    assert True\n").unwrap();
    write(
        root.path(),
        &config(
            &["python.test"],
            "[engines.test.quality]\nenabled=true\nrepeat_runs=3\nslow_test_threshold=0\ntimeout=1.0\nmode='report'\n",
            &format!(
                "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
                quoted(&python())
            ),
        ),
    );
    let env = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ]);
    let actual = run(root.path(), &env);
    assert_eq!(
        actual.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        actual.outputs
    );
    let output = &actual.outputs["project.python.test"];
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(!output.observation.timed_out);
    let details = output
        .extra
        .get("test_quality")
        .map(|_| &output.extra)
        .or_else(|| {
            output
                .extra
                .as_object()
                .unwrap()
                .values()
                .find(|value| value.get("test_quality").is_some())
        })
        .unwrap();
    assert_eq!(details["test_quality"]["repeat_timeouts"], 1, "{output:?}");
    assert_eq!(details["test_quality"]["repeat_runs"], 3);
    assert_eq!(details["test_quality"]["flaky_tests"], 0);
    assert_eq!(
        fs::read_to_string(root.path().join("tests/attempts.txt")).unwrap(),
        "3"
    );
    assert!(
        details["optional_steps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|step| step["timed_out"] == true)
    );
}

#[test]
#[ignore = "explicit stdlib-only Python runtime with absent pytest and optional coverage CLI acceptance"]
fn missing_pytest_uses_real_unittest_with_optional_coverage_and_preserves_fail_skip_empty_states() {
    use std::process::Command;
    for coverage_present in [false, true] {
        let root = tempfile::tempdir().unwrap();
        clean_source(root.path());
        fs::create_dir(root.path().join("tests")).unwrap();
        let selected = python();
        let virtualenv = root.path().join(".venv");
        assert!(
            Command::new(&selected)
                .args(["-m", "venv", "--without-pip"])
                .arg(&virtualenv)
                .status()
                .unwrap()
                .success()
        );
        let executable = virtualenv.join("bin/python");
        if coverage_present {
            let purelib = Command::new(&selected)
                .args([
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ])
                .output()
                .unwrap();
            assert!(purelib.status.success());
            let source =
                std::path::PathBuf::from(String::from_utf8(purelib.stdout).unwrap().trim());
            let purelib = Command::new(&executable)
                .args([
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ])
                .output()
                .unwrap();
            assert!(purelib.status.success());
            let destination =
                std::path::PathBuf::from(String::from_utf8(purelib.stdout).unwrap().trim());
            for entry in fs::read_dir(source).unwrap() {
                let entry = entry.unwrap();
                if entry.file_name().to_string_lossy().starts_with("coverage") {
                    copy_project_tool_tree(&entry.path(), &destination.join(entry.file_name()));
                }
            }
            assert!(
                Command::new(&executable)
                    .args(["-m", "coverage", "--version"])
                    .output()
                    .unwrap()
                    .status
                    .success()
            );
        }
        let pytest = Command::new(&executable)
            .args(["-m", "pytest", "--version"])
            .output()
            .unwrap();
        assert_eq!(pytest.status.code(), Some(1));
        assert!(String::from_utf8_lossy(&pytest.stderr).contains("No module named pytest"));
        let configured = config(
            &["python.test", "python.coverage"],
            "",
            &format!(
                "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
                quoted(&executable.to_string_lossy())
            ),
        )
        .replace(
            "[checks.\"python.coverage\"]\nenabled=true\nrequired=true\n",
            "[checks.\"python.coverage\"]\nenabled=true\nrequired=false\n",
        );
        write(root.path(), &configured);
        let cases = if coverage_present {
            vec![(
                "def test_ok(self):\n        self.assertTrue(True)",
                GateVerdict::Pass,
            )]
        } else {
            vec![
                (
                    "def test_ok(self):\n        self.assertTrue(True)",
                    GateVerdict::Pass,
                ),
                (
                    "def test_bad(self):\n        self.assertTrue(False)",
                    GateVerdict::Fail,
                ),
                (
                    "@unittest.skip('optional')\n    def test_skip(self):\n        pass",
                    GateVerdict::Incomplete,
                ),
                ("def helper(self):\n        pass", GateVerdict::Incomplete),
            ]
        };
        for (body, verdict) in cases {
            fs::write(
                root.path().join("tests/test_standard.py"),
                format!("import unittest\nclass Standard(unittest.TestCase):\n    {body}\n"),
            )
            .unwrap();
            let actual = run(
                root.path(),
                &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
            );
            assert_eq!(
                actual.result.gate.selected, verdict,
                "coverage={coverage_present}: {:?}",
                actual.outputs
            );
            let test = &actual.outputs["project.python.test"];
            let policy = &actual.outputs["project.python.coverage"];
            assert!(policy.observation.measurements.is_empty());
            assert!(policy.score.is_none());
            assert!(matches!(
                policy.observation.state,
                TaskState::Failed | TaskState::Blocked
            ));
            if verdict != GateVerdict::Incomplete {
                assert_eq!(test.observation.state, TaskState::Succeeded);
                assert!(
                    test.observation
                        .measurements
                        .iter()
                        .any(|metric| metric.name == "unittest.cases"
                            && metric.denominator == Some(1))
                );
            }
            if verdict == GateVerdict::Fail {
                assert!(
                    test.observation
                        .findings
                        .iter()
                        .any(|finding| finding.rule_id == "unittest.failed")
                );
            }
        }
    }
}

#[test]
fn optional_empty_integration_is_located_not_applicable_and_required_empty_scope_is_incomplete() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    for required in [false, true] {
        let configured = config(&["integration"], "", "").replace(
            "[checks.\"integration\"]\nenabled=true\nrequired=true\n",
            &format!("[checks.\"integration\"]\nenabled=true\nrequired={required}\n"),
        );
        write(root.path(), &configured);
        let planned = prepare(root.path(), &BTreeMap::new());
        assert!(planned.plan.checks[0].tasks().is_empty());
        let actual = run(root.path(), &BTreeMap::new());
        assert_eq!(
            actual.result.gate.selected,
            if required {
                GateVerdict::Incomplete
            } else {
                GateVerdict::Pass
            },
            "{:?}",
            actual.outputs
        );
        if !required {
            let output = &actual.outputs["project.integration"];
            assert_eq!(output.status, "SKIP");
            assert!(
                output
                    .targets
                    .iter()
                    .any(|target| target.target_name == "Integration:NotApplicable"
                        && target.start_line == 1)
            );
            assert!(
                output
                    .observation
                    .measurements
                    .iter()
                    .all(|metric| metric.evidence == ici_domain::EvidenceLevel::NotApplicable)
            );
            assert!(output.observation.findings.is_empty());
        }
    }
}

#[test]
#[ignore = "explicit pytest expected-failure count and baseline carry acceptance"]
fn expected_pytest_failure_counts_as_tool_pass_and_carries_a_prior_normal_failure() {
    let root = tempfile::tempdir().unwrap();
    clean_source(root.path());
    fs::create_dir(root.path().join("tests")).unwrap();
    let source = root.path().join("tests/test_expected.py");
    fs::write(
        &source,
        "import pytest\ndef test_known():\n    assert False\n",
    )
    .unwrap();
    write(
        root.path(),
        &config(
            &["python.test"],
            "",
            &format!(
                "[components.python]\nexecutable={}\ntest_paths=['tests']\n",
                quoted(&python())
            ),
        ),
    );
    let env = BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
    ]);
    let first = run(root.path(), &env);
    assert_eq!(first.result.gate.selected, GateVerdict::Fail);
    let fingerprint = first
        .result
        .findings
        .iter()
        .find(|finding| finding.rule_id == "pytest.failed")
        .unwrap()
        .fingerprint
        .clone();
    ici_execution::storage::write_result(
        &root.path().join("baseline.json"),
        &first.result,
        ici_execution::storage::WriteMode::CreateNew,
    )
    .unwrap();
    fs::write(
        &source,
        "import pytest\n@pytest.mark.xfail(strict=True)\ndef test_known():\n    assert False\n",
    )
    .unwrap();
    let actual = execute::execute(
        &registry::parse(["ici", "next", "verify", "--baseline", "baseline.json"]).unwrap(),
        root.path(),
        &env,
        false,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        actual.result.gate.selected,
        GateVerdict::Pass,
        "{:?}",
        actual.outputs
    );
    let output = &actual.outputs["project.python.test"];
    assert!(
        output
            .observation
            .measurements
            .iter()
            .any(|metric| metric.name == "pytest.cases"
                && metric.numerator == Some(1)
                && metric.denominator == Some(1))
    );
    let details = output
        .extra
        .get("test_execution")
        .map(|_| &output.extra)
        .or_else(|| {
            output
                .extra
                .as_object()
                .unwrap()
                .values()
                .find(|value| value.get("test_execution").is_some())
        })
        .unwrap();
    assert_eq!(details["passed_tests"], 1);
    assert_eq!(details["total_tests"], 1);
    assert_eq!(details["test_execution"]["complete"], true);
    assert_eq!(
        details["test_outcomes"]["tests/test_expected.py::test_known"],
        "XFAIL"
    );
    assert!(details["line_coverage"].is_null());
    let comparison = actual.result.baseline.as_ref().unwrap();
    assert!(comparison.carried.contains(&fingerprint), "{comparison:?}");
    assert!(
        !comparison.resolved.contains(&fingerprint),
        "{comparison:?}"
    );
}
