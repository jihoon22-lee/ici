//! Compilation evidence, explicit legacy syntax fallback, and artifact contracts.
mod config_scope;
mod fallback;
mod linker;
pub(crate) mod package_flags;
pub use config_scope::external_build_directories;
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
const LEGACY_UNUSED_UNAVAILABLE: &str =
    "Exact C++ unused-function analysis requires a compilation database";
#[derive(Default)]
pub struct CppPlans {
    /// Only explicit legacy commands may use the documented c++17 syntax fallback.
    pub legacy_projection: bool,
    /// A single legacy engine command (`ici lint`, ...): frozen runs it without an analysis
    /// context, so context-bound tools report the "approved capability inventory" refusal.
    pub legacy_standalone: bool,
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
    /// Legacy `ici dead` without recorded compilation: frozen SKIP (ERROR when required) target.
    legacy_unavailable: BTreeMap<String, bool>,
    /// Legacy outputs decided while planning preparation: frozen build FAIL/ERROR for a declared
    /// build that cannot be planned, or the sanitizer SKIP when no C++ test source exists.
    preparation_failures: BTreeMap<String, AnalysisOutput>,
    inactive_paths: BTreeMap<String, Vec<String>>,
    abi_inputs: BTreeMap<String, BTreeMap<String, usize>>,
    compiler_configurations: BTreeMap<String, BTreeMap<String, usize>>,
    compiler_components: BTreeMap<String, String>,
    resolved: BTreeMap<String, std::result::Result<ResolvedTool, String>>,
    fallbacks: BTreeMap<String, fallback::FallbackPlan>,
    compile_externals: BTreeMap<String, Vec<String>>,
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
            self.contexts.insert(
                component.id.clone(),
                context(project, component, environment),
            );
        }
        let context = if self.legacy_standalone
            && matches!(check.id.as_str(), "cpp.diagnostics" | "cpp.tidy")
        {
            // Frozen `ici lint` runs without an analysis context: the C++ syntax check always
            // takes the c++17 heuristic fallback, whatever compilation database exists.
            CompilationContext::default()
        } else {
            self.contexts[&component.id].clone()
        };
        if self.legacy_projection && check.id == "cpp.compile" {
            match external_build_directories(project, component, environment) {
                Ok(directories) => {
                    self.compile_externals.insert(id.into(), directories);
                }
                Err(error) => return Some(blocked(error)),
            }
        }
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
                if self.compile_scope_is_empty(id) {
                    return Some(Work::Internal);
                }
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
                if self.legacy_projection
                    && context.database_path.is_none()
                    && context.units.is_empty()
                    && context.diagnostics.is_empty()
                {
                    return Some(
                        match self.bind_fallback(project, component, id, environment) {
                            Ok(work) => work,
                            Err(error) => blocked(error),
                        },
                    );
                }
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
        if self.legacy_projection && linker_tasks.is_empty() && context.units.is_empty() {
            // Frozen DeadCodeEngine reports C++UnusedFunctionsUnavailable instead of running.
            self.legacy_unavailable
                .insert(id.into(), mode == "required");
            return Ok(Work::Internal);
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
        if context.units.is_empty() && self.legacy_projection {
            // Frozen LintEngine without an exact context: one ClangTidyUnavailable and one
            // ClazyUnavailable target at "." (WARN, or ERROR when the tool is required).
            let mut unavailable = vec![];
            for (field, name, label) in [
                ("clang_tidy", "clang-tidy", "ClangTidyUnavailable"),
                ("clazy", "clazy-standalone", "ClazyUnavailable"),
            ] {
                if mode(field) == "off" {
                    continue;
                }
                let tool = if name == "clazy-standalone" {
                    "clazy"
                } else {
                    name
                };
                let resolvable = ToolRequest {
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
                .is_ok();
                let message = if self.legacy_standalone {
                    format!(
                        "{tool} requires an approved capability inventory and exact compilation context"
                    )
                } else if resolvable {
                    format!("{tool} requires an exact compilation database")
                } else {
                    format!("{tool} is unavailable or incompletely probed")
                };
                unavailable.push((label, message, mode(field) == "required"));
            }
            self.preparation_failures
                .insert(id.into(), legacy_tooling_unavailable(id, &unavailable));
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
fn context(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
) -> CompilationContext {
    let explicit = component
        .settings
        .pointer("/project/compile_database")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty())
        .map(str::to_owned);
    let explicit = if explicit.is_some() {
        explicit
    } else if let Some(value) = project
        .effective
        .pointer("/project/compile_database")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty())
    {
        match project.resolve_path("project.compile_database", value, environment) {
            Ok(path) => Some(path),
            Err(error) => {
                return CompilationContext {
                    diagnostics: vec![ici_cpp::ContextDiagnostic::error(
                        "invalid-database-setting",
                        error.to_string(),
                    )],
                    ..Default::default()
                };
            }
        }
    } else {
        None
    };
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
    let present = base.is_dir();
    let entries = if !present {
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
    // Frozen `ici.artifacts` locates contract findings at the build directory; "." is not a
    // project-relative finding path here, so a root build keeps its declaration file instead.
    let location = if directory == "." {
        build
            .origin
            .file
            .strip_prefix(&format!("{}/", project.root.display()))
            .unwrap_or("ici.toml")
    } else {
        directory
    };
    let candidates: Vec<_> = entries.iter().map(|entry| entry.relative.clone()).collect();
    let mut satisfied = 0u64;
    let mut produced = 0u64;
    for pattern in &build.artifacts {
        let matches = if present {
            match ici_config::matching_source_paths(
                &view,
                &candidates,
                std::slice::from_ref(pattern),
            ) {
                Ok(matches) => matches,
                Err(error) => return failed(output, &error.to_string()),
            }
        } else {
            vec![]
        };
        let mut count = 0u64;
        let mut directories = 0u64;
        for path in matches {
            let entry = entries
                .iter()
                .find(|entry| entry.relative == path)
                .expect("matched bounded inventory");
            // Frozen resolves each match strictly: a dangling link vanished, so it is no match.
            if project.root.join(&path).canonicalize().is_err() {
                continue;
            }
            let mut target = InspectionTarget::at(&path, 1);
            target.target_name = "DeclaredArtifact".into();
            target.metrics.insert("build".into(), json!(build.id));
            if !entry.contained {
                target.status = "FAIL".into();
                target.message = format!(
                    "declared artifact {path} of build '{}' resolves outside the workspace — it is not an artifact this run produced",
                    build.id
                );
                let mut located = target.clone();
                located.file_path = location.into();
                add_artifact_finding(
                    &mut output,
                    &located,
                    "artifact.invalid",
                    &format!("invalid:{}:{path}", build.id),
                );
            } else if entry.file {
                count += 1;
                target.message = "Declared regular artifact exists inside the workspace".into();
            } else {
                directories += 1;
                continue;
            }
            output.targets.push(target);
        }
        if count > 0 {
            satisfied += 1;
            produced += count;
        } else {
            let detail = if !present {
                "the build directory is absent".to_string()
            } else if directories > 0 {
                format!("only {directories} non-file match(es)")
            } else {
                "no match under the build directory".to_string()
            };
            let mut target = InspectionTarget::at(location, 1);
            target.status = "FAIL".into();
            target.target_name = "MissingDeclaredArtifact".into();
            target.message = format!(
                "declared artifact {} of build '{}' produced nothing: {detail}",
                ici_analysis::unicode::python_repr(pattern),
                build.id
            );
            add_artifact_finding(
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
/// Frozen `ici.artifacts` raw fingerprint: `artifact-` + the first 16 hex digits of
/// sha1("<kind>:<build>:<pattern or path>"); run attribution then derives the envelope id.
fn add_artifact_finding(
    output: &mut AnalysisOutput,
    target: &InspectionTarget,
    rule: &str,
    identity: &str,
) {
    use sha1::Digest;
    add_finding(output, target, rule, identity);
    let digest = format!("{:x}", sha1::Sha1::digest(identity.as_bytes()));
    if let Some(finding) = output.observation.findings.last_mut() {
        finding.fingerprint = format!("artifact-{}", &digest[..16]);
        finding.evidence = ici_domain::EvidenceLevel::Measured;
    }
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
/// Checks that consume a prepared native build variant and are blocked when it is unavailable.
pub fn preparation_dependent(check_id: &str, variant: &str, has_tasks: bool) -> bool {
    match variant {
        "coverage" => check_id == "cpp.coverage",
        "sanitize" => check_id == "cpp.sanitize",
        "thread-sanitize" => check_id == "cpp.tsan",
        "link-reachability" => check_id == "cpp.dead",
        _ => {
            matches!(
                check_id,
                "cpp.compile"
                    | "cpp.diagnostics"
                    | "cpp.tidy"
                    | "cpp.artifact"
                    | "cpp.binary-compat"
            ) || (has_tasks
                && !matches!(
                    check_id,
                    "cpp.test" | "cpp.coverage" | "cpp.sanitize" | "cpp.tsan"
                ))
        }
    }
}

/// Frozen `_count_adapter_artifacts` (no shared analysis context under `ici build`): regular,
/// non-symlink files in the shadow tree that are linked binaries or `!<arch>` static archives,
/// objects and coverage notes excluded. `None` when the walk cannot be completed.
pub fn legacy_shadow_linked_outputs(shadow: &Path) -> Option<usize> {
    const MAGICS: [&[u8]; 6] = [
        b"\x7fELF",
        b"MZ",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    ];
    let mut pending = vec![shadow.to_path_buf()];
    let (mut visited, mut count) = (0usize, 0usize);
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory).ok()? {
            let entry = entry.ok()?;
            visited += 1;
            if visited > 100_000 {
                return None;
            }
            let kind = entry.file_type().ok()?;
            let path = entry.path();
            if kind.is_dir() {
                pending.push(path);
                continue;
            }
            let extension = path.extension().and_then(|value| value.to_str());
            if !kind.is_file() || matches!(extension, Some("o" | "obj" | "gcno" | "gcda")) {
                continue;
            }
            let mut prefix = [0u8; 8];
            let read = std::fs::File::open(&path)
                .and_then(|mut file| std::io::Read::read(&mut file, &mut prefix))
                .unwrap_or(0);
            let prefix = &prefix[..read];
            let archive =
                matches!(extension, Some("a" | "lib")) && prefix.starts_with(b"!<arch>\n");
            if archive || MAGICS.iter().any(|magic| prefix.starts_with(magic)) {
                count += 1;
            }
        }
    }
    Some(count)
}

/// Frozen `_unavailable` targets of clang-tidy and Clazy in a legacy lint run.
fn legacy_tooling_unavailable(id: &str, unavailable: &[(&str, String, bool)]) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici.cpp.tooling");
    let errors: Vec<_> = unavailable
        .iter()
        .filter(|(_, _, required)| *required)
        .map(|(_, message, _)| message.clone())
        .collect();
    output.status = if !errors.is_empty() {
        "ERROR"
    } else if unavailable.is_empty() {
        "PASS"
    } else {
        "WARN"
    }
    .into();
    output.summary = if errors.is_empty() {
        unavailable
            .iter()
            .map(|(_, message, _)| message.as_str())
            .collect::<Vec<_>>()
            .join("; ")
    } else {
        errors.join("; ")
    };
    output.extra["legacy_tool_warnings"] = json!(
        unavailable
            .iter()
            .filter(|(_, _, required)| !*required)
            .map(|(_, message, _)| message)
            .collect::<Vec<_>>()
    );
    for (label, message, required) in unavailable {
        output.observation.limitations.push(message.clone());
        let mut target = InspectionTarget::at(".", 1);
        target.target_name = (*label).into();
        target.status = if *required { "ERROR" } else { "WARN" }.into();
        target.message = message.clone();
        output.targets.push(target);
    }
    output
}

/// Frozen `_cpp_lint` heuristic fallback projection of the legacy `cpp.diagnostics` output:
/// `Compiler:<rule>` / `C++Syntax` targets with kind-prefixed messages (errors FAIL, warnings and
/// notes WARN, notes as their own targets), `C++Syntax:fallback` PASS for clean files, and the
/// frozen LintEngine summary over these targets, the clang tooling targets and tool warnings.
pub fn legacy_fallback_lint(output: &mut AnalysisOutput, tooling: Option<&AnalysisOutput>) {
    const FALLBACK: &str = "No compilation database is available; C++ syntax analysis used the c++17 heuristic fallback";
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let details = output
        .extra
        .get("finding_details")
        .cloned()
        .unwrap_or_default();
    let findings = output.observation.findings.clone();
    let mut targets = vec![];
    for target in &output.targets {
        if target.status == "PASS" && target.target_name.is_empty() {
            let mut clean = target.clone();
            clean.target_name = "C++Syntax:fallback".into();
            clean.message = "Compiler syntax analysis completed without diagnostics.".into();
            targets.push(clean);
            continue;
        }
        let Some(finding) = findings.iter().find(|finding| {
            finding.primary_location.path == target.file_path
                && finding.primary_location.start_line == target.start_line
                && finding.primary_location.start_column == target.start_column
                && finding.message == target.message
                && finding.native_rule_id == target.target_name
        }) else {
            targets.push(target.clone());
            continue;
        };
        let Some(legacy) = details
            .get(&finding.fingerprint)
            .and_then(|detail| detail.get("legacy_message"))
            .and_then(Value::as_str)
        else {
            return;
        };
        let kind = legacy.split(": ").next().unwrap_or_default();
        let mut primary = target.clone();
        primary.message = legacy.into();
        primary.status = if kind.contains("error") || kind == "sorry, unimplemented" {
            "FAIL"
        } else {
            "WARN"
        }
        .into();
        primary.target_name = if finding.native_rule_id.starts_with('-') {
            format!("Compiler:{}", finding.native_rule_id)
        } else {
            "C++Syntax".into()
        };
        targets.push(primary);
        for note in &finding.related_locations {
            if !note.label.starts_with("note: ") {
                continue;
            }
            let mut related = InspectionTarget::at(&note.path, note.start_line);
            related.start_column = note.start_column;
            related.target_name = "C++Syntax".into();
            related.status = "WARN".into();
            related.message = note.label.clone();
            targets.push(related);
        }
    }
    output.targets = targets;
    let all: Vec<&InspectionTarget> = output
        .targets
        .iter()
        .chain(
            tooling
                .into_iter()
                .flat_map(|tooling| tooling.targets.iter()),
        )
        .collect();
    let fail = all.iter().filter(|target| target.status == "FAIL").count();
    let mut warnings = vec![FALLBACK.to_string()];
    warnings.extend(
        tooling
            .and_then(|tooling| tooling.extra.get("legacy_tool_warnings"))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned),
    );
    let messages: BTreeSet<&str> = all
        .iter()
        .filter(|target| target.status == "WARN")
        .map(|target| target.message.as_str())
        .collect();
    let warn = all.iter().filter(|target| target.status == "WARN").count()
        + warnings
            .iter()
            .filter(|message| !messages.contains(message.as_str()))
            .count();
    output.status = if fail > 0 { "FAIL" } else { "WARN" }.into();
    output.summary = if fail == 0 {
        warnings
            .iter()
            .take(2)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ")
    } else {
        format!("{fail} Errors, {warn} Warnings Found")
    };
}

/// Frozen SanitizeEngine / ThreadSanitizeEngine direct g++ projection. Each instrumented test
/// group ends with its "Instrumented suite" target: a group with sanitizer diagnostics keeps
/// only those located targets, a clean run becomes one `<label>` PASS target, and the summary
/// is the frozen clean or "<n> <noun> Defect(s) Detected" text. Any other outcome (an executable
/// failing without a diagnostic) is left untouched rather than guessed.
pub fn legacy_direct_sanitizer(output: &mut AnalysisOutput, check_id: &str) {
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let (label, clean, summary, noun) = if check_id == "cpp.tsan" {
        (
            "TSan",
            "ThreadSanitizer completed without diagnostics",
            "Thread Safety & TSan Clean (0 Defects)",
            "Thread Safety",
        )
    } else {
        (
            "ASan/UBSan",
            "AddressSanitizer and UndefinedBehaviorSanitizer completed",
            "Memory Safety & Sanitize Clean (0 Defects)",
            "Memory / Resource",
        )
    };
    let mut reshaped = vec![];
    let mut group: Vec<InspectionTarget> = vec![];
    for target in &output.targets {
        if target.target_name != "Instrumented suite" {
            group.push(target.clone());
            continue;
        }
        let (executables, diagnostics): (Vec<_>, Vec<_>) = group.drain(..).partition(|item| {
            item.target_name == item.file_path && item.message.starts_with("executable exited")
        });
        if !diagnostics.is_empty() {
            reshaped.extend(diagnostics);
        } else if executables.len() == 1 && executables[0].status == "PASS" {
            let mut target = InspectionTarget::at(&executables[0].file_path, 1);
            target.target_name = label.into();
            target.message = clean.into();
            reshaped.push(target);
        } else {
            return;
        }
    }
    if !group.is_empty() || reshaped.is_empty() {
        return;
    }
    let issues = reshaped
        .iter()
        .filter(|target| matches!(target.status.as_str(), "WARN" | "FAIL"))
        .count();
    output.targets = reshaped;
    output.summary = if output.status == "PASS" {
        summary.into()
    } else {
        format!("{issues} {noun} Defect(s) Detected")
    };
}

/// Frozen BinaryCompatibilityEngine verdict when it cannot select or inspect artifacts: the
/// not-applicable SKIP, or `BinaryCompatibility:AnalysisUnavailable` ERROR at ".".
pub fn legacy_binary_compat_unavailable(id: &str, error: Option<&str>) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "binarycompat");
    let mut target = InspectionTarget::at(".", 1);
    if let Some(error) = error {
        output.status = "ERROR".into();
        output.summary = format!("Binary compatibility analysis incomplete: {error}");
        output.observation.limitations.push(error.into());
        target.target_name = "BinaryCompatibility:AnalysisUnavailable".into();
        target.status = "ERROR".into();
        target.message = error.into();
    } else {
        output.status = "SKIP".into();
        output.summary =
            "Binary compatibility not applicable: no selected manifest artifacts".into();
        output.observation.measurements.push(Measurement {
            name: "applicability".into(),
            value: 0.0,
            evidence: ici_domain::EvidenceLevel::NotApplicable,
            unit: "checks".into(),
            numerator: None,
            denominator: None,
        });
        output.extra["elf"] = json!({"state":"NOT_APPLICABLE","artifacts_checked":0,"facts":[]});
        target.target_name = "BinaryCompatibility:NotApplicable".into();
        target.status = "SKIP".into();
        target.message = "No executable or shared-library manifest artifacts were selected".into();
    }
    output.targets.push(target);
    output
}

/// Frozen BinaryCompatibilityEngine targets and summary for measured ELF evidence: one
/// `BinaryCompatibility:<variant>:shadow:<path>` target per inspected artifact at its shadow
/// path, `BinaryCompatibility:NonElf` SKIP for allowed non-ELF files, and the
/// "ELF compatibility: N checked, V violation(s), S skipped" summary.
pub fn legacy_binary_compat(output: &mut AnalysisOutput, shadow: &str, variant: &str) {
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let prefix = if shadow.is_empty() || shadow == "." {
        String::new()
    } else {
        format!("{}/", shadow.trim_end_matches('/'))
    };
    let (mut checked, mut skipped) = (0usize, 0usize);
    let findings = output.observation.findings.clone();
    for target in &mut output.targets {
        if target.target_name != "ELF/ABI artifact" {
            continue;
        }
        let path = target.file_path.clone();
        let relative = path.strip_prefix(&prefix).unwrap_or(&path).to_owned();
        let identity = format!("{variant}:shadow:{relative}");
        target.file_path = relative;
        if target.message == "selected artifact is not ELF" {
            if target.status == "SKIP" {
                skipped += 1;
                target.target_name = "BinaryCompatibility:NonElf".into();
                target.message = "Manifest artifact is not ELF and policy allows it".into();
            } else {
                target.target_name = format!("BinaryCompatibility:{identity}");
                target.message = "Selected binary manifest artifact is not an ELF object".into();
            }
            continue;
        }
        checked += 1;
        let violations = findings
            .iter()
            .filter(|finding| finding.primary_location.path == path)
            .count();
        target.target_name = format!("BinaryCompatibility:{identity}");
        target.message = if violations > 0 {
            format!("{violations} binary compatibility violation(s)")
        } else {
            "ELF dependency, loader path, and ABI evidence verified".into()
        };
    }
    output.summary = format!(
        "ELF compatibility: {checked} checked, {} violation(s), {skipped} skipped",
        findings.len()
    );
}

/// Frozen `_build_with_adapter` success target: `BuildAdapter[<backend>]` PASS at the descriptor,
/// "<select_backend reason>; produced N artifact(s)".
pub fn legacy_adapter_success(
    root: &Path,
    component_root: &str,
    system: &str,
    descriptor: &str,
    produced: usize,
) -> InspectionTarget {
    let base = crate::files::under(root, component_root);
    let name = Path::new(descriptor)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(descriptor)
        .to_owned();
    let reason = match system {
        "cmake" => {
            let mut reason =
                "CMakeLists.txt at the project root selected the CMake backend".to_string();
            let mut projects: Vec<String> = std::fs::read_dir(&base)
                .into_iter()
                .flatten()
                .flatten()
                .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
                .filter_map(|entry| entry.file_name().into_string().ok())
                .filter(|name| name.ends_with(".pro"))
                .collect();
            projects.sort();
            if let Some(first) = projects.first() {
                reason.push_str(&format!("; {first} was present and passed over"));
            }
            reason
        }
        "qmake" => format!("{name} at the project root selected the qmake backend"),
        _ => format!("{name} at the project root selected the configured Make backend"),
    };
    let backend = match system {
        "cmake" => "cmake",
        "qmake" => "qmake",
        _ => "make",
    };
    let mut target = InspectionTarget::at(&name, 1);
    target.target_name = format!("BuildAdapter[{backend}]");
    target.message = format!("{reason}; produced {produced} artifact(s)");
    target
}

/// Frozen sanitizer engines compile only `tests/**/*.cpp` under the project root. `true` means
/// the walk proved there is none; a symlinked `tests`, an unreadable entry or a walk over its
/// bounds is not proof, so the normal (blocking) preparation path decides those.
pub fn legacy_sanitizer_tests_absent(root: &Path) -> bool {
    const IGNORED: [&str; 7] = [
        ".venv",
        "venv",
        "build",
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
    ];
    let tests = root.join("tests");
    let Ok(metadata) = std::fs::symlink_metadata(&tests) else {
        return true;
    };
    if !metadata.is_dir() {
        return !metadata.file_type().is_symlink();
    }
    let mut pending = vec![(tests, 0usize)];
    let mut entries = 0usize;
    while let Some((directory, depth)) = pending.pop() {
        let Ok(listing) = std::fs::read_dir(&directory) else {
            return false;
        };
        for entry in listing {
            entries += 1;
            let (Ok(entry), true) = (entry, entries <= 100_000) else {
                return false;
            };
            let Ok(kind) = entry.file_type() else {
                return false;
            };
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if kind.is_dir() {
                if depth >= 64 {
                    return false;
                }
                if !IGNORED.contains(&name.as_ref())
                    && !name.starts_with("v0.")
                    && !name.starts_with("v1.")
                {
                    pending.push((entry.path(), depth + 1));
                }
            } else if kind.is_file() && Path::new(name.as_ref()).extension() == Some("cpp".as_ref())
            {
                return false;
            }
        }
    }
    true
}

/// Frozen SanitizeEngine / ThreadSanitizeEngine verdict when no C++ test source exists: one
/// not-required `C++Sanitizer` SKIP target at `tests`, before any backend or build is chosen.
pub fn legacy_sanitizer_skip(id: &str, check_id: &str) -> AnalysisOutput {
    const MESSAGE: &str = "No C++ sanitizer test sources were selected; compilation was not run";
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "SKIP".into();
    output.summary = if check_id == "cpp.tsan" {
        "ThreadSanitizer skipped: no applicable C++ checks were executed"
    } else {
        "Sanitize skipped: no applicable checks were executed"
    }
    .into();
    output.observation.measurements.push(Measurement {
        name: "applicability".into(),
        value: 0.0,
        // Frozen `_mark_scope_skip` counts a skipped scope, so the verdict is ESTIMATED (it keeps
        // the required legacy suite at ERROR), not NOT_APPLICABLE.
        evidence: ici_domain::EvidenceLevel::Estimated,
        unit: "checks".into(),
        numerator: None,
        denominator: None,
    });
    output.observation.limitations.push(MESSAGE.into());
    let mut target = InspectionTarget::at("tests", 1);
    target.target_name = "C++Sanitizer".into();
    target.status = "SKIP".into();
    target.message = MESSAGE.into();
    output.targets.push(target);
    output
}

/// Frozen `_fail_adapter_scope` for a sanitizer build declaration that cannot be configured: one
/// `C++Sanitizer` ERROR target at the descriptor whose message is the configuration error.
pub fn legacy_sanitizer_configuration_error(
    id: &str,
    descriptor: &str,
    message: &str,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "ERROR".into();
    output.summary = message.into();
    output.observation.limitations.push(message.into());
    let mut target = InspectionTarget::at(descriptor, 1);
    target.target_name = "C++Sanitizer".into();
    target.status = "ERROR".into();
    target.message = message.into();
    output.targets.push(target);
    output
}

/// Frozen BuildEngine result for a native build that did not produce a complete artifact set:
/// one located adapter target (`BuildAdapter` FAIL, or `BuildError` ERROR for configuration).
pub fn build_failure(
    id: &str,
    descriptor: &str,
    finding_path: &str,
    configuration: bool,
    message: &str,
    detail: &str,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    let status = if configuration { "ERROR" } else { "FAIL" };
    // The build verdict was measured: a failed build is a located, blocking finding (gate FAIL
    // when the build check is required, as under `ici build`), not missing evidence.
    output.observation.state = TaskState::Succeeded;
    output.observation.limitations.push(detail.into());
    output.status = status.into();
    output.summary = if configuration {
        message.into()
    } else {
        "Build did not produce a complete artifact set".into()
    };
    let mut target = InspectionTarget::at(descriptor, 1);
    target.target_name = if configuration {
        "BuildError"
    } else {
        "BuildAdapter"
    }
    .into();
    target.status = status.into();
    target.message = message.into();
    // The frozen target may sit at "."; the blocking finding needs a project file location.
    let mut located = target.clone();
    located.file_path = finding_path.into();
    // A frozen message may end inside stderr whitespace; finding text must be trimmed.
    located.message = message.trim().into();
    add_finding(
        &mut output,
        &located,
        "build.adapter",
        &format!("build.adapter:{finding_path}:{message}"),
    );
    if let Some(finding) = output.observation.findings.last_mut() {
        finding.native_rule_id = target.target_name.clone();
        finding.category = "build".into();
    }
    output.targets.push(target);
    output
}

pub(crate) fn failed_probe(
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
    /// Record the frozen legacy output for a check decided while planning its preparation.
    pub fn fail_preparation(&mut self, id: &str, output: AnalysisOutput) {
        self.preparation_failures.insert(id.into(), output);
    }
    /// Whether this task runs the legacy c++17 heuristic fallback.
    pub fn is_fallback(&self, id: &str) -> bool {
        self.fallbacks.contains_key(id)
    }
    /// A frozen legacy output decided while planning, if any.
    pub fn planned(&self, id: &str) -> Option<&AnalysisOutput> {
        self.preparation_failures.get(id)
    }
    /// Whether a frozen legacy output was already decided for this task while planning.
    pub fn planned_output(&self, id: &str) -> bool {
        self.preparation_failures.contains_key(id)
    }
    pub fn handles(&self, id: &str) -> bool {
        self.diagnostics.contains_key(id)
            || self.includes.contains_key(id)
            || self.inactive.contains_key(id)
            || self.legacy_unavailable.contains_key(id)
            || self.preparation_failures.contains_key(id)
            || self.boundaries.contains_key(id)
            || self.linkers.contains_key(id)
            || self.fallbacks.contains_key(id)
    }
    /// Completion does not establish a reusable header or compiler-library dependency closure.
    pub fn scope_complete(&self, id: &str) -> bool {
        !self.diagnostics.contains_key(id)
            && !self.includes.contains_key(id)
            && !self.boundaries.contains_key(id)
            && !self.linkers.contains_key(id)
            && !self.fallbacks.contains_key(id)
            && !self.legacy_unavailable.contains_key(id)
            && !self.preparation_failures.contains_key(id)
    }
    pub fn finalize_plan(&self, plan: &mut ici_application::plan::Plan) {
        for planned in &mut plan.checks {
            if self.fallbacks.contains_key(&planned.task_id) {
                planned.check.needs.clear();
                plan.limitations.push(format!("{}: legacy c++17 heuristic fallback; ordered pkg-config probes resolve only the declared package flags at execution; no recorded compilation or reusable header closure is claimed",planned.task_id));
            }
            if let Some(required) = self.legacy_unavailable.get(&planned.task_id) {
                planned.check.needs.clear();
                plan.limitations.push(format!(
                    "{}: {LEGACY_UNUSED_UNAVAILABLE}; the legacy projection reports it as {} like the frozen pyz, without running the compiler",
                    planned.task_id,
                    if *required { "ERROR" } else { "SKIP" }
                ));
            }
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
        for fallback in self.fallbacks.values_mut() {
            for provider in &mut fallback.providers {
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
        if self.fallbacks.contains_key(&request.task_id) {
            return self.run_fallback(project, component, request, scheduler, cancellation, events);
        }
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
        if let Some(output) = self.preparation_failures.get(&request.task_id) {
            return output.clone();
        }
        if let Some(required) = self.legacy_unavailable.get(&request.task_id) {
            let mut output = AnalysisOutput::new(&request.task_id, "ici.cpp");
            let status = if *required { "ERROR" } else { "SKIP" };
            output.status = status.into();
            output.summary = LEGACY_UNUSED_UNAVAILABLE.into();
            output.observation.measurements.push(Measurement {
                name: "cpp_unused_configurations_checked".into(),
                value: 0.0,
                evidence: ici_domain::EvidenceLevel::NotRun,
                unit: "configurations".into(),
                numerator: None,
                denominator: None,
            });
            output
                .observation
                .limitations
                .push(LEGACY_UNUSED_UNAVAILABLE.into());
            // Frozen: one target at the first selected C/C++ source in path order.
            let path = request
                .inputs
                .iter()
                .map(|source| source.path.clone())
                .min()
                .unwrap_or_else(|| ".".into());
            let mut target = InspectionTarget::at(&path, 1);
            target.target_name = "C++UnusedFunctionsUnavailable".into();
            target.status = status.into();
            target.message = LEGACY_UNUSED_UNAVAILABLE.into();
            output.targets.push(target);
            return output;
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
            let legacy_disabled = self.legacy_projection && request.check_id == "cpp.dead";
            let paths = if paths.is_empty() || legacy_disabled {
                // Frozen DeadCodeEngine reports one disabled-policy target at the project root.
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
                } else if legacy_disabled {
                    "C++UnusedFunctionsDisabled"
                } else {
                    "CppAnalysisDisabled"
                }
                .into();
                target.status = "SKIP".into();
                target.message = if legacy_disabled {
                    "Compiler-backed C++ unused-function analysis is disabled".into()
                } else {
                    reason.clone()
                };
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
                                if request.check_id == "cpp.cycle" {
                                    let limit = request.options["max_reported"]
                                        .as_u64()
                                        .unwrap_or(20)
                                        .max(1);
                                    let limit = usize::try_from(limit).unwrap_or(usize::MAX);
                                    let truncated = targets.len().saturating_sub(limit);
                                    output.targets.extend(targets.into_iter().take(limit));
                                    output.extra["include_diagnostics_truncated"] =
                                        json!(truncated);
                                } else {
                                    output.targets.extend(targets);
                                }
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
            return ici_cpp::analyze_compile_context(
                request,
                &self.legacy_compile_context(&request.task_id, &context),
            );
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
            if request.check_id == "cpp.diagnostics" {
                self.preserve_diagnostic_scope(&component.id, &mut output);
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
