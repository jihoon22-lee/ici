//! Compilation evidence, explicit legacy syntax fallback, and artifact contracts.
mod config_scope;
mod fallback;
pub(crate) mod legacy_lint_extra;
mod legacy_syntax;
mod legacy_tests;
mod legacy_tooling;
mod linker;
mod next_plan;
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
pub use legacy_syntax::set_legacy_syntax_budget_for_tests;
pub use legacy_tests::*;
pub use legacy_tooling::set_legacy_tooling_budget_for_tests;
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
const LEGACY_LINKER_UNAVAILABLE: &str = "Exact GNU ELF reachability requires a root CMake project";
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
    /// Legacy dead checks whose GNU ELF linker policy (`required` = true) cannot run because the
    /// project has no root CMake backend (frozen `run_cpp_linker_dead_symbols` unavailable).
    legacy_linker_unavailable: BTreeMap<String, bool>,
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
    /// Legacy lint with one clang tool resolvable: the frozen Unavailable target of the other.
    legacy_tooling_missing: BTreeMap<String, Vec<legacy_tooling::MissingTool>>,
    /// Every recorded unit of a component's database, before the component-file filter (frozen
    /// `context.compilation.units`, read by Qt generated-code verification).
    database_units: BTreeMap<String, Vec<CompilationUnit>>,
    /// Legacy lint syntax units: the component's units plus Qt generated `moc_*` / `qrc_*` /
    /// `mocs_compilation.cpp` units of the database (frozen `_cpp_lint._selected_units`).
    legacy_syntax_units: BTreeMap<String, Vec<CompilationUnit>>,
    /// Legacy `cpp.diagnostics` tasks that stop before any replay (frozen `_run_exact`: error
    /// context diagnostics, no replayable production unit, the selected-unit budget).
    legacy_context_errors: BTreeMap<String, legacy_syntax::Terminal>,
    /// Frozen next-plan `<id>: no compilation database captured its C++ scope` gaps (printed in
    /// every plan; gate reasons only under `--require-full`).
    pub next_scope_gaps: Vec<String>,
    /// Frozen next-plan reasons for natively blocked C++ checks (human plan text only).
    next_blocked_reasons: BTreeMap<String, String>,
    /// Legacy `cpp.diagnostics` selected units in frozen order with their diagnostic targets
    /// and replay task (or the error that replaced the replay), by task.
    legacy_unit_notes: BTreeMap<String, Vec<legacy_syntax::UnitNote>>,
    /// Frozen capability version banners of the legacy lint replay compilers (by path).
    compiler_versions: BTreeMap<String, String>,
    /// Legacy lint tooling tasks whose clazy is the `clazy` compiler wrapper: the approved
    /// clang++ it runs (frozen `CLANGXX`).
    legacy_clazy_wrappers: BTreeMap<String, PathBuf>,
    /// Legacy lint `cpp.tidy` runs of clang-tidy and clazy over their selected units in frozen
    /// order, by task.
    legacy_tool_plans: BTreeMap<String, Vec<legacy_tooling::ToolPlan>>,
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
            let (selected, units) = context(project, component, environment);
            self.contexts.insert(component.id.clone(), selected);
            self.database_units.insert(component.id.clone(), units);
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

        // Frozen next planning: a component with no linked build and no discovered database
        // blocks its compilation-backed checks before anything runs.
        if !self.legacy_projection
            && matches!(check.id.as_str(), "cpp.compile" | "cpp.diagnostics")
            && context.database_path.is_none()
            && context.units.is_empty()
            && context.diagnostics.is_empty()
            && linked_build(project, component).is_none()
            && !self.compile_scope_is_empty(id)
        {
            return Some(blocked(format!(
                "no compilation database for the component's C++ scope \u{2014} no build unit is linked to {}; declare [builds.<id>] with a directory that will hold compile_commands.json and set the component's build, or generate the database out of band",
                component.id
            )));
        }
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
                // Frozen `run_cpp_lint`: more than 2048 selected sources stop before the exact
                // or fallback replay with one C++SyntaxBudgetError at the project root.
                if self.legacy_projection
                    && let Some(terminal) = fallback::legacy_source_budget(
                        component,
                        // Frozen `compilation_context_present` decides exact or heuristic.
                        if context.database_path.is_none()
                            && context.units.is_empty()
                            && context.diagnostics.is_empty()
                        {
                            "heuristic"
                        } else {
                            "exact"
                        },
                    )
                {
                    self.legacy_context_errors.insert(id.into(), terminal);
                    return Some(Work::Internal);
                }
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
                let selected = if self.legacy_projection {
                    // Frozen `load_compilation_context` orders units by (source, directory,
                    // configuration digest).
                    let mut units = self.legacy_lint_units(&component.id, &context);
                    units.sort_by_cached_key(|unit| {
                        (
                            unit.source.clone(),
                            unit.directory.clone(),
                            legacy_configuration_key(unit),
                        )
                    });
                    units
                } else {
                    vec![]
                };
                // Frozen `_run_exact`: error context diagnostics, no replayable production
                // unit or too many selected units stop before any replay.
                if self.legacy_projection
                    && (context.database_path.is_some()
                        || context
                            .diagnostics
                            .iter()
                            .any(|diagnostic| diagnostic.level == "error"))
                    && let Some(terminal) = legacy_syntax::terminal(&context, selected.len())
                {
                    self.legacy_context_errors.insert(id.into(), terminal);
                    return Some(Work::Internal);
                }
                if context.units.is_empty() {
                    return Some(blocked(
                        "no usable recorded compilation units; provide the declared build's compilation database",
                    ));
                }
                let mut notes = vec![];
                let units = if self.legacy_projection {
                    // Frozen per-unit order: the unit's diagnostics, then its replay unless
                    // one is an error or the replay command is rejected.
                    let mut replayed = vec![];
                    for unit in selected {
                        let source = unit.source.clone();
                        let (before, error) = legacy_syntax::unit_targets(&unit);
                        let replay = match error {
                            Some(error) => Err((None, error)),
                            None => match self.compiler(project, &unit, environment) {
                                Err(reason) => return Some(blocked(reason)),
                                Ok(tool) => match ici_cpp::replay_command(
                                    &project.root,
                                    &unit,
                                    &tool.path,
                                    ReplayPurpose::Diagnostics,
                                ) {
                                    Ok(_) => Ok(format!("{id}.unit{}", replayed.len())),
                                    Err(error) => {
                                        let (target, message) =
                                            legacy_syntax::replay_error(&unit, &error);
                                        Err((Some(target), message))
                                    }
                                },
                            },
                        };
                        if replay.is_ok() {
                            replayed.push(unit);
                        }
                        notes.push(legacy_syntax::UnitNote {
                            source,
                            before,
                            replay,
                        });
                    }
                    self.legacy_syntax_units
                        .insert(component.id.clone(), replayed.clone());
                    replayed
                } else {
                    context.units.clone()
                };
                if self.legacy_projection {
                    self.legacy_unit_notes.insert(id.into(), notes);
                }
                let mut providers = vec![];
                for (index, unit) in units.iter().enumerate() {
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
                    request.options.legacy = self.legacy_projection;
                    if self.legacy_projection {
                        // Frozen `compiler_diagnostic_command` requests GCC JSON diagnostics.
                        request
                            .options
                            .compiler_argv
                            .push("-fdiagnostics-format=json".into());
                    }
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
                    if self.legacy_projection {
                        let path = request.tool.path.to_string_lossy().into_owned();
                        self.compiler_versions.entry(path).or_insert_with(|| {
                            legacy_tooling::legacy_version_banner(
                                &request.tool.path,
                                &project.root,
                                environment,
                                "g++",
                            )
                            .unwrap_or_default()
                        });
                    }
                    let mut provider = match ici_providers::plan(&request) {
                        Ok(provider) => provider,
                        Err(error) => return Some(blocked(error.to_string())),
                    };
                    if self.legacy_projection {
                        legacy_syntax_limits(&mut provider.task);
                    }
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
        // A disabled unused-function scope next to it keeps the native linker binding.
        let linker_unavailable = self.legacy_projection
            && linker != "off"
            && mode != "off"
            && !legacy_root_cmake(&project.root, component);
        let linker_tasks = if linker != "off" && !linker_unavailable {
            self.bind_linker(project, component, id, environment)?
        } else {
            vec![]
        };
        if linker_unavailable {
            self.legacy_linker_unavailable
                .insert(id.into(), linker == "required");
        }
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
            if self.legacy_projection {
                // Frozen `compiler_diagnostic_command` requests GCC JSON diagnostics (ranges).
                provider.task.argv.push("-fdiagnostics-format=json".into());
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
        // Legacy: the internal lexical analysis runs and reports the required-boundary error.
        if !context.complete() {
            return (policy == "required" && !self.legacy_projection).then(|| {
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
            Err(error) => {
                return (policy == "required" && !self.legacy_projection)
                    .then(|| blocked(error.to_string()));
            }
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
        if context.units.is_empty()
            && self.legacy_projection
            && (self.legacy_standalone || context.database_path.is_none())
        {
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
                let found = ToolRequest {
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
                .ok();
                // Frozen `_ready_tool`: only a tool that passed its capability probe is
                // "resolvable"; a failed probe is "unavailable or incompletely probed".
                let banner = found.as_ref().and_then(|resolved| {
                    legacy_tooling::legacy_version_banner(
                        &resolved.path,
                        &project.root,
                        environment,
                        tool,
                    )
                });
                let resolvable = banner.is_some();
                let message = if self.legacy_standalone {
                    format!(
                        "{tool} requires an approved capability inventory and exact compilation context"
                    )
                } else if resolvable {
                    format!("{tool} requires an exact compilation database")
                } else {
                    format!("{tool} is unavailable or incompletely probed")
                };
                let record = legacy_tooling::missing_tool_evidence(
                    label,
                    &message,
                    &found
                        .as_ref()
                        .map(|tool| {
                            // Frozen: the found capability path when its probe failed, the
                            // resolved executable when only the database is missing.
                            if resolvable {
                                legacy_tooling::resolved_executable(&tool.path)
                            } else {
                                tool.path.to_string_lossy().into_owned()
                            }
                        })
                        .unwrap_or_default(),
                    banner.as_deref().unwrap_or_default(),
                );
                unavailable.push((label, message, mode(field) == "required", record));
            }
            self.preparation_failures.insert(
                id.into(),
                legacy_tooling_unavailable(id, &unavailable, config),
            );
            return Ok(Work::Internal);
        }
        if context.units.is_empty() && !self.legacy_projection {
            return Err("clang tooling requires usable recorded compilation units".into());
        }
        // Legacy: unresolvable clang tools are reported unavailable whatever the unit count.
        if context.units.len() > 2048 && !self.legacy_projection {
            return Err("clang tooling exceeds 2048 selected compilation units".into());
        }
        let mut selected = vec![];
        let mut missing = vec![];
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
            let resolved = request.resolve().map_err(|error| error.to_string());
            // Frozen capability probe (D-A): a resolvable tool whose `--version` probe fails in
            // the run environment is the frozen `<Tool>Unavailable`, not an execution error.
            // The probed banner is the frozen capability version of the tool's findings.
            let probe = |tool: ResolvedTool, versions: &mut BTreeMap<String, String>| {
                match legacy_tooling::legacy_version_banner(
                    &tool.path,
                    &project.root,
                    environment,
                    if kind == ProviderKind::Clazy {
                        "clazy"
                    } else {
                        "clang-tidy"
                    },
                ) {
                    Some(banner) => {
                        // Frozen `regular_executable` runs the resolved tool; its banner is
                        // found under both paths.
                        let resolved = legacy_tooling::resolved_executable(&tool.path);
                        versions.insert(resolved, banner.clone());
                        versions.insert(tool.path.to_string_lossy().into_owned(), banner);
                        Ok(tool)
                    }
                    None => Err("capability probe failed".to_owned()),
                }
            };
            let resolved = match resolved {
                Ok(tool) if self.legacy_projection => probe(tool, &mut self.compiler_versions),
                other => other,
            };
            // Frozen `ToolProbe("clazy", ("clazy-standalone", "clazy"))`: without a complete
            // clazy-standalone the `clazy` compiler wrapper is the next candidate, and it needs
            // an approved clang++ capability (frozen `_ready_tool`).
            let resolved = match resolved {
                Err(_) if self.legacy_projection && kind == ProviderKind::Clazy => {
                    let wrapper = ToolRequest {
                        id: "clazy".into(),
                        explicit: None,
                        ..request.clone()
                    }
                    .resolve()
                    .map_err(|error| error.to_string())
                    .and_then(|tool| probe(tool, &mut self.compiler_versions));
                    match wrapper {
                        Ok(tool) => {
                            let clangxx = ToolRequest {
                                id: "clang++".into(),
                                explicit: None,
                                ..request.clone()
                            }
                            .resolve()
                            .ok()
                            .filter(|clangxx| {
                                legacy_tooling::legacy_version_banner(
                                    &clangxx.path,
                                    &project.root,
                                    environment,
                                    "clang++",
                                )
                                .is_some()
                            });
                            match clangxx {
                                Some(clangxx) => {
                                    self.legacy_clazy_wrappers.insert(
                                        id.into(),
                                        PathBuf::from(legacy_tooling::resolved_executable(
                                            &clangxx.path,
                                        )),
                                    );
                                    Ok(tool)
                                }
                                None => {
                                    let message = "the clazy compiler wrapper requires an approved clang++ capability";
                                    let path = legacy_tooling::resolved_executable(&tool.path);
                                    let version = self
                                        .compiler_versions
                                        .get(&path)
                                        .cloned()
                                        .unwrap_or_default();
                                    missing.push((
                                        "ClazyUnavailable",
                                        message.into(),
                                        mode(field) == "required",
                                        legacy_tooling::missing_tool_evidence(
                                            "ClazyUnavailable",
                                            message,
                                            &path,
                                            &version,
                                        ),
                                    ));
                                    continue;
                                }
                            }
                        }
                        Err(error) => Err(error),
                    }
                }
                other => other,
            };
            match resolved {
                Ok(tool) => {
                    self.tools.insert(name.into(), tool.clone());
                    selected.push((kind, tool));
                }
                Err(_) if self.legacy_projection => {
                    let (tool, label) = if kind == ProviderKind::Clazy {
                        ("clazy", "ClazyUnavailable")
                    } else {
                        ("clang-tidy", "ClangTidyUnavailable")
                    };
                    let message = format!("{tool} is unavailable or incompletely probed");
                    // Frozen capability path: the found executable whose probe failed.
                    let path = request
                        .resolve()
                        .map(|tool| tool.path.to_string_lossy().into_owned())
                        .unwrap_or_default();
                    missing.push((
                        label,
                        message.clone(),
                        mode(field) == "required",
                        legacy_tooling::missing_tool_evidence(label, &message, &path, ""),
                    ));
                }
                Err(error) if mode(field) == "required" => return Err(error),
                Err(_) => {}
            }
        }
        if !selected.is_empty() && !missing.is_empty() {
            self.legacy_tooling_missing
                .insert(id.into(), missing.clone());
        }
        if selected.is_empty() && !missing.is_empty() {
            // Frozen LintEngine with an exact context and no resolvable clang tooling: the same
            // unavailable targets at "." as without a context.
            self.preparation_failures
                .insert(id.into(), legacy_tooling_unavailable(id, &missing, config));
            return Ok(Work::Internal);
        }
        if self.legacy_projection && !selected.is_empty() {
            return self.bind_legacy_tooling(
                project,
                component,
                context,
                id,
                environment,
                &selected,
            );
        }
        if context.units.len() > 2048 {
            return Err("clang tooling exceeds 2048 selected compilation units".into());
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
    /// Frozen legacy LintEngine clang-tidy / clazy runs (`run_clang_tidy` / `run_clazy`) over an
    /// exact context: per tool a project-level stop (context ingestion errors, the configured
    /// clang-tidy config, the selected-unit bound) or, per selected unit in frozen order, the
    /// error that replaces its run (unit context errors, a rejected replay, a discovered
    /// `.clang-tidy`) or its task with the frozen tooling argv (`tooling_arguments` plus the
    /// selected GCC's library projection).
    fn bind_legacy_tooling(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        context: &CompilationContext,
        id: &str,
        environment: &BTreeMap<String, String>,
        selected: &[(ProviderKind, ResolvedTool)],
    ) -> Result<Work, String> {
        use legacy_tooling::{ToolPlan, ToolUnit, UnitStep};
        let config = component.settings.pointer("/engines/lint");
        let root = project
            .root
            .canonicalize()
            .unwrap_or_else(|_| project.root.clone());
        let ingestion = context
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.level == "error");
        // Frozen `load_compilation_context` order: (source, directory, configuration digest).
        let mut units: Vec<(usize, CompilationUnit)> =
            context.units.iter().cloned().enumerate().collect();
        units.sort_by_cached_key(|(_, unit)| {
            (
                unit.source.clone(),
                unit.directory.clone(),
                legacy_configuration_key(unit),
            )
        });
        let wrapper = self.legacy_clazy_wrappers.get(id).cloned();
        let mut plans = vec![];
        let mut providers = vec![];
        let mut tasks = vec![];
        for (kind, tool) in selected {
            let clazy = *kind == ProviderKind::Clazy;
            let (name, prefix, suffix) = if clazy {
                ("clazy", "Clazy", "clazy")
            } else {
                ("clang-tidy", "ClangTidy", "tidy")
            };
            let explicit = if clazy {
                Ok(None)
            } else {
                legacy_tooling::legacy_explicit_config(
                    &root,
                    config
                        .and_then(|cfg| cfg.get("clang_tidy_config"))
                        .and_then(Value::as_str),
                )
            };
            let mut plan = ToolPlan {
                clazy,
                terminal: vec![],
                units: vec![],
            };
            let at_root = |name: String, message: String| vec![(".".to_owned(), name, message)];
            // Frozen `_normalized_sources` (before the tool is ready): the source-count bound.
            // (Unreadable production sources never reach a step: the run's input snapshot
            // refuses them first.)
            plan.terminal = if context.selected_sources.len() > legacy_syntax::MAX_SELECTED_UNITS {
                at_root(
                    format!("{prefix}ContextError"),
                    format!("{name} source count exceeds the bounded limit"),
                )
            } else if ingestion {
                at_root(
                    format!("{prefix}ContextError"),
                    format!("{name} did not run because compilation context ingestion has errors"),
                )
            } else if let Err(message) = &explicit {
                at_root("ClangTidyConfigError".into(), message.clone())
            } else if units.len() > legacy_syntax::MAX_SELECTED_UNITS {
                at_root(
                    format!("{prefix}ContextError"),
                    format!("{name} translation-unit count exceeds the bounded limit"),
                )
            } else {
                vec![]
            };
            let explicit = explicit.unwrap_or_default();
            if !plan.terminal.is_empty() {
                plans.push(plan);
                continue;
            }
            let checks = if clazy {
                let checks = string_list(config.and_then(|cfg| cfg.get("clazy_checks")));
                if checks.is_empty() {
                    config
                        .and_then(|cfg| cfg.get("clazy_profile"))
                        .and_then(Value::as_str)
                        .unwrap_or("level0")
                        .to_owned()
                } else {
                    checks.join(",")
                }
            } else {
                string_list(config.and_then(|cfg| cfg.get("clang_tidy_checks"))).join(",")
            };
            for (index, unit) in &units {
                let source = unit.source.clone();
                let error = |name: String, message: String| ToolUnit {
                    source: source.clone(),
                    step: UnitStep::Error { name, message },
                    task: None,
                };
                if legacy_syntax::unit_targets(unit).1.is_some() {
                    plan.units.push(error(
                        format!("{prefix}ContextError"),
                        format!("{name} skipped a compilation unit with context errors: {source}"),
                    ));
                    continue;
                }
                let compiler = self.compiler(project, unit, environment)?;
                let replay = match ici_cpp::replay_command(
                    &project.root,
                    unit,
                    &compiler.path,
                    ReplayPurpose::Diagnostics,
                ) {
                    Ok(replay) => replay,
                    Err(failure) => {
                        let (code, message) = legacy_syntax::frozen_replay(unit, &failure);
                        plan.units.push(error(
                            format!("{prefix}ReplayError"),
                            format!("{name} replay {code}: {source}: {message}"),
                        ));
                        continue;
                    }
                };
                let configuration = if clazy {
                    None
                } else if explicit.is_some() {
                    explicit.clone()
                } else {
                    match legacy_tooling::legacy_discovered_config(&root, &replay.source) {
                        Ok(found) => found,
                        Err(message) => {
                            plan.units
                                .push(error("ClangTidyConfigError".into(), message));
                            continue;
                        }
                    }
                };
                let task_id = format!("{id}.{suffix}{index}");
                let mut task = ici_cpp::clang_tidy_boundary_task(
                    &project.root,
                    unit,
                    &compiler.path,
                    &tool.path,
                    &ici_cpp::GccStdlibProjection::default(),
                )
                .map_err(|error| error.to_string())?;
                // Frozen `tooling_arguments`: the syntax replay without its program, analysis
                // suffix and source, with the warning policy already demoted.
                let source_path = replay.source.display().to_string();
                let mut arguments: Vec<String> = replay.argv[1..replay.argv.len() - 4]
                    .iter()
                    .filter(|argument| **argument != source_path)
                    .cloned()
                    .collect();
                arguments.push("-fdiagnostics-color=never".into());
                let mut searches =
                    ici_cpp::gcc_include_search_tasks(&project.root, unit, &compiler.path)
                        .map_err(|error| error.to_string())?;
                if !searches.is_empty() {
                    arguments.push("{observed-gcc-stdlib}".into());
                }
                // Frozen `regular_executable`: the approved tool runs by its resolved path.
                let mut argv = vec![legacy_tooling::resolved_executable(&tool.path)];
                if clazy && let Some(clangxx) = &wrapper {
                    // Frozen compiler-wrapper provider: clang++ runs the clazy plugin.
                    argv.extend(arguments);
                    argv.extend([
                        "-Wall".into(),
                        "-Wextra".into(),
                        "-fsyntax-only".into(),
                        source_path.clone(),
                    ]);
                    task.env_overlay
                        .insert("CLANGXX".into(), clangxx.to_string_lossy().into_owned());
                    task.env_overlay
                        .insert("CLAZY_CHECKS".into(), checks.clone());
                } else {
                    if clazy {
                        argv.extend([format!("--checks={checks}"), "--only-qt".into()]);
                    } else {
                        argv.push("--use-color=false".into());
                        match &configuration {
                            Some(path) => argv.push(format!("--config-file={}", path.display())),
                            // Never inherit a user- or machine-level configuration.
                            None => argv.push("--config={}".into()),
                        }
                        if !checks.is_empty() {
                            argv.push(format!("--checks={checks}"));
                        } else if configuration.is_none() {
                            argv.push(
                                "--checks=-*,bugprone-*,clang-analyzer-*,performance-*".into(),
                            );
                        }
                    }
                    argv.extend([source_path.clone(), "--".into()]);
                    argv.extend(arguments);
                }
                let mut request = ProviderRequest::new(
                    *kind,
                    tool.clone(),
                    &project.root,
                    &task.cwd,
                    vec![unit.source.clone()],
                    &task_id,
                );
                request.component_id = Some(component.id.clone());
                request.analysis_unit_ids = vec![unit_id(component, unit)];
                request.variant = Some(unit.variant.clone());
                request.options.database_dir = Some(project.root.to_string_lossy().into_owned());
                let mut provider =
                    ici_providers::plan(&request).map_err(|error| error.to_string())?;
                task.id.clone_from(&task_id);
                task.provider = kind.name().into();
                task.analysis_unit_ids = request.analysis_unit_ids;
                task.argv = argv;
                // Frozen `_OUTPUT_CHARS`.
                task.output_limit_bytes = Some(1_000_000);
                if let Some(path) = &configuration {
                    if let Ok(relative) = path.strip_prefix(&root) {
                        self.compiler_configurations
                            .entry(id.into())
                            .or_default()
                            .insert(relative.to_string_lossy().into_owned(), 1024 * 1024);
                    }
                    task.input_refs.push(path.to_string_lossy().into_owned());
                }
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
                provider.task = task;
                for (search_index, search) in searches.iter_mut().enumerate() {
                    search.id = format!("{task_id}.search{search_index}");
                    search.analysis_unit_ids = vec![unit_id(component, unit)];
                }
                if !searches.is_empty() {
                    tasks.extend(searches.clone());
                    self.tooling_searches.insert(task_id.clone(), searches);
                }
                tasks.push(provider.task.clone());
                providers.push(provider);
                plan.units.push(ToolUnit {
                    source,
                    task: Some(task_id.clone()),
                    step: UnitStep::Run(task_id),
                });
            }
            plans.push(plan);
        }
        if providers.is_empty() {
            // Nothing runs: the frozen targets are decided now.
            let mut output = AnalysisOutput::new(id, "clang-tooling");
            output.observation.state = TaskState::Succeeded;
            legacy_tooling::legacy_tooling_projection(
                &mut output,
                vec![],
                self.legacy_tooling_missing
                    .get(id)
                    .map(Vec::as_slice)
                    .unwrap_or_default(),
                &context.uncovered_sources,
                &plans,
                &BTreeMap::new(),
                &legacy_tooling::ToolingFacts {
                    probes: &BTreeMap::new(),
                    config,
                    wrapper: wrapper.is_some(),
                },
            );
            self.preparation_failures.insert(id.into(), output);
            return Ok(Work::Internal);
        }
        self.diagnostics.insert(id.into(), providers);
        self.legacy_tool_plans.insert(id.into(), plans);
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
/// A clang-tidy config that could inject compiler arguments (`ExtraArgs`, `ExtraArgsBefore`) or
/// pull in a parent config (`InheritParentConfig`). LLVM's YAML parser decodes escapes only in
/// double-quoted scalars, so `"\x45xtraArgs"` is the key `ExtraArgs` although its text is not.
/// Without a double quote or without a backslash the raw text is the decoded text (plain and
/// single-quoted folding never joins characters), so a file holding both is refused outright
/// instead of tracking quotes, as are NUL bytes (UTF-16/32). With those two rules the raw text
/// is the decoded text, so the literal keys keep frozen's case-insensitive ASCII word-bounded
/// match (`\b(?:ExtraArgs(?:Before)?|InheritParentConfig)\b`): an in-word mention such as
/// `notExtraArgs` names a different key and is accepted. Frozen matched the raw text only; the
/// escape and NUL refusals are an intended security correction.
pub(crate) fn clang_tidy_config_is_unsafe(bytes: &[u8]) -> bool {
    let lowered = bytes.to_ascii_lowercase();
    let word = |byte: u8| byte.is_ascii_alphanumeric() || byte == b'_';
    let mentions = |needle: &[u8]| {
        lowered
            .windows(needle.len())
            .enumerate()
            .any(|(start, window)| {
                let end = start + needle.len();
                window == needle
                    && (start == 0 || !word(lowered[start - 1]))
                    && lowered.get(end).is_none_or(|byte| !word(*byte))
            })
    };
    bytes.contains(&0)
        || mentions(b"extraargs")
        || mentions(b"extraargsbefore")
        || mentions(b"inheritparentconfig")
        || (bytes.contains(&b'"') && bytes.contains(&b'\\'))
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
        if clang_tidy_config_is_unsafe(text.as_bytes()) {
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
/// The component's compilation context and every unit its database recorded.
fn context(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
) -> (CompilationContext, Vec<CompilationUnit>) {
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
                return (
                    CompilationContext {
                        diagnostics: vec![ici_cpp::ContextDiagnostic::error(
                            "invalid-database-setting",
                            error.to_string(),
                        )],
                        ..Default::default()
                    },
                    vec![],
                );
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
    let units = context.units.clone();
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
    (context, units)
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

/// Frozen `core/cmake._capture_artifact_manifest` for one legacy CMake build session: the
/// linked outputs of its shadow tree (`_linked_artifact_paths`: regular non-symlink files with
/// an executable magic or `!<arch>` archives, objects/coverage notes excluded, in `Path` order),
/// each with the frozen kind, digest, size, mode, `cmake.build` producer, `<variant>:shadow:<path>`
/// id, derived target and the report-safe build command. The run identity (commit and digests)
/// is attached by the legacy report projection. `None` when the walk cannot be completed.
pub fn legacy_cmake_artifact_manifest(
    root: &Path,
    shadow: &Path,
    variant: &str,
    command: &[String],
    producer: &str,
) -> Option<Value> {
    const MAGICS: [&[u8]; 6] = [
        b"\x7fELF",
        b"MZ",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    ];
    let root_text = root.to_string_lossy().trim_end_matches('/').to_owned();
    let command: Vec<String> = command
        .iter()
        .enumerate()
        .map(|(index, argument)| {
            let argument = if index == 0 {
                Path::new(argument)
                    .file_name()
                    .map_or(argument.clone(), |name| name.to_string_lossy().into_owned())
            } else {
                argument.clone()
            };
            argument
                .replace(&format!("{root_text}/"), "")
                .replace(&root_text, ".")
        })
        .collect();
    let mut pending = vec![shadow.to_path_buf()];
    let mut files = vec![];
    let mut visited = 0usize;
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory).ok()? {
            let entry = entry.ok()?;
            visited += 1;
            if visited > 200_000 {
                return None;
            }
            let kind = entry.file_type().ok()?;
            if kind.is_dir() {
                pending.push(entry.path());
            } else if kind.is_file() {
                files.push(entry.path());
            }
        }
    }
    // Frozen `sorted(paths)`: `Path` order compares component by component.
    files.sort_by(|left, right| left.components().cmp(right.components()));
    let mut artifacts = vec![];
    for path in files {
        let extension = path.extension().and_then(|value| value.to_str());
        if matches!(extension, Some("o" | "obj" | "gcno" | "gcda")) {
            continue;
        }
        let mut prefix = [0u8; 8];
        let read = std::fs::File::open(&path)
            .and_then(|mut file| std::io::Read::read(&mut file, &mut prefix))
            .unwrap_or(0);
        let prefix = &prefix[..read];
        let archive = matches!(extension, Some("a" | "lib")) && prefix.starts_with(b"!<arch>\n");
        if !archive && !MAGICS.iter().any(|magic| prefix.starts_with(magic)) {
            continue;
        }
        let Ok(relative) = path.strip_prefix(shadow) else {
            continue;
        };
        let relative = relative.to_string_lossy().replace('\\', "/");
        let name = path.file_name()?.to_string_lossy().into_owned();
        let kind = if matches!(extension, Some("a" | "lib")) {
            "static-library"
        } else if matches!(extension, Some("so" | "dylib" | "dll")) || name.contains(".so.") {
            "shared-library"
        } else {
            "executable"
        };
        // Frozen `_artifact_target`.
        let mut target = match kind {
            "static-library" => name
                .strip_suffix(".a")
                .or_else(|| name.strip_suffix(".lib"))
                .unwrap_or(&name)
                .to_owned(),
            "shared-library" => match name.split_once(".so.") {
                Some((stem, _)) => stem.to_owned(),
                None => [".so", ".dylib", ".dll"]
                    .iter()
                    .find_map(|suffix| name.strip_suffix(suffix))
                    .unwrap_or(&name)
                    .to_owned(),
            },
            _ => Path::new(&name)
                .file_stem()
                .map_or(name.clone(), |stem| stem.to_string_lossy().into_owned()),
        };
        if target.starts_with("lib") && target.len() > 3 {
            target = target[3..].to_owned();
        }
        if target.is_empty() {
            target.clone_from(&relative);
        }
        let bytes = std::fs::read(&path).ok()?;
        let metadata = std::fs::metadata(&path).ok()?;
        let id = format!("{variant}:shadow:{relative}");
        let id = if id.len() <= 512 {
            id
        } else {
            format!(
                "{variant}:shadow:path-{}",
                ici_domain::sha256(&id).trim_start_matches("sha256:")
            )
        };
        artifacts.push(json!({
            "path": relative, "scope": "shadow", "kind": kind,
            "sha256": ici_domain::sha256(&bytes), "size": bytes.len(),
            "mode": std::os::unix::fs::PermissionsExt::mode(&metadata.permissions()) & 0o7777,
            "producer": producer, "id": id, "target": target, "command": command,
        }));
    }
    let shadow_root = shadow
        .strip_prefix(root)
        .ok()
        .map(|path| path.to_string_lossy().into_owned());
    Some(json!({"variant": variant, "shadow_root": shadow_root, "artifacts": artifacts}))
}

/// Frozen `compile_db` configuration identity of one recorded unit (`canonical_digest` of the
/// recorded directory, argv and output; first 12 hex digits).
/// Internal target metric carrying a replayed unit's frozen configuration digest to the lint
/// projection; removed before the target is reported.
const LEGACY_CONFIGURATION: &str = "ici.legacy_configuration";

fn legacy_configuration_key(unit: &CompilationUnit) -> String {
    ici_domain::canonical_json(
        &json!({"directory":unit.directory,"argv":unit.argv,"output":unit.output}),
    )
    .map(ici_domain::sha256)
    .unwrap_or_default()
}

fn legacy_configuration_digest(unit: &CompilationUnit) -> Option<String> {
    let key = legacy_configuration_key(unit);
    (!key.is_empty()).then(|| key.trim_start_matches("sha256:")[..12].to_string())
}

/// Frozen `gcc_standard_library_projection` probes of one tool unit as `ToolEvidence`: the C++
/// search, then (when the C++ search completed) the C search.
fn legacy_probe_evidence(
    searches: &[TaskSpec],
    outcomes: &BTreeMap<String, ici_execution::TaskOutcome>,
    version: &str,
) -> Vec<Value> {
    let mut records = vec![];
    for search in searches {
        let Some(outcome) = outcomes.get(&search.id) else {
            break;
        };
        records.push(legacy_tooling::process_evidence(
            "g++ stdlib include search",
            version,
            &search.argv,
            outcome,
            "",
        ));
        if outcome.timed_out || outcome.truncated || outcome.exit_code != Some(0) {
            break;
        }
    }
    records
}

/// Frozen `_unavailable` targets of clang-tidy and Clazy in a legacy lint run.
fn legacy_tooling_unavailable(
    id: &str,
    unavailable: &[legacy_tooling::MissingTool],
    config: Option<&Value>,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici.cpp.tooling");
    let errors: Vec<_> = unavailable
        .iter()
        .filter(|(_, _, required, _)| *required)
        .map(|(_, message, ..)| message.clone())
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
            .map(|(_, message, ..)| message.as_str())
            .collect::<Vec<_>>()
            .join("; ")
    } else {
        errors.join("; ")
    };
    output.extra["legacy_tool_warnings"] = json!(
        unavailable
            .iter()
            .filter(|(_, _, required, _)| !*required)
            .map(|(_, message, ..)| message)
            .collect::<Vec<_>>()
    );
    output.extra["legacy_lint_counters"] = legacy_tooling::unplanned_counters(unavailable, config);
    if errors.is_empty() && !unavailable.is_empty() {
        // Frozen LintEngine: an unavailable optional tool leaves the lint verdict estimated.
        let mut available = Measurement::measured("clang_tooling_available", 0.0);
        available.evidence = ici_domain::EvidenceLevel::Estimated;
        output.observation.measurements.push(available);
    }
    // Frozen `_ready_tool` records the unavailable tool as evidence without a command, and
    // `_validated_context` without an analysis context records the missing context.
    output.extra["tool_evidence"] = json!(
        unavailable
            .iter()
            .map(|(.., record)| record.clone())
            .collect::<Vec<_>>()
    );
    for (label, message, required, _) in unavailable {
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
/// Returns whether the output was projected (complete PASS/FAIL syntax evidence).
pub fn legacy_fallback_lint(
    output: &mut AnalysisOutput,
    tooling: Option<&AnalysisOutput>,
    standalone: bool,
    configurations: Option<&BTreeMap<String, String>>,
    qt: Option<&ici_cpp::QtCodegenOutcome>,
) -> bool {
    // A recorded subset (frozen ContextMissing WARN targets) is still projected; the verdict is
    // the frozen tool ERROR.
    let partial = output.extra.get("legacy_context_missing").is_some()
        && output.targets.iter().all(|target| {
            target.status != "ERROR" && output.observation.state != TaskState::Cancelled
        });
    // A clean heuristic fallback is WARN (`run_fallback` marks its estimated PASS); the frozen
    // verdict below is recomputed from the projected targets either way.
    // Frozen syntax execution errors are projected targets with an ERROR verdict.
    let executed = output.extra.get("legacy_syntax_errors").is_some();
    if !partial
        && (!(matches!(output.status.as_str(), "PASS" | "FAIL" | "WARN") || executed)
            || !output.observation.evidence_is_complete())
    {
        return false;
    }
    let details = output
        .extra
        .get("finding_details")
        .cloned()
        .unwrap_or_default();
    let findings = output.observation.findings.clone();
    let mut targets = vec![];
    let (mut primaries, mut notes) = (vec![], vec![]);
    for target in &output.targets {
        if target.status == "PASS" && target.target_name.is_empty() {
            let mut clean = target.clone();
            // Frozen `_evaluate_process`: the clean unit is named by its configuration digest,
            // `fallback` when no compilation database recorded one.
            let own = clean.metrics.remove(LEGACY_CONFIGURATION);
            clean.target_name = format!(
                "C++Syntax:{}",
                own.as_ref()
                    .and_then(Value::as_str)
                    .unwrap_or_else(|| configurations
                        .and_then(|items| items.get(&target.file_path))
                        .map_or("fallback", String::as_str))
            );
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
            return false;
        };
        let kind = legacy.split(": ").next().unwrap_or_default();
        let mut primary = target.clone();
        // Frozen standalone `ici lint` parses g++ text diagnostics (a start line and column
        // only); under `ici verify` its JSON diagnostics end on their own line.
        if standalone {
            primary.end_line = None;
            primary.end_column = None;
        } else {
            primary.end_line = primary.end_line.or(Some(primary.start_line));
        }
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
        primaries.push((targets.len() - 1, finding.fingerprint.clone()));
        for note in &finding.related_locations {
            if !note.label.starts_with("note: ") {
                continue;
            }
            let mut related = InspectionTarget::at(&note.path, note.start_line);
            if !standalone {
                related.end_line = note.end_line.or(Some(note.start_line));
                related.end_column = note.end_column;
            }
            related.start_column = note.start_column;
            related.target_name = "C++Syntax".into();
            related.status = "WARN".into();
            related.message = note.label.clone();
            targets.push(related);
            notes.push((targets.len() - 1, finding.fingerprint.clone()));
        }
    }
    output.targets = targets;
    // Frozen `_lint_cpp`: Qt generated-code targets follow the syntax targets.
    if let Some(qt) = qt {
        output.targets.extend(qt.targets.iter().cloned());
    }
    legacy_fallback_findings(output, &primaries, &notes, configurations.is_some());
    if let Some(qt) = qt {
        legacy_qt_projection(output, qt);
    }
    legacy_lint_verdict(output, tooling, configurations.is_none());
    true
}

/// Frozen `_syntax_process_error` / `_syntax_output_error` for one legacy C++ syntax replay:
/// a timed-out, truncated, unparseable or exit-inconsistent run becomes one
/// `C++SyntaxExecutionError` target for its source and a LintEngine tool error. Returns the
/// frozen error message after replacing the unit output; a cancelled run is left untouched.
/// Frozen `_cpp_lint` per-unit process limits: `_TIMEOUT_SECONDS` (120 s) and
/// `_OUTPUT_CHARS` (1,000,000 bytes captured per stream, `core/runner._BoundedCapture`).
/// Frozen `_cpp_lint` syntax replay bounds and `core/cpp_replay.replay_environment`: the
/// compiler runs under the C locale with `TERM=dumb`, so its diagnostics keep ASCII quotes
/// whatever the caller's locale.
pub(crate) fn legacy_syntax_limits(task: &mut TaskSpec) {
    task.timeout_seconds = Some(120.0);
    task.output_limit_bytes = Some(1_000_000);
    task.env_overlay.extend(ici_cpp::replay_environment());
}

pub(crate) fn legacy_syntax_execution_error(
    output: &mut AnalysisOutput,
    outcome: &ici_execution::TaskOutcome,
    source: &str,
    compiler: &str,
) -> Option<String> {
    if outcome.cancelled {
        return None;
    }
    let name = source.rsplit('/').next().unwrap_or(source);
    let compiler = compiler.rsplit('/').next().unwrap_or(compiler);
    let details = output.extra.get("finding_details");
    let has_error = output.observation.findings.iter().any(|finding| {
        let kind = details
            .and_then(|details| details.get(&finding.fingerprint))
            .and_then(|detail| detail.get("legacy_message"))
            .and_then(Value::as_str)
            .and_then(|message| message.split(": ").next())
            .unwrap_or_default();
        kind.contains("error") || kind == "sorry, unimplemented"
    });
    let parse_error = output.status == "ERROR"
        && !output
            .summary
            .contains("nonzero exit without attributable diagnostics")
        && !output.summary.contains("exited with unsupported code");
    let message = match outcome.exit_code {
        _ if outcome.timed_out => format!("C++ syntax check timed out: {name}"),
        _ if outcome.truncated => format!("C++ syntax output was truncated: {name}"),
        None => format!("C++ syntax check terminated unexpectedly: {name}"),
        Some(code) if code < 0 => format!("C++ syntax check terminated unexpectedly: {name}"),
        _ if parse_error => format!("C++ syntax output was not parseable: {name}"),
        Some(code) if code >= 2 => format!("{compiler} failed with exit code {code}: {name}"),
        Some(code) if code != 0 && output.observation.findings.is_empty() => {
            format!("C++ syntax output had no diagnostics: {name}")
        }
        Some(0) if has_error => {
            format!("{compiler} reported an error with a successful exit: {name}")
        }
        Some(1) if !has_error => {
            format!("{compiler} failed without an error diagnostic: {name}")
        }
        _ => return None,
    };
    let mut target = InspectionTarget::at(source, 1);
    target.target_name = "C++SyntaxExecutionError".into();
    target.status = "ERROR".into();
    target.message = message.clone();
    output.targets = vec![target];
    output.observation.findings.clear();
    if let Some(extra) = output.extra.as_object_mut() {
        extra.remove("finding_details");
    }
    // The frozen run is recorded and evaluated (engine ERROR, NOT_RUN evidence): the projected
    // target carries the failure instead of an incomplete native observation.
    output.observation.state = TaskState::Succeeded;
    output.observation.timed_out = false;
    output.observation.truncated = false;
    output.status = "ERROR".into();
    output.summary = message.clone();
    Some(message)
}

/// Frozen `compile_db._RowError` messages for the row-shape codes; other codes keep the
/// native message.
pub(crate) fn legacy_context_message(code: &str, message: &str) -> String {
    format!(
        "{code}: {}",
        ici_cpp::legacy_diagnostic_message(code, message)
    )
}

/// Appends frozen syntax execution errors after the context errors already recorded.
pub(crate) fn legacy_record_syntax_errors(output: &mut AnalysisOutput, errors: Vec<String>) {
    if errors.is_empty() {
        return;
    }
    let mut all: Vec<Value> = output
        .extra
        .get("legacy_tool_errors")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    all.extend(errors.iter().map(|error| json!(error)));
    output.extra["legacy_tool_errors"] = json!(all);
    output.extra["legacy_syntax_errors"] = json!(errors);
    output.status = "ERROR".into();
    output.summary = errors.join("; ");
}

/// Frozen `_evaluate_process`: a run that parsed any diagnostic (in any file) records no
/// clean `C++Syntax:<configuration>` target for its source.
pub(crate) fn legacy_clean_only_without_diagnostics(output: &mut AnalysisOutput) {
    if !output.observation.findings.is_empty() {
        output
            .targets
            .retain(|target| !(target.status == "PASS" && target.target_name.is_empty()));
    }
}

/// Frozen LintEngine use of `verify_qt_codegen`: its findings (`ici.qt.*`, after the C++
/// findings), its errors after the syntax tool errors and its warnings after the syntax
/// warnings; a Qt warning without an error makes the lint evidence estimated.
fn legacy_qt_projection(output: &mut AnalysisOutput, qt: &ici_cpp::QtCodegenOutcome) {
    let findings = output.extra["legacy_findings"]
        .as_array_mut()
        .expect("legacy findings list");
    findings.extend(qt.findings.iter().map(ici_cpp::QtFinding::legacy_value));
    if !qt.errors.is_empty() {
        let mut errors: Vec<Value> = output
            .extra
            .get("legacy_tool_errors")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        errors.extend(qt.errors.iter().map(|error| json!(error)));
        output.extra["legacy_tool_errors"] = json!(errors);
    }
    output.extra["legacy_qt_warnings"] = json!(qt.warnings);
    if qt.errors.is_empty() && !qt.warnings.is_empty() {
        let mut estimated = Measurement::measured("qt_codegen_verified", 0.0);
        estimated.evidence = ici_domain::EvidenceLevel::Estimated;
        output.observation.measurements.push(estimated);
    }
}

/// Frozen `LintEngine` status and summary over the compiler syntax targets, the clang tooling
/// targets and the tool warnings (engines/lint.py, mode pass_warn_fail): its own tool errors
/// first (the report joins the clang tooling errors after them), `0 Violations Found` when nothing is reported, the first two tool warnings when there
/// is no failure, else the error/warning counts.
pub fn legacy_lint_verdict(
    output: &mut AnalysisOutput,
    tooling: Option<&AnalysisOutput>,
    fallback: bool,
) {
    const FALLBACK: &str = "No compilation database is available; C++ syntax analysis used the c++17 heuristic fallback";
    let tool_list = |key: &str| -> Vec<String> {
        tooling
            .and_then(|tooling| tooling.extra.get(key))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect()
    };
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
    let mut warnings = if fallback {
        vec![FALLBACK.to_string()]
    } else {
        vec![]
    };
    // Frozen order: syntax warnings, Qt generated-code warnings, then clang tool warnings.
    warnings.extend(
        output
            .extra
            .get("legacy_qt_warnings")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned),
    );
    warnings.extend(tool_list("legacy_tool_warnings"));
    // Only this output's own tool errors: the report joins every ERROR check's summary into
    // the frozen `"; ".join(errors[:3])`.
    let errors: Vec<String> = output
        .extra
        .get("legacy_tool_errors")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
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
    output.status = if !errors.is_empty() {
        "ERROR"
    } else if fail > 0 {
        "FAIL"
    } else if warn > 0 {
        "WARN"
    } else {
        "PASS"
    }
    .into();
    output.summary = if !errors.is_empty() {
        errors
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ")
    } else if output.status == "PASS" {
        "0 Violations Found".into()
    } else if fail == 0 && !warnings.is_empty() {
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

/// Frozen `DeadCodeEngine` over `run_cpp_unused_functions` with a selected database. Exact
/// evidence is atomic: the first configuration that fails (in database order) yields one
/// `C++UnusedFunctionError` and every source observed before it a discarded-observation target;
/// otherwise each source gets `C++UnusedFunctions` or one `Compiler:-Wunused-function` per
/// agreed diagnostic. Only a context-complete native run is projected.
fn legacy_unused_projection(
    output: &mut AnalysisOutput,
    observations: &[ici_cpp::CompilerConfigurationObservation],
    runs: &[(usize, Option<i32>, bool)],
    compiler: &str,
) {
    if output.targets.iter().any(|target| {
        !matches!(
            target.target_name.as_str(),
            "UnusedInternalFunctions" | "UnusedInternalFunction" | "incomplete-unused-probe"
        )
    }) {
        return;
    }
    let tool = Path::new(compiler)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| compiler.to_string());
    let mut order: Vec<_> = observations.iter().zip(runs).collect();
    order.sort_by_key(|(_, (index, _, _))| *index);
    let scope_tool = format!("{tool} unused-function");
    let scope = |target: &InspectionTarget, severity: &str, confidence: &str, remediation: &str| {
        json!({"rule_id":"ici.legacy.dead.target","category":"maintainability","severity":severity,
            "confidence":confidence,"primary_location":{"path":target.file_path,"start_line":target.start_line,
            "end_line":target.end_line,"start_column":target.start_column,"end_column":target.end_column,
            "label":target.target_name},"related_locations":[],"message":target.message,
            "explanation":"This target records the bounded TU-local compiler unused-function scope.",
            "remediation":remediation,"fixes":[],"tool_rule_id":"","tool_name":scope_tool,"tool_version":"",
            "metrics":target.metrics.iter().map(|(key, value)| (key.clone(), json!({"value":value,"unit":""}))).collect::<serde_json::Map<_, _>>(),
            "snippet":"","suppression":{"kind":"none","reason":"","suppressed":false}})
    };
    let tool_evidence: Vec<_> = order
        .iter()
        .map(|(_, (_, code, _))| json!({"name":scope_tool,"path":compiler,"returncode":code}))
        .collect();
    if let Some(position) = order
        .iter()
        .position(|(observation, _)| !observation.complete)
    {
        let (observation, (_, code, timed_out)) = order[position];
        let failure = match code {
            _ if *timed_out => "compiler timed out".to_string(),
            Some(code) if *code > 0 => format!("compiler exited with code {code}"),
            _ => "compiler terminated unexpectedly".to_string(),
        };
        let message = format!("C++ unused-function {failure}: {}", observation.source);
        let mut error = InspectionTarget::at(&observation.source, 1);
        error.target_name = "C++UnusedFunctionError".into();
        error.status = "ERROR".into();
        error.message = message.clone();
        let mut targets = vec![error];
        let observed: BTreeSet<_> = order[..position]
            .iter()
            .map(|(observation, _)| observation.source.clone())
            .collect();
        for source in observed {
            let mut target = InspectionTarget::at(&source, 1);
            target.target_name = "C++UnusedFunctionsInvalidated".into();
            target.status = "SKIP".into();
            target.message = "Compiler observations were discarded because the exact C++ unused-function scope did not complete atomically".into();
            targets.push(target);
        }
        let remediation =
            "Restore a complete, safe compilation context when exact analysis did not run.";
        output.extra["legacy_findings"] = json!(
            targets
                .iter()
                .map(|target| scope(
                    target,
                    if target.status == "ERROR" {
                        "critical"
                    } else {
                        "info"
                    },
                    "low",
                    remediation
                ))
                .collect::<Vec<_>>()
        );
        output.extra["tool_evidence"] = json!(tool_evidence[..=position]);
        // Frozen exact evidence is atomic: the agreed findings of other sources are discarded.
        output.observation.findings.clear();
        output.targets = targets;
        output.status = "ERROR".into();
        output.summary = message;
        return;
    }
    let mut findings = vec![];
    let mut details = serde_json::Map::new();
    let mut issues = 0;
    let native = output.observation.findings.clone();
    for target in &mut output.targets {
        let configurations = target
            .metrics
            .get("configurations")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        let count = if configurations == 0 {
            observations
                .iter()
                .filter(|item| item.source == target.file_path)
                .count()
        } else {
            configurations
        };
        target.metrics.clear();
        target
            .metrics
            .insert("configurations_checked".into(), json!(count));
        if target.target_name == "UnusedInternalFunctions" {
            target.target_name = "C++UnusedFunctions".into();
            target.status = "PASS".into();
            target.message = format!(
                "Compiler found no unused internal-linkage function in {count} analyzed configuration(s)"
            );
            findings.push(scope(target, "info", "exact", ""));
        } else {
            issues += 1;
            target.target_name = "Compiler:-Wunused-function".into();
            target.status = "WARN".into();
            target.message = format!(
                "Compiler reports {} in all {count} analyzed configuration(s)",
                target.message.trim_start_matches("warning: ")
            );
            // The native consensus finding becomes the frozen exact `_cpp_findings` entry.
            for finding in native.iter().filter(|finding| {
                finding.primary_location.path == target.file_path
                    && finding.primary_location.start_line == target.start_line
                    && finding.primary_location.start_column == target.start_column
            }) {
                details.insert(finding.fingerprint.clone(), json!({
                    "legacy_rule_id":"ici.legacy.dead.target","legacy_message":target.message,
                    "legacy_label":"Compiler:-Wunused-function","legacy_category":"maintainability",
                    "legacy_confidence":"exact","legacy_severity":"medium","tool_name":tool,
                    "tool_rule_id":"-Wunused-function","tool_version":"",
                    "explanation":"The selected compiler reported -Wunused-function for this internal-linkage definition in every exact configuration of its production translation unit. This is not whole-program or linker reachability evidence.",
                    "remediation":"Remove the function or add a real reference in every affected configuration; retain it explicitly with [[maybe_unused]] only when the inactive use is intentional.",
                    "metrics":{"configurations_checked":{"value":count,"unit":"configurations"}},
                    "related_locations":[]}));
            }
        }
    }
    output.extra["legacy_findings"] = json!(findings);
    output.extra["finding_details"] = Value::Object(details);
    output.extra["tool_evidence"] = json!(tool_evidence);
    output.summary = if issues == 0 {
        "No dead-code findings detected in the analyzed scopes".into()
    } else {
        format!(
            "{issues} dead-code finding(s): 0 heuristic Python, {issues} compiler-local C/C++, 0 target-local GNU ELF"
        )
    };
}
/// Frozen `LintEngine._cpp_findings` in the heuristic fallback: every text diagnostic, each note
/// included, is its own compiler finding with medium confidence (only an exact replay is exact),
/// no related locations, and the target name as its label only when that name is unique in its
/// file. Notes have no native finding of their own, so their frozen findings are supplied whole
/// (`legacy_findings`). The adapted targets name the first compiler invocation as their tool.
fn legacy_fallback_findings(
    output: &mut AnalysisOutput,
    primaries: &[(usize, String)],
    notes: &[(usize, String)],
    exact: bool,
) {
    let confidence = if exact { "exact" } else { "medium" };
    let mut counts = BTreeMap::<(&str, &str), usize>::new();
    for target in &output.targets {
        *counts
            .entry((&target.file_path, target.target_name.trim()))
            .or_default() += 1;
    }
    let label = |target: &InspectionTarget| {
        if counts[&(target.file_path.as_str(), target.target_name.trim())] == 1 {
            target.target_name.clone()
        } else {
            String::new()
        }
    };
    let details = &output.extra["finding_details"];
    let tool = |fingerprint: &str, key: &str| {
        details[fingerprint][key]
            .as_str()
            .unwrap_or_default()
            .to_owned()
    };
    // Frozen adapted targets name the first compiler run (its probed banner) as their tool.
    let first_version = output.extra["concrete_tasks"]
        .pointer("/0/version")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let tool = |fingerprint: &str, key: &str| {
        let value = tool(fingerprint, key);
        if key == "tool_version" && value.is_empty() {
            first_version.clone()
        } else {
            value
        }
    };
    let mut explicit = vec![];
    let mut patches = vec![];
    for (index, fingerprint) in primaries {
        patches.push((fingerprint.clone(), label(&output.targets[*index])));
    }
    for (index, parent) in notes {
        let target = &output.targets[*index];
        explicit.push(json!({"rule_id":"ici.legacy.lint.target","category":"correctness","severity":"medium",
            "confidence":confidence,"primary_location":{"path":target.file_path,"start_line":target.start_line,
            "end_line":target.end_line,"start_column":target.start_column,"end_column":target.end_column,"label":label(target)},
            "related_locations":[],"message":target.message,
            "explanation":"Compiler diagnostic from a sanitized translation-unit replay.","remediation":"",
            "fixes":[],"tool_rule_id":"","tool_name":tool(parent,"tool_name"),"tool_version":tool(parent,"tool_version"),
            "metrics":{},"snippet":"","suppression":{"kind":"none","reason":"","suppressed":false}}));
    }
    let tool_name = primaries
        .first()
        .map(|(_, fingerprint)| tool(fingerprint, "tool_name"))
        .filter(|name| !name.is_empty());
    for (fingerprint, label) in patches {
        let detail = &mut output.extra["finding_details"][&fingerprint];
        if detail["tool_version"]
            .as_str()
            .unwrap_or_default()
            .is_empty()
        {
            detail["tool_version"] = json!(first_version);
        }
        detail["legacy_confidence"] = json!(confidence);
        detail["legacy_label"] = json!(label);
        detail["related_locations"] = json!([]);
    }
    output.extra["legacy_findings"] = json!(explicit);
    legacy_syntax_tool_evidence(&mut output.extra, tool_name);
}

/// Frozen `_cpp_lint` tool evidence: one compiler run per replayed unit (`concrete_tasks`), named
/// by the compiler (frozen `replay.compiler`), with its process facts and execution error.
pub(crate) fn legacy_syntax_tool_evidence(extra: &mut Value, tool_name: Option<String>) {
    let runs: Vec<Value> = extra["concrete_tasks"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|task| Some((task, task["argv"].as_array()?)))
        .filter_map(|(task, argv)| {
            let path = argv.first()?.as_str()?;
            let name = tool_name
                .clone()
                .unwrap_or_else(|| path.rsplit('/').next().unwrap_or(path).to_owned());
            Some(json!({"name":name,"path":path,"argv":argv,
                "version":task["version"].as_str().unwrap_or_default(),
                "returncode":task.get("returncode").cloned().unwrap_or(Value::Null),
                "error":task["error"].as_str().unwrap_or_default(),
                "timed_out":task["timed_out"].as_bool().unwrap_or(false),
                "truncated":task["truncated"].as_bool().unwrap_or(false)}))
        })
        .collect();
    extra["tool_evidence"] = json!(runs);
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
    // Frozen `parse_readelf` errors (analysis/_elf.py) for a readelf that ran but returned
    // incomplete evidence: one AnalysisUnavailable ERROR at "." and the "incomplete" summary.
    let frozen_error = output
        .summary
        .split_once(": ")
        .and_then(|(code, _)| match code {
            "readelf-header-missing" => Some("readelf output is missing ELF header facts"),
            "readelf-bounds" => Some("readelf output exceeds the bounded text contract"),
            _ => None,
        });
    if output.observation.state != TaskState::Succeeded
        && let Some(message) = frozen_error
    {
        let mut target = InspectionTarget::at(".", 1);
        target.target_name = "BinaryCompatibility:AnalysisUnavailable".into();
        target.status = "ERROR".into();
        target.message = message.into();
        output.targets = vec![target];
        output.summary = format!("Binary compatibility analysis incomplete: {message}");
        return;
    }
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
    // Frozen `binary_finding(rule, record.path, ...)` locates each ABI finding at the
    // manifest record path, relative to the shadow root (its fingerprint follows).
    for finding in &mut output.observation.findings {
        if let Some(relative) = finding.primary_location.path.strip_prefix(&prefix) {
            finding.primary_location.path = relative.to_owned();
        }
    }
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

/// Frozen SanitizeEngine/ThreadSanitizeEngine direct g++ run: each C++ test is compiled with
/// every production source, so a production source that does not compile is one
/// `SanitizerCompile` ERROR per test with `_tool_failure_message` (the whitespace-collapsed
/// compiler stderr, first 300 characters); the summary joins the first three tool errors.
pub fn legacy_sanitizer_compile_failures(
    id: &str,
    tests: &[String],
    stderr: &str,
) -> AnalysisOutput {
    let snippet: String = stderr
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(300)
        .collect();
    let message = format!("Sanitizer compilation failed with exit code 1: {snippet}");
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "ERROR".into();
    output.summary = vec![message.as_str(); tests.len().min(3)].join("; ");
    output.observation.limitations.push(message.clone());
    for test in tests {
        let mut target = InspectionTarget::at(test, 1);
        target.target_name = "SanitizerCompile".into();
        target.status = "ERROR".into();
        target.message = message.clone();
        output.targets.push(target);
    }
    output
}

/// Frozen `run_cpp_linker_dead_symbols` needs the root CMake backend (`select_backend` picks
/// CMake for a root CMakeLists.txt); any other legacy project reports the linker scope as
/// unavailable instead of preparing a link-reachability build.
fn legacy_root_cmake(root: &Path, component: &Component) -> bool {
    crate::files::under(root, &component.root)
        .join("CMakeLists.txt")
        .is_file()
}

/// Frozen TestEngine coverage-adapter configure refusal (`build.make.coverage_build_argv` missing,
/// an unsupported backend contract): the message is the engine's tool error, nothing is located.
pub fn legacy_test_configuration_error(id: &str, message: &str) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "ERROR".into();
    output.summary = message.into();
    // No test ran, so the frozen TEM over zero tests is 0.00 / 5.0.
    output.score = Some(0.0);
    output.max_score = Some(5.0);
    output.extra["legacy_test_configuration_error"] = json!(true);
    output.observation.limitations.push(message.into());
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
    /// Frozen `_cpp_lint._selected_units`: the selected units plus the database's Qt generated
    /// `moc_*` / `qrc_*` (`.cpp`/`.cc`/`.cxx`) and `mocs_compilation.cpp` units, in database
    /// order. Unity builds already select every recorded C/C++ unit.
    fn legacy_lint_units(
        &self,
        component: &str,
        context: &CompilationContext,
    ) -> Vec<CompilationUnit> {
        let Some(all) = self.database_units.get(component) else {
            return context.units.clone();
        };
        let generated = |source: &str| {
            let name = source.rsplit('/').next().unwrap_or(source);
            (name == "mocs_compilation.cpp" || name.starts_with("moc_") || name.starts_with("qrc_"))
                && [".cpp", ".cc", ".cxx"]
                    .iter()
                    .any(|suffix| name.ends_with(suffix))
        };
        if context.unity_build {
            return context.units.clone();
        }
        let mut units: Vec<CompilationUnit> = all
            .iter()
            .filter(|unit| context.units.contains(unit) || generated(&unit.source))
            .cloned()
            .collect();
        // Units the coverage confirmation added (never in the raw database) keep their place.
        for unit in &context.units {
            if !units.contains(unit) {
                units.push(unit.clone());
            }
        }
        units
    }
    /// Frozen `verify_qt_codegen` for a legacy lint `cpp.diagnostics` output: Qt generated-code
    /// linkage and Qt-major evidence over the source directories, the analysis context (none
    /// under standalone `ici lint`) and the syntax replays that succeeded.
    pub fn legacy_qt_codegen(
        &self,
        project: &ResolvedProject,
        component: &Component,
        output: &AnalysisOutput,
    ) -> ici_cpp::QtCodegenOutcome {
        let configured = component
            .settings
            .pointer("/legacy/project/source_dirs")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect::<Vec<_>>()
            });
        let names = configured.unwrap_or_else(|| {
            ["src", "lib", "app", "packages", "python"]
                .map(str::to_owned)
                .to_vec()
        });
        let source_dirs: Vec<PathBuf> = names
            .iter()
            .map(|name| project.root.join(name))
            .filter(|path| path.is_dir())
            .collect();
        let (cpp_files, cpp_headers) = ici_cpp::legacy_cpp_inputs(&project.root, &source_dirs);
        let compiled: BTreeSet<String> = output
            .extra
            .get("legacy_replayed_sources")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        let empty = vec![];
        let context = self.contexts.get(&component.id);
        let units = self.database_units.get(&component.id).unwrap_or(&empty);
        ici_cpp::verify_qt_codegen(&ici_cpp::QtCodegenRequest {
            root: &project.root,
            source_dirs: &source_dirs,
            cpp_files: &cpp_files,
            cpp_headers: &cpp_headers,
            context: (!self.legacy_standalone).then(|| ici_cpp::QtContext {
                database: context.is_some_and(|context| context.database_path.is_some()),
                has_errors: context.is_some_and(|context| {
                    context
                        .diagnostics
                        .iter()
                        .any(|diagnostic| diagnostic.level == "error")
                }),
                units,
            }),
            compiled_sources: &compiled,
        })
    }
    /// Frozen `compile_db` configuration identities (`canonical_digest` of the recorded
    /// directory, argv and output; first 12 hex digits) per source of a component's context.
    pub fn legacy_lint_configurations(&self, component: &str) -> Option<BTreeMap<String, String>> {
        let context = self.contexts.get(component)?;
        context.database_path.as_ref()?;
        let mut digests = BTreeMap::new();
        for unit in self
            .legacy_syntax_units
            .get(component)
            .unwrap_or(&context.units)
        {
            let digest = legacy_configuration_digest(unit)?;
            digests.entry(unit.source.clone()).or_insert(digest);
        }
        Some(digests)
    }
    /// The frozen targets of one legacy `cpp.tidy` run (`<task>.tidy<n>` / `<task>.clazy<n>`
    /// on recorded unit n).
    fn legacy_tool_run(
        &self,
        component: &str,
        provider: &ProviderPlan,
        outcome: &ici_execution::TaskOutcome,
        output: &AnalysisOutput,
    ) -> Option<legacy_tooling::LegacyToolRun> {
        let clazy = provider.kind == ProviderKind::Clazy;
        let suffix = provider.task.id.rsplit('.').next()?;
        let index: usize = suffix
            .strip_prefix(if clazy { "clazy" } else { "tidy" })?
            .parse()
            .ok()?;
        let unit = self.contexts.get(component)?.units.get(index)?;
        let version = provider
            .task
            .argv
            .first()
            .and_then(|path| self.compiler_versions.get(path))
            .map_or("", String::as_str);
        Some(legacy_tooling::legacy_tool_run(
            clazy,
            &unit.source,
            &legacy_configuration_digest(unit)?,
            (&provider.task.argv, version),
            outcome,
            output,
        ))
    }
    /// A frozen legacy output decided while planning, if any.
    pub fn planned(&self, id: &str) -> Option<&AnalysisOutput> {
        self.preparation_failures.get(id)
    }
    /// Whether a frozen legacy output was already decided for this task while planning.
    pub fn planned_output(&self, id: &str) -> bool {
        self.preparation_failures.contains_key(id)
    }
    pub fn is_legacy_context_error(&self, id: &str) -> bool {
        self.legacy_context_errors.contains_key(id)
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
            || self.legacy_context_errors.contains_key(id)
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
            && !self.legacy_context_errors.contains_key(id)
    }
    pub fn finalize_plan(&self, plan: &mut ici_application::plan::Plan) {
        for planned in &mut plan.checks {
            // Frozen `_run_exact` stops (context errors, no replayable unit, unit budget) are
            // lint results of their own; they never wait for the compile_db evidence.
            if self.legacy_context_errors.contains_key(&planned.task_id)
                || (self.legacy_projection
                    && planned.check.id == "cpp.tidy"
                    && self.preparation_failures.contains_key(&planned.task_id))
            {
                planned.check.needs.clear();
            }
            if self.fallbacks.contains_key(&planned.task_id) {
                planned.check.needs.clear();
                plan.limitations.push(format!("{}: legacy c++17 heuristic fallback; ordered pkg-config probes resolve only the declared package flags at execution; no recorded compilation or reusable header closure is claimed",planned.task_id));
            }
            if let Some(required) = self.legacy_unavailable.get(&planned.task_id) {
                planned.check.needs.clear();
                let linker_required = self
                    .legacy_linker_unavailable
                    .get(&planned.task_id)
                    .copied()
                    .unwrap_or(false);
                // Frozen `DeadCodeEngine._effective_required`: unavailable automatic C++ and
                // linker policies do not make the engine required (a Python dead check keeps it
                // required).
                if !*required && !linker_required {
                    planned.check.required = false;
                }
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
        if let Some(terminal) = self.legacy_context_errors.get(&request.task_id) {
            return legacy_syntax::terminal_output(request, terminal);
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
            let linker = self
                .legacy_linker_unavailable
                .get(&request.task_id)
                .copied();
            let required = *required;
            let mut output = AnalysisOutput::new(&request.task_id, "ici.cpp");
            // Frozen `DeadCodeEngine._result_state`: required policies report their analysis
            // errors (first three, "; "-joined), automatic ones the not-run SKIP summary.
            let errors: Vec<&str> = [
                required.then_some(LEGACY_UNUSED_UNAVAILABLE),
                (linker == Some(true)).then_some(LEGACY_LINKER_UNAVAILABLE),
            ]
            .into_iter()
            .flatten()
            .collect();
            let status = if errors.is_empty() { "SKIP" } else { "ERROR" };
            output.status = status.into();
            output.summary = if errors.is_empty() {
                "C++ dead-code analysis not run: exact native context unavailable".into()
            } else {
                errors.join("; ")
            };
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
            // Frozen `DeadCodeEngine._scope_finding` for each not-run scope target.
            let scope = |name: &str,
                         required: bool,
                         message: &str,
                         explanation: &str,
                         remediation: &str| {
                let mut target = InspectionTarget::at(&path, 1);
                target.target_name = name.into();
                target.status = if required { "ERROR" } else { "SKIP" }.into();
                target.message = message.into();
                let finding = json!({"rule_id":"ici.legacy.dead.target",
                    "category":"maintainability","severity":if required {"critical"} else {"info"},
                    "confidence":"low","primary_location":{"path":path,"start_line":1,"end_line":null,
                    "start_column":null,"end_column":null,"label":name},"related_locations":[],
                    "message":message,"explanation":explanation,"remediation":remediation,
                    "fixes":[],"tool_rule_id":"","tool_name":"","tool_version":"","metrics":{},"snippet":"",
                    "suppression":{"kind":"none","reason":"","suppressed":false}});
                (target, finding)
            };
            let mut scopes = vec![scope(
                "C++UnusedFunctionsUnavailable",
                required,
                LEGACY_UNUSED_UNAVAILABLE,
                "This target records the bounded TU-local compiler unused-function scope.",
                "Restore a complete, safe compilation context when exact analysis did not run.",
            )];
            if let Some(linker_required) = linker {
                output
                    .observation
                    .limitations
                    .push(LEGACY_LINKER_UNAVAILABLE.into());
                scopes.push(scope(
                    "C++LinkerReachabilityUnavailable",
                    linker_required,
                    LEGACY_LINKER_UNAVAILABLE,
                    "This target records the bounded target-local GNU ELF section-GC scope.",
                    "Restore the supported GNU ELF CMake Release context when exact target-local analysis did not run.",
                ));
            }
            output.extra["legacy_findings"] = json!(
                scopes
                    .iter()
                    .map(|(_, finding)| finding.clone())
                    .collect::<Vec<_>>()
            );
            output
                .targets
                .extend(scopes.into_iter().map(|(target, _)| target));
            return output;
        }
        if let Some(reason) = self.inactive.get(&request.task_id) {
            let mut output = AnalysisOutput::new(&request.task_id, "ici.cpp");
            output.status = "SKIP".into();
            output.summary = reason.clone();
            if self.legacy_projection
                && project.schema_family == ici_config::SchemaFamily::Legacy
                && let Some(frozen) = legacy_not_applicable(&request.check_id, &request.inputs)
            {
                output.summary = frozen.summary.into();
                output.observation.measurements.push(Measurement {
                    name: "applicability".into(),
                    value: 0.0,
                    evidence: ici_domain::EvidenceLevel::NotApplicable,
                    unit: "checks".into(),
                    numerator: None,
                    denominator: None,
                });
                for (path, message) in frozen.targets {
                    let mut target = InspectionTarget::at(&path, 1);
                    target.target_name = frozen.target_name.into();
                    target.status = "SKIP".into();
                    target.message = message.into();
                    output.targets.push(target);
                }
                return output;
            }
            output.observation.measurements.push(Measurement {
                name: "applicability".into(),
                value: 0.0,
                evidence: ici_domain::EvidenceLevel::NotApplicable,
                unit: "checks".into(),
                numerator: None,
                denominator: None,
            });
            if self.legacy_projection && request.check_id == "cpp.tidy" {
                // Frozen `run_clang_tidy` / `run_clazy` with both modes off add no lint target.
                return output;
            }
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
                            "compiler boundary probe {} did not complete ({}, timed_out={}, truncated={}): {}",
                            outcome.task_id,
                            outcome.ending(),
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
                let mut unresolved = vec![];
                let legacy_cycle = request.check_id == "cpp.cycle"
                    && request.options["legacy_projection"].as_bool() == Some(true);
                for outcome in schedule.outcomes {
                    events.emit(
                        "task.completed",
                        Some(&outcome.task_id),
                        Some(&component.id),
                        &outcome.state_label(),
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
                            format!("include probe ended with {}", outcome.ending())
                        }))
                    };
                    match trace {
                        Ok(trace) => observations.push(ici_cpp::CompilerIncludeObservation {
                            source: probe.unit.source.clone(),
                            configuration: probe.unit.configuration.clone(),
                            complete: trace.complete(),
                            trace,
                        }),
                        Err(_)
                            if legacy_cycle
                                && outcome.completed()
                                && !outcome.timed_out
                                && !outcome.truncated
                                && outcome.exit_code.is_some_and(|code| code != 0)
                                && let Ok(stderr) = std::str::from_utf8(&outcome.stderr)
                                && let Ok(targets) = ici_cpp::parse_missing_include_targets(
                                    &project.root,
                                    Path::new(&probe.task.cwd),
                                    &project.root.join(&probe.unit.source),
                                    stderr,
                                )
                                && !targets.is_empty() =>
                        {
                            // Frozen `_evaluate_trace`: a trace that stops at located missing
                            // headers records no edge and its unresolved includes; the graph
                            // of the other units is still analysed.
                            unresolved.push(ici_cpp::UnresolvedIncludeTrace {
                                source: probe.unit.source.clone(),
                                configuration: probe.unit.configuration.clone(),
                                targets,
                            });
                        }
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
                    return ici_cpp::analyze_compiler_include_cycles_with_unresolved(
                        request,
                        &context,
                        &observations,
                        &unresolved,
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
            let mut legacy_tool_plans = self
                .legacy_tool_plans
                .get(&request.task_id)
                .filter(|_| self.legacy_projection && request.check_id == "cpp.tidy")
                .cloned();
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
            // Frozen `g++ stdlib include search` evidence of each legacy tool task.
            let mut probe_evidence: BTreeMap<String, Vec<Value>> = BTreeMap::new();
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
                        &outcome.state_label(),
                    );
                    outcomes.insert(outcome.task_id.clone(), outcome);
                }
                for provider in &mut providers {
                    let Some(searches) = self.tooling_searches.get(&provider.task.id) else {
                        continue;
                    };
                    if legacy_tool_plans.is_some() {
                        probe_evidence.insert(
                            provider.task.id.clone(),
                            legacy_probe_evidence(
                                searches,
                                &outcomes,
                                self.compiler_versions
                                    .get(&searches[0].argv[0])
                                    .map_or("", String::as_str),
                            ),
                        );
                    }
                    let projection = match ici_cpp::gcc_standard_library_projection(
                        &outcomes[&searches[0].id],
                        &outcomes[&searches[1].id],
                        Path::new(&provider.task.cwd),
                    ) {
                        Ok(projection) => projection,
                        Err(error) if legacy_tool_plans.is_some() => {
                            // Frozen `ClangTidyToolchainContextError` / `Clazy...`: that unit
                            // does not run; the other units and tools still do.
                            let (code, message) = legacy_tooling::legacy_gcc_projection_error(
                                &outcomes[&searches[0].id],
                                &outcomes[&searches[1].id],
                                &error,
                            );
                            // Frozen `_record_gcc_projection_evidence`: the projection error on
                            // the last probe it ran.
                            if let Some(last) = probe_evidence
                                .get_mut(&provider.task.id)
                                .and_then(|records| records.last_mut())
                            {
                                last["error"] = json!(message);
                            }
                            for plan in legacy_tool_plans.iter_mut().flatten() {
                                for unit in &mut plan.units {
                                    if matches!(&unit.step, legacy_tooling::UnitStep::Run(task) if *task == provider.task.id)
                                    {
                                        let (name, prefix) = if plan.clazy {
                                            ("clazy", "Clazy")
                                        } else {
                                            ("clang-tidy", "ClangTidy")
                                        };
                                        unit.step = legacy_tooling::UnitStep::Error {
                                            name: format!("{prefix}ToolchainContextError"),
                                            message: format!(
                                                "{name} GCC stdlib replay {code}: {}: {message}",
                                                unit.source
                                            ),
                                        };
                                    }
                                }
                            }
                            continue;
                        }
                        Err(error) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "clang-tooling"),
                                &error.to_string(),
                            );
                        }
                    };
                    // The observed projection takes the placeholder's place (frozen appends it
                    // to the tooling arguments, before the compiler wrapper's analysis suffix).
                    let position = provider
                        .task
                        .argv
                        .iter()
                        .position(|argument| argument == "{observed-gcc-stdlib}")
                        .unwrap_or(provider.task.argv.len());
                    provider.task.argv.splice(
                        position..(position + 1).min(provider.task.argv.len()),
                        projection.arguments.clone(),
                    );
                    projections.insert(provider.task.id.clone(),json!({"stdlib":projection,"argv":provider.task.argv,"probe_tasks":searches}));
                }
            }
            let tasks: Vec<_> = providers
                .iter()
                .map(|provider| provider.task.clone())
                .collect();
            let start = |task: &TaskSpec| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    Some(&component.id),
                    "recorded compiler diagnostics",
                )
            };
            let legacy_notes = self
                .legacy_unit_notes
                .get(&request.task_id)
                .filter(|_| self.legacy_projection && request.check_id == "cpp.diagnostics");
            let mut tool_stops = BTreeMap::new();
            let scheduled = if let Some(plans) = &legacy_tool_plans {
                // Frozen `_execute_selected_units`: per tool, one unit at a time under its
                // global budget.
                let by_id: BTreeMap<String, TaskSpec> = tasks
                    .iter()
                    .map(|task| (task.id.clone(), task.clone()))
                    .collect();
                legacy_tooling::run_budgeted(plans, &by_id, |task| {
                    scheduler
                        .run_with_start(&[task], cancellation, start)
                        .map_err(|error| error.to_string())
                        .map(|schedule| schedule.outcomes.into_iter().next().expect("one outcome"))
                })
                .map(|(outcomes, stops)| {
                    tool_stops = stops;
                    (outcomes, None)
                })
            } else if let Some(notes) = legacy_notes {
                // Frozen `_run_exact`: one unit at a time under the global time budget.
                let units: Vec<_> = notes
                    .iter()
                    .map(|note| {
                        let task = note
                            .replay
                            .as_ref()
                            .ok()
                            .and_then(|id| tasks.iter().find(|task| &task.id == id).cloned());
                        (note.source.clone(), task)
                    })
                    .collect();
                legacy_syntax::run_budgeted(&units, |task| {
                    scheduler
                        .run_with_start(&[task], cancellation, start)
                        .map_err(|error| error.to_string())
                        .map(|schedule| schedule.outcomes.into_iter().next().expect("one outcome"))
                })
            } else {
                scheduler
                    .run_with_start(&tasks, cancellation, start)
                    .map(|schedule| (schedule.outcomes, None))
                    .map_err(|error| error.to_string())
            };
            let (scheduled, budget_stop) = match scheduled {
                Ok(scheduled) => scheduled,
                Err(error) => {
                    return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error);
                }
            };
            let executed: BTreeSet<String> = scheduled
                .iter()
                .map(|outcome| outcome.task_id.clone())
                .collect();
            // Frozen `ProcessResult` facts of each executed legacy syntax replay.
            let run_facts: BTreeMap<String, Value> = scheduled
                .iter()
                .map(|outcome| {
                    (
                        outcome.task_id.clone(),
                        json!({"returncode":legacy_tooling::frozen_returncode(outcome),
                            "timed_out":outcome.timed_out,"truncated":outcome.truncated}),
                    )
                })
                .collect();
            let mut outputs = vec![];
            let mut unused_observations = vec![];
            let mut unused_runs = vec![];
            let mut legacy_tool_runs = vec![];
            let mut replayed = BTreeSet::new();
            let mut syntax_errors = vec![];
            let mut syntax_messages = BTreeMap::new();
            for outcome in scheduled {
                events.emit(
                    "task.completed",
                    Some(&outcome.task_id),
                    Some(&component.id),
                    &outcome.state_label(),
                );
                let provider = providers
                    .iter()
                    .find(|provider| provider.task.id == outcome.task_id)
                    .expect("outcome from declared compiler");
                let mut output = ici_providers::observe_full(provider, &outcome);
                if self.legacy_projection && request.check_id == "cpp.diagnostics" {
                    let unit = providers
                        .iter()
                        .position(|item| item.task.id == outcome.task_id)
                        .and_then(|index| {
                            self.legacy_syntax_units
                                .get(&component.id)
                                .and_then(|units| units.get(index))
                        })
                        .cloned();
                    if let Some(unit) = &unit
                        && let Some(error) = legacy_syntax_execution_error(
                            &mut output,
                            &outcome,
                            &unit.source,
                            provider.task.argv.first().map_or("", String::as_str),
                        )
                    {
                        syntax_errors.push((outcome.task_id.clone(), error));
                    }
                    legacy_clean_only_without_diagnostics(&mut output);
                    // Frozen `_evaluate_process` names a clean unit by its own configuration,
                    // also when one source has several.
                    if let Some(digest) = unit.as_ref().and_then(legacy_configuration_digest) {
                        for target in &mut output.targets {
                            if target.status == "PASS" && target.target_name.is_empty() {
                                target
                                    .metrics
                                    .insert(LEGACY_CONFIGURATION.into(), json!(digest));
                            }
                        }
                    }
                }
                // Frozen `_successful_cpp_replays`: an exit-0 syntax replay with parseable output.
                if self.legacy_projection
                    && request.check_id == "cpp.diagnostics"
                    && outcome.exit_code == Some(0)
                    && output.observation.evidence_is_complete()
                    && output.status != "ERROR"
                    && let Some(unit) = providers
                        .iter()
                        .position(|item| item.task.id == outcome.task_id)
                        .and_then(|index| {
                            self.legacy_syntax_units
                                .get(&component.id)
                                .and_then(|units| units.get(index))
                        })
                {
                    replayed.insert(unit.source.clone());
                }
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
                    unused_runs.push((index, outcome.exit_code, outcome.timed_out));
                }
                if self.legacy_projection && request.check_id == "cpp.tidy" {
                    // Frozen clang tool findings name the tool (`clang-tidy` / `clazy`) and its
                    // probed capability version.
                    let name = if provider.kind == ProviderKind::Clazy {
                        "clazy"
                    } else {
                        "clang-tidy"
                    };
                    let version = provider
                        .task
                        .argv
                        .first()
                        .and_then(|path| self.compiler_versions.get(path))
                        .cloned()
                        .unwrap_or_default();
                    if let Some(details) = output
                        .extra
                        .get_mut("finding_details")
                        .and_then(Value::as_object_mut)
                    {
                        for detail in details.values_mut() {
                            detail["tool_name"] = json!(name);
                            detail["tool_version"] = json!(version);
                        }
                    }
                }
                if self.legacy_projection
                    && request.check_id == "cpp.tidy"
                    && let Some(run) =
                        self.legacy_tool_run(&component.id, provider, &outcome, &output)
                {
                    legacy_tool_runs.push(run);
                    if provider.kind == ProviderKind::Clazy {
                        legacy_tooling::legacy_clazy_note_findings(&mut output);
                    }
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
                if self.legacy_projection {
                    legacy_unused_projection(
                        &mut output,
                        &unused_observations,
                        &unused_runs,
                        &providers[0].task.argv[0],
                    );
                }
                return output;
            }
            if self.legacy_projection
                && request.check_id == "cpp.diagnostics"
                && let Some(notes) = self.legacy_unit_notes.get(&request.task_id)
            {
                // Frozen unit order: each unit's diagnostic targets precede its replay targets;
                // a unit that is not replayed keeps its place with its error target, and the
                // tool errors follow the same order.
                let mut by_task: BTreeMap<String, AnalysisOutput> = outputs
                    .into_iter()
                    .map(|output| (output.observation.task_id.clone(), output))
                    .collect();
                let mut errors: BTreeMap<String, String> = syntax_errors.drain(..).collect();
                syntax_messages.clone_from(&errors);
                outputs = vec![];
                for (index, note) in notes.iter().enumerate() {
                    if let Some(stop) = budget_stop.as_ref().filter(|stop| stop.index == index) {
                        outputs.push(legacy_syntax::budget_output(&request.task_id, stop));
                        syntax_errors.push((String::new(), stop.error.clone()));
                        break;
                    }
                    match &note.replay {
                        Ok(task) => {
                            if let Some(mut output) = by_task.remove(task) {
                                output.targets.splice(0..0, note.before.iter().cloned());
                                outputs.push(output);
                            }
                            syntax_errors
                                .extend(errors.remove(task).map(|error| (task.clone(), error)));
                        }
                        Err((_, error)) => {
                            outputs.extend(legacy_syntax::skipped_output(&request.task_id, note));
                            syntax_errors.push((String::new(), error.clone()));
                        }
                    }
                }
                outputs.extend(by_task.into_values());
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
            if self.legacy_projection && request.check_id == "cpp.diagnostics" {
                output.extra["legacy_replayed_sources"] = json!(replayed);
            }
            if self.legacy_projection && request.check_id == "cpp.tidy" {
                let uncovered = self
                    .contexts
                    .get(&component.id)
                    .map(|context| context.uncovered_sources.clone())
                    .unwrap_or_default();
                legacy_tooling::legacy_tooling_projection(
                    &mut output,
                    legacy_tool_runs,
                    self.legacy_tooling_missing
                        .get(&request.task_id)
                        .map(Vec::as_slice)
                        .unwrap_or_default(),
                    &uncovered,
                    legacy_tool_plans.as_deref().unwrap_or_default(),
                    &tool_stops,
                    &legacy_tooling::ToolingFacts {
                        probes: &probe_evidence,
                        config: component.settings.pointer("/engines/lint"),
                        wrapper: self.legacy_clazy_wrappers.contains_key(&request.task_id),
                    },
                );
            }
            if request.check_id == "cpp.diagnostics" {
                self.preserve_diagnostic_scope(&component.id, &mut output);
                if self.legacy_projection {
                    if let Some(context) = self.contexts.get(&component.id) {
                        output
                            .targets
                            .splice(0..0, legacy_syntax::context_targets(context));
                    }
                    legacy_record_syntax_errors(
                        &mut output,
                        syntax_errors.into_iter().map(|(_, error)| error).collect(),
                    );
                    // Frozen lint tool evidence: one compiler run per replayed unit, in unit
                    // order (`_record_process`; a syntax execution error is its `error`).
                    let notes = self.legacy_unit_notes.get(&request.task_id);
                    let order: Vec<(&str, &TaskSpec)> = match notes {
                        Some(notes) => notes
                            .iter()
                            .filter_map(|note| {
                                let id = note.replay.as_ref().ok()?;
                                Some((
                                    note.source.as_str(),
                                    tasks.iter().find(|task| &task.id == id)?,
                                ))
                            })
                            .collect(),
                        None => tasks.iter().map(|task| ("", task)).collect(),
                    };
                    let order: Vec<_> = order
                        .into_iter()
                        .filter(|(_, task)| executed.contains(&task.id))
                        .collect();
                    output.extra["concrete_tasks"] = json!(
                        order
                            .iter()
                            .map(|(_, task)| {
                                let version = task
                                    .argv
                                    .first()
                                    .and_then(|path| self.compiler_versions.get(path))
                                    .cloned()
                                    .unwrap_or_default();
                                let mut record = json!({"id":task.id,"argv":task.argv,"version":version,
                                    "error":syntax_messages.get(&task.id).cloned().unwrap_or_default()});
                                if let Some(facts) = run_facts.get(&task.id).and_then(Value::as_object) {
                                    for (key, value) in facts {
                                        record[key] = value.clone();
                                    }
                                }
                                record
                            })
                            .collect::<Vec<_>>()
                    );
                    legacy_syntax_tool_evidence(&mut output.extra, None);
                    // Frozen `_run_exact` counters: every replayed configuration, its distinct
                    // sources and the production sources without a recorded command.
                    let sources: BTreeSet<&str> = order.iter().map(|(source, _)| *source).collect();
                    output.extra["legacy_lint_counters"] = json!({
                        "cpp_analysis_mode": "exact",
                        "cpp_configurations_checked": order.len(),
                        "cpp_sources_checked": sources.len(),
                        "cpp_context_missing": self
                            .contexts
                            .get(&component.id)
                            .map_or(0, |context| context.uncovered_sources.len()),
                    });
                }
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
            &outcome.state_label(),
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

struct LegacyNotApplicable {
    summary: &'static str,
    target_name: &'static str,
    targets: Vec<(String, &'static str)>,
}

/// Frozen applicability results of the Python-only legacy engines on C/C++ sources:
/// `TypeCheckEngine._mark_cpp_type_check_skipped` (one target per C/C++ translation unit) and
/// the single project-root target of resource/security/python_compat `_not_applicable`.
fn legacy_not_applicable(
    check_id: &str,
    inputs: &[ici_domain::SourceInput],
) -> Option<LegacyNotApplicable> {
    const NO_PYTHON: &str = "No applicable Python source files were selected";
    let root = |summary, target_name| LegacyNotApplicable {
        summary,
        target_name,
        targets: vec![(".".into(), NO_PYTHON)],
    };
    Some(match check_id {
        "cpp.type" => {
            let mut targets: Vec<(String, &'static str)> = inputs
                .iter()
                .filter(|source| {
                    [".cpp", ".cc", ".cxx", ".c"]
                        .iter()
                        .any(|suffix| source.path.ends_with(suffix))
                })
                .map(|source| {
                    (
                        source.path.clone(),
                        "C++ type checking is not implemented; source was not type-checked",
                    )
                })
                .collect();
            targets.sort();
            if targets.is_empty() {
                targets.push((
                    ".".into(),
                    "No applicable C++ source files were selected; type checking was not run",
                ));
            }
            LegacyNotApplicable {
                summary: "Type checking not applicable: no Python sources; C++ is unsupported",
                target_name: "C++TypeCheck",
                targets,
            }
        }
        "cpp.resource" => root(
            "Resource analysis skipped: no Python source files",
            "Resource:NotApplicable",
        ),
        "cpp.security" => root(
            "Security analysis skipped: no Python source files",
            "Security:NotApplicable",
        ),
        "cpp.python-compat" => root(
            "Python compatibility analysis skipped: no Python source files",
            "PythonRuntime:NotApplicable",
        ),
        _ => return None,
    })
}

/// These legacy descriptors inspect Python scope and still owe C++ callers an applicability result.
/// Frozen `ici verify` always lists its C/C++ compilation-database and ThreadSanitizer engines.
/// A legacy suite without any C/C++ component has no `cpp.compile`/`cpp.tsan` to carry them, so
/// these Python-scoped stand-ins report the frozen not-applicable results.
pub fn legacy_cpp_absent_checks(project: &ResolvedProject) -> Vec<CheckDefinition> {
    if project
        .components
        .iter()
        .any(|component| component.languages.iter().any(|language| language == "cpp"))
    {
        return vec![];
    }
    [
        (
            "python.compile-db",
            "C/C++ compilation database applicability",
        ),
        ("python.tsan", "C/C++ ThreadSanitizer applicability"),
    ]
    .into_iter()
    .map(|(id, title)| CheckDefinition {
        id: id.into(),
        title: title.into(),
        language: "python".into(),
        // Frozen CompileDbEngine and ThreadSanitizeEngine default `required` to true.
        required: true,
        ..type_not_applicable_check()
    })
    .collect()
}
/// The frozen engine results for [`legacy_cpp_absent_checks`]: `CompileDbEngine` with no
/// production translation unit and `ThreadSanitizeEngine` with no C/C++ source.
pub fn legacy_cpp_absent_output(check_id: &str, task_id: &str) -> Option<AnalysisOutput> {
    let (summary, target) = match check_id {
        "python.compile-db" => ("No production C/C++ translation units are in scope.", None),
        "python.tsan" => (
            "ThreadSanitizer skipped: no applicable C++ checks were executed",
            Some((
                "ThreadSanitizer",
                "No applicable C++ sources were selected; ThreadSanitizer was not run",
            )),
        ),
        _ => return None,
    };
    let mut output = AnalysisOutput::new(task_id, "ici.cpp");
    output.status = "SKIP".into();
    output.summary = summary.into();
    output.observation.measurements.push(Measurement {
        name: "cpp_sources".into(),
        value: 0.0,
        evidence: ici_domain::EvidenceLevel::NotApplicable,
        unit: "files".into(),
        numerator: None,
        denominator: None,
    });
    if let Some((name, message)) = target {
        let mut item = InspectionTarget::at(".", 1);
        item.target_name = name.into();
        item.status = "SKIP".into();
        item.message = message.into();
        output.targets.push(item);
    }
    Some(output)
}
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
        // Frozen TypeCheckEngine defaults `required` to true even with nothing type-checkable.
        required: id == "cpp.type",
        ..type_not_applicable_check()
    })
    .collect()
}

#[cfg(test)]
mod context_message_tests {
    /// The shared `missing-directory` code projects to the two frozen `compile_db` texts from
    /// the two distinct native messages (ici-cpp `context.rs`): an absent field and a gone cwd.
    #[test]
    fn missing_directory_projects_absent_field_and_gone_directory_to_distinct_frozen_texts() {
        assert_eq!(
            super::legacy_context_message(
                "missing-directory",
                "compile entry has no directory field"
            ),
            "missing-directory: A compilation database entry has no directory field."
        );
        assert_eq!(
            super::legacy_context_message("missing-directory", "compile cwd no longer exists"),
            "missing-directory: The compilation working directory does not exist."
        );
        assert_eq!(
            super::legacy_context_message("missing-file", "compile entry has no file field"),
            "missing-file: A compilation database entry has no file field."
        );
        assert_eq!(
            super::legacy_context_message("invalid-entry", "compile entry is not an object"),
            "invalid-entry: A compilation database entry is not an object."
        );
        // Codes without a frozen text keep the native message.
        assert_eq!(
            super::legacy_context_message("response-file-cycle", "cycle"),
            "response-file-cycle: cycle"
        );
    }
}

#[cfg(test)]
mod clang_tidy_config_tests {
    use super::clang_tidy_config_is_unsafe as unsafe_config;

    #[test]
    fn plain_and_single_quoted_configs_without_argument_keys_are_accepted() {
        assert!(!unsafe_config(
            b"Checks: '-*,clang-analyzer-*'\nHeaderFilterRegex: '.*\\.h'\n"
        ));
        assert!(!unsafe_config(
            b"Checks: \"-*,bugprone-*\"\n# a \"quoted\" note\n"
        ));
    }

    #[test]
    fn a_double_quote_beside_any_backslash_is_refused_without_tracking_quotes() {
        // A quote inside a single-quoted scalar or a comment cannot desynchronise the check.
        for text in [
            &b"Checks: 'a\"b'\n\"\\x45xtraArgs\": ['-fplugin=x.so']\n"[..],
            b"# say \"hi\nHeaderFilterRegex: '.*\\.h'\n",
        ] {
            assert!(unsafe_config(text), "{}", String::from_utf8_lossy(text));
        }
    }

    #[test]
    fn literal_argument_and_parent_keys_are_refused_in_any_case_or_form() {
        for text in [
            &b"ExtraArgs: ['-fplugin=x.so']\n"[..],
            b"extraargsbefore: [-Xclang]\n",
            b"{InheritParentConfig: true}\n",
            b"? !!str ExtraArgs\n: [-fplugin=x.so]\n",
        ] {
            assert!(unsafe_config(text), "{}", String::from_utf8_lossy(text));
        }
    }

    #[test]
    fn literal_keys_match_on_ascii_word_boundaries_like_frozen() {
        // Frozen `\b(?:ExtraArgs(?:Before)?|InheritParentConfig)\b` (bytes, IGNORECASE).
        for text in [
            &b"# notExtraArgs and InheritParentConfigs are comments\nChecks: '-*'\n"[..],
            b"Checks: '-*'\nX_ExtraArgs: [a]\nExtraArgs2: [b]\nExtraArgsBeforeX: [c]\n",
            b"Checks: '-*'\nExtraArgsAfter: [d]\nmyInheritParentConfig: true\n",
        ] {
            assert!(!unsafe_config(text), "{}", String::from_utf8_lossy(text));
        }
        for text in [
            &b"ExtraArgs:[a]"[..],
            b"Checks: '-*'\n-ExtraArgs-\n",
            b"'EXTRAARGSBEFORE': [a]",
            b"\xc3\xa9ExtraArgs\xc3\xa9: [a]",
            b"x.inheritparentconfig.y",
            b"InheritParentConfig",
        ] {
            assert!(unsafe_config(text), "{}", String::from_utf8_lossy(text));
        }
    }

    #[test]
    fn escaped_double_quoted_keys_and_nul_encodings_are_refused() {
        for text in [
            &b"\"\\x45xtraArgs\": ['-fplugin=x.so']\n"[..],
            b"\"Extra\\u0041rgs\": [-fplugin=x.so]\n",
            b"\"Extra\\\n  Args\": [-fplugin=x.so]\n",
            b"\xff\xfeE\0x\0",
        ] {
            assert!(unsafe_config(text), "{}", String::from_utf8_lossy(text));
        }
    }
}
