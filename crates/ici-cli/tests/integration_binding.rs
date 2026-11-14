//! Explicit process and component ownership contracts survive native binding.
use ici_cli::{execute, planning, registry};
use ici_domain::{GateVerdict, TaskState};
use ici_execution::Cancellation;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn configuration(extra: &str, component: &str) -> String {
    let mut text = "schema_version=2\nprofile='deep'\n".to_owned();
    for check in ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled={}\nrequired={}\n",
            check.id,
            check.id == "integration",
            check.id == "integration"
        ));
    }
    text.push_str(extra);
    text.push_str(
        "\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\nsources=['app.py']\n",
    );
    text.push_str(component);
    text
}
fn prepare(root: &Path, environment: &BTreeMap<String, String>) -> planning::PreparedPlan {
    planning::prepare(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root,
        environment,
        false,
    )
    .unwrap()
}
fn run(root: &Path, environment: &BTreeMap<String, String>) -> execute::ExecutedRun {
    execute::execute(
        &registry::parse(["ici", "next", "verify"]).unwrap(),
        root,
        environment,
        false,
        &Cancellation::default(),
    )
    .unwrap()
}
#[test]
fn integration_accepts_declared_alias_and_named_path_targets_without_core_fallback() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    let tools = root.path().join("tools");
    fs::create_dir(&tools).unwrap();
    let selected = tools.join("selected-runtime");
    fs::write(&selected, "#!/bin/sh\nprintf 'measured-contract\\n'\n").unwrap();
    fs::set_permissions(&selected, fs::Permissions::from_mode(0o755)).unwrap();
    let environment = BTreeMap::from([("PATH".into(), tools.to_string_lossy().into_owned())]);
    for (executable, python) in [
        (
            "'{python:current}'".to_owned(),
            format!(
                "[components.python]\nexecutable={}\n",
                serde_json::to_string(&selected).unwrap()
            ),
        ),
        (
            "'{python:declared}'".into(),
            format!(
                "[components.python]\nexecutable={}\n",
                serde_json::to_string(&selected).unwrap()
            ),
        ),
        (
            "'{python:chosen}'".into(),
            "[components.integrations.python_targets]\nchosen='selected-runtime'\n".into(),
        ),
    ] {
        fs::write(root.path().join("ici.toml"), configuration("", &format!("[[components.integrations]]\nname='process'\nargv=[{executable}]\nstdout_contains=['measured-contract']\n{python}"))).unwrap();
        let plan = prepare(root.path(), &environment);
        let task = &plan.validations["project.integration"][0].tasks[0];
        assert_eq!(task.argv[0], selected.to_string_lossy());
        assert!(task.tool_digest.is_some());
        let actual = run(root.path(), &environment);
        assert_eq!(
            actual.result.gate.selected,
            GateVerdict::Pass,
            "{:?}",
            actual.outputs
        );
        assert_eq!(
            actual.outputs["project.integration"].observation.state,
            TaskState::Succeeded
        );
    }
    let missing = prepare(root.path(), &BTreeMap::new());
    assert!(
        missing.plan.checks[0]
            .blocked()
            .unwrap()
            .contains("not found")
    );
    fs::write(
        root.path().join("ici.toml"),
        configuration(
            "",
            "[[components.integrations]]\nname='raw'\nargv=['selected-runtime']\n",
        ),
    )
    .unwrap();
    assert!(
        ici_config::resolve_with_environment(root.path(), None, None, &environment)
            .unwrap_err()
            .to_string()
            .contains("typed")
    );
}
#[test]
fn integration_build_placeholders_require_the_components_link_or_declared_need() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("app.py"), "VALUE=1\n").unwrap();
    fs::create_dir(root.path().join("dist")).unwrap();
    fs::write(root.path().join("dist/payload.txt"), "payload\n").unwrap();
    fs::write(
        root.path().join("dist/consumer"),
        "#!/bin/sh\ntest -s \"$1\"\n",
    )
    .unwrap();
    fs::set_permissions(
        root.path().join("dist/consumer"),
        fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    let extra = "[builds.payload]\ndirectory='dist'\nartifacts=['consumer','payload.txt']\n";
    let case = "[[components.integrations]]\nname='linked'\nargv=['{artifact:payload/consumer}','{artifact:payload/payload.txt}']\n";
    for (link, allowed) in [
        ("", false),
        ("build='payload'\n", true),
        ("needs=['payload']\n", true),
    ] {
        fs::write(
            root.path().join("ici.toml"),
            configuration(extra, &format!("{link}{case}")),
        )
        .unwrap();
        let plan = prepare(root.path(), &BTreeMap::new());
        if allowed {
            assert!(plan.plan.checks[0].blocked().is_none());
            let actual = run(root.path(), &BTreeMap::new());
            assert_eq!(
                actual.result.gate.selected,
                GateVerdict::Pass,
                "{:?}",
                actual.outputs
            );
        } else {
            assert!(
                plan.plan.checks[0]
                    .blocked()
                    .unwrap()
                    .contains("not linked")
            );
        }
        assert_eq!(
            fs::read_to_string(root.path().join("dist/payload.txt")).unwrap(),
            "payload\n"
        );
    }
}
