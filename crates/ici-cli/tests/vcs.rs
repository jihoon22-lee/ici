use ici_cli::{execute, planning, registry, vcs};
use ici_execution::{Cancellation, Runner};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, process::Command};
fn git(root: &std::path::Path, args: &[&str]) -> String {
    let output = Command::new("/usr/bin/git")
        .args(args)
        .current_dir(root)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap()
}
fn configuration() -> String {
    let mut text = "schema_version=2\n".to_string();
    for check in ici_domain::builtin_catalog()
        .checks
        .iter()
        .filter(|check| check.language == "python")
    {
        text.push_str(&format!(
            "[checks.'{}']\nenabled={}\n",
            check.id,
            check.id == "python.line"
        ));
    }
    text
}
#[test]
fn actual_git_clean_dirty_untracked_states_are_observed_without_plan_execution() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "VALUE=1\n").unwrap();
    fs::write(root.path().join("ici.toml"), configuration()).unwrap();
    git(root.path(), &["init", "--quiet"]);
    git(root.path(), &["add", "."]);
    git(
        root.path(),
        &[
            "-c",
            "user.name=ici-test",
            "-c",
            "user.email=ici@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
    );
    let expected = git(root.path(), &["rev-parse", "HEAD"]).trim().to_string();
    let environment = BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]);
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    let prepared = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
    assert_eq!(
        prepared.document()["source_identity"]["tasks"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(prepared.text().contains("Source identity (read-only)"));
    assert!(!root.path().join(".ici").exists());
    let runner = Runner::with_environment(environment.clone());
    let state = vcs::inspect(&prepared.source_identity, &runner, &Cancellation::default());
    assert_eq!(state.commit.as_deref(), Some(expected.as_str()));
    assert!(state.clean);
    // CLI captures Git before writing its own untracked output namespace.
    let run = execute::execute(
        &invocation,
        root.path(),
        &environment,
        true,
        &Cancellation::default(),
    )
    .unwrap();
    assert_eq!(
        run.result.identity.source.commit.as_deref(),
        Some(expected.as_str())
    );
    assert!(!run.result.identity.source.dirty);
    fs::write(root.path().join("main.py"), "VALUE=2\n").unwrap();
    assert!(!vcs::inspect(&prepared.source_identity, &runner, &Cancellation::default()).clean);
    git(root.path(), &["add", "main.py"]);
    assert!(!vcs::inspect(&prepared.source_identity, &runner, &Cancellation::default()).clean);
    fs::write(root.path().join("untracked.py"), "VALUE=3\n").unwrap();
    assert!(!vcs::inspect(&prepared.source_identity, &runner, &Cancellation::default()).clean);
}
#[test]
fn failed_status_invalid_head_timeout_and_missing_git_cannot_claim_clean() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join(".git")).unwrap();
    fs::write(root.path().join("main.py"), "VALUE=1\n").unwrap();
    fs::write(root.path().join("ici.toml"), configuration()).unwrap();
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    for script in [
        format!(
            "#!/bin/sh\ncase \"$*\" in *rev-parse*) printf '%s\\n' '{}';; *) exit 1;; esac\n",
            "a".repeat(40)
        ),
        "#!/bin/sh\nprintf 'not-a-commit\\n'\n".into(),
        "#!/bin/sh\n/bin/sleep 2\n".into(),
    ] {
        let path = root.path().join("git");
        fs::write(&path, script).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        let environment =
            BTreeMap::from([("PATH".into(), root.path().to_string_lossy().into_owned())]);
        let mut prepared = planning::prepare(&invocation, root.path(), &environment, true).unwrap();
        for task in &mut prepared.source_identity.tasks {
            task.timeout_seconds = Some(0.05);
        }
        let state = vcs::inspect(
            &prepared.source_identity,
            &Runner::with_environment(environment),
            &Cancellation::default(),
        );
        assert!(!state.clean);
        assert!(state.limitation.is_some());
    }
    let prepared = planning::prepare(&invocation, root.path(), &BTreeMap::new(), true).unwrap();
    assert!(prepared.source_identity.tasks.is_empty());
    assert!(
        !vcs::inspect(
            &prepared.source_identity,
            &Runner::with_environment(BTreeMap::new()),
            &Cancellation::default()
        )
        .clean
    );
}
