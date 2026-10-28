//! Existing compilation evidence and artifact contracts. This facade never invents build commands.
mod linker;
use ici_application::plan::{Work, file_language};
use ici_config::{Component, ResolvedProject};
use ici_cpp::{CompilationContext, CompilationUnit, ContextRequest, ReplayPurpose};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, CheckDefinition, Finding, InspectionTarget, Measurement,
    SourceSpan, TaskSpec, TaskState, sha256,
};
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest,
    resolver::{ResolvedTool, ToolRequest},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct IncludeProbe {
    pub task: TaskSpec,
    pub unit: CompilationUnit,
}
#[derive(Clone, Debug)]
pub struct BoundaryProbe {
    pub task: TaskSpec,
    pub unit: CompilationUnit,
    pub tidy: bool,
}
#[derive(Default)]
pub struct CppPlans {
    pub contexts: BTreeMap<String, CompilationContext>,
    pub diagnostics: BTreeMap<String, Vec<ProviderPlan>>,
    pub includes: BTreeMap<String, Vec<IncludeProbe>>,
    pub tools: BTreeMap<String, ResolvedTool>,
    /// Before/after input metadata only, never a cache identity or a claim about artifact contents.
    pub artifacts: BTreeMap<String, Value>,
    /// Tool arguments are derived only from the visible selected-driver search probes.
    pub tooling_searches: BTreeMap<String, Vec<TaskSpec>>,
    pub boundaries: BTreeMap<String, Vec<BoundaryProbe>>,
    pub unused: BTreeMap<String, Vec<CompilationUnit>>,
    linkers: BTreeMap<String, linker::LinkerPlan>,
    held_contexts: Vec<crate::export_context::PreparedContext>,
    source_session: ici_cpp::CppAnalysisSession,
    inactive: BTreeMap<String, String>,
    inactive_paths: BTreeMap<String, Vec<String>>,
    abi_inputs: BTreeMap<String, BTreeMap<String, usize>>,
    compiler_configurations: BTreeMap<String, BTreeMap<String, usize>>,
    compiler_components: BTreeMap<String, String>,
    resolved: BTreeMap<String, std::result::Result<ResolvedTool, String>>,
}
impl CppPlans {
    fn resolve_searches(
        &self,
        tasks: &mut [TaskSpec],
        scheduler: &ici_execution::Scheduler,
        cancellation: &ici_execution::Cancellation,
        events: &crate::events::EventLog,
        component: &str,
    ) -> Result<Value, String> {
        let searches: Vec<_> = tasks
            .iter()
            .flat_map(|task| {
                self.tooling_searches
                    .get(&task.id)
                    .into_iter()
                    .flatten()
                    .cloned()
            })
            .collect();
        let outcomes = run_cpp_tasks(
            &searches,
            scheduler,
            cancellation,
            events,
            component,
            "selected compiler library search",
        )?;
        let outcomes: BTreeMap<_, _> = outcomes
            .into_iter()
            .map(|outcome| (outcome.task_id.clone(), outcome))
            .collect();
        let mut projections = BTreeMap::new();
        for task in tasks {
            let Some(searches) = self.tooling_searches.get(&task.id) else {
                continue;
            };
            let projection = ici_cpp::gcc_standard_library_projection(
                &outcomes[&searches[0].id],
                &outcomes[&searches[1].id],
                Path::new(&task.cwd),
            )
            .map_err(|error| error.to_string())?;
            task.argv
                .retain(|argument| argument != "{observed-gcc-stdlib}");
            task.argv.extend(projection.arguments.clone());
            projections.insert(
                task.id.clone(),
                json!({"stdlib":projection,"argv":task.argv,"probe_tasks":searches}),
            );
        }
        Ok(json!(projections))
    }
    pub fn bind(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        check: &CheckDefinition,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Option<Work> {
        if check.language != "cpp" {
            return None;
        }
        if matches!(
            check.id.as_str(),
            "cpp.type"
                | "cpp.resource"
                | "cpp.security"
                | "cpp.python-compat"
                | "cpp.binary-compat"
        ) {
            return match check.id.as_str() {
                "cpp.type" | "cpp.resource" | "cpp.security" | "cpp.python-compat" => {
                    let reason = if check.id == "cpp.type" {
                        "The type engine is not applicable to C/C++"
                    } else {
                        "No Python source files are selected; this engine is not applicable to C/C++"
                    };
                    self.inactive.insert(id.into(), reason.into());
                    Some(Work::Internal)
                }
                "cpp.binary-compat" => match crate::cpp_validation::abi_scope(project, component) {
                    Ok(Some(scope)) => {
                        self.abi_inputs.insert(
                            id.into(),
                            scope
                                .artifacts
                                .iter()
                                .map(|path| (path.clone(), 256 * 1024 * 1024))
                                .collect(),
                        );
                        if scope.implicit && scope.targets.is_empty() && !scope.artifacts.is_empty()
                        {
                            if scope.required || check.required {
                                return Some(blocked(
                                    "binary compatibility requires published executable or shared-library artifacts",
                                ));
                            }
                            self.inactive_paths.insert(id.into(), scope.artifacts);
                            self.inactive.insert(id.into(),"Binary compatibility is not applicable: declared artifacts contain no executable or shared library".into());
                            Some(Work::Internal)
                        } else {
                            None
                        }
                    }
                    Ok(None) => None,
                    Err(reason) => Some(blocked(reason)),
                },

                _ => unreachable!("handled applicability check"),
            };
        }
        let disabled = match check.id.as_str() {
            "cpp.tidy"
                if component
                    .settings
                    .pointer("/engines/lint/clang_tidy")
                    .and_then(Value::as_str)
                    == Some("off")
                    && component
                        .settings
                        .pointer("/engines/lint/clazy")
                        .and_then(Value::as_str)
                        == Some("off") =>
            {
                Some("clang-tidy and Clazy are explicitly disabled")
            }
            "cpp.dead"
                if component
                    .settings
                    .pointer("/engines/dead/cpp_unused")
                    .and_then(Value::as_str)
                    == Some("off")
                    && component
                        .settings
                        .pointer("/engines/dead/cpp_linker")
                        .and_then(Value::as_str)
                        .unwrap_or("off")
                        == "off" =>
            {
                Some("C++ unused-function and linker analysis are explicitly disabled")
            }
            _ => None,
        };
        if let Some(reason) = disabled {
            self.inactive.insert(id.into(), reason.into());
            return Some(Work::Internal);
        }
        if !self.contexts.contains_key(&component.id) {
            self.contexts
                .insert(component.id.clone(), context(project, component));
        }
        let context = self.contexts[&component.id].clone();
        let configurations = context
            .database_path
            .iter()
            .map(|path| (path.clone(), 32 * 1024 * 1024))
            .chain(
                context
                    .response_files
                    .iter()
                    .map(|input| (input.path.clone(), 4 * 1024 * 1024)),
            )
            .collect();
        self.compiler_configurations
            .insert(id.into(), configurations);
        self.compiler_components
            .insert(id.into(), component.id.clone());

        match check.id.as_str() {
            "cpp.complexity" | "cpp.cognitive" => {
                self.bind_boundaries(project, component, &context, check, id, environment)
            }
            "cpp.cycle" if context.complete() => Some(
                match self.bind_include_traces(project, component, &context, id, environment) {
                    Ok(work) => work,
                    Err(reason) => blocked(reason),
                },
            ),
            "cpp.dead" => Some(
                match self.bind_unused(project, component, &context, id, environment) {
                    Ok(work) => work,
                    Err(reason) => blocked(reason),
                },
            ),
            "cpp.tidy" => Some(
                match self.bind_tooling(project, component, &context, id, environment) {
                    Ok(work) => work,
                    Err(reason) => blocked(reason),
                },
            ),
            "cpp.compile" => {
                // Unity sources are containers; observed includes must establish original membership.
                if (context.unity_build && !context.uncovered_sources.is_empty())
                    || context
                        .units
                        .iter()
                        .any(|unit| !component.files.contains(&unit.source))
                {
                    let mut probes = vec![];
                    for (index, unit) in context.units.iter().enumerate() {
                        let tool = match self.compiler(project, unit, environment) {
                            Ok(tool) => tool,
                            Err(reason) => return Some(blocked(reason)),
                        };
                        let mut task = match ici_cpp::compiler_probe_task(
                            &project.root,
                            unit,
                            &tool.path,
                            ReplayPurpose::IncludeTrace,
                            &format!("{id}.include{index}"),
                        ) {
                            Ok(task) => task,
                            Err(error) => return Some(blocked(error.to_string())),
                        };
                        task.analysis_unit_ids = vec![unit_id(component, unit)];
                        task.input_refs = task
                            .input_refs
                            .into_iter()
                            .map(|path| {
                                if Path::new(&path).is_absolute() {
                                    path
                                } else {
                                    project.root.join(path).to_string_lossy().into_owned()
                                }
                            })
                            .collect();
                        probes.push(IncludeProbe {
                            task,
                            unit: unit.clone(),
                        });
                    }
                    if probes.is_empty() {
                        return Some(Work::Internal);
                    }
                    let tasks = probes.iter().map(|probe| probe.task.clone()).collect();
                    self.includes.insert(id.into(), probes);
                    Some(Work::steps(tasks, false))
                } else {
                    Some(Work::Internal)
                }
            }
            "cpp.diagnostics" => {
                if context.units.is_empty() {
                    return Some(blocked(
                        "no usable recorded compilation units; provide the declared build's compilation database",
                    ));
                }
                let mut providers = vec![];
                for (index, unit) in context.units.iter().enumerate() {
                    let tool = match self.compiler(project, unit, environment) {
                        Ok(tool) => tool,
                        Err(reason) => return Some(blocked(reason)),
                    };
                    let replay = match ici_cpp::replay_command(
                        &project.root,
                        unit,
                        &tool.path,
                        ReplayPurpose::Diagnostics,
                    ) {
                        Ok(replay) => replay,
                        Err(error) => return Some(blocked(error.to_string())),
                    };
                    let mut request = ProviderRequest::new(
                        ProviderKind::Compiler,
                        tool,
                        &project.root,
                        &replay.cwd,
                        vec![unit.source.clone()],
                        format!("{id}.unit{index}"),
                    );
                    request.component_id = Some(component.id.clone());
                    request.analysis_unit_ids = vec![unit_id(component, unit)];
                    request.variant = Some(unit.variant.clone());
                    request.options.compiler_argv = replay.argv;
                    request.options.source_token =
                        Some(replay.source.to_string_lossy().into_owned());
                    request.input_refs = context
                        .database_path
                        .iter()
                        .map(|path| project.root.join(path).to_string_lossy().into_owned())
                        .chain(unit.response_files.iter().map(|input| {
                            project
                                .root
                                .join(&input.path)
                                .to_string_lossy()
                                .into_owned()
                        }))
                        .collect();
                    let mut provider = match ici_providers::plan(&request) {
                        Ok(provider) => provider,
                        Err(error) => return Some(blocked(error.to_string())),
                    };
                    provider.scope_complete = false;
                    provider.limitations.push(
                        "compiler header/module dependency closure has not been proven reusable"
                            .into(),
                    );
                    providers.push(provider);
                }
                let tasks = providers
                    .iter()
                    .map(|provider| provider.task.clone())
                    .collect();
                self.diagnostics.insert(id.into(), providers);
                Some(Work::steps(tasks, false))
            }
            "cpp.artifact" => {
                let Some(build) = linked_build(project, component) else {
                    return Some(blocked(
                        "no build linked; declare [builds.<id>] and components[].build",
                    ));
                };
                if build.artifacts.is_empty() {
                    return Some(blocked(format!(
                        "build {} declares no artifact contract",
                        build.id
                    )));
                }
                self.artifacts
                    .insert(component.id.clone(), artifact_identity(project, build));
                Some(Work::Internal)
            }
            _ => None,
        }
    }
    fn compiler(
        &mut self,
        project: &ResolvedProject,
        unit: &CompilationUnit,
        environment: &BTreeMap<String, String>,
    ) -> std::result::Result<ResolvedTool, String> {
        let executable = unit
            .argv
            .first()
            .map(String::as_str)
            .unwrap_or(&unit.compiler);
        let key = format!("{}:{executable}", unit.directory);
        if let Some(result) = self.resolved.get(&key) {
            return result.clone();
        }
        let name = Path::new(executable)
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| "recorded compiler has no valid executable name".to_string())?;
        let configured = project
            .tools
            .get(name)
            .and_then(|tool| tool.path.as_deref());
        let explicit = configured
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    project.root.join(path)
                }
            })
            .or_else(|| executable.contains('/').then(|| PathBuf::from(executable)));
        let request = ToolRequest {
            id: name.into(),
            explicit,
            project_root: project.root.join(&unit.directory),
            pack_root: None,
            path_env: environment.get("PATH").cloned(),
        };
        let result = request.resolve().map_err(|error| error.to_string());
        if let Ok(tool) = &result {
            self.tools.insert(format!("compiler:{key}"), tool.clone());
        }
        self.resolved.insert(key, result.clone());
        result
    }
    fn bind_include_traces(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Work, String> {
        let mut probes = vec![];
        for (index, unit) in context.units.iter().enumerate() {
            let tool = self.compiler(project, unit, environment)?;
            let mut task = ici_cpp::compiler_probe_task(
                &project.root,
                unit,
                &tool.path,
                ReplayPurpose::IncludeTrace,
                &format!("{id}.include{index}"),
            )
            .map_err(|error| error.to_string())?;
            bind_inputs(project, component, context, unit, &mut task);
            probes.push(IncludeProbe {
                task,
                unit: unit.clone(),
            });
        }
        let tasks = probes.iter().map(|probe| probe.task.clone()).collect();
        self.includes.insert(id.into(), probes);
        Ok(Work::steps(tasks, false))
    }
    fn bind_unused(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Work, String> {
        let mode = component
            .settings
            .pointer("/engines/dead/cpp_unused")
            .and_then(Value::as_str)
            .unwrap_or("auto");
        let linker = component
            .settings
            .pointer("/engines/dead/cpp_linker")
            .and_then(Value::as_str)
            .unwrap_or("off");
        if mode == "off" && linker == "off" {
            self.inactive.insert(
                id.into(),
                "C++ unused-function and linker analysis are explicitly disabled".into(),
            );
            return Ok(Work::Internal);
        }
        let linker_tasks = if linker != "off" {
            self.bind_linker(project, component, id, environment)?
        } else {
            vec![]
        };
        if mode == "off" {
            return Ok(Work::steps(linker_tasks, false));
        }
        if !context.complete() {
            return Err(
                "unused-function analysis requires complete recorded compilation evidence".into(),
            );
        }
        if context.unity_build {
            return Err(
                "unused-function ownership through aggregate/unity builds is unsupported".into(),
            );
        }
        let mut providers = vec![];
        for (index, unit) in context.units.iter().enumerate() {
            let compiler = self.compiler(project, unit, environment)?;
            let replay = ici_cpp::replay_command(
                &project.root,
                unit,
                &compiler.path,
                ReplayPurpose::UnusedFunctions,
            )
            .map_err(|error| error.to_string())?;
            let task_id = format!("{id}.unit{index}");
            let mut provider_request = ProviderRequest::new(
                ProviderKind::Compiler,
                compiler.clone(),
                &project.root,
                &replay.cwd,
                vec![unit.source.clone()],
                &task_id,
            );
            provider_request.options.compiler_argv = replay.argv;
            provider_request.options.source_token =
                Some(replay.source.to_string_lossy().into_owned());
            provider_request.component_id = Some(component.id.clone());
            provider_request.analysis_unit_ids = vec![unit_id(component, unit)];
            provider_request.variant = Some(unit.variant.clone());
            let mut provider =
                ici_providers::plan(&provider_request).map_err(|error| error.to_string())?;
            provider.task = ici_cpp::compiler_probe_task(
                &project.root,
                unit,
                &compiler.path,
                ReplayPurpose::UnusedFunctions,
                &task_id,
            )
            .map_err(|error| error.to_string())?;
            if provider.context.compiler_columns.as_deref() == Some("gnu-byte") {
                provider.task.argv.extend([
                    "-fdiagnostics-column-unit=byte".into(),
                    "-fdiagnostics-column-origin=1".into(),
                ]);
            }
            bind_inputs(project, component, context, unit, &mut provider.task);
            providers.push(provider);
        }
        let mut tasks: Vec<_> = providers
            .iter()
            .map(|provider| provider.task.clone())
            .collect();
        tasks.extend(linker_tasks);
        self.diagnostics.insert(id.into(), providers);
        self.unused.insert(id.into(), context.units.clone());
        Ok(Work::steps(tasks, false))
    }
    fn bind_boundaries(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        check: &CheckDefinition,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Option<Work> {
        let engine = if check.id == "cpp.complexity" {
            "complexity"
        } else {
            "cognitive"
        };
        let policy = component
            .settings
            .pointer(&format!("/engines/{engine}/cpp_boundaries"))
            .and_then(Value::as_str)
            .unwrap_or("auto");
        if policy == "off" {
            return None;
        }
        if !context.complete() {
            return (policy == "required").then(|| {
                blocked("compiler boundaries require complete recorded compilation evidence")
            });
        }
        let named = |name: &str| {
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
        };
        let selected = named("clang-tidy")
            .map(|tool| (tool, true))
            .or_else(|_| named("clang++").map(|tool| (tool, false)));
        let (tool, tidy) = match selected {
            Ok(selected) => selected,
            Err(error) => return (policy == "required").then(|| blocked(error.to_string())),
        };
        self.tools
            .insert(format!("boundaries:{}", tool.id), tool.clone());
        let mut probes = vec![];
        let mut tasks = vec![];
        for (index, unit) in context
            .units
            .iter()
            .enumerate()
            .filter(|(_, unit)| component.files.contains(&unit.source))
        {
            let compiler = match self.compiler(project, unit, environment) {
                Ok(tool) => tool,
                Err(reason) => return Some(blocked(reason)),
            };
            let task_id = format!("{id}.boundaries{index}");
            let native = if tidy {
                ici_cpp::clang_tidy_boundary_task(
                    &project.root,
                    unit,
                    &compiler.path,
                    &tool.path,
                    &ici_cpp::GccStdlibProjection::default(),
                )
            } else {
                ici_cpp::compiler_probe_task(
                    &project.root,
                    unit,
                    &tool.path,
                    ReplayPurpose::AstJson,
                    &task_id,
                )
            };
            let mut task = match native {
                Ok(task) => task,
                Err(error) => return Some(blocked(error.to_string())),
            };
            task.id = task_id.clone();
            if tidy {
                task.argv.push("-w".into());
            }
            bind_inputs(project, component, context, unit, &mut task);
            let mut searches =
                match ici_cpp::gcc_include_search_tasks(&project.root, unit, &compiler.path) {
                    Ok(tasks) => tasks,
                    Err(error) => return Some(blocked(error.to_string())),
                };
            for (index, search) in searches.iter_mut().enumerate() {
                search.id = format!("{task_id}.search{index}");
                search.analysis_unit_ids = vec![unit_id(component, unit)];
            }
            if !searches.is_empty() {
                task.argv.push("{observed-gcc-stdlib}".into());
                tasks.extend(searches.clone());
                self.tooling_searches.insert(task_id, searches);
            }
            tasks.push(task.clone());
            probes.push(BoundaryProbe {
                task,
                unit: unit.clone(),
                tidy,
            });
        }
        if probes.is_empty() {
            return (policy == "required").then(|| {
                blocked("no selected original translation units for compiler boundary observation")
            });
        }
        self.boundaries.insert(id.into(), probes);
        Some(Work::steps(tasks, false))
    }
    fn bind_tooling(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Work, String> {
        let config = component.settings.pointer("/engines/lint");
        let mode = |field| {
            config
                .and_then(|cfg| cfg.get(field))
                .and_then(Value::as_str)
                .unwrap_or("auto")
        };
        if mode("clang_tidy") == "off" && mode("clazy") == "off" {
            self.inactive.insert(
                id.into(),
                "clang-tidy and Clazy are explicitly disabled".into(),
            );
            return Ok(Work::Internal);
        }
        if context.units.is_empty() {
            return Err("clang tooling requires usable recorded compilation units".into());
        }
        if context.units.len() > 2048 {
            return Err("clang tooling exceeds 2048 selected compilation units".into());
        }
        let mut selected = vec![];
        for (field, name, kind) in [
            ("clang_tidy", "clang-tidy", ProviderKind::ClangTidy),
            ("clazy", "clazy-standalone", ProviderKind::Clazy),
        ] {
            if mode(field) == "off" {
                continue;
            }
            let request = ToolRequest {
                id: name.into(),
                explicit: project
                    .tools
                    .get(name)
                    .and_then(|tool| tool.path.as_deref())
                    .map(|path| crate::files::under(&project.root, path)),
                project_root: project.root.clone(),
                pack_root: None,
                path_env: environment.get("PATH").cloned(),
            };
            match request.resolve() {
                Ok(tool) => {
                    self.tools.insert(name.into(), tool.clone());
                    selected.push((kind, tool));
                }
                Err(error) if mode(field) == "required" => return Err(error.to_string()),
                Err(_) => {}
            }
        }
        if selected.is_empty() {
            return Err("clang-tidy and Clazy are disabled or unavailable".into());
        }
        let mut providers = vec![];
        let mut tasks = vec![];
        for (index, unit) in context.units.iter().enumerate() {
            let compiler = self.compiler(project, unit, environment)?;
            for (kind, tool) in &selected {
                let suffix = if *kind == ProviderKind::Clazy {
                    "clazy"
                } else {
                    "tidy"
                };
                let task_id = format!("{id}.{suffix}{index}");
                // The same strict positive replay policy backs metrics and diagnostic tooling.
                let base = ici_cpp::clang_tidy_boundary_task(
                    &project.root,
                    unit,
                    &compiler.path,
                    &tool.path,
                    &ici_cpp::GccStdlibProjection::default(),
                )
                .map_err(|error| error.to_string())?;
                let separator = base
                    .argv
                    .iter()
                    .position(|argument| argument == "--")
                    .expect("native tooling separator");
                let compiler_args = base.argv[separator + 1..].to_vec();
                let mut request = ProviderRequest::new(
                    *kind,
                    tool.clone(),
                    &project.root,
                    &base.cwd,
                    vec![unit.source.clone()],
                    &task_id,
                );
                request.component_id = Some(component.id.clone());
                request.analysis_unit_ids = vec![unit_id(component, unit)];
                request.variant = Some(unit.variant.clone());
                request.options.database_dir = Some(project.root.to_string_lossy().into_owned());
                let mut provider =
                    ici_providers::plan(&request).map_err(|error| error.to_string())?;
                provider.task = base;
                provider.task.id = task_id.clone();
                provider.task.provider = kind.name().into();
                provider.task.analysis_unit_ids = request.analysis_unit_ids;
                provider.task.argv = vec![tool.path.to_string_lossy().into_owned()];
                if *kind == ProviderKind::ClangTidy {
                    provider.task.argv.push("--use-color=false".into());
                    let selected_config = tooling_config(project, component, &unit.source)?;
                    if let Some(path) = selected_config {
                        let relative = path
                            .strip_prefix(&project.root)
                            .map_err(|_| "clang-tidy configuration leaves workspace")?
                            .to_string_lossy()
                            .into_owned();
                        self.compiler_configurations
                            .entry(id.into())
                            .or_default()
                            .insert(relative, 1024 * 1024);
                        provider
                            .task
                            .argv
                            .push(format!("--config-file={}", path.display()));
                        provider
                            .task
                            .input_refs
                            .push(path.to_string_lossy().into_owned());
                    } else {
                        provider.task.argv.push("--config={}".into());
                    }
                    let checks = string_list(config.and_then(|cfg| cfg.get("clang_tidy_checks")));
                    if !checks.is_empty() {
                        provider
                            .task
                            .argv
                            .push(format!("--checks={}", checks.join(",")));
                    } else if !provider
                        .task
                        .argv
                        .iter()
                        .any(|arg| arg.starts_with("--config-file="))
                    {
                        provider
                            .task
                            .argv
                            .push("--checks=-*,bugprone-*,clang-analyzer-*,performance-*".into());
                    }
                } else {
                    let checks = string_list(config.and_then(|cfg| cfg.get("clazy_checks")));
                    let checks = if checks.is_empty() {
                        config
                            .and_then(|cfg| cfg.get("clazy_profile"))
                            .and_then(Value::as_str)
                            .unwrap_or("level0")
                            .to_owned()
                    } else {
                        checks.join(",")
                    };
                    if checks.is_empty()
                        || !checks
                            .chars()
                            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | ','))
                    {
                        return Err("invalid Clazy check selection".into());
                    }
                    provider
                        .task
                        .argv
                        .extend([format!("--checks={checks}"), "--only-qt".into()]);
                }
                provider.task.argv.extend([
                    project
                        .root
                        .join(&unit.source)
                        .to_string_lossy()
                        .into_owned(),
                    "--".into(),
                ]);
                provider.task.argv.extend(compiler_args);
                let mut searches =
                    ici_cpp::gcc_include_search_tasks(&project.root, unit, &compiler.path)
                        .map_err(|error| error.to_string())?;
                for (search_index, search) in searches.iter_mut().enumerate() {
                    search.id = format!("{task_id}.search{search_index}");
                    search.analysis_unit_ids = vec![unit_id(component, unit)];
                }
                if !searches.is_empty() {
                    provider.task.argv.push("{observed-gcc-stdlib}".into());
                    tasks.extend(searches.clone());
                    self.tooling_searches.insert(task_id, searches);
                }
                provider.task.input_refs = provider
                    .task
                    .input_refs
                    .iter()
                    .map(|path| {
                        if Path::new(path).is_absolute() {
                            path.clone()
                        } else {
                            project.root.join(path).to_string_lossy().into_owned()
                        }
                    })
                    .chain(
                        context
                            .database_path
                            .iter()
                            .map(|path| project.root.join(path).to_string_lossy().into_owned()),
                    )
                    .collect();
                tasks.push(provider.task.clone());
                providers.push(provider);
            }
        }
        self.diagnostics.insert(id.into(), providers);
        Ok(Work::steps(tasks, false))
    }
    pub fn configuration_identity(&self) -> Value {
        json!({"contexts":self.contexts.iter().map(|(id,context)|(id,json!({"database":context.database_path,"digest":context.database_digest,"responses":context.response_files,"units":context.units,"selected":context.selected_sources}))).collect::<BTreeMap<_,_>>(),"artifacts":self.artifacts})
    }
}
fn blocked(reason: impl Into<String>) -> Work {
    Work::Blocked {
        reason: reason.into(),
    }
}
fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn bind_inputs(
    project: &ResolvedProject,
    component: &Component,
    context: &CompilationContext,
    unit: &CompilationUnit,
    task: &mut TaskSpec,
) {
    task.analysis_unit_ids = vec![unit_id(component, unit)];
    task.input_refs = task
        .input_refs
        .iter()
        .map(|path| {
            if Path::new(path).is_absolute() {
                path.clone()
            } else {
                project.root.join(path).to_string_lossy().into_owned()
            }
        })
        .chain(
            context
                .database_path
                .iter()
                .map(|path| project.root.join(path).to_string_lossy().into_owned()),
        )
        .collect();
}
fn tooling_config(
    project: &ResolvedProject,
    component: &Component,
    source: &str,
) -> Result<Option<PathBuf>, String> {
    let explicit = component
        .settings
        .pointer("/engines/lint/clang_tidy_config")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty());
    let selected = if let Some(path) = explicit {
        Some(crate::files::under(&project.root, path))
    } else {
        let mut directory = project.root.join(source).parent().map(Path::to_owned);
        let mut selected = None;
        while let Some(current) = directory {
            if !current.starts_with(&project.root) {
                break;
            }
            let candidate = current.join(".clang-tidy");
            if candidate.symlink_metadata().is_ok() {
                selected = Some(candidate);
                break;
            }
            directory = current.parent().map(Path::to_owned);
        }
        selected
    };
    if let Some(path) = &selected {
        if !path.starts_with(&project.root)
            || path.canonicalize().map_err(|error| error.to_string())? != *path
        {
            return Err("clang-tidy config must be contained without symlink traversal".into());
        }
        let bytes = ici_execution::storage::read_bounded_regular(path, 1024 * 1024)
            .map_err(|error| error.to_string())?;
        let text = std::str::from_utf8(&bytes)
            .map_err(|_| "clang-tidy config is not UTF-8".to_string())?;
        let normalized = text.to_ascii_lowercase();
        if text.contains('\0')
            || normalized.contains("extraargs")
            || normalized.contains("inheritparentconfig")
        {
            return Err(
                "clang-tidy config may not inject compiler arguments or inherit parent config"
                    .into(),
            );
        }
    }
    Ok(selected)
}
pub fn linked_build<'a>(
    project: &'a ResolvedProject,
    component: &Component,
) -> Option<&'a ici_config::Build> {
    component
        .build
        .as_ref()
        .and_then(|id| project.builds.iter().find(|build| &build.id == id))
}
fn context(project: &ResolvedProject, component: &Component) -> CompilationContext {
    let explicit = component
        .settings
        .pointer("/project/compile_database")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty())
        .map(|path| {
            if Path::new(path).is_absolute() {
                path.into()
            } else {
                let base = Path::new(&component.declared_in)
                    .parent()
                    .filter(|path| path.is_absolute())
                    .unwrap_or(&project.root);
                base.join(path).to_string_lossy().into_owned()
            }
        });
    let mut directories = BTreeSet::new();
    if component.root != "." {
        directories.insert(component.root.clone());
        directories.insert(format!("{}/build", component.root));
    }
    let build = linked_build(project, component);
    if let Some(directory) = build
        .and_then(|build| build.directory.as_deref())
        .filter(|directory| *directory != ".")
    {
        directories.insert(
            directory
                .trim_start_matches("./")
                .trim_end_matches('/')
                .to_owned(),
        );
    }
    let mut context = ici_cpp::load_compilation_context(
        &project.root,
        &ContextRequest {
            database: explicit,
            variant: build.map(|build| build.variant.clone()),
            build_directories: directories.into_iter().collect(),
            selected_sources: component
                .files
                .iter()
                .filter(|file| file_language(file) == Some("cpp"))
                .cloned()
                .collect(),
        },
    );
    // A shared database does not widen a component into unrelated production translation units.
    let selected: BTreeSet<_> = component.files.iter().collect();
    let build_directory = build.and_then(|build| build.directory.as_deref());
    context.units.retain(|unit| {
        selected.contains(&unit.source)
            || unit.source.contains("/Unity/")
            || unit.source.contains("unity_")
            || build_directory.is_some_and(|directory| {
                unit.source
                    .strip_prefix(directory)
                    .is_some_and(|tail| tail.starts_with('/'))
            })
    });
    context
        .source_coverage
        .retain(|source, _| context.units.iter().any(|unit| &unit.source == source));
    context
}
pub fn analyze_contract(
    project: &ResolvedProject,
    component: &Component,
    request: &AnalysisRequest,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "ici.artifacts");
    let Some(build) = linked_build(project, component) else {
        return failed(output, "no declared build linked");
    };
    let directory = build.directory.as_deref().unwrap_or(".");
    let base = crate::files::under(&project.root, directory);
    let entries = if !base.exists() {
        vec![]
    } else {
        match crate::files::tree(&project.root, &base) {
            Ok(entries) => entries,
            Err(error) => return failed(output, &format!("artifact inventory: {error}")),
        }
    };
    let view = Component {
        root: directory.into(),
        ..component.clone()
    };
    let candidates: Vec<_> = entries.iter().map(|entry| entry.relative.clone()).collect();
    let mut satisfied = 0u64;
    let mut produced = 0u64;
    for pattern in &build.artifacts {
        let matches = match ici_config::matching_source_paths(
            &view,
            &candidates,
            std::slice::from_ref(pattern),
        ) {
            Ok(matches) => matches,
            Err(error) => return failed(output, &error.to_string()),
        };
        let mut count = 0u64;
        for path in matches {
            let entry = entries
                .iter()
                .find(|entry| entry.relative == path)
                .expect("matched bounded inventory");
            let mut target = InspectionTarget::at(&path, 1);
            target.target_name = "DeclaredArtifact".into();
            target.metrics.insert("build".into(), json!(build.id));
            if !entry.contained {
                target.status = "FAIL".into();
                target.message = format!(
                    "declared artifact {path} of build '{}' resolves outside the workspace",
                    build.id
                );
                add_finding(
                    &mut output,
                    &target,
                    "artifact.invalid",
                    &format!("invalid:{}:{path}", build.id),
                );
            } else if entry.file {
                count += 1;
                target.message = "Declared regular artifact exists inside the workspace".into();
            } else {
                continue;
            }
            output.targets.push(target);
        }
        if count > 0 {
            satisfied += 1;
            produced += count;
        } else {
            let location = if directory == "." {
                build
                    .origin
                    .file
                    .strip_prefix(&format!("{}/", project.root.display()))
                    .unwrap_or("ici.toml")
            } else {
                directory
            };
            let mut target = InspectionTarget::at(location, 1);
            target.status = "FAIL".into();
            target.target_name = "MissingDeclaredArtifact".into();
            target.message = format!(
                "declared artifact {pattern:?} of build '{}' produced nothing: no regular match under the build directory",
                build.id
            );
            add_finding(
                &mut output,
                &target,
                "artifact.missing",
                &format!("missing:{}:{pattern}", build.id),
            );
            output.targets.push(target);
        }
    }
    output.observation.measurements = vec![
        Measurement {
            name: "artifacts.satisfied".into(),
            value: satisfied as f64,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "globs".into(),
            numerator: Some(satisfied),
            denominator: (!build.artifacts.is_empty()).then_some(build.artifacts.len() as u64),
        },
        Measurement {
            name: "artifacts.produced".into(),
            value: produced as f64,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "files".into(),
            numerator: None,
            denominator: None,
        },
    ];
    output.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    output.summary = format!(
        "{satisfied}/{} declared artifact patterns satisfied",
        build.artifacts.len()
    );
    output.extra = json!({"build":build.id,"directory":directory,"patterns":build.artifacts,"satisfied":satisfied,"produced":produced});
    output
}
fn add_finding(output: &mut AnalysisOutput, target: &InspectionTarget, rule: &str, identity: &str) {
    let mut finding = Finding::new(
        rule,
        &target.message,
        SourceSpan::line(&target.file_path, target.start_line),
        &output.observation.provider,
    );
    finding.fingerprint = sha256(identity);
    finding.severity = "high".into();
    finding.confidence = "high".into();
    output.observation.findings.push(finding);
}
fn failed(mut output: AnalysisOutput, reason: &str) -> AnalysisOutput {
    output.observation.state = TaskState::Failed;
    output.observation.limitations.push(reason.into());
    output.status = "ERROR".into();
    output.summary = reason.into();
    if output.targets.is_empty() {
        let mut target = InspectionTarget::at(".", 1);
        target.target_name = "CppEvidenceUnavailable".into();
        target.status = "ERROR".into();
        target.message = reason.into();
        output.targets.push(target);
    }
    output
}
fn failed_probe(
    id: &str,
    provider: &str,
    path: &str,
    outcome: &ici_execution::TaskOutcome,
    reason: &str,
) -> AnalysisOutput {
    let mut output = failed(AnalysisOutput::new(id, provider), reason);
    output.targets[0].file_path = path.into();
    output.observation.timed_out = outcome.timed_out;
    output.observation.truncated = outcome.truncated;
    output.observation.signal = outcome.signal.map(|signal| signal.to_string());
    output.observation.exit_code = outcome.exit_code;
    output.observation.duration_seconds = Some(outcome.duration_seconds);
    if outcome.cancelled || outcome.state == TaskState::Cancelled {
        output.observation.state = TaskState::Cancelled;
    }
    output.extra["process"] = json!({"task_id":outcome.task_id,"exit_code":outcome.exit_code,"truncated":outcome.truncated,"timed_out":outcome.timed_out,"cancelled":outcome.cancelled,"stderr":String::from_utf8_lossy(&outcome.stderr).chars().take(1024).collect::<String>()});
    output
}
fn artifact_identity(project: &ResolvedProject, build: &ici_config::Build) -> Value {
    use std::os::unix::fs::MetadataExt;
    let directory = build.directory.as_deref().unwrap_or(".");
    let base = crate::files::under(&project.root, directory);
    if !base.exists() {
        return json!({"directory":directory,"missing":true});
    }
    match crate::files::tree(&project.root, &base) {
        Ok(entries) => {
            let view = Component {
                root: directory.into(),
                ..project.components[0].clone()
            };
            let candidates: Vec<_> = entries.iter().map(|entry| entry.relative.clone()).collect();
            let matched = ici_config::matching_source_paths(&view, &candidates, &build.artifacts)
                .unwrap_or_default();
            json!(matched.into_iter().map(|path|{let metadata=std::fs::metadata(project.root.join(&path)).ok();(path,metadata.map(|metadata|json!({"size":metadata.len(),"dev":metadata.dev(),"ino":metadata.ino(),"mtime":metadata.mtime(),"mtime_ns":metadata.mtime_nsec(),"ctime":metadata.ctime(),"ctime_ns":metadata.ctime_nsec()})))}).collect::<BTreeMap<_,_>>())
        }
        Err(error) => json!({"directory":directory,"error":error.to_string()}),
    }
}

impl CppPlans {
    pub fn handles(&self, id: &str) -> bool {
        self.diagnostics.contains_key(id)
            || self.includes.contains_key(id)
            || self.inactive.contains_key(id)
            || self.boundaries.contains_key(id)
            || self.linkers.contains_key(id)
    }
    /// Completion does not establish a reusable header or compiler-library dependency closure.
    pub fn scope_complete(&self, id: &str) -> bool {
        !self.diagnostics.contains_key(id)
            && !self.includes.contains_key(id)
            && !self.boundaries.contains_key(id)
            && !self.linkers.contains_key(id)
    }
    pub fn finalize_plan(&self, plan: &mut ici_application::plan::Plan) {
        for planned in &mut plan.checks {
            if let Some(reason) = self.inactive.get(&planned.task_id) {
                planned.check.needs.clear();
                plan.limitations.push(format!(
                    "{}: {reason}; this not-applicable check consumes no compiler evidence",
                    planned.task_id
                ));
            }
        }
    }
    pub fn attach_sources(&mut self, sources: std::sync::Arc<ici_providers::SourceCoordinates>) {
        for providers in self.diagnostics.values_mut() {
            for provider in providers {
                ici_providers::attach_sources(provider, sources.clone());
            }
        }
    }
    pub fn run(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        scheduler: &ici_execution::Scheduler,
        cancellation: &ici_execution::Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        if self.linkers.contains_key(&request.task_id) {
            let linker_output =
                self.run_linker(project, component, request, scheduler, cancellation, events);
            if self.unused.contains_key(&request.task_id) {
                let linker = self.linkers.remove(&request.task_id).expect("bound linker");
                let unused = self.run(project, component, request, scheduler, cancellation, events);
                self.linkers.insert(request.task_id.clone(), linker);
                return merge(
                    &request.task_id,
                    "ici.cpp.dead",
                    vec![unused, linker_output],
                );
            }
            return linker_output;
        }
        if let Some(reason) = self.inactive.get(&request.task_id) {
            let mut output = AnalysisOutput::new(&request.task_id, "ici.cpp");
            output.status = "SKIP".into();
            output.summary = reason.clone();
            output.observation.measurements.push(Measurement {
                name: "applicability".into(),
                value: 0.0,
                evidence: ici_domain::EvidenceLevel::NotApplicable,
                unit: "checks".into(),
                numerator: None,
                denominator: None,
            });
            let paths = self
                .inactive_paths
                .get(&request.task_id)
                .cloned()
                .unwrap_or_else(|| {
                    request
                        .inputs
                        .iter()
                        .map(|source| source.path.clone())
                        .collect()
                });
            let paths = if paths.is_empty() {
                vec![".".into()]
            } else {
                paths
            };
            for path in paths {
                let mut target = InspectionTarget::at(&path, 1);
                target.target_name = if request.check_id == "cpp.binary-compat" {
                    "BinaryCompatibility:NotApplicable"
                } else if request.check_id == "cpp.type" {
                    "CppType:NotApplicable"
                } else if request.check_id == "cpp.resource" {
                    "Resource:NotApplicable"
                } else if request.check_id == "cpp.security" {
                    "Security:NotApplicable"
                } else if request.check_id == "cpp.python-compat" {
                    "PythonCompatibility:NotApplicable"
                } else {
                    "CppAnalysisDisabled"
                }
                .into();
                target.status = "SKIP".into();
                target.message = reason.clone();
                output.targets.push(target);
            }
            if request.check_id == "cpp.binary-compat" {
                output.extra["elf"] =
                    json!({"state":"NOT_APPLICABLE","artifacts_checked":0,"facts":[]});
            }
            return output;
        }
        if request.check_id == "cpp.artifact" {
            return analyze_contract(project, component, request);
        }
        if let Some(probes) = self.boundaries.get(&request.task_id).cloned() {
            let mut tasks: Vec<_> = probes.iter().map(|probe| probe.task.clone()).collect();
            let projections = match self.resolve_searches(
                &mut tasks,
                scheduler,
                cancellation,
                events,
                &component.id,
            ) {
                Ok(projections) => projections,
                Err(reason) => {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "ici.metrics"),
                        &reason,
                    );
                }
            };
            let outcomes = match run_cpp_tasks(
                &tasks,
                scheduler,
                cancellation,
                events,
                &component.id,
                "compiler function boundaries",
            ) {
                Ok(outcomes) => outcomes,
                Err(reason) => {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "ici.metrics"),
                        &reason,
                    );
                }
            };
            let mut evidence = vec![];
            let mut sizes = vec![];
            for outcome in outcomes {
                let probe = probes
                    .iter()
                    .find(|probe| probe.task.id == outcome.task_id)
                    .expect("declared boundary task");
                if !outcome.prerequisite_succeeded() {
                    return failed_probe(
                        &request.task_id,
                        "ici.metrics",
                        &probe.unit.source,
                        &outcome,
                        &format!(
                            "compiler boundary probe {} did not complete (exit {:?}, timed_out={}, truncated={}): {}",
                            outcome.task_id,
                            outcome.exit_code,
                            outcome.timed_out,
                            outcome.truncated,
                            outcome.error.as_deref().unwrap_or("")
                        ),
                    );
                }
                let Some(input) = request
                    .inputs
                    .iter()
                    .find(|input| input.path == probe.unit.source)
                else {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "ici.metrics"),
                        "compiler source snapshot is absent",
                    );
                };
                let source = project.root.join(&probe.unit.source);
                let parsed = if probe.tidy {
                    let stdout = match std::str::from_utf8(&outcome.stdout) {
                        Ok(text) => text,
                        Err(_) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "ici.metrics"),
                                "compiler size output is not UTF-8",
                            );
                        }
                    };
                    let stderr = match std::str::from_utf8(&outcome.stderr) {
                        Ok(text) => text,
                        Err(_) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "ici.metrics"),
                                "compiler size diagnostics are not UTF-8",
                            );
                        }
                    };
                    ici_cpp::parse_clang_tidy_boundaries(
                        stdout,
                        stderr,
                        input,
                        &source,
                        &probe.unit.configuration,
                    )
                    .map(|fact| {
                        let compiler = fact.compiler.clone();
                        sizes.push(fact);
                        compiler
                    })
                } else {
                    let stdout = match std::str::from_utf8(&outcome.stdout) {
                        Ok(text) => text,
                        Err(_) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "ici.metrics"),
                                "compiler AST output is not UTF-8",
                            );
                        }
                    };
                    ici_cpp::parse_clang_function_boundaries(
                        stdout,
                        input,
                        &source,
                        &probe.unit.configuration,
                    )
                };
                match parsed {
                    Ok(fact) => evidence.push(fact),
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "ici.metrics"),
                            &error.to_string(),
                        );
                    }
                }
            }
            let mut output = self
                .source_session
                .analyze_with_compiler_context_cancellable(
                    request,
                    &self.contexts[&component.id],
                    &evidence,
                    &cancellation.flag(),
                );
            ici_cpp::apply_function_size_metrics(&mut output, &sizes);
            output.extra["tooling_projections"] = projections;
            return output;
        }
        if request.check_id == "cpp.compile" || self.includes.contains_key(&request.task_id) {
            let mut context = self.contexts[&component.id].clone();
            if let Some(probes) = self.includes.get(&request.task_id) {
                let tasks: Vec<_> = probes.iter().map(|probe| probe.task.clone()).collect();
                let schedule = match scheduler.run_with_start(&tasks, cancellation, |task| {
                    events.emit(
                        "task.started",
                        Some(&task.id),
                        Some(&component.id),
                        "compiler include evidence",
                    )
                }) {
                    Ok(schedule) => schedule,
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "ici.compile"),
                            &error.to_string(),
                        );
                    }
                };
                let mut observations = vec![];
                for outcome in schedule.outcomes {
                    events.emit(
                        "task.completed",
                        Some(&outcome.task_id),
                        Some(&component.id),
                        &format!("{:?}", outcome.state),
                    );
                    let probe = probes
                        .iter()
                        .find(|probe| probe.task.id == outcome.task_id)
                        .expect("outcome from declared probe");
                    let trace = if outcome.completed() && outcome.exit_code == Some(0) {
                        std::str::from_utf8(&outcome.stderr)
                            .map_err(|error| error.to_string())
                            .and_then(|text| {
                                ici_cpp::parse_include_trace(
                                    &project.root,
                                    Path::new(&probe.task.cwd),
                                    &project.root.join(&probe.unit.source),
                                    text,
                                )
                                .map_err(|error| error.to_string())
                            })
                    } else {
                        Err(outcome.error.clone().unwrap_or_else(|| {
                            format!("include probe exited {:?}", outcome.exit_code)
                        }))
                    };
                    match trace {
                        Ok(trace) => observations.push(ici_cpp::CompilerIncludeObservation {
                            source: probe.unit.source.clone(),
                            configuration: probe.unit.configuration.clone(),
                            complete: trace.complete(),
                            trace,
                        }),
                        Err(reason) => {
                            let mut output = failed_probe(
                                &request.task_id,
                                "compiler.includes",
                                &probe.unit.source,
                                &outcome,
                                &reason,
                            );
                            if let Ok(stderr) = std::str::from_utf8(&outcome.stderr)
                                && let Ok(targets) = ici_cpp::parse_missing_include_targets(
                                    &project.root,
                                    Path::new(&probe.task.cwd),
                                    &project.root.join(&probe.unit.source),
                                    stderr,
                                )
                            {
                                output.targets.extend(targets);
                            }
                            return output;
                        }
                    }
                }
                if request.check_id == "cpp.cycle" {
                    return ici_cpp::analyze_compiler_include_cycles(
                        request,
                        &context,
                        &observations,
                    );
                }
                match ici_cpp::confirm_compilation_coverage(&context, &observations) {
                    Ok(confirmed) => {
                        context = confirmed;
                        self.contexts.insert(component.id.clone(), context.clone());
                    }
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "ici.compile"),
                            &error.to_string(),
                        );
                    }
                }
            }
            return ici_cpp::analyze_compile_context(request, &context);
        }
        if let Some(mut providers) = self.diagnostics.get(&request.task_id).cloned() {
            let searches: Vec<_> = providers
                .iter()
                .flat_map(|provider| {
                    self.tooling_searches
                        .get(&provider.task.id)
                        .into_iter()
                        .flatten()
                        .cloned()
                })
                .collect();
            let mut projections = BTreeMap::new();
            if !searches.is_empty() {
                let schedule = match scheduler.run_with_start(&searches, cancellation, |task| {
                    events.emit(
                        "task.started",
                        Some(&task.id),
                        Some(&component.id),
                        "selected compiler library search",
                    )
                }) {
                    Ok(schedule) => schedule,
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "clang-tooling"),
                            &error.to_string(),
                        );
                    }
                };
                let mut outcomes = BTreeMap::new();
                for outcome in schedule.outcomes {
                    events.emit(
                        "task.completed",
                        Some(&outcome.task_id),
                        Some(&component.id),
                        &format!("{:?}", outcome.state),
                    );
                    outcomes.insert(outcome.task_id.clone(), outcome);
                }
                for provider in &mut providers {
                    let Some(searches) = self.tooling_searches.get(&provider.task.id) else {
                        continue;
                    };
                    let projection = match ici_cpp::gcc_standard_library_projection(
                        &outcomes[&searches[0].id],
                        &outcomes[&searches[1].id],
                        Path::new(&provider.task.cwd),
                    ) {
                        Ok(projection) => projection,
                        Err(error) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "clang-tooling"),
                                &error.to_string(),
                            );
                        }
                    };
                    provider
                        .task
                        .argv
                        .retain(|argument| argument != "{observed-gcc-stdlib}");
                    provider.task.argv.extend(projection.arguments.clone());
                    projections.insert(provider.task.id.clone(),json!({"stdlib":projection,"argv":provider.task.argv,"probe_tasks":searches}));
                }
            }
            let tasks: Vec<_> = providers
                .iter()
                .map(|provider| provider.task.clone())
                .collect();
            let schedule = match scheduler.run_with_start(&tasks, cancellation, |task| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    Some(&component.id),
                    "recorded compiler diagnostics",
                )
            }) {
                Ok(schedule) => schedule,
                Err(error) => {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "compiler"),
                        &error.to_string(),
                    );
                }
            };
            let mut outputs = vec![];
            let mut unused_observations = vec![];
            for outcome in schedule.outcomes {
                events.emit(
                    "task.completed",
                    Some(&outcome.task_id),
                    Some(&component.id),
                    &format!("{:?}", outcome.state),
                );
                let provider = providers
                    .iter()
                    .find(|provider| provider.task.id == outcome.task_id)
                    .expect("outcome from declared compiler");
                let output = ici_providers::observe_full(provider, &outcome);
                if let Some(units) = self.unused.get(&request.task_id) {
                    let index = providers
                        .iter()
                        .position(|item| item.task.id == outcome.task_id)
                        .expect("declared unused provider");
                    unused_observations.push(ici_cpp::CompilerConfigurationObservation {
                        source: units[index].source.clone(),
                        configuration: units[index].configuration.clone(),
                        complete: outcome.prerequisite_succeeded()
                            && output.observation.evidence_is_complete(),
                        findings: output.observation.findings.clone(),
                    });
                }
                outputs.push(output);
            }
            if self.unused.contains_key(&request.task_id) {
                let mut output = ici_cpp::unused_function_consensus(
                    request,
                    &self.contexts[&component.id],
                    &unused_observations,
                );
                for probe in &outputs {
                    output.observation.timed_out |= probe.observation.timed_out;
                    output.observation.truncated |= probe.observation.truncated;
                    output.observation.duration_seconds = Some(
                        output.observation.duration_seconds.unwrap_or(0.0)
                            + probe.observation.duration_seconds.unwrap_or(0.0),
                    );
                    if probe.observation.state == TaskState::Cancelled {
                        output.observation.state = TaskState::Cancelled;
                    }
                    output
                        .observation
                        .limitations
                        .extend(probe.observation.limitations.clone());
                }
                return output;
            }
            let mut output = merge(
                &request.task_id,
                if request.check_id == "cpp.tidy" {
                    "clang-tooling"
                } else {
                    "compiler"
                },
                outputs,
            );
            if !projections.is_empty() {
                output.extra["tooling_projections"] = json!(projections);
            }
            return output;
        }
        failed(
            AnalysisOutput::new(&request.task_id, "ici.cpp"),
            "no C++ execution observer bound",
        )
    }
    pub fn partial_input_available(&self, dependency: &str, consumer: &str) -> bool {
        self.contexts.iter().any(|(component, context)| {
            dependency == format!("{component}.cpp.compile")
                && consumer.starts_with(&format!("{component}.cpp."))
                && !context.units.is_empty()
        })
    }
}
pub fn merge(id: &str, provider: &str, outputs: Vec<AnalysisOutput>) -> AnalysisOutput {
    let mut result = AnalysisOutput::new(id, provider);
    let mut details = serde_json::Map::new();
    let mut summaries = vec![];
    for output in outputs {
        if output.observation.state != TaskState::Succeeded {
            result.observation.state = output.observation.state;
        }
        if output.status == "FAIL" && result.status == "PASS" {
            result.status = "FAIL".into();
        }
        result.observation.timed_out |= output.observation.timed_out;
        result.observation.truncated |= output.observation.truncated;
        result.observation.duration_seconds = Some(
            result.observation.duration_seconds.unwrap_or(0.0)
                + output.observation.duration_seconds.unwrap_or(0.0),
        );
        result
            .observation
            .findings
            .extend(output.observation.findings);
        result
            .observation
            .measurements
            .extend(output.observation.measurements);
        result
            .observation
            .tool_versions
            .extend(output.observation.tool_versions);
        result
            .observation
            .limitations
            .extend(output.observation.limitations);
        result.targets.extend(output.targets);
        if let Some(rows) = output
            .extra
            .get("finding_details")
            .and_then(Value::as_object)
        {
            details.extend(rows.clone());
        }
        summaries.push(output.summary);
        result.extra[&output.observation.task_id] = output.extra;
    }
    if result.observation.state != TaskState::Succeeded {
        result.status = "ERROR".into();
    }
    result.extra["finding_details"] = Value::Object(details);
    result.summary = summaries.join("; ");
    result
}

fn unit_id(component: &Component, unit: &CompilationUnit) -> String {
    format!(
        "{}.cpp.{}",
        component.id,
        unit.configuration
            .strip_prefix("sha256:")
            .unwrap_or(&unit.configuration)
    )
}
fn run_cpp_tasks(
    tasks: &[TaskSpec],
    scheduler: &ici_execution::Scheduler,
    cancellation: &ici_execution::Cancellation,
    events: &crate::events::EventLog,
    component: &str,
    description: &str,
) -> Result<Vec<ici_execution::TaskOutcome>, String> {
    let schedule = scheduler
        .run_with_start(tasks, cancellation, |task| {
            events.emit("task.started", Some(&task.id), Some(component), description)
        })
        .map_err(|error| error.to_string())?;
    for outcome in &schedule.outcomes {
        events.emit(
            "task.completed",
            Some(&outcome.task_id),
            Some(component),
            &format!("{:?}", outcome.state),
        );
    }
    Ok(schedule.outcomes)
}

pub fn supplemental_checks(project: &ResolvedProject) -> Vec<CheckDefinition> {
    if !project.components.iter().any(|component| {
        component.languages.iter().any(|language| language == "cpp")
            && (project.schema_family == ici_config::SchemaFamily::Legacy
                || component.checks.contains_key("cpp.dead")
                || project.checks.contains_key("cpp.dead")
                || project.origins.iter().any(|(key, origin)| {
                    origin.layer != "defaults"
                        && (key == "engines.dead" || key.starts_with("engines.dead."))
                }))
    }) {
        return vec![];
    }
    vec![CheckDefinition {
        id: "cpp.dead".into(),
        title: "C++ internal and linker dead functions".into(),
        language: "cpp".into(),
        tool: None,
        required: true,
        profiles: vec![
            ici_domain::Profile::Fast,
            ici_domain::Profile::Standard,
            ici_domain::Profile::Deep,
        ],
        needs: vec![],
        provides: vec![],
        is_static: true,
    }]
}

/// Legacy type descriptor preserves an explicit located C/C++ applicability result.
pub fn type_not_applicable_check() -> CheckDefinition {
    CheckDefinition {
        id: "cpp.type".into(),
        title: "C/C++ type engine applicability".into(),
        language: "cpp".into(),
        tool: None,
        required: false,
        profiles: vec![
            ici_domain::Profile::Fast,
            ici_domain::Profile::Standard,
            ici_domain::Profile::Deep,
        ],
        needs: vec![],
        provides: vec![],
        is_static: true,
    }
}

/// These legacy descriptors inspect Python scope and still owe C++ callers an applicability result.
pub fn legacy_not_applicable_checks() -> Vec<CheckDefinition> {
    [
        ("cpp.type", "C/C++ type engine applicability"),
        ("cpp.resource", "C/C++ resource engine applicability"),
        ("cpp.security", "C/C++ security engine applicability"),
        (
            "cpp.python-compat",
            "C/C++ Python compatibility applicability",
        ),
    ]
    .into_iter()
    .map(|(id, title)| CheckDefinition {
        id: id.into(),
        title: title.into(),
        ..type_not_applicable_check()
    })
    .collect()
}
