//! Explicit legacy syntax fallback. Recorded compilation commands never receive these flags.
use super::*;
use ici_domain::EvidenceLevel;
use ici_execution::{Cancellation, Scheduler};
use std::fs;

const MARKER: &str = "{observed-pkg-config-cflags}";
#[derive(Clone)]
pub(super) struct FallbackPlan {
    pub providers: Vec<ProviderPlan>,
    units: Vec<CompilationUnit>,
    compiler: ResolvedTool,
    probes: Vec<TaskSpec>,
    packages: Vec<String>,
    limitations: Vec<String>,
    observed: Option<Value>,
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

fn provider(
    project: &ResolvedProject,
    component: &Component,
    unit: &CompilationUnit,
    compiler: &ResolvedTool,
    id: &str,
) -> Result<ProviderPlan, String> {
    let replay = ici_cpp::replay_command(
        &project.root,
        unit,
        &compiler.path,
        ReplayPurpose::Diagnostics,
    )
    .map_err(|error| error.to_string())?;
    let mut request = ProviderRequest::new(
        ProviderKind::Compiler,
        compiler.clone(),
        &project.root,
        &replay.cwd,
        vec![unit.source.clone()],
        id,
    );
    request.component_id = Some(component.id.clone());
    request.analysis_unit_ids = vec![unit_id(component, unit)];
    request.variant = Some("legacy-c++17-fallback".into());
    request.options.compiler_argv = replay.argv;
    request.options.source_token = Some(replay.source.to_string_lossy().into_owned());
    let mut planned = ici_providers::plan(&request).map_err(|error| error.to_string())?;
    planned.scope_complete = false;
    planned.limitations.push("Legacy c++17 syntax fallback has no recorded build configuration or compiler header/module closure; findings are estimated".into());
    Ok(planned)
}

impl CppPlans {
    pub fn observed_configuration_for(&self, id: &str) -> Option<Value> {
        self.fallbacks
            .get(id)
            .and_then(|plan| plan.observed.clone())
    }
    pub(super) fn bind_fallback(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Work, String> {
        let sources: Vec<_> = component
            .files
            .iter()
            .filter(|path| ici_cpp::is_translation_unit(path))
            .collect();
        if sources.is_empty() || sources.len() > 2048 {
            return Err(
                "legacy syntax fallback requires 1..2048 selected translation units".into(),
            );
        }
        let compiler = configured_tool(project, "g++", environment)?;
        self.tools.insert("g++".into(), compiler.clone());
        let flags = include_flags(project, component)?;
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
            return Err(
                "pkg-config expressions must be bounded printable arguments and cannot start with an option".into(),
            );
        }
        let mut probes = vec![];
        let mut limitations = vec![];
        if !packages.is_empty() {
            match configured_tool(project, "pkg-config", environment) {
                Ok(tool) => {
                    self.tools.insert("pkg-config".into(), tool.clone());
                    for (index, package) in packages.iter().enumerate() {
                        let mut task = TaskSpec::command(
                            format!("{id}.pkg{index}"),
                            "pkg-config",
                            vec![
                                tool.path.display().to_string(),
                                "--cflags".into(),
                                package.clone(),
                            ],
                            project.root.display().to_string(),
                        );
                        task.timeout_seconds = Some(5.0);
                        task.output_limit_bytes = Some(1024 * 1024);
                        task.tool_digest = Some(tool.digest.clone());
                        task.env_inherit = Some(vec![
                            "PATH".into(),
                            "LANG".into(),
                            "LC_ALL".into(),
                            "PKG_CONFIG_PATH".into(),
                            "PKG_CONFIG_LIBDIR".into(),
                            "PKG_CONFIG_SYSROOT_DIR".into(),
                        ]);
                        probes.push(task);
                    }
                }
                Err(error) => limitations.push(format!(
                    "pkg-config is unavailable; no package flags were invented: {error}"
                )),
            }
        }
        let mut providers = vec![];
        let mut units = vec![];
        for (index, source) in sources.iter().enumerate() {
            let argv = std::iter::once(compiler.path.display().to_string())
                .chain(std::iter::once("-std=c++17".into()))
                .chain(flags.iter().cloned())
                .chain(std::iter::once((*source).clone()))
                .collect::<Vec<_>>();
            let configuration = sha256(
                serde_json::to_vec(&ici_cpp::normalized_configuration_arguments(
                    &project.root,
                    &project.root,
                    &argv,
                ))
                .map_err(|error| error.to_string())?,
            );
            let unit = CompilationUnit {
                source: (*source).clone(),
                directory: ".".into(),
                compiler: "g++".into(),
                language: "c++".into(),
                standard: "c++17".into(),
                configuration,
                original_argv: argv.clone(),
                argv,
                output: String::new(),
                target: String::new(),
                variant: "legacy-c++17-fallback".into(),
                defines: vec![],
                include_paths: vec![],
                sysroot: String::new(),
                sysroot_scope: String::new(),
                response_files: vec![],
                diagnostics: vec![],
            };
            let mut planned = provider(
                project,
                component,
                &unit,
                &compiler,
                &format!("{id}.unit{index}"),
            )?;
            if !probes.is_empty() {
                planned.task.argv.push(MARKER.into());
            }
            providers.push(planned);
            units.push(unit);
        }
        let tasks = probes
            .iter()
            .cloned()
            .chain(providers.iter().map(|provider| provider.task.clone()))
            .collect();
        self.fallbacks.insert(
            id.into(),
            FallbackPlan {
                providers,
                units,
                compiler,
                probes,
                packages,
                limitations,
                observed: None,
            },
        );
        Ok(Work::steps(tasks, false))
    }

    pub(super) fn run_fallback(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        scheduler: &Scheduler,
        cancellation: &Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        let plan = self.fallbacks[&request.task_id].clone();
        let outcomes = match run_cpp_tasks(
            &plan.probes,
            scheduler,
            cancellation,
            events,
            &component.id,
            "declared pkg-config compiler flags",
        ) {
            Ok(outcomes) => outcomes,
            Err(error) => return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error),
        };
        let mut flags = vec![];
        let mut records = vec![];
        let mut limitations = plan.limitations.clone();
        for probe in &plan.probes {
            let outcome = outcomes
                .iter()
                .find(|outcome| outcome.task_id == probe.id)
                .expect("declared package probe");
            if !outcome.completed()
                || outcome.timed_out
                || outcome.truncated
                || outcome.signal.is_some()
            {
                return failed_probe(
                    &request.task_id,
                    "pkg-config",
                    &plan.units[0].source,
                    outcome,
                    "pkg-config observation did not finish completely",
                );
            }
            let mut package_flags = vec![];
            if outcome.exit_code == Some(0) {
                let stdout = match std::str::from_utf8(&outcome.stdout) {
                    Ok(text) if !text.contains('\0') => text,
                    _ => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "pkg-config"),
                            "pkg-config flags must be UTF-8 without NUL",
                        );
                    }
                };
                package_flags = stdout.split_whitespace().map(str::to_owned).collect();
                if package_flags.len() > 4096
                    || package_flags.iter().any(|flag| flag.len() > 8192)
                    || flags.len() + package_flags.len() > 4096
                {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "pkg-config"),
                        "pkg-config flags exceed the bounded token budget",
                    );
                }
                flags.extend(package_flags.clone());
            } else {
                limitations.push(format!(
                    "{} was unavailable (exit {:?}); no flags were invented",
                    probe.argv[2], outcome.exit_code
                ));
            }
            records.push(json!({"task":probe,"exit_code":outcome.exit_code,"stdout_sha256":sha256(&outcome.stdout),"stderr_sha256":sha256(&outcome.stderr),"flags":package_flags}));
        }
        let mut providers = vec![];
        for (index, base) in plan.units.iter().enumerate() {
            let mut unit = base.clone();
            let source = unit.argv.pop().expect("declared source operand");
            unit.argv.extend(flags.iter().cloned());
            unit.argv.push(source);
            unit.configuration = sha256(
                serde_json::to_vec(&ici_cpp::normalized_configuration_arguments(
                    &project.root,
                    &project.root,
                    &unit.argv,
                ))
                .expect("configuration json"),
            );
            let mut planned = match provider(
                project,
                component,
                &unit,
                &plan.compiler,
                &plan.providers[index].task.id,
            ) {
                Ok(provider) => provider,
                Err(error) => {
                    return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error);
                }
            };
            planned.context.source_coordinates =
                plan.providers[index].context.source_coordinates.clone();
            providers.push(planned);
        }
        let concrete: Vec<_> = providers
            .iter()
            .map(|provider| provider.task.clone())
            .collect();
        let observed = json!({"mode":"legacy-c++17-fallback","packages":plan.packages,"package_outcomes":plan.probes.iter().map(|probe|outcomes.iter().find(|outcome|outcome.task_id==probe.id).map(|outcome|outcome.exit_code)).collect::<Vec<_>>(),"compiler_argv":concrete.iter().map(|task|ici_cpp::normalized_configuration_arguments(&project.root,Path::new(&task.cwd),&task.argv)).collect::<Vec<_>>()});
        self.fallbacks
            .get_mut(&request.task_id)
            .expect("bound fallback")
            .observed = Some(observed);
        let results = match run_cpp_tasks(
            &concrete,
            scheduler,
            cancellation,
            events,
            &component.id,
            "legacy c++17 syntax fallback",
        ) {
            Ok(results) => results,
            Err(error) => return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error),
        };
        let outputs = results
            .iter()
            .map(|outcome| {
                ici_providers::observe_full(
                    providers
                        .iter()
                        .find(|provider| provider.task.id == outcome.task_id)
                        .expect("declared fallback"),
                    outcome,
                )
            })
            .collect();
        let mut output = merge(&request.task_id, "compiler", outputs);
        for finding in &mut output.observation.findings {
            finding.evidence = EvidenceLevel::Estimated;
            finding.confidence = "medium".into();
            finding.limitations.push(
                "No recorded build configuration; legacy c++17 heuristic syntax fallback".into(),
            );
        }
        for metric in &mut output.observation.measurements {
            metric.evidence = EvidenceLevel::Estimated;
        }
        if output.observation.state == TaskState::Succeeded && output.status == "PASS" {
            output.status = "WARN".into();
        }
        output.observation.limitations.extend(limitations);
        output.observation.limitations.push("No compilation database was available; syntax analysis used the legacy c++17 heuristic fallback. Package metadata and compiler header/module closure have not been established reusable".into());
        output.extra["cpp_analysis_mode"] = json!("heuristic");
        output.extra["pkg_config"] = json!(records);
        output.extra["concrete_tasks"] = json!(concrete);
        output
    }
}
