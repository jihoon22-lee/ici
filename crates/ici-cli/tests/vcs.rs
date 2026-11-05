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

/// An enclosing repository above the project, as a host `/tmp/.git` would be: without a ceiling
/// native still reports that Git identity is unavailable when no `git` runs (never hidden), and
/// `GIT_CEILING_DIRECTORIES` scopes discovery exactly like Git's own — the ceiling and everything
/// above it are not examined, the project's own repository still is.
#[test]
fn git_ceiling_directories_scope_enclosing_repository_discovery_like_git() {
    let base = tempfile::tempdir().unwrap();
    let work = base.path().join("work");
    let project = work.join("project");
    fs::create_dir_all(&project).unwrap();
    fs::write(project.join("main.py"), "VALUE=1\n").unwrap();
    fs::write(project.join("ici.toml"), configuration()).unwrap();
    git(base.path(), &["init", "--quiet"]);
    git(
        base.path(),
        &[
            "-c",
            "user.name=ici-test",
            "-c",
            "user.email=ici@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "enclosing",
        ],
    );
    let enclosing = git(base.path(), &["rev-parse", "HEAD"]).trim().to_string();
    let invocation = registry::parse(["ici", "analyze"]).unwrap();
    let ceiling = work.to_string_lossy().into_owned();
    let observe = |environment: BTreeMap<String, String>| {
        let prepared = planning::prepare(&invocation, &project, &environment, true).unwrap();
        let state = vcs::inspect(
            &prepared.source_identity,
            &Runner::with_environment(environment),
            &Cancellation::default(),
        );
        (prepared.source_identity.tasks.len(), state)
    };

    // No git can run: the enclosing repository is reported, not silently ignored.
    let (tasks, state) = observe(BTreeMap::from([("PATH".into(), String::new())]));
    assert_eq!(tasks, 0);
    assert_eq!(
        state.limitation.as_deref(),
        Some("Git source identity unavailable; file-byte identity remains available")
    );
    assert!(state.commit.is_none() && !state.clean);

    // Real git without a ceiling finds the enclosing repository, like `git rev-parse` would.
    let (tasks, state) = observe(BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]));
    assert_eq!(tasks, 2);
    assert_eq!(state.commit.as_deref(), Some(enclosing.as_str()));

    // A ceiling at the project's parent: neither native nor git looks above it.
    for path in [String::new(), "/usr/bin:/bin".into()] {
        let (tasks, state) = observe(BTreeMap::from([
            ("PATH".into(), path),
            ("GIT_CEILING_DIRECTORIES".into(), ceiling.clone()),
        ]));
        assert_eq!(tasks, 0);
        assert!(state.limitation.is_none() && state.commit.is_none());
    }
    let refused = Command::new("/usr/bin/git")
        .args(["rev-parse", "--verify", "HEAD"])
        .current_dir(&project)
        .env("GIT_CEILING_DIRECTORIES", &ceiling)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .output()
        .unwrap();
    assert!(
        !refused.status.success(),
        "git agrees the ceiling hides the repository"
    );

    // Relative entries are ignored, as Git ignores them.
    let (tasks, _) = observe(BTreeMap::from([
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("GIT_CEILING_DIRECTORIES".into(), "work".into()),
    ]));
    assert_eq!(tasks, 2);

    // The project's own repository is still found below the ceiling.
    git(&project, &["init", "--quiet"]);
    let (tasks, state) = observe(BTreeMap::from([
        ("PATH".into(), String::new()),
        ("GIT_CEILING_DIRECTORIES".into(), ceiling.clone()),
    ]));
    assert_eq!(tasks, 0);
    assert_eq!(
        state.limitation.as_deref(),
        Some("Git source identity unavailable; file-byte identity remains available")
    );
}
