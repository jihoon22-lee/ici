//! Read-only Git identity is descriptive; captured file bytes remain the cache identity.
use ici_config::ResolvedProject;
use ici_domain::{TaskKind, TaskSpec, TaskState};
use ici_execution::{Cancellation, Runner};
use ici_providers::resolver::ToolRequest;
use serde::Serialize;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Clone, Default, Serialize)]
pub struct VcsPlan {
    pub tasks: Vec<TaskSpec>,
    pub limitation: Option<String>,
}
#[derive(Default)]
pub struct VcsState {
    pub commit: Option<String>,
    pub clean: bool,
    pub limitation: Option<String>,
}
/// Whether Git's repository discovery from `root` can reach a `.git`: like Git, it examines
/// `root` and its parents but never enters a `GIT_CEILING_DIRECTORIES` entry (absolute paths
/// only, compared by realpath) or anything above one, so the run environment can scope it.
fn discoverable(root: &Path, environment: &BTreeMap<String, String>) -> bool {
    let real = |path: &Path| std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let ceilings: Vec<PathBuf> = environment
        .get("GIT_CEILING_DIRECTORIES")
        .into_iter()
        .flat_map(|value| value.split(':'))
        .map(Path::new)
        .filter(|entry| entry.is_absolute())
        .map(real)
        .collect();
    let root = real(root);
    for (index, directory) in root.ancestors().enumerate() {
        if index > 0 && ceilings.iter().any(|ceiling| ceiling == directory) {
            return false;
        }
        if directory.join(".git").exists() {
            return true;
        }
    }
    false
}
pub fn plan(project: &ResolvedProject, environment: &BTreeMap<String, String>) -> VcsPlan {
    if !discoverable(&project.root, environment) {
        return VcsPlan::default();
    }
    let tool = ToolRequest {
        id: "git".into(),
        explicit: project
            .tools
            .get("git")
            .and_then(|tool| tool.path.as_ref())
            .map(PathBuf::from),
        project_root: project.root.clone(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve();
    let tool = match tool {
        Ok(tool) => tool,
        Err(_) => {
            return VcsPlan {
                tasks: vec![],
                limitation: Some(
                    "Git source identity unavailable; file-byte identity remains available".into(),
                ),
            };
        }
    };
    let tasks = [
        ("source.git.head", vec!["rev-parse", "--verify", "HEAD"]),
        (
            "source.git.status",
            vec![
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "-z",
                "--",
                ".",
            ],
        ),
    ]
    .into_iter()
    .map(|(id, args)| {
        let mut argv = vec![
            tool.path.to_string_lossy().into_owned(),
            "--no-optional-locks".into(),
            "-c".into(),
            "core.fsmonitor=false".into(),
            "-c".into(),
            "core.untrackedCache=false".into(),
        ];
        argv.extend(args.into_iter().map(str::to_owned));
        let mut task = TaskSpec::command(id, "git", argv, project.root.to_string_lossy());
        task.kind = TaskKind::Probe;
        task.timeout_seconds = Some(2.0);
        task.output_limit_bytes = Some(512 * 1024);
        task.tool_digest = Some(tool.digest.clone());
        task.cacheable = false;
        task
    })
    .collect();
    VcsPlan {
        tasks,
        limitation: None,
    }
}
/// Frozen `_git_project_version`: `git describe --tags --always` from the project root within two
/// seconds. No Git, no repository, a timeout or empty output all read as no version.
pub fn describe(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
) -> Option<String> {
    let tool = ToolRequest {
        id: "git".into(),
        explicit: project
            .tools
            .get("git")
            .and_then(|tool| tool.path.as_ref())
            .map(PathBuf::from),
        project_root: project.root.clone(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .ok()?;
    let argv = vec![
        tool.path.to_string_lossy().into_owned(),
        "--no-optional-locks".into(),
        "-c".into(),
        "core.fsmonitor=false".into(),
        "describe".into(),
        "--tags".into(),
        "--always".into(),
    ];
    let mut task = TaskSpec::command(
        "source.git.describe",
        "git",
        argv,
        project.root.to_string_lossy(),
    );
    task.kind = TaskKind::Probe;
    task.timeout_seconds = Some(2.0);
    task.output_limit_bytes = Some(8192);
    task.tool_digest = Some(tool.digest);
    task.cacheable = false;
    let outcome =
        Runner::with_environment(environment.clone()).run(&task, &Cancellation::default());
    if outcome.state != TaskState::Succeeded
        || outcome.exit_code != Some(0)
        || outcome.timed_out
        || outcome.truncated
    {
        return None;
    }
    let text = String::from_utf8_lossy(&outcome.stdout).trim().to_owned();
    (!text.is_empty()).then_some(text)
}
pub fn inspect(plan: &VcsPlan, runner: &Runner, cancellation: &Cancellation) -> VcsState {
    let mut state = VcsState {
        limitation: plan.limitation.clone(),
        ..Default::default()
    };
    for task in &plan.tasks {
        let outcome = runner.run(task, cancellation);
        if outcome.state != TaskState::Succeeded
            || outcome.exit_code != Some(0)
            || outcome.timed_out
            || outcome.truncated
            || !outcome.cleanup.is_clean()
        {
            state.clean = false;
            state.limitation = Some(format!(
                "{} unavailable; source cleanliness is unknown",
                task.id
            ));
            return state;
        }
        if task.id == "source.git.head" {
            let commit = String::from_utf8_lossy(&outcome.stdout).trim().to_owned();
            if !matches!(commit.len(), 40 | 64)
                || !commit.bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                state.limitation = Some(
                    "Git returned an invalid commit identity; source cleanliness is unknown".into(),
                );
                return state;
            }
            state.commit = Some(commit);
        } else {
            state.clean = state.commit.is_some() && outcome.stdout.is_empty();
        }
    }
    state
}
