//! Shared visible package-flag observation for explicit legacy compiler operations.
use super::*;
use ici_execution::{Cancellation, Scheduler, TaskOutcome};
use std::fs;

#[derive(Clone, Debug)]
pub(crate) struct LegacyFlagsPlan {
    pub compiler: ResolvedTool,
    pub includes: Vec<String>,
    pub probes: Vec<TaskSpec>,
    pub packages: Vec<String>,
    pub limitations: Vec<String>,
    pub tools: BTreeMap<String, ResolvedTool>,
}
pub(crate) struct ObservedFlags {
    pub arguments: Vec<String>,
    pub records: Vec<Value>,
    pub outcomes: Vec<Option<i32>>,
    pub limitations: Vec<String>,
}
pub(crate) enum FlagsFailure {
    Invalid(String),
    Execution(Box<TaskOutcome>),
}
fn include_flags(project: &ResolvedProject, component: &Component) -> Result<Vec<String>, String> {
    let mut directories = BTreeSet::new();
    let mut pending = vec![(project.root.join("include"), 0usize)];
    let mut entries = 0usize;
    while let Some((directory, depth)) = pending.pop() {
        let metadata = match fs::symlink_metadata(&directory) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.to_string()),
        };
        if metadata.file_type().is_symlink() {
            return Err("fallback include directory traverses a symlink".into());
        }
        if !metadata.is_dir() {
            continue;
        }
        if depth > 32 {
            return Err("fallback include directory tree exceeds 32 levels".into());
        }
        let canonical = directory
            .canonicalize()
            .map_err(|error| error.to_string())?;
        if !canonical.starts_with(&project.root) {
            return Err("fallback include directory leaves workspace".into());
        }
        directories.insert(canonical);
        for entry in fs::read_dir(&directory).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            entries += 1;
            if entries > 4096 {
                return Err("fallback include tree exceeds 4096 entries".into());
            }
            let kind = entry.file_type().map_err(|error| error.to_string())?;
            if kind.is_dir()
                && !matches!(
                    entry.file_name().to_str(),
                    Some(".git" | ".hg" | ".svn" | ".venv" | "build" | "dist" | "node_modules")
                )
            {
                pending.push((entry.path(), depth + 1));
            }
        }
    }
    for source in crate::inputs::source_roots(component, &project.root) {
        let path = project.root.join(source).join("include");
        if let Ok(metadata) = fs::symlink_metadata(&path) {
            if metadata.file_type().is_symlink() {
                return Err("fallback source include directory is a symlink".into());
            }
            if metadata.is_dir() {
                let canonical = path.canonicalize().map_err(|error| error.to_string())?;
                if !canonical.starts_with(&project.root) {
                    return Err("fallback source include directory leaves workspace".into());
                }
                directories.insert(canonical);
            }
        }
    }
    Ok(directories
        .into_iter()
        .map(|path| format!("-I{}", path.display()))
        .collect())
}

fn configured_tool(
    project: &ResolvedProject,
    name: &str,
    environment: &BTreeMap<String, String>,
) -> Result<ResolvedTool, String> {
    ToolRequest {
        id: name.into(),
        explicit: project
            .tools
            .get(name)
            .and_then(|tool| tool.path.as_deref())
            .map(|path| crate::files::under(&project.root, path)),
        project_root: project.root.clone(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(|error| error.to_string())
}

pub(crate) fn compiler_available(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
) -> bool {
    configured_tool(project, "g++", environment).is_ok()
}

/// One bounded `pkg-config --cflags <package>` task (5 s, 1 MiB, the pkg-config search
/// variables inherited), shared by the fallback's execution probes and the verify preparation
/// probe of the analysis context.
fn package_probe(
    project: &ResolvedProject,
    tool: &ResolvedTool,
    id: String,
    package: &str,
) -> TaskSpec {
    let mut task = TaskSpec::command(
        id,
        "pkg-config",
        vec![
            tool.path.display().to_string(),
            "--cflags".into(),
            package.to_owned(),
        ],
        project.root.display().to_string(),
    );
    task.timeout_seconds = Some(5.0);
    task.output_limit_bytes = Some(1024 * 1024);
    task.tool_digest = Some(tool.digest.clone());
    task.env_inherit = Some(
        [
            "PATH",
            "LANG",
            "LC_ALL",
            "PKG_CONFIG_PATH",
            "PKG_CONFIG_LIBDIR",
            "PKG_CONFIG_SYSROOT_DIR",
        ]
        .map(str::to_owned)
        .to_vec(),
    );
    task
}

/// Frozen `discover_project_model` -> `get_all_cpp_includes` -> `get_cpp_pkg_config_flags`:
/// the `project.cpp_pkg_config` flags of the historical `verify` analysis context, probed once
/// while `verify` prepares (root ruling 2026-11-14), for every backend, a CMake or qmake
/// database run included, and never while the report is assembled. Nothing runs when no
/// package is configured. Like frozen, the packages run in configuration order, a package whose
/// probe does not exit 0 adds nothing, and no pkg-config on the search path adds nothing.
/// Native bounds (the fallback probe's): a package expression that is empty (frozen drops
/// those too), starts with `-` or holds a control character is not passed to pkg-config, a
/// probe past 5 s or 1 MiB, ended by a signal or printing non-UTF-8/NUL output adds nothing
/// (frozen: 300 s, 1,000,000 characters), and more than 128 packages or 4,096 flags add none.
pub(crate) fn preparation_flags(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
) -> Vec<String> {
    let packages: Vec<String> = string_list(project.effective.pointer("/project/cpp_pkg_config"))
        .into_iter()
        .filter(|package| !package.is_empty())
        .collect();
    if packages.is_empty() || packages.len() > 128 {
        return vec![];
    }
    let Ok(tool) = configured_tool(project, "pkg-config", environment) else {
        return vec![];
    };
    let runner = ici_execution::Runner::with_environment(environment.clone());
    let mut flags: Vec<String> = vec![];
    for (index, package) in packages.iter().enumerate() {
        if package.len() > 1024 || package.starts_with('-') || package.chars().any(char::is_control)
        {
            continue;
        }
        let mut task = package_probe(
            project,
            &tool,
            format!("legacy.analysis_context.pkg{index}"),
            package,
        );
        task.kind = ici_domain::TaskKind::Probe;
        task.cacheable = false;
        let outcome = runner.run(&task, &Cancellation::default());
        if !outcome.completed()
            || outcome.exit_code != Some(0)
            || outcome.timed_out
            || outcome.truncated
            || outcome.signal.is_some()
        {
            continue;
        }
        let Some(text) = std::str::from_utf8(&outcome.stdout)
            .ok()
            .filter(|text| !text.contains('\0'))
        else {
            continue;
        };
        flags.extend(text.split_whitespace().map(str::to_owned));
        if flags.len() > 4096 || flags.iter().any(|flag| flag.len() > 8192) {
            return vec![];
        }
    }
    flags
}

pub(crate) fn plan_flags(
    project: &ResolvedProject,
    component: &Component,
    id: &str,
    environment: &BTreeMap<String, String>,
) -> Result<LegacyFlagsPlan, String> {
    let compiler = configured_tool(project, "g++", environment)?;
    let includes = include_flags(project, component)?;
    let packages = string_list(
        component
            .settings
            .pointer("/project/cpp_pkg_config")
            .or_else(|| project.effective.pointer("/project/cpp_pkg_config")),
    );
    if packages.len() > 128
        || packages.iter().any(|package| {
            package.is_empty()
                || package.len() > 1024
                || package.starts_with('-')
                || package.chars().any(char::is_control)
        })
    {
        return Err("pkg-config expressions must be bounded printable arguments and cannot start with an option".into());
    }
    let mut tools = BTreeMap::from([("g++".into(), compiler.clone())]);
    let mut probes = vec![];
    let mut limitations = vec![];
    if !packages.is_empty() {
        match configured_tool(project, "pkg-config", environment) {
            Ok(tool) => {
                tools.insert("pkg-config".into(), tool.clone());
                for (index, package) in packages.iter().enumerate() {
                    probes.push(package_probe(
                        project,
                        &tool,
                        format!("{id}.pkg{index}"),
                        package,
                    ));
                }
            }
            Err(error) => limitations.push(format!(
                "pkg-config is unavailable; no package flags were invented: {error}"
            )),
        }
    }
    Ok(LegacyFlagsPlan {
        compiler,
        includes,
        probes,
        packages,
        limitations,
        tools,
    })
}

pub(crate) fn observe_flags(
    probes: &[TaskSpec],
    initial_limitations: &[String],
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
    component: &str,
) -> Result<ObservedFlags, FlagsFailure> {
    let outcomes = run_cpp_tasks(
        probes,
        scheduler,
        cancellation,
        events,
        component,
        "declared pkg-config compiler flags",
    )
    .map_err(FlagsFailure::Invalid)?;
    let mut arguments = vec![];
    let mut records = vec![];
    let mut limitations = initial_limitations.to_vec();
    let mut statuses = vec![];
    for probe in probes {
        let outcome = outcomes
            .iter()
            .find(|outcome| outcome.task_id == probe.id)
            .expect("declared package probe");
        if !outcome.completed()
            || outcome.timed_out
            || outcome.truncated
            || outcome.signal.is_some()
        {
            return Err(FlagsFailure::Execution(Box::new(outcome.clone())));
        }
        let mut flags = vec![];
        if outcome.exit_code == Some(0) {
            let stdout = std::str::from_utf8(&outcome.stdout)
                .ok()
                .filter(|text| !text.contains('\0'))
                .ok_or_else(|| {
                    FlagsFailure::Invalid("pkg-config flags must be UTF-8 without NUL".into())
                })?;
            flags = stdout
                .split_whitespace()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            if flags.len() > 4096
                || flags.iter().any(|flag| flag.len() > 8192)
                || arguments.len() + flags.len() > 4096
            {
                return Err(FlagsFailure::Invalid(
                    "pkg-config flags exceed the bounded token budget".into(),
                ));
            }
            arguments.extend(flags.clone());
        } else {
            limitations.push(format!(
                "{} was unavailable ({}); no flags were invented",
                probe.argv[2],
                outcome.ending()
            ));
        }
        statuses.push(outcome.exit_code);
        records.push(json!({"task":probe,"exit_code":outcome.exit_code,"stdout_sha256":sha256(&outcome.stdout),"stderr_sha256":sha256(&outcome.stderr),"flags":flags}));
    }
    Ok(ObservedFlags {
        arguments,
        records,
        outcomes: statuses,
        limitations,
    })
}
