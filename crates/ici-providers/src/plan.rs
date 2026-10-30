//! Provider argv builders consume declared inputs; they do not probe or execute tools.
use crate::{ProviderError, resolver::ResolvedTool};
use ici_domain::{TaskKind, TaskSpec, Validate};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderKind {
    RuffLint,
    RuffFormat,
    Ty,
    Mypy,
    Clippy,
    Eslint,
    Tsc,
    Compiler,
    ClangTidy,
    Clazy,
}
impl ProviderKind {
    pub fn name(self) -> &'static str {
        match self {
            Self::RuffLint | Self::RuffFormat => "ruff",
            Self::Ty => "ty",
            Self::Mypy => "mypy",
            Self::Clippy => "clippy",
            Self::Eslint => "eslint",
            Self::Tsc => "tsc",
            Self::Compiler => "compiler",
            Self::ClangTidy => "clang-tidy",
            Self::Clazy => "clazy",
        }
    }
    pub fn for_check(check: &str, tool: &str) -> Option<Self> {
        match (check, tool) {
            ("python.lint", "ruff") => Some(Self::RuffLint),
            ("python.format", "ruff") => Some(Self::RuffFormat),
            ("python.type", "mypy") => Some(Self::Mypy),
            ("python.type", "ty") => Some(Self::Ty),
            ("rust.clippy", _) => Some(Self::Clippy),
            ("javascript.lint" | "typescript.lint", _) => Some(Self::Eslint),
            ("typescript.type", _) => Some(Self::Tsc),
            ("cpp.diagnostics", _) => Some(Self::Compiler),
            ("cpp.tidy", "clazy-standalone" | "clazy") => Some(Self::Clazy),
            ("cpp.tidy", _) => Some(Self::ClangTidy),
            _ => None,
        }
    }
    pub fn accepts_exit(self, exit: i32) -> bool {
        match self {
            Self::Clippy => matches!(exit, 0 | 101),
            Self::Tsc => matches!(exit, 0..=2),
            Self::ClangTidy | Self::Clazy => exit == 0,
            _ => matches!(exit, 0 | 1),
        }
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct NativeOptions {
    pub python_version: Option<String>,
    /// Native tsconfig/jsconfig path, relative to cwd or absolute. No invented config.
    pub project_config: Option<String>,
    pub database_dir: Option<String>,
    pub clazy_checks: Option<String>,
    /// Compile context supplies already expanded/launcher-stripped argv for one TU.
    pub compiler_argv: Vec<String>,
    pub source_token: Option<String>,
    pub jobs: usize,
    pub advisory: bool,
    /// Historical `ici <engine>` view: frozen argv and output grammar. Absent from modern plans.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub legacy: bool,
    /// Frozen `engines.type.mypy_profile = "ici"` arguments for the historical mypy argv.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub legacy_args: Vec<String>,
    /// Frozen mypy operands (source directories) when they differ from the declared files.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub legacy_targets: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProviderRequest {
    pub kind: ProviderKind,
    pub tool: ResolvedTool,
    pub workspace_root: PathBuf,
    pub cwd: PathBuf,
    /// Workspace-relative, declared source files. Builders pass absolute paths, not options.
    pub targets: Vec<String>,
    pub task_id: String,
    pub component_id: Option<String>,
    pub analysis_unit_ids: Vec<String>,
    pub variant: Option<String>,
    pub input_refs: Vec<String>,
    pub options: NativeOptions,
}
impl ProviderRequest {
    pub fn new(
        kind: ProviderKind,
        tool: ResolvedTool,
        workspace_root: impl Into<PathBuf>,
        cwd: impl Into<PathBuf>,
        targets: Vec<String>,
        task_id: impl Into<String>,
    ) -> Self {
        Self {
            kind,
            tool,
            workspace_root: workspace_root.into(),
            cwd: cwd.into(),
            targets,
            task_id: task_id.into(),
            component_id: None,
            analysis_unit_ids: vec![],
            variant: None,
            input_refs: vec![],
            options: NativeOptions::default(),
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DiagnosticContext {
    pub workspace_root: PathBuf,
    pub cwd: PathBuf,
    pub targets: Vec<String>,
    pub component_id: Option<String>,
    pub analysis_unit_ids: Vec<String>,
    pub variant: Option<String>,
    #[serde(default)]
    pub inspected_files: Option<Vec<String>>,
    #[serde(default)]
    pub compiler_columns: Option<String>,
    #[serde(default)]
    pub tool_name: String,
    #[serde(default)]
    pub tool_version: String,
    #[serde(skip)]
    pub source_coordinates: std::sync::Arc<crate::SourceCoordinates>,
    /// Historical engine view (frozen target shapes); never set for modern plans.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub legacy: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProviderPlan {
    pub task: TaskSpec,
    pub kind: ProviderKind,
    pub context: DiagnosticContext,
    pub advisory: bool,
    /// Dependency/configuration closure proof for cache/baseline, not process completion.
    pub scope_complete: bool,
    pub limitations: Vec<String>,
}
pub fn plan(request: &ProviderRequest) -> Result<ProviderPlan, ProviderError> {
    if !request.workspace_root.is_absolute()
        || !request.cwd.is_absolute()
        || !request.tool.path.is_absolute()
    {
        return Err(ProviderError(
            "workspace, cwd and executable must be absolute".into(),
        ));
    }
    if request.targets.is_empty() {
        return Err(ProviderError("provider must name source targets".into()));
    }
    let mut targets = Vec::new();
    for target in &request.targets {
        ici_domain::validation::relative_path(target, false)
            .map_err(|e| ProviderError(e.to_string()))?;
        targets.push(
            request
                .workspace_root
                .join(target)
                .to_string_lossy()
                .into_owned(),
        );
    }
    let mut argv = vec![request.tool.path.to_string_lossy().into_owned()];
    let mut kind = TaskKind::Analyze;
    let mut work_dirs = vec![];
    let mut resource_keys = vec![];
    let mut environment = std::collections::BTreeMap::from([
        ("RAYON_NUM_THREADS".into(), "2".into()),
        ("OMP_NUM_THREADS".into(), "2".into()),
    ]);
    let mut limitations = vec!["native configuration/dependency closure is not complete; cache reuse and resolved-baseline proof are disabled".into()];
    let mut timeout = 300.0;
    let compiler_columns = compiler_columns(request);
    match request.kind {
        ProviderKind::RuffLint => {
            push(
                &mut argv,
                &[
                    "check",
                    "--no-fix",
                    "--no-fix-only",
                    "--no-unsafe-fixes",
                    "--output-format",
                    "json",
                    "--quiet",
                    "--force-exclude",
                    "--no-cache",
                    "--",
                ],
            );
            argv.extend(targets);
        }
        ProviderKind::RuffFormat => {
            push(
                &mut argv,
                &["format", "--check", "--force-exclude", "--no-cache", "--"],
            );
            argv.extend(targets);
        }
        ProviderKind::Ty => {
            push(
                &mut argv,
                &[
                    "check",
                    "--output-format",
                    "sarif",
                    "--no-progress",
                    "--force-exclude",
                ],
            );
            if let Some(version) = &request.options.python_version {
                push(&mut argv, &["--python-version", version]);
            }
            argv.push("--".into());
            argv.extend(targets);
            timeout = 600.0;
        }
        ProviderKind::Mypy if request.options.legacy => {
            // Frozen `TypeCheckEngine._run_mypy`: `mypy [ici profile] <source dirs>`, so project
            // configuration decides columns, codes and output style exactly as it did. Only the
            // cache moves out of the project (no `.mypy_cache` write); it never changes output.
            push(
                &mut argv,
                &request
                    .options
                    .legacy_args
                    .iter()
                    .map(String::as_str)
                    .collect::<Vec<_>>(),
            );
            push(
                &mut argv,
                &["--no-incremental", "--cache-dir", "/dev/null", "--"],
            );
            if request.options.legacy_targets.is_empty() {
                argv.extend(targets);
            } else {
                for target in &request.options.legacy_targets {
                    ici_domain::validation::relative_path(target, false)
                        .map_err(|e| ProviderError(e.to_string()))?;
                    argv.push(
                        request
                            .workspace_root
                            .join(target)
                            .to_string_lossy()
                            .into_owned(),
                    );
                }
            }
            timeout = 600.0;
            limitations.push(
                "mypy may load project plugins; imports and stubs remain project dependencies"
                    .into(),
            );
        }
        ProviderKind::Mypy => {
            push(
                &mut argv,
                &[
                    "--show-error-codes",
                    "--show-column-numbers",
                    "--no-color-output",
                    "--no-pretty",
                    "--no-incremental",
                    "--cache-dir",
                    "/dev/null",
                    "--",
                ],
            );
            argv.extend(targets);
            timeout = 600.0;
            limitations.push(
                "mypy may load project plugins; imports and stubs remain project dependencies"
                    .into(),
            );
        }
        ProviderKind::Clippy => {
            let target = request.cwd.join(".ici/rust-v1/cargo-target");
            let jobs = if request.options.jobs == 0 {
                2
            } else {
                request.options.jobs.min(2)
            }
            .to_string();
            push(
                &mut argv,
                &[
                    "clippy",
                    "--offline",
                    "--locked",
                    "--message-format=json",
                    "--jobs",
                    &jobs,
                    "--target-dir",
                    &target.to_string_lossy(),
                ],
            );
            work_dirs.push(target.to_string_lossy().into_owned());
            resource_keys = work_dirs.clone();
            environment.insert("RUSTUP_AUTO_INSTALL".into(), "0".into());
            environment.insert("CARGO_NET_OFFLINE".into(), "true".into());
            kind = TaskKind::Prepare;
            limitations.push("Cargo can execute build.rs/proc macros; dependencies must already be available offline".into());
        }
        ProviderKind::Eslint => {
            push(&mut argv, &["--format", "json", "--no-fix", "--"]);
            argv.extend(targets);
            limitations.push("ESLint executes project configuration and plugins".into());
        }
        ProviderKind::Tsc => {
            let metadata = request
                .cwd
                .join(".ici/rust-v1/typescript/analysis.tsbuildinfo");
            push(
                &mut argv,
                &[
                    "--noEmit",
                    "--pretty",
                    "false",
                    "--incremental",
                    "true",
                    "--tsBuildInfoFile",
                    &metadata.to_string_lossy(),
                ],
            );
            if let Some(project) = &request.options.project_config {
                push(&mut argv, &["--project", project]);
            } else {
                argv.extend(targets);
            }
            work_dirs.push(
                metadata
                    .parent()
                    .expect("metadata parent")
                    .to_string_lossy()
                    .into_owned(),
            );
            resource_keys = work_dirs.clone();
            kind = TaskKind::Prepare;
            limitations.push("TypeScript incremental metadata is written under .ici/rust; referenced output builds are not performed".into());
        }
        ProviderKind::Compiler => {
            let source = request.options.source_token.as_deref().ok_or_else(|| {
                ProviderError("compiler task needs an original source token".into())
            })?;
            let transformed = transform_compiler_argv(&request.options.compiler_argv, source)?;
            argv.extend(transformed.argv.into_iter().skip(1));
            if compiler_columns.as_deref() == Some("gnu-byte") {
                argv.retain(|argument| {
                    !argument.starts_with("-fdiagnostics-column-unit=")
                        && !argument.starts_with("-fdiagnostics-column-origin=")
                });
                argv.push("-fdiagnostics-column-unit=byte".into());
                argv.push("-fdiagnostics-column-origin=1".into());
            }
            limitations.extend(
                transformed
                    .dropped
                    .into_iter()
                    .map(|item| format!("compiler transform removed: {item}")),
            );
        }
        ProviderKind::ClangTidy | ProviderKind::Clazy => {
            let database = request.options.database_dir.as_deref().ok_or_else(|| {
                ProviderError("tidy/clazy requires a declared compile database directory".into())
            })?;
            push(&mut argv, &["-p", database]);
            if request.kind == ProviderKind::Clazy
                && let Some(checks) = &request.options.clazy_checks
            {
                if checks.is_empty()
                    || !checks
                        .chars()
                        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | ','))
                {
                    return Err(ProviderError(
                        "invalid explicit Clazy check selection".into(),
                    ));
                }
                argv.push(format!("--checks={checks}"));
            }
            argv.extend(targets);
            timeout = 900.0;
        }
    }
    let mut task = TaskSpec::command(
        &request.task_id,
        request.kind.name(),
        argv,
        request.cwd.to_string_lossy(),
    );
    task.kind = kind;
    task.cacheable = false;
    task.tool_digest = Some(request.tool.digest.clone());
    task.analysis_unit_ids
        .clone_from(&request.analysis_unit_ids);
    task.variant.clone_from(&request.variant);
    task.input_refs = request
        .targets
        .iter()
        .map(|target| request.workspace_root.join(target))
        .chain(request.input_refs.iter().map(|reference| {
            let path = Path::new(reference);
            if path.is_absolute() {
                path.to_path_buf()
            } else {
                request.cwd.join(path)
            }
        }))
        .map(|path| path.to_string_lossy().into_owned())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    task.work_dirs = work_dirs;
    task.resource_keys = resource_keys;
    task.env_overlay = environment;
    task.timeout_seconds = Some(timeout);
    task.validate().map_err(|e| ProviderError(e.to_string()))?;
    Ok(ProviderPlan {
        task,
        kind: request.kind,
        context: DiagnosticContext {
            workspace_root: request.workspace_root.clone(),
            cwd: request.cwd.clone(),
            targets: request.targets.clone(),
            component_id: request.component_id.clone(),
            analysis_unit_ids: request.analysis_unit_ids.clone(),
            variant: request.variant.clone(),
            inspected_files: None,
            compiler_columns,
            tool_name: request
                .tool
                .path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
            tool_version: request.tool.version.clone().unwrap_or_default(),
            source_coordinates: Default::default(),
            legacy: request.options.legacy,
        },
        advisory: request.options.advisory,
        scope_complete: false,
        limitations,
    })
}
fn compiler_columns(request: &ProviderRequest) -> Option<String> {
    if matches!(request.kind, ProviderKind::ClangTidy | ProviderKind::Clazy) {
        return Some("clang-byte".into());
    }
    if request.kind != ProviderKind::Compiler {
        return None;
    }
    let canonical = request.tool.path.canonicalize().ok();
    let names = [
        canonical.as_deref(),
        Some(request.tool.path.as_path()),
        request.options.compiler_argv.first().map(Path::new),
    ];
    let gnu = regex::Regex::new(r"(?:^|-)(?:g\+\+|gcc)(?:-\d.*)?$").expect("constant regex");
    for path in names.into_iter().flatten() {
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("");
        if name.contains("clang") {
            return Some("clang-byte".into());
        }
        if gnu.is_match(name) {
            return Some("gnu-byte".into());
        }
    }
    None
}
fn push(argv: &mut Vec<String>, entries: &[&str]) {
    argv.extend(entries.iter().map(|arg| (*arg).into()));
}

#[derive(Clone, Debug)]
pub struct TransformedInvocation {
    pub argv: Vec<String>,
    pub dropped: Vec<String>,
}
pub fn transform_compiler_argv(
    argv: &[String],
    source: &str,
) -> Result<TransformedInvocation, ProviderError> {
    let executable = argv
        .first()
        .ok_or_else(|| ProviderError("empty compiler invocation".into()))?;
    let name = Path::new(executable)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let family =
        regex::Regex::new(r"^(?:c\+\+|g\+\+|gcc|clang\+\+|clang|cc|em\+\+|emcc)(?:-\d+.*)?$")
            .expect("constant regex");
    if !family.is_match(name) {
        return Err(ProviderError(format!("unsupported compiler driver {name}")));
    }
    if !argv.iter().any(|arg| arg == source) {
        return Err(ProviderError(
            "compile source token is absent from invocation".into(),
        ));
    }
    let mut transformed = vec![executable.clone(), "-fsyntax-only".into()];
    let mut dropped = vec![];
    let mut iter = argv[1..].iter();
    while let Some(arg) = iter.next() {
        if arg.starts_with('@') {
            return Err(ProviderError(
                "response files must be expanded by compile-context discovery".into(),
            ));
        }
        if ["-o", "-MF", "-MT", "-MQ", "-MJ", "-include-pch"].contains(&arg.as_str()) {
            let value = iter
                .next()
                .ok_or_else(|| ProviderError(format!("missing operand for {arg}")))?;
            dropped.push(format!("{arg} {value}"));
            continue;
        }
        if [
            "-c",
            "-S",
            "-E",
            "-M",
            "-MD",
            "-MM",
            "-MMD",
            "-pipe",
            "-shared",
            "-pie",
            "-pg",
            "--coverage",
            "-ftest-coverage",
        ]
        .contains(&arg.as_str())
            || [
                "-o",
                "-MF",
                "-MT",
                "-MQ",
                "-MJ",
                "-flto",
                "-save-temps",
                "-fdump",
                "-fprofile-",
                "-Wa,",
                "-Wl,",
                "-Wp,",
                "-l",
                "-L",
            ]
            .iter()
            .any(|prefix| arg.starts_with(prefix))
        {
            dropped.push(arg.clone());
            continue;
        }
        if arg != source {
            transformed.push(arg.clone());
        }
    }
    transformed.push("-fdiagnostics-color=never".into());
    transformed.push("-fdiagnostics-show-option".into());
    if !transformed
        .iter()
        .any(|argument| argument == "-fdiagnostics-format=json")
    {
        transformed.push("-fdiagnostics-parseable-fixits".into());
    }
    transformed.push(source.into());
    Ok(TransformedInvocation {
        argv: transformed,
        dropped,
    })
}
/// Lexical normalization only; caller provides a canonical root and snapshotted target list.
pub(crate) fn relative_location(
    path: &str,
    context: &DiagnosticContext,
) -> Result<String, ProviderError> {
    let path = Path::new(path);
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        context.cwd.join(path)
    };
    let mut normalized = PathBuf::new();
    for part in absolute.components() {
        match part {
            Component::ParentDir => {
                if !normalized.pop() {
                    return Err(ProviderError("location escapes filesystem root".into()));
                }
            }
            Component::CurDir => {}
            other => normalized.push(other.as_os_str()),
        }
    }
    let relative = normalized
        .strip_prefix(&context.workspace_root)
        .map_err(|_| {
            ProviderError(format!(
                "diagnostic outside workspace: {}",
                normalized.display()
            ))
        })?;
    let relative = relative
        .to_str()
        .ok_or_else(|| ProviderError("diagnostic path is not UTF-8".into()))?
        .replace('\\', "/");
    ici_domain::validation::relative_path(&relative, false)
        .map_err(|e| ProviderError(e.to_string()))?;
    Ok(relative)
}

/// A separate inventory command is visible to plan/scheduler; analysis never silently probes.
pub fn ruff_inventory_task(plan: &ProviderPlan) -> Result<TaskSpec, ProviderError> {
    if !matches!(plan.kind, ProviderKind::RuffLint | ProviderKind::RuffFormat) {
        return Err(ProviderError(
            "file inventory is only supported for Ruff".into(),
        ));
    }
    let mut task = plan.task.clone();
    task.id.push_str(".files");
    task.kind = TaskKind::Probe;
    task.cacheable = false;
    task.argv = vec![
        plan.task.argv[0].clone(),
        "check".into(),
        "--show-files".into(),
        "--force-exclude".into(),
        "--no-cache".into(),
        "--no-fix".into(),
        "--no-fix-only".into(),
        "--no-unsafe-fixes".into(),
        "--".into(),
    ];
    task.argv.extend(plan.context.targets.iter().map(|target| {
        plan.context
            .workspace_root
            .join(target)
            .to_string_lossy()
            .into_owned()
    }));
    task.timeout_seconds = Some(30.0);
    task.validate()
        .map_err(|error| ProviderError(error.to_string()))?;
    Ok(task)
}
/// Install scope evidence only from a fresh successful inventory and unchanged configuration.
/// The application includes this context plus every input digest in its cache identity.
pub fn apply_ruff_inventory(
    plan: &mut ProviderPlan,
    outcome: &ici_execution::TaskOutcome,
    configuration: &crate::native_config::ConfigEvidence,
) -> Result<(), ProviderError> {
    if !matches!(plan.kind, ProviderKind::RuffLint | ProviderKind::RuffFormat) {
        return Err(ProviderError(
            "scope inventory is only valid for Ruff".into(),
        ));
    }
    if outcome.task_id != format!("{}.files", plan.task.id)
        || !outcome.completed()
        || outcome.exit_code != Some(0)
    {
        return Err(ProviderError(
            "Ruff inventory did not complete for this task".into(),
        ));
    }
    let stdout =
        std::str::from_utf8(&outcome.stdout).map_err(|error| ProviderError(error.to_string()))?;
    let files = crate::diagnostics::ruff_inspected_files(stdout, plan)?;
    if files
        .iter()
        .any(|file| !plan.context.targets.contains(file))
    {
        return Err(ProviderError(
            "Ruff inventory included an undeclared target".into(),
        ));
    }
    for (path, expected) in &configuration.files {
        let bytes = ici_execution::storage::read_bounded_regular(path, 8 * 1024 * 1024)
            .map_err(|error| ProviderError(error.to_string()))?;
        if &ici_domain::sha256(bytes) != expected {
            return Err(ProviderError(
                "native configuration changed during inventory".into(),
            ));
        }
    }
    for reference in configuration.input_refs() {
        if !plan.task.input_refs.contains(&reference) {
            plan.task.input_refs.push(reference);
        }
    }
    plan.task.input_refs.sort();
    plan.scope_complete = configuration.complete && !files.is_empty() && outcome.stderr.is_empty();
    plan.context.inspected_files = Some(files);
    plan.task.cacheable = plan.scope_complete;
    if plan.scope_complete {
        plan.limitations
            .retain(|item| !item.starts_with("native configuration/dependency closure"));
    }
    Ok(())
}
