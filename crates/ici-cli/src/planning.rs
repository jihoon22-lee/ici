//! Native CLI-to-application bridge. Preparing a plan reads inputs and executable bytes only.
use crate::{
    commands::{CliError, Result, under},
    registry::Invocation,
};
use ici_application::{
    ApplicationError,
    graph::TaskGraph,
    plan::{Plan, Work, file_language},
    request::RunRequest,
};
use ici_config::ResolvedProject;
use ici_domain::Profile;
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest,
    resolver::{ResolvedTool, ToolRequest},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub struct PreparedPlan {
    pub project: ResolvedProject,
    pub cpp: crate::cpp::CppPlans,
    pub request: RunRequest,
    pub plan: Plan,
    pub graph: TaskGraph,
    pub providers: BTreeMap<String, ProviderPlan>,
    pub probes: BTreeMap<String, ici_domain::TaskSpec>,
    pub validations: BTreeMap<String, Vec<ici_validation::ValidationPlan>>,
    /// Declared suite steps of a check in order, with the reason of those that cannot run.
    pub suite_steps: BTreeMap<String, Vec<(String, Option<String>)>>,
    pub tools: BTreeMap<String, ResolvedTool>,
    pub native_configuration: BTreeMap<String, ici_providers::native_config::ConfigEvidence>,
    pub legacy_projection: bool,
    pub artifact_builds: BTreeMap<String, crate::artifact_build::ArtifactBuildPlan>,
    pub context_preparations: Vec<crate::export_context::ContextPreparationPlan>,
    pub source_identity: crate::vcs::VcsPlan,
    pub legacy_lines: BTreeMap<String, crate::legacy_lines::LineScope>,
}
pub fn prepare(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
) -> Result<PreparedPlan> {
    prepare_for_run(invocation, cwd, environment, static_only, "${run}")
}
pub fn prepare_for_run(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
) -> Result<PreparedPlan> {
    prepare_for_run_omitting(
        invocation,
        cwd,
        environment,
        static_only,
        run_label,
        &BTreeSet::new(),
    )
}
pub fn prepare_for_run_omitting(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
    generated_outputs: &BTreeSet<String>,
) -> Result<PreparedPlan> {
    prepare_with_contexts(
        invocation,
        cwd,
        environment,
        static_only,
        run_label,
        generated_outputs,
        &[],
    )
}
pub fn prepare_with_contexts(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
    generated_outputs: &BTreeSet<String>,
    contexts: &[crate::export_context::PreparedContext],
) -> Result<PreparedPlan> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_config")
        .or_else(|| invocation.value("local_path"))
        .map(|path| under(cwd, path));
    // Historical commands (engines, verify, doctor, publish, export) root at the working directory.
    let resolve = if crate::cutover::stable(&invocation.command()) {
        ici_config::resolve_legacy_command_with_environment
    } else {
        ici_config::resolve_with_environment
    };
    let prefix = if crate::legacy::is_legacy(invocation) {
        crate::commands::STABLE_CONFIG_PREFIX
    } else {
        crate::commands::CONFIG_PREFIX
    };
    let project = resolve(cwd, explicit.as_deref(), local.as_deref(), environment)
        .map_err(|e| crate::commands::classed(prefix, e))?;
    let project = crate::export_context::overlay(&project, contexts);
    let mut prepared = prepare_resolved_omitting(
        invocation,
        project,
        environment,
        static_only,
        run_label,
        generated_outputs,
    )?;
    prepared.cpp.attach_prepared(contexts);
    crate::preparation::bind_compiled_artifacts(&mut prepared, contexts, environment)?;
    Ok(prepared)
}
/// Frozen `_announce` for a run that stops after scope resolution: the configuration resolves
/// and the request names a valid scope and profile. `None` when the refusal comes earlier.
pub fn announcement(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Option<String> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_config")
        .or_else(|| invocation.value("local_path"))
        .map(|path| under(cwd, path));
    let project = ici_config::resolve_with_environment(
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .ok()?;
    let mut languages = invocation.values("language").to_vec();
    if invocation.flag("python") {
        languages.push("python".into());
    }
    if invocation.flag("cpp") {
        languages.push("cpp".into());
    }
    let profile = match invocation.value("profile") {
        Some(value) => Some(serde_json::from_value::<Profile>(Value::String(value.into())).ok()?),
        None => None,
    };
    let request = RunRequest {
        components: invocation.values("component").to_vec(),
        languages,
        profile,
        require_full: invocation.flag("require_full"),
        ..Default::default()
    };
    crate::run_text::announce(&project, &request)
}
pub fn prepare_resolved(
    invocation: &Invocation,
    project: ResolvedProject,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
) -> Result<PreparedPlan> {
    prepare_resolved_omitting(
        invocation,
        project,
        environment,
        static_only,
        run_label,
        &BTreeSet::new(),
    )
}
fn prepare_resolved_omitting(
    invocation: &Invocation,
    mut project: ResolvedProject,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
    generated_outputs: &BTreeSet<String>,
) -> Result<PreparedPlan> {
    // Frozen `verify` and `dead` know the run by its validated metadata, so its report, its
    // publication and its saved identity name one project. A refusal stops them before any tool.
    if project.schema_family == ici_config::SchemaFamily::Legacy
        && matches!(invocation.command().as_str(), "verify" | "dead")
        && let Ok(name) = ici_config::read_project_metadata(&project.root)
            .and_then(|metadata| metadata.legacy_name(&project.root))
    {
        project.workspace_name = name;
    }
    let owned_outputs = match crate::artifact_build::owned_outputs(&project.root) {
        Ok(paths) => paths,
        Err(reason) => {
            project.limitations.push(format!("generated artifact ownership could not be verified; files remain analysis inputs: {reason}"));
            BTreeSet::new()
        }
    };
    for component in &mut project.components {
        // A legacy command reads a `type = "python"` project's C/C++ sources and a
        // `type = "cpp"` project's Python sources like frozen's engines
        // (`ici_config::Component::legacy_widening`); the next path keeps the declared scope.
        if crate::legacy::is_legacy(invocation)
            && let Some((languages, files)) = component.legacy_widening.take()
        {
            component.languages = languages;
            component.files = files;
        }
        component
            .files
            .retain(|path| !generated_outputs.contains(path) && !owned_outputs.contains(path));
    }
    let mut languages = invocation.values("language").to_vec();
    if invocation.flag("python") {
        languages.push("python".into());
    }
    if invocation.flag("cpp") {
        languages.push("cpp".into());
    }
    languages.sort();
    languages.dedup();
    let profile = invocation
        .value("profile")
        .map(|value| {
            serde_json::from_value::<Profile>(Value::String(value.into()))
                .map_err(|e| CliError(e.to_string()))
        })
        .transpose()?
        .or_else(|| crate::legacy::individual_profile(invocation));
    // A frozen single-engine command selects every check of its descriptor but runs the test
    // engine without an analysis context, so deep test-quality observations stay off. Only an
    // explicit v2 `profile = "deep"` keeps them for the individual command.
    if invocation.value("profile").is_none()
        && crate::legacy::individual_profile(invocation).is_some()
        && (project.schema_family == ici_config::SchemaFamily::Legacy
            || project.profile != Profile::Deep)
    {
        for component in &mut project.components {
            if let Some(settings) = component.settings.as_object_mut() {
                let engines = settings.entry("engines").or_insert_with(|| json!({}));
                if let Some(test) = engines
                    .as_object_mut()
                    .map(|engines| engines.entry("test").or_insert_with(|| json!({})))
                    .and_then(Value::as_object_mut)
                {
                    let quality = test.entry("quality").or_insert_with(|| json!({}));
                    if let Some(quality) = quality.as_object_mut() {
                        quality.insert("enabled".into(), json!(false));
                    }
                }
            }
        }
    }
    if let Some(id) = match crate::legacy::engine(&invocation.command()) {
        Some("sanitize") => Some("python.sanitize"),
        Some("build") => Some("python.artifact"),
        _ => None,
    } {
        project
            .checks
            .entry(id.into())
            .or_insert(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: json!({"enabled":true}),
            });
    }
    if crate::legacy::engine(&invocation.command()) == Some("dead") {
        project
            .checks
            .entry("cpp.dead".into())
            .or_insert(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: json!({"enabled":true}),
            });
    }
    let mut definitions = ici_domain::builtin_catalog().checks;
    let cpp_binary = definitions
        .iter()
        .find(|check| check.id == "cpp.binary-compat")
        .expect("built-in ABI check");
    let cpp_binary_selected = project.components.iter().any(|component| {
        component.languages.iter().any(|language| language == "cpp")
            && (invocation.values("component").is_empty()
                || invocation.values("component").contains(&component.id))
            && (languages.is_empty() || languages.iter().any(|language| language == "cpp"))
            && ici_application::plan::configured_check(&project, component, cpp_binary).0
            && match crate::cpp_validation::abi_scope(&project, component) {
                Ok(Some(scope)) => !scope.targets.is_empty(),
                // Frozen: an explicit `engines.binary_compat.artifacts` selection that names no
                // published artifact is the C++ engine's own AnalysisUnavailable ERROR, not the
                // Python fallback's skip.
                Err(_) => component
                    .settings
                    .pointer("/engines/binary_compat/artifacts")
                    .and_then(Value::as_array)
                    .is_some_and(|selected| !selected.is_empty()),
                Ok(None) => false,
            }
    });
    // Frozen `SanitizeEngine._analysis_scopes` puts the Python scope in play from the project tree
    // (`tests/**/*.py`, or a python/hybrid type with a `tests` path), not from the selected
    // sources; read once here for the catalog and the plan. Historical commands only.
    let legacy_python_sanitize_scope = crate::legacy::legacy_schema(invocation, &project)
        && project
            .components
            .iter()
            .any(|component| component.root == ".")
        && ici_application::legacy_scope::legacy_python_sanitize_scope(
            &project.root,
            &project.effective,
        );
    // Frozen `TestEngine._run_project_tests` attempts its Python and C++ suites from the tests
    // tree, the project type and the selected sources (not from the component's languages); read
    // once here for the plan. Historical commands on a legacy root component only.
    let legacy_test_scope = crate::legacy::legacy_schema(invocation, &project)
        .then(|| {
            project
                .components
                .iter()
                .find(|component| component.root == ".")
        })
        .flatten()
        .map(|component| {
            ici_application::legacy_scope::legacy_test_scope(
                &project.root,
                &project.effective,
                &component.files,
            )
        });
    let mut supplemental =
        crate::validation::supplemental_checks(&project, legacy_python_sanitize_scope);
    supplemental.extend(crate::cpp::supplemental_checks(&project));
    supplemental.extend(crate::legacy::annotation_check(invocation, &mut project));
    if !cpp_binary_selected {
        supplemental.push(crate::legacy::python_binary_compat_check());
    }
    // A typed engine command keeps its subject on a schema 2 project: "not applicable here" rather
    // than a refusal that no check was selected. Under `verify` these rows are legacy-only.
    if crate::legacy::legacy_schema(invocation, &project)
        || crate::legacy::engine(&invocation.command()).is_some()
    {
        supplemental.extend(crate::cpp::legacy_not_applicable_checks());
        supplemental.extend(crate::cpp::legacy_cpp_absent_checks(&project));
    }
    definitions.extend(supplemental.iter().cloned());
    crate::validation::apply_legacy_coverage_defaults(&mut project);
    // The legacy descriptor cost profile is applied once, in planning (`check_profiles`), which
    // omits out-of-profile checks with the honest "not in profile" reason.
    let selected_checks = crate::legacy::select(invocation, &mut project, &definitions);
    // After `legacy::select` (which enables every selected check of a typed command): frozen
    // complexity.py:62 measures C++ for a declared `type = "python"` project only when
    // `project_root.rglob("*.cpp")` finds a path: a `.cc`/`.cxx`/`.c`-only tree is not measured.
    if crate::legacy::is_legacy(invocation)
        && project.schema_family == ici_config::SchemaFamily::Legacy
        && crate::legacy_support::declared_type(&project.effective) == Some("python")
        && !crate::legacy_support::rglob_suffix(&project.root, ".cpp")
    {
        project.checks.insert(
            "cpp.complexity".into(),
            ici_config::CheckSetting {
                enabled: false,
                required: false,
                exemptions: BTreeMap::new(),
                settings: json!({"enabled":false}),
            },
        );
    }
    // Frozen test.py:256 attempts Python tests only for a `tests/**/test_*.py` suite or a
    // declared `python`/`hybrid` type with Python sources: a declared `type = "cpp"` project's
    // Python sources (kept by the legacy widening) never make a Python test suite of their own.
    if crate::legacy::is_legacy(invocation)
        && project.schema_family == ici_config::SchemaFamily::Legacy
        && crate::legacy_support::declared_type(&project.effective) == Some("cpp")
        && !crate::legacy_support::has_python_tests(&project.root)
    {
        let off = ici_config::CheckSetting {
            enabled: false,
            required: false,
            exemptions: BTreeMap::new(),
            settings: json!({"enabled":false}),
        };
        for check in ["python.test", "python.coverage"] {
            // The legacy coverage defaults are component settings; they must not re-enable it.
            for component in &mut project.components {
                component.checks.insert(check.into(), off.clone());
            }
            project.checks.insert(check.into(), off.clone());
        }
    }
    // A widened `type = "python"` scope without a translation unit (frozen `get_all_cpp_sources`
    // finds nothing; only `get_all_cpp_headers` does): cycle, dup and dead read the headers
    // (cycle.py:54, dup.py:82, dead.py:90), while lint, type, complexity, cognitive, exception
    // and sanitize read `project_cpp_sources()` only and see no C/C++ (compile_db still runs:
    // its no-unit SKIP, compile_db.py:160).
    if crate::legacy::is_legacy(invocation)
        && project.schema_family == ici_config::SchemaFamily::Legacy
        && crate::legacy_support::declared_type(&project.effective) == Some("python")
        && project.components.iter().any(|component| {
            component.languages.iter().any(|language| language == "cpp")
                && !component
                    .files
                    .iter()
                    .any(|file| ici_cpp::is_translation_unit(file))
        })
    {
        for check in [
            "cpp.diagnostics",
            "cpp.tidy",
            "cpp.type",
            "cpp.complexity",
            "cpp.cognitive",
            "cpp.exception",
            "cpp.sanitize",
        ] {
            project.checks.insert(
                check.into(),
                ici_config::CheckSetting {
                    enabled: false,
                    required: false,
                    exemptions: BTreeMap::new(),
                    settings: json!({"enabled":false}),
                },
            );
        }
    }
    let request = RunRequest {
        components: invocation.values("component").to_vec(),
        languages,
        profile,
        require_full: invocation.flag("require_full"),
        static_only,
        checks: selected_checks,
        legacy_profiles: crate::legacy::legacy_schema(invocation, &project),
        legacy_python_sanitize_scope,
        legacy_test_scope,
    };
    // Frozen `_scope`/`_profile`: an unknown component or language, and a request that selects
    // no check, are configuration refusals of the requested scope (`config: `, exit 2).
    let scope_prefix = if crate::legacy::is_legacy(invocation) {
        crate::commands::STABLE_CONFIG_PREFIX
    } else {
        crate::commands::CONFIG_PREFIX
    };
    request
        .scope(&project)
        .map_err(|e| crate::commands::classed(scope_prefix, e))?;
    let mut cpp = crate::cpp::CppPlans::default();
    cpp.legacy_projection = crate::legacy::legacy_schema(invocation, &project);
    cpp.legacy_standalone = cpp.legacy_projection && invocation.command() != "verify";
    let mut artifact_builds = BTreeMap::new();
    let mut validations = BTreeMap::new();
    let mut suite_steps = BTreeMap::new();
    let mut providers = BTreeMap::new();
    let mut tools = BTreeMap::new();
    let mut native_configuration = BTreeMap::new();
    let pack = tool_pack(environment);
    let mut plan = ici_application::plan::plan_with_checks(
        &project,
        &request,
        &supplemental,
        |component, check, task_id| {
            if check.id == "python.artifact" {
                return Ok(
                    match crate::artifact_build::describe_with_environment(
                        &project,
                        component,
                        task_id,
                        environment,
                    ) {
                        Ok(build) => {
                            let work = Work::native_prepare_after(
                                build.version_probe.iter().cloned().collect(),
                                build.task.clone(),
                            );
                            artifact_builds.insert(task_id.into(), build);
                            work
                        }
                        // Frozen `_package_python` records these as a BuildError at ".".
                        Err(reason)
                            if crate::legacy::is_legacy(invocation)
                                && project.schema_family == ici_config::SchemaFamily::Legacy
                                && let Some(message) =
                                    crate::artifact_build::legacy_python_build_error(
                                        &project.root,
                                        &reason,
                                    ) =>
                        {
                            cpp.fail_preparation(
                                task_id,
                                crate::cpp::build_failure(
                                    task_id, ".", "ici.toml", true, &message, &reason,
                                ),
                            );
                            Work::Internal
                        }
                        Err(reason) => Work::Blocked { reason },
                    },
                );
            }
            let component_root = if component.root == "." {
                project.root.clone()
            } else {
                project.root.join(&component.root)
            };
            // A next-v1 `cpp.binary-compat` is frozen next's artifact-contract expansion, not
            // the published-manifest applicability the C++ binder decides.
            let frozen_next_binary = check.id == "cpp.binary-compat"
                && crate::cpp_validation::frozen_next_binary(&project, &request);
            if !frozen_next_binary
                && let Some(work) = cpp.bind(&project, component, check, task_id, environment)
            {
                // A rebound ABI check whose explicit shadow selection names no observed output
                // is the frozen engine's own ERROR, exactly as for the validation binding below.
                if crate::legacy::legacy_schema(invocation, &project)
                    && check.id == "cpp.binary-compat"
                    && let Work::Blocked { reason } = &work
                    && let Some(output) = legacy_binary_selection(component, task_id, reason)
                {
                    cpp.fail_preparation(task_id, output);
                    return Ok(Work::Internal);
                }
                return Ok(work);
            }
            if let Some(bound) = crate::cpp_validation::bind(
                &project,
                component,
                check,
                task_id,
                &request,
                environment,
                run_label,
            ) {
                if crate::legacy::legacy_schema(invocation, &project)
                    && check.id == "cpp.binary-compat"
                    && let Work::Blocked { reason } = &bound.work
                    && let Some(output) = legacy_binary_selection(component, task_id, reason)
                {
                    cpp.fail_preparation(task_id, output);
                    return Ok(Work::Internal);
                }
                if check.id == "cpp.test" && matches!(bound.work, Work::Blocked { .. }) {
                    // Every descriptor-free test source failed to compile: those are the
                    // measured test failures, not a missing suite.
                    let failures = crate::cpp::direct_test_compile_failures(
                        &project,
                        &component.id,
                        environment,
                    );
                    if !failures.is_empty() {
                        let mut output = crate::cpp::test_compile_failures_only(task_id, &failures);
                        if crate::legacy::legacy_schema(invocation, &project) {
                            crate::cpp::legacy_direct_tests(&mut output, &failures);
                        }
                        cpp.fail_preparation(task_id, output);
                        return Ok(Work::Internal);
                    }
                }
                tools.extend(bound.tools);
                if !bound.suite_steps.is_empty() {
                    suite_steps.insert(task_id.to_string(), bound.suite_steps);
                }
                if !bound.plans.is_empty() {
                    validations.insert(task_id.into(), bound.plans);
                }
                return Ok(bound.work);
            }
            if let Some(bound) = crate::validation::bind(
                &project,
                component,
                check,
                task_id,
                &request,
                environment,
                run_label,
            ) {
                if let Some(tool) = bound.tool {
                    tools.insert(format!("{}:python", component.id), tool);
                }
                if !bound.plans.is_empty() {
                    validations.insert(task_id.into(), bound.plans);
                }
                return Ok(bound.work);
            }
            let tool_id = match check.id.as_str() {
                "python.lint" | "python.format" => "ruff",
                // Frozen legacy `ici type`/`verify` always ran the project's mypy
                // (`engines/type_check.py`); a schema 2 project keeps its resolved provider.
                "python.type" if crate::legacy::legacy_schema(invocation, &project) => "mypy",
                "python.type" => match component.python_type_provider.as_deref() {
                    None | Some("ty") => "ty",
                    Some("mypy") => "mypy",
                    Some(other) => {
                        return Err(ApplicationError(format!(
                            "unknown Python type provider {other}"
                        )));
                    }
                },
                "rust.clippy" => "clippy",
                "javascript.lint" | "typescript.lint" => "eslint",
                "typescript.type" => "tsc",
                // Runtime/build/compile checks bind through their own context adapters, not a null
                // registry tool. They stay visibly blocked until those adapters supply real work.
                "python.test"
                | "python.coverage"
                | "python.compat-runtime"
                | "cpp.compile"
                | "cpp.diagnostics"
                | "cpp.tidy"
                | "cpp.test"
                | "cpp.coverage"
                | "cpp.artifact"
                | "cpp.sanitize"
                | "cpp.tsan"
                | "cpp.binary-compat"
                | "python.binary-compat"
                | "integration" => {
                    return Ok(Work::Blocked {
                        reason: format!(
                            "{} requires its native runtime/build context adapter",
                            check.id
                        ),
                    });
                }
                _ if matches!(check.language.as_str(), "python" | "cpp") => {
                    return Ok(Work::Internal);
                }
                _ => {
                    return Ok(Work::Blocked {
                        reason: format!("no native provider for {}", check.id),
                    });
                }
            };
            let kind = ProviderKind::for_check(&check.id, tool_id)
                .ok_or_else(|| ApplicationError(format!("no provider for {}", check.id)))?;
            let explicit = project
                .tools
                .get(tool_id)
                .and_then(|setting| setting.path.as_deref())
                .map(|path| under(&project.root, path));
            // Frozen legacy engines found mypy and ruff their own way (`validation::legacy_mypy`,
            // `validation::legacy_ruff`); the selection is recorded in the run limitations.
            let mut legacy_tool = None;
            let tool = if tool_id == "mypy"
                && explicit.is_none()
                && crate::legacy::legacy_schema(invocation, &project)
            {
                match crate::validation::legacy_mypy(
                    &project,
                    invocation.command() == "verify",
                    environment,
                ) {
                    Ok((tool, module, note)) => {
                        legacy_tool = Some((module, note));
                        tool
                    }
                    Err(reason) => return Ok(Work::Blocked { reason }),
                }
            } else if let Some(selected) = (tool_id == "ruff"
                && explicit.is_none()
                && crate::legacy::legacy_schema(invocation, &project))
            .then(|| crate::validation::legacy_ruff(&project, environment))
            .flatten()
            {
                match selected {
                    Ok((tool, note)) => {
                        legacy_tool = Some((false, note));
                        tool
                    }
                    Err(reason) => return Ok(Work::Blocked { reason }),
                }
            } else {
                match (ToolRequest {
                    id: tool_id.into(),
                    explicit,
                    project_root: component_root.clone(),
                    pack_root: pack.clone(),
                    path_env: environment.get("PATH").cloned(),
                })
                .resolve()
                {
                    Ok(tool) => tool,
                    Err(error) => {
                        // Frozen next tool lookup (application/selection.py, gates.py): a tool
                        // found nowhere is `<tool> is not available` in the plan, its JSON,
                        // the gate and the limitations; other resolution failures keep the
                        // native reason.
                        return Ok(Work::Blocked {
                            reason: crate::plan_text::tool_blocked_reason(&error.to_string())
                                .into_owned(),
                        });
                    }
                }
            };
            let targets: Vec<_> = component
                .files
                .iter()
                .filter(|p| file_language(p) == Some(check.language.as_str()))
                .cloned()
                .collect();
            let legacy_mypy =
                kind == ProviderKind::Mypy && crate::legacy::legacy_schema(invocation, &project);
            // Frozen `_mypy_targets`: the source directories holding the selected sources (else
            // the files themselves), relative to the project and sorted. Inputs stay the files.
            let mut legacy_targets: Vec<_> = component
                .source_dirs
                .iter()
                .filter(|directory| {
                    legacy_mypy
                        && targets.iter().any(|file| {
                            directory.as_str() == "."
                                || file == *directory
                                || file.starts_with(&format!("{directory}/"))
                        })
                })
                .cloned()
                .collect();
            legacy_targets.sort();
            legacy_targets.dedup();
            let evidence = ici_providers::native_config::discover_native_configuration(
                kind,
                &project.root,
                &component_root,
                &targets,
            )
            .map_err(|e| ApplicationError(e.to_string()))?;
            let mut provider_request = ProviderRequest::new(
                kind,
                tool.clone(),
                &project.root,
                &component_root,
                targets,
                task_id,
            );
            provider_request.component_id = Some(component.id.clone());
            provider_request.analysis_unit_ids =
                vec![format!("{}.{}", component.id, check.language)];
            provider_request.input_refs = evidence
                .files
                .keys()
                .map(|p| p.to_string_lossy().into_owned())
                .collect();
            provider_request.options.jobs = 2;
            if matches!(kind, ProviderKind::RuffLint | ProviderKind::RuffFormat)
                && crate::legacy::legacy_schema(invocation, &project)
            {
                provider_request.options.legacy = true;
            }
            if legacy_mypy {
                provider_request.options.legacy = true;
                provider_request.options.legacy_targets = legacy_targets;
                let settings = component
                    .checks
                    .get(&check.id)
                    .map(|setting| &setting.settings);
                let profile = component
                    .settings
                    .pointer("/engines/type/mypy_profile")
                    .or_else(|| settings.and_then(|settings| settings.get("mypy_profile")))
                    .and_then(Value::as_str);
                if profile == Some("ici") {
                    provider_request.options.legacy_args = [
                        "--check-untyped-defs",
                        "--warn-redundant-casts",
                        "--warn-unused-ignores",
                    ]
                    .map(String::from)
                    .to_vec();
                }
                if legacy_tool.as_ref().is_some_and(|(module, _)| *module) {
                    provider_request
                        .options
                        .legacy_args
                        .splice(0..0, ["-m".to_string(), "mypy".to_string()]);
                }
            }
            provider_request.options.python_version = component
                .settings
                .pointer("/python/target_version")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if kind == ProviderKind::Tsc {
                provider_request.options.project_config = ["tsconfig.json", "jsconfig.json"]
                    .into_iter()
                    .find(|name| component_root.join(name).is_file())
                    .map(str::to_owned);
            }
            let decided = component
                .checks
                .get(&check.id)
                .or_else(|| project.checks.get(&check.id));
            provider_request.options.advisory = kind == ProviderKind::Ty
                && component.python_type_provider.is_none()
                && !decided.is_some_and(|setting| {
                    setting.settings.get("required").and_then(Value::as_bool) == Some(true)
                });
            let mut provider = ici_providers::plan(&provider_request)
                .map_err(|e| ApplicationError(e.to_string()))?;
            if let Some((_, note)) = legacy_tool {
                provider.limitations.push(note);
            }
            provider.scope_complete = evidence.complete;
            provider.limitations.extend(evidence.limitations.clone());
            provider.task.cacheable = provider.scope_complete && !provider.task.mutating();
            let work = if provider.advisory {
                Work::process_advisory(provider.task.clone())
            } else {
                Work::process(provider.task.clone())
            };
            tools.insert(format!("{}:{tool_id}", component.id), tool);
            providers.insert(task_id.to_owned(), provider);
            native_configuration.insert(task_id.to_owned(), evidence);
            Ok(work)
        },
    )
    .map_err(|e| {
        if e.0 == ici_application::plan::NOTHING_SELECTED {
            crate::commands::classed(scope_prefix, e)
        } else {
            CliError(e.to_string())
        }
    })?;
    tools.extend(cpp.tools.clone());
    let probes = providers
        .iter()
        .filter(|(_, provider)| {
            matches!(
                provider.kind,
                ProviderKind::RuffLint | ProviderKind::RuffFormat
            )
        })
        .map(|(id, provider)| {
            // The legacy formatter probes `ruff format --help` like frozen instead.
            if provider.context.legacy && provider.kind == ProviderKind::RuffFormat {
                ici_providers::plan::ruff_format_capability_task(provider)
            } else {
                ici_providers::plan::ruff_inventory_task(provider)
            }
            .map(|task| (id.clone(), task))
        })
        .collect::<std::result::Result<_, _>>()
        .map_err(|e| CliError(e.to_string()))?;
    cpp.finalize_plan(&mut plan);
    suite_steps.extend(std::mem::take(&mut cpp.blocked_steps));
    // Frozen `ici verify` builds its analysis context itself, whatever `[engines.compile_db]`
    // says: with compile_db disabled the lint engine still replays that context, so a legacy
    // C++ syntax/clang check of a component whose compile check is not selected consumes no
    // compile-inputs token.
    let compile_selected: BTreeSet<String> = plan
        .checks
        .iter()
        .filter(|check| check.check.id == "cpp.compile")
        .map(|check| check.component_id.clone())
        .collect();
    for check in &mut plan.checks {
        if cpp.legacy_projection
            && !compile_selected.contains(&check.component_id)
            && matches!(check.check.id.as_str(), "cpp.diagnostics" | "cpp.tidy")
        {
            check.check.needs.clear();
        }
        // A legacy binary-compat verdict decided while planning consumes no build prerequisite.
        if check.check.id == "cpp.binary-compat" && cpp.planned_output(&check.task_id) {
            check.check.needs.clear();
        }
        // Standalone legacy lint never consumes compile inputs (see `legacy::select`).
        if cpp.legacy_standalone
            && matches!(check.check.id.as_str(), "cpp.diagnostics" | "cpp.tidy")
        {
            check.check.needs.clear();
        }
    }
    let mut context_preparations = vec![];
    if plan.profile != Profile::Fast {
        for component in &project.components {
            let preparation = native_preparation(invocation, &project, component, static_only);
            if preparation == NativePreparation::None {
                continue;
            }
            let selected: BTreeSet<String> = plan
                .checks
                .iter()
                .filter(|check| check.component_id == component.id && check.check.language == "cpp")
                .map(|check| check.check.id.clone())
                .collect();
            let mut variants = BTreeSet::new();
            let primary = crate::cpp::linked_build(&project, component)
                .map(|build| build.variant.as_str())
                .unwrap_or("release");
            if selected.iter().any(|id| {
                matches!(
                    id.as_str(),
                    "cpp.compile" | "cpp.artifact" | "cpp.binary-compat" | "python.binary-compat"
                ) || (!cpp.legacy_standalone
                    && matches!(id.as_str(), "cpp.diagnostics" | "cpp.tidy"))
            }) {
                variants.insert(primary);
            }
            if selected.contains("cpp.dead")
                && component
                    .settings
                    .pointer("/engines/dead/cpp_unused")
                    .and_then(Value::as_str)
                    .unwrap_or("auto")
                    != "off"
            {
                variants.insert(primary);
            }
            // Frozen never configures a build for a project without `tests/**/*.cpp`.
            let cpp_tests_absent = cpp.legacy_projection
                && crate::legacy_execute::tests_have_cpp_sources(&project.root) == Some(false);
            if !cpp_tests_absent && selected.contains("cpp.coverage") {
                variants.insert("coverage");
            } else if !cpp_tests_absent && selected.contains("cpp.test") {
                variants.insert(if crate::cpp::linked_build(&project, component).is_some() {
                    primary
                } else {
                    "test"
                });
            }
            for (check_id, variant) in [
                ("cpp.sanitize", "sanitize"),
                ("cpp.tsan", "thread-sanitize"),
            ] {
                if !selected.contains(check_id) {
                    continue;
                }
                if !cpp.legacy_projection
                    || !crate::cpp::legacy_sanitizer_tests_absent(&project.root)
                {
                    variants.insert(variant);
                    continue;
                }
                // Frozen sanitizer engines skip before choosing a backend when the project has
                // no `tests/**/*.cpp`: nothing is built, so the variant is not prepared.
                plan.limitations.push(format!(
                    "{} {variant}: no tests/**/*.cpp sanitizer test sources; the variant was not prepared or built",
                    component.id
                ));
                let python_planned = plan.checks.iter().any(|check| {
                    check.component_id == component.id
                        && check.check.id == "python.sanitize"
                        && !matches!(check.work, Work::Blocked { .. })
                });
                let scope = crate::cpp::legacy_sanitizer_scope(
                    &project.root,
                    &project.effective,
                    component,
                    check_id,
                    python_planned,
                );
                for check in plan.checks.iter_mut().filter(|check| {
                    check.component_id == component.id && check.check.id == check_id
                }) {
                    cpp.fail_preparation(
                        &check.task_id,
                        match scope {
                            crate::cpp::LegacySanitizerScope::Cpp => {
                                crate::cpp::legacy_sanitizer_skip(&check.task_id, check_id)
                            }
                            crate::cpp::LegacySanitizerScope::PythonOnly => {
                                crate::cpp::legacy_sanitizer_python_only(&check.task_id)
                            }
                            crate::cpp::LegacySanitizerScope::None => {
                                crate::cpp::legacy_sanitizer_no_scope(&check.task_id, check_id)
                            }
                        },
                    );
                    validations.remove(&check.task_id);
                    check.check.needs.clear();
                    check.work = Work::Internal;
                }
            }
            if selected.contains("cpp.dead")
                && component
                    .settings
                    .pointer("/engines/dead/cpp_linker")
                    .and_then(Value::as_str)
                    .unwrap_or("off")
                    != "off"
                // Frozen GNU ELF reachability needs the root CMake backend; a legacy project
                // without one reports the linker scope as unavailable without preparing.
                && !(cpp.legacy_projection
                    && component
                        .settings
                        .pointer("/engines/dead/cpp_unused")
                        .and_then(Value::as_str)
                        .unwrap_or("auto")
                        != "off"
                    && !crate::files::under(&project.root, &component.root)
                        .join("CMakeLists.txt")
                        .is_file())
                // Nor does a standalone legacy `ici dead` whose automatic linker scope has no
                // probed cmake/readelf/addr2line (frozen probes only the selected support rows).
                && !(cpp.legacy_projection
                    && component
                        .settings
                        .pointer("/engines/dead/cpp_unused")
                        .and_then(Value::as_str)
                        == Some("off")
                    && crate::cpp::legacy_linker_toolset_stop(
                        &project,
                        component,
                        environment,
                        cpp.legacy_standalone,
                    )
                    .is_some())
                && !(cpp.legacy_standalone
                    && crate::cpp::legacy_linker_unprobed_tool(&project, component).is_some())
            {
                variants.insert("link-reachability");
            }
            if preparation == NativePreparation::IsolatedOnly {
                variants.retain(|variant| *variant == "link-reachability");
            }
            for variant in variants {
                let mut selected_project = project.clone();
                let mut selected_component = component.clone();
                if let Some(build) = selected_project
                    .builds
                    .iter_mut()
                    .find(|build| Some(&build.id) == component.build.as_ref())
                {
                    build.variant = variant.into();
                } else {
                    // Undeclared legacy builds are resolved by the native descriptor reader.
                    selected_component.settings["native_requested_variant"] = json!(variant);
                }
                if variant == "release"
                    && (selected.contains("cpp.artifact") || selected.contains("cpp.binary-compat"))
                {
                    selected_component.settings["native_release_artifacts"] = json!(true);
                }
                // Frozen `prepare_cmake/qmake_compilation_context` keep the project's own database
                // (`existing.database_path is not None`) and prepare nothing for analysis.
                let existing_database = variant == "release"
                    && cpp.legacy_projection
                    && !cpp.legacy_standalone
                    && legacy_selected_database(&project);
                // Frozen `ici verify` configures the release analysis database
                // (`-DCMAKE_UNITY_BUILD=OFF`) before its deep BuildEngine reuses that shadow.
                if variant == "release"
                    && cpp.legacy_projection
                    && !cpp.legacy_standalone
                    && !existing_database
                {
                    selected_component.settings["native_analysis_database"] = json!(true);
                }
                let binary =
                    std::env::current_exe().map_err(|error| CliError(error.to_string()))?;
                match crate::export_context::plan_preparation(
                    &selected_project,
                    &selected_component,
                    environment,
                    &binary,
                ) {
                    Ok(mut preparation) => {
                        if existing_database
                            && preparation.direct.is_none()
                            && matches!(preparation.build.system.as_str(), "cmake" | "qmake")
                        {
                            // Only the BuildEngine's release artifacts need this build.
                            if selected_component.settings.get("native_release_artifacts")
                                != Some(&json!(true))
                            {
                                continue;
                            }
                            preparation.existing_database = true;
                        }
                        preparation.inputs.retain(|path| {
                            !owned_outputs.contains(path)
                                && !is_command_output(invocation, &project.root, path)
                        });
                        for (name, tool) in &preparation.tools {
                            tools.insert(
                                format!("prepare:{}:{variant}:{name}", component.id),
                                tool.clone(),
                            );
                        }
                        preparation.configured_context_on_build_failure = cpp.legacy_projection
                            && variant == "release"
                            && preparation.build.system == "cmake";
                        // Frozen publishes (and bounds) a build session's manifest only under
                        // `verify`, whose sessions carry an analysis context.
                        preparation.legacy_manifest_bounds = cpp.legacy_projection
                            && !cpp.legacy_standalone
                            && matches!(
                                preparation.build.system.as_str(),
                                "cmake" | "qmake" | "make"
                            )
                            && matches!(
                                variant,
                                "coverage" | "sanitize" | "thread-sanitize" | "release"
                            );
                        preparation.legacy_runtime_session = cpp.legacy_projection
                            && matches!(
                                variant,
                                "coverage" | "test" | "sanitize" | "thread-sanitize"
                            );
                        context_preparations.push(preparation);
                    }
                    Err(crate::export_context::PreparationUnavailable::Misconfigured {
                        reason,
                        descriptor,
                    }) => {
                        // A declared or explicitly enabled build that cannot be planned blocks
                        // the variant's dependent checks; it never falls back or passes.
                        let configuration = reason;
                        let reason =
                            format!("declared {variant} build cannot be prepared: {configuration}");
                        plan.limitations
                            .push(format!("{} {variant}: {reason}", component.id));
                        let legacy_projection = cpp.legacy_projection;
                        // Frozen `prepare_cmake_compilation_context` (core/cmake_context.py:204)
                        // with no `cmake` on PATH: `run_process` cannot start it, the configure
                        // session is not configured, and the run keeps analysing without a
                        // database (`cmake-configure-failed`). Only the BuildEngine checks stay
                        // bound to the failed release build, like an execution-time failure.
                        let cmake = descriptor
                            .as_deref()
                            .is_some_and(|path| path.ends_with("CMakeLists.txt"))
                            || crate::cpp::linked_build(&project, component)
                                .and_then(|build| build.system.as_deref())
                                == Some("cmake");
                        let analysis_fallback = legacy_projection
                            && !cpp.legacy_standalone
                            && !existing_database
                            && variant == "release"
                            && cmake
                            && configuration.starts_with("cmake: not found;");
                        if analysis_fallback {
                            cpp.legacy_planned_fallbacks.push((
                                component.id.clone(),
                                ici_cpp::ContextDiagnostic::error(
                                    "cmake-configure-failed",
                                    "Canonical CMake configure did not complete.",
                                ),
                            ));
                        }
                        for check in plan.checks.iter_mut().filter(|check| {
                            check.component_id == component.id
                                && check.check.language == "cpp"
                                && (!analysis_fallback
                                    || matches!(
                                        check.check.id.as_str(),
                                        "cpp.artifact" | "cpp.binary-compat"
                                    ))
                                && (crate::cpp::preparation_dependent(
                                    &check.check.id,
                                    variant,
                                    !check.tasks().is_empty(),
                                ) || (legacy_projection
                                    && variant == "coverage"
                                    && check.check.id == "cpp.test"))
                        }) {
                            // Context preparation is planned only for legacy commands.
                            if check.check.id == "cpp.test" {
                                // Frozen TestEngine `_run_cpp_tests_via_adapter`: a coverage
                                // configure refusal is the engine's tool error (ERROR, NOT_RUN,
                                // no targets); no test runs.
                                let mut output = crate::cpp::legacy_test_configuration_error(
                                    &check.task_id,
                                    &configuration,
                                );
                                // Its backend selection is the one record frozen keeps.
                                let linked = crate::cpp::linked_build(&project, component)
                                    .and_then(|build| build.system.clone());
                                if let Some(selected) = descriptor.as_deref() {
                                    let system = linked.unwrap_or_else(|| {
                                        if selected.ends_with("CMakeLists.txt") {
                                            "cmake".into()
                                        } else if selected.ends_with(".pro") {
                                            "qmake".into()
                                        } else {
                                            "make".into()
                                        }
                                    });
                                    output.extra["tool_evidence"] =
                                        crate::cpp::legacy_build_session_evidence(
                                            &project.root,
                                            &component.root,
                                            &system,
                                            selected,
                                            &[],
                                        );
                                }
                                cpp.fail_preparation(&check.task_id, output);
                                validations.remove(&check.task_id);
                                check.check.needs.clear();
                                check.work = Work::Internal;
                            } else if variant == "release" && check.check.id == "cpp.artifact" {
                                // Frozen BuildEngine: FAIL, "Build did not produce a complete
                                // artifact set", one adapter target at the descriptor.
                                cpp.fail_preparation(
                                    &check.task_id,
                                    crate::cpp::build_failure(
                                        &check.task_id,
                                        descriptor.as_deref().unwrap_or("."),
                                        descriptor.as_deref().unwrap_or("ici.toml"),
                                        false,
                                        &reason,
                                        &reason,
                                    ),
                                );
                                check.check.needs.clear();
                                check.work = Work::Internal;
                            } else if matches!(check.check.id.as_str(), "cpp.sanitize" | "cpp.tsan")
                            {
                                // Frozen sanitizer engines: the adapter configure failure is a
                                // `C++Sanitizer` ERROR at the descriptor; nothing is built. The
                                // backend was selected first (`BuildSession` selection record).
                                let file = descriptor.as_deref().unwrap_or(".");
                                let mut output = crate::cpp::legacy_sanitizer_configuration_error(
                                    &check.task_id,
                                    file,
                                    &configuration,
                                );
                                let name = Path::new(file)
                                    .file_name()
                                    .map(|name| name.to_string_lossy().into_owned())
                                    .unwrap_or_default();
                                let system = match name.as_str() {
                                    "CMakeLists.txt" => Some("cmake"),
                                    "Makefile" | "makefile" | "GNUmakefile" => Some("make"),
                                    _ if name.ends_with(".pro") => Some("qmake"),
                                    _ => None,
                                };
                                if legacy_projection && let Some(system) = system {
                                    output.extra["tool_evidence"] =
                                        crate::cpp::legacy_build_session_evidence(
                                            &project.root,
                                            &component.root,
                                            system,
                                            file,
                                            &[],
                                        );
                                }
                                cpp.fail_preparation(&check.task_id, output);
                                validations.remove(&check.task_id);
                                check.check.needs.clear();
                                check.work = Work::Internal;
                            } else {
                                check.work = Work::Blocked {
                                    reason: reason.clone(),
                                };
                            }
                        }
                    }
                    Err(crate::export_context::PreparationUnavailable::NothingToPrepare(
                        reason,
                    )) if variant == "release"
                        && selected.contains("cpp.artifact")
                        && crate::export_context::unadapted_descriptor(&crate::files::under(
                            &project.root,
                            &component.root,
                        ))
                        .is_some() =>
                    {
                        // Frozen BuildEngine refuses generic g++ next to an unadapted descriptor.
                        let (label, name) = crate::export_context::unadapted_descriptor(
                            &crate::files::under(&project.root, &component.root),
                        )
                        .unwrap_or_default();
                        let file = if component.root == "." {
                            name
                        } else {
                            format!("{}/{name}", component.root)
                        };
                        let message = format!(
                            "{label} at the project root has no configured adapter; CMake, qmake, and explicitly enabled Make are supported, and generic g++ was not invoked"
                        );
                        plan.limitations
                            .push(format!("{} {variant}: {reason}; {message}", component.id));
                        for check in plan.checks.iter_mut().filter(|check| {
                            check.component_id == component.id && check.check.id == "cpp.artifact"
                        }) {
                            cpp.fail_preparation(
                                &check.task_id,
                                crate::cpp::build_failure(
                                    &check.task_id,
                                    ".",
                                    &file,
                                    true,
                                    &message,
                                    &message,
                                ),
                            );
                            check.check.needs.clear();
                            check.work = Work::Internal;
                        }
                    }
                    Err(crate::export_context::PreparationUnavailable::NothingToPrepare(
                        reason,
                    )) => {
                        let direct = if crate::legacy::legacy_schema(invocation, &project)
                            && match variant {
                                "release" => selected.contains("cpp.artifact"),
                                "test" => selected.contains("cpp.test"),
                                "coverage" => selected.contains("cpp.coverage"),
                                "sanitize" => selected.contains("cpp.sanitize"),
                                "thread-sanitize" => selected.contains("cpp.tsan"),
                                _ => false,
                            } {
                            crate::export_context::plan_legacy_direct_preparation(
                                &selected_project,
                                &selected_component,
                                environment,
                            )
                        } else {
                            Err("direct compiler was not selected for this scope".into())
                        };
                        match direct {
                            Ok(mut preparation) => {
                                preparation.inputs.retain(|path| {
                                    !owned_outputs.contains(path)
                                        && !is_command_output(invocation, &project.root, path)
                                });
                                for (name, tool) in &preparation.tools {
                                    tools.insert(
                                        format!("prepare:{}:{variant}:{name}", component.id),
                                        tool.clone(),
                                    );
                                }
                                context_preparations.push(preparation);
                            }
                            Err(direct_reason)
                                if variant == "release"
                                    && selected.contains("cpp.artifact")
                                    && direct_reason == crate::cpp::LEGACY_NO_GXX =>
                            {
                                // Frozen `_record_tool_error`: one BuildError at ".".
                                plan.limitations
                                    .push(format!("{} {variant}: {direct_reason}", component.id));
                                for check in plan.checks.iter_mut().filter(|check| {
                                    check.component_id == component.id
                                        && check.check.id == "cpp.artifact"
                                }) {
                                    let mut output = crate::cpp::build_failure(
                                        &check.task_id,
                                        ".",
                                        "ici.toml",
                                        true,
                                        &direct_reason,
                                        &direct_reason,
                                    );
                                    output.extra["legacy_targets_only"] = json!(true);
                                    output.extra["tool_evidence"] = json!([{
                                        "name": "g++ build", "path": "g++", "argv": ["g++"],
                                        "returncode": null, "timed_out": false,
                                        "truncated": false, "error": direct_reason,
                                    }]);
                                    cpp.fail_preparation(&check.task_id, output);
                                    check.check.needs.clear();
                                    check.work = Work::Internal;
                                }
                            }
                            Err(direct_reason)
                                if variant == "release"
                                    && selected.contains("cpp.artifact")
                                    && let Some(unreadable) =
                                        crate::export_context::legacy_unreadable_sources(
                                            &direct_reason,
                                        ) =>
                            {
                                // Frozen `_count_cpp_main_definitions`: one BuildError per
                                // unreadable source at its path; nothing is compiled.
                                plan.limitations.push(format!(
                                    "{} {variant}: {}",
                                    component.id,
                                    unreadable
                                        .iter()
                                        .map(|(_, message)| message.as_str())
                                        .collect::<Vec<_>>()
                                        .join("; ")
                                ));
                                let errors = unreadable
                                    .iter()
                                    .map(|(path, message)| {
                                        (path.clone(), path.clone(), message.clone())
                                    })
                                    .collect::<Vec<_>>();
                                for check in plan.checks.iter_mut().filter(|check| {
                                    check.component_id == component.id
                                        && check.check.id == "cpp.artifact"
                                }) {
                                    cpp.fail_preparation(
                                        &check.task_id,
                                        crate::cpp::legacy_build_errors(&check.task_id, &errors),
                                    );
                                    check.check.needs.clear();
                                    check.work = Work::Internal;
                                }
                            }
                            Err(direct_reason)
                                if variant == "release"
                                    && selected.contains("cpp.artifact")
                                    && direct_reason.starts_with("C++ adapter required:") =>
                            {
                                // Frozen BuildEngine `_record_error`: one BuildError at ".".
                                plan.limitations.push(format!(
                                    "{} {variant}: {direct_reason}; generic g++ was not invoked",
                                    component.id
                                ));
                                let located = component
                                    .files
                                    .iter()
                                    .find(|path| file_language(path) == Some("cpp"))
                                    .cloned()
                                    .unwrap_or_else(|| "ici.toml".into());
                                for check in plan.checks.iter_mut().filter(|check| {
                                    check.component_id == component.id
                                        && check.check.id == "cpp.artifact"
                                }) {
                                    cpp.fail_preparation(
                                        &check.task_id,
                                        crate::cpp::build_failure(
                                            &check.task_id,
                                            ".",
                                            &located,
                                            true,
                                            &direct_reason,
                                            &direct_reason,
                                        ),
                                    );
                                    check.check.needs.clear();
                                    check.work = Work::Internal;
                                }
                            }
                            Err(direct_reason) => plan.limitations.push(format!(
                                "{} {variant}: no automatic preparation available: {reason}; {direct_reason}",
                                component.id
                            )),
                        }
                    }
                }
            }
        }
    }
    if !crate::legacy::is_legacy(invocation)
        && (matches!(
            invocation.command().as_str(),
            "next plan" | "doctor" | "next doctor"
        ) || (invocation.command() == "analyze" && invocation.flag("plan")))
    {
        // Doctor and plan run nothing, but static preparation refusals (an unowned or symlinked
        // shadow, a missing or symlinked workdir, an unavailable configured executable) are
        // plan-time facts users look for here.
        let binary = std::env::current_exe().map_err(|error| CliError(error.to_string()))?;
        for component in project
            .components
            .iter()
            .filter(|component| component.languages.iter().any(|language| language == "cpp"))
        {
            if let Err(crate::export_context::PreparationUnavailable::Misconfigured {
                reason,
                descriptor,
            }) =
                crate::export_context::plan_preparation(&project, component, environment, &binary)
            {
                plan.limitations.push(format!(
                    "{} release: declared release build cannot be prepared{}: {reason} (static preparation preflight; nothing was built)",
                    component.id,
                    descriptor.map(|path| format!(" ({path})")).unwrap_or_default()
                ));
            }
        }
    }
    // Frozen BuildEngine.run (build.py:61-87) reads the project name, version and type before
    // `_prepare_output_tree`: a refused name or version is one BuildError 'Build metadata or
    // setup error: <refusal>' at ".", with no target path ('Build target was not prepared'), and
    // nothing is configured, compiled, packaged or created.
    if cpp.legacy_standalone && project.schema_family == ici_config::SchemaFamily::Legacy {
        for component in &project.components {
            let ids = plan
                .checks
                .iter()
                .filter(|check| {
                    check.component_id == component.id
                        && matches!(check.check.id.as_str(), "cpp.artifact" | "python.artifact")
                })
                .map(|check| check.task_id.clone())
                .collect::<BTreeSet<_>>();
            if ids.is_empty() {
                continue;
            }
            let base = crate::files::under(&project.root, &component.root);
            let Err(refusal) = ici_config::read_project_metadata(&base)
                .and_then(|metadata| metadata.legacy_name(&base).map(|_| metadata))
            else {
                continue;
            };
            if refusal.configuration {
                continue;
            }
            let message = format!("Build metadata or setup error: {}", refusal.message);
            plan.limitations
                .push(format!("{} release: {message}", component.id));
            for id in &ids {
                let mut output =
                    crate::cpp::build_failure(id, ".", "ici.toml", true, &message, &message);
                output.extra["legacy_targets_only"] = json!(true);
                for key in ["project_name", "project_version", "target_path"] {
                    output.extra[key] = json!("");
                }
                output.extra["metrics_summary"] = json!("Build target was not prepared");
                cpp.fail_preparation(id, output);
                artifact_builds.remove(id);
            }
            for check in plan
                .checks
                .iter_mut()
                .filter(|check| ids.contains(&check.task_id))
            {
                check.check.needs.clear();
                check.work = Work::Internal;
            }
            context_preparations.retain(|preparation| {
                !(preparation.component_id == component.id
                    && preparation.build.variant == "release")
            });
        }
    }
    let mut preparation_owners = BTreeMap::new();
    for preparation in &context_preparations {
        let key = (&preparation.build.id, &preparation.build.variant);
        if let Some(previous) = preparation_owners.insert(key, &preparation.build.owner_digest)
            && previous != &preparation.build.owner_digest
        {
            return Err(CliError(format!(
                "build {} variant {} has conflicting preparation configurations; declare separate builds",
                preparation.build.id, preparation.build.variant
            )));
        }
    }
    for check in &mut plan.checks {
        let scoped_contract = format!("{}.artifact-contract", check.component_id);
        if check.check.id != "cpp.binary-compat"
            || !validations.contains_key(&check.task_id)
            || !check
                .check
                .needs
                .iter()
                .any(|need| need == &scoped_contract)
        {
            continue;
        }
        let Some(component) = project
            .components
            .iter()
            .find(|component| component.id == check.component_id)
        else {
            continue;
        };
        if crate::cpp::linked_build(&project, component).is_none()
            && matches!(crate::cpp_validation::abi_scope(&project, component),Ok(Some(scope)) if !scope.targets.is_empty())
        {
            check.check.needs.retain(|need| need != &scoped_contract);
            plan.limitations.push(format!("{} uses verified published or typed artifact bytes without a linked build producer", check.task_id));
        }
    }
    if !crate::legacy::legacy_schema(invocation, &project) {
        cpp.frozen_next_plan(&project, &mut plan, request.require_full, environment);
    }
    let mut legacy_lines =
        crate::legacy_lines::collect(&project, &plan, invocation, generated_outputs)?;
    legacy_lines.extend(crate::legacy_scan_tests::collect(
        &project, &plan, invocation,
    )?);
    // Frozen `LineEngine` counted a tree with no in-scope source (documentation only, sources
    // outside `source_dirs`) as a measured empty scope with its repository extras. It is one
    // engine over every language: a hybrid component without C++ (or Python) sources still
    // counts the other language's files, so the empty language adds nothing and is not run.
    let counted: BTreeSet<String> = plan
        .checks
        .iter()
        .filter(|check| {
            legacy_lines
                .get(&check.task_id)
                .is_some_and(|scope| !scope.inputs.is_empty())
        })
        .map(|check| check.component_id.clone())
        .collect();
    let empty_line_scope = |check: &ici_application::plan::PlannedCheck| {
        matches!(check.check.id.as_str(), "python.line" | "cpp.line")
            && matches!(&check.work, Work::Blocked { reason } if reason.starts_with("no sources in scope"))
    };
    plan.checks.retain(|check| {
        !(empty_line_scope(check)
            && legacy_lines
                .get(&check.task_id)
                .is_some_and(|scope| scope.inputs.is_empty())
            && counted.contains(&check.component_id))
    });
    // A legacy security scope with only `tests/` sources (engines.security.scan_tests) is measured
    // like the line views: the planner's empty-source block does not apply to it.
    let empty_security_scope = |check: &ici_application::plan::PlannedCheck| {
        check.check.id == "python.security"
            && matches!(&check.work, Work::Blocked { reason } if reason.starts_with("no sources in scope"))
    };
    for check in &mut plan.checks {
        // Frozen `LineEngine` measures an empty project too: zero lines over zero files.
        let line_scope = legacy_lines.get(&check.task_id);
        if (empty_line_scope(check) && line_scope.is_some())
            || (empty_security_scope(check)
                && line_scope.is_some_and(|scope| !scope.inputs.is_empty()))
        {
            check.work = Work::Internal;
        }
    }
    // A component whose selection left out a link's target (decisions.md "component root 밖을
    // 가리키는 선택 symlink") hands no tool its incomplete scope. The tool steps the partial intake
    // blocks at execution (`inputs::uncaptured_block`) are blocked here with the same reason, so
    // the plan never previews a step that verify refuses; so is a schema 1 `python.artifact`
    // preparation the link may reach (`inputs::outside_link_preparation`).
    let selection = (plan.components.clone(), plan.languages.clone());
    for check in &mut plan.checks {
        if let Some(crate::inputs::PreparationPolicy::Refused(reason)) =
            crate::inputs::outside_link_preparation(
                &project,
                (&selection.0, &selection.1),
                &artifact_builds,
                check,
            )
        {
            check.work = Work::Blocked { reason };
            continue;
        }
        if !matches!(check.work, Work::Process { .. } | Work::Steps { .. })
            || matches!(check.check.id.as_str(), "cpp.compile" | "python.compile-db")
            || cpp.diagnostic_steps.contains_key(&check.task_id)
        {
            continue;
        }
        let link = project
            .components
            .iter()
            .find(|component| component.id == check.component_id)
            .and_then(|component| {
                component
                    .outside_links
                    .iter()
                    .find(|link| link.language == check.check.language)
            });
        if let Some(link) = link {
            check.work = Work::Blocked {
                reason: link.reason(&check.component_id),
            };
        }
    }
    let mut graph = TaskGraph::build(&plan.checks).map_err(|e| CliError(e.to_string()))?;
    disabled_producers(&plan, &definitions, &mut graph);
    let source_identity = crate::vcs::plan(&project, environment);
    let legacy_projection = crate::legacy::legacy_schema(invocation, &project);
    Ok(PreparedPlan {
        project,
        cpp,
        request,
        plan,
        graph,
        providers,
        probes,
        validations,
        suite_steps,
        tools,
        native_configuration,
        legacy_projection,
        artifact_builds,
        context_preparations,
        source_identity,
        legacy_lines,
    })
}
/// What a run may build for a component.
/// A check whose input comes from a check the project turned off stays blocked and says so: the
/// dependency is named, never re-enabled behind the project's `enabled = false`.
fn disabled_producers(
    plan: &Plan,
    definitions: &[ici_domain::CheckDefinition],
    graph: &mut TaskGraph,
) {
    for (task_id, reason) in &mut graph.blocked {
        let Some(planned) = plan.checks.iter().find(|check| &check.task_id == task_id) else {
            continue;
        };
        let prefix = format!("{}.", planned.component_id);
        let mut named = vec![];
        for need in &planned.check.needs {
            if !reason.contains(&format!("needs {need}, which no selected check produces")) {
                continue;
            }
            let capability = need.strip_prefix(&prefix).unwrap_or(need);
            for omitted in plan.omitted.iter().filter(|omitted| {
                omitted.component_id == planned.component_id && omitted.reason == "disabled"
            }) {
                if definitions.iter().any(|definition| {
                    definition.id == omitted.check_id
                        && definition
                            .provides
                            .iter()
                            .any(|provided| provided == capability)
                }) {
                    named.push(format!(
                        "{} produces it and is disabled (enabled = false)",
                        omitted.check_id
                    ));
                }
            }
        }
        if !named.is_empty() {
            reason.push_str(&format!(" — {}", named.join(", ")));
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum NativePreparation {
    None,
    /// Only the isolated link-reachability shadow: the user's own build tree is left alone.
    IsolatedOnly,
    All,
}

/// Who runs a native build. A legacy project's historical commands do, as frozen's did. A schema 2
/// project does when a run asks for build evidence (never `analyze`) and the component links a
/// declared build of a known system. `prepare = "explicit"` leaves it to the user entirely, a build
/// directory that already exists is the user's evidence to read, and an undeclared build stays a
/// reported gap under every command.
/// Frozen `_select_database` (core/_compile_db_paths.py) selects a database: a configured
/// `project.compile_database` of any value, else an existing root `compile_commands.json` or
/// `build/compile_commands.json`.
fn legacy_selected_database(project: &ResolvedProject) -> bool {
    project
        .effective
        .pointer("/project/compile_database")
        .is_some_and(|value| !value.is_null())
        || ["compile_commands.json", "build/compile_commands.json"]
            .iter()
            .any(|candidate| project.root.join(candidate).exists())
}
fn native_preparation(
    invocation: &Invocation,
    project: &ResolvedProject,
    component: &ici_config::Component,
    static_only: bool,
) -> NativePreparation {
    if crate::legacy::legacy_schema(invocation, project) {
        return NativePreparation::All;
    }
    if project.schema_family != ici_config::SchemaFamily::SparseV2 || static_only {
        return NativePreparation::None;
    }
    let Some(build) = crate::cpp::linked_build(project, component) else {
        return NativePreparation::None;
    };
    if build.system.is_none() || build.prepare.as_deref() == Some("explicit") {
        return NativePreparation::None;
    }
    if build
        .directory
        .as_deref()
        .is_some_and(|directory| under(&project.root, directory).is_dir())
    {
        return NativePreparation::IsolatedOnly;
    }
    NativePreparation::All
}
pub fn is_command_output(invocation: &Invocation, root: &Path, path: &str) -> bool {
    let path = under(root, path);
    if crate::legacy::engine(&invocation.command()).is_some_and(|family| {
        crate::report::same_file(&path, &root.join(format!("{family}_report.json")))
    }) {
        return true;
    }
    let explicit = [
        "result",
        "events",
        "html",
        "sarif",
        "write_baseline",
        "baseline",
        "page",
    ]
    .into_iter()
    .filter_map(|key| invocation.value(key))
    .map(|path| under(root, path));
    let standard = [
        crate::DEFAULT_RESULT,
        crate::DEFAULT_PAGE,
        "verify_report.json",
        "verify_report.html",
        ".ici/rust-v1/publish.json",
    ]
    .into_iter()
    .map(|path| root.join(path));
    explicit
        .chain(standard)
        .any(|output| crate::report::same_file(&path, &output))
}
pub(crate) fn tool_pack(environment: &BTreeMap<String, String>) -> Option<PathBuf> {
    if let Some(directory) = environment
        .get("ICI_NATIVE_TOOL_PACK")
        .filter(|p| !p.is_empty())
    {
        return Some(PathBuf::from(directory));
    }
    std::env::current_exe().ok().and_then(|path| {
        let parent = path.parent()?;
        std::iter::once(parent.join("tools"))
            .chain(parent.parent().map(|root| root.join("tools")))
            .find(|directory| directory.join("manifest.json").is_file())
    })
}
/// The run's toolchain identity: every resolved tool and every step's pinned executable, except
/// an executable this run's preparation built (exactly one of its declared outputs, e.g. a direct
/// test binary): that is the project's code under test, whose digest changes with each source
/// edit and would make every later baseline "incompatible" (frozen's toolchain identity names
/// only the probed tools).
fn toolchain_roles<'a>(
    tools: impl Iterator<Item = (&'a str, &'a Path, &'a str)>,
    tasks: impl Iterator<Item = &'a ici_domain::TaskSpec>,
    built: &BTreeSet<&str>,
) -> BTreeSet<(String, String)> {
    let prepared = |path: &str| built.contains(path);
    let mut roles: BTreeSet<_> = tools
        .filter(|(_, path, _)| !prepared(&path.to_string_lossy()))
        .map(|(id, _, digest)| (format!("tool:{id}"), digest.to_owned()))
        .collect();
    for task in tasks {
        if task
            .argv
            .first()
            .is_some_and(|executable| prepared(executable))
        {
            continue;
        }
        if let Some(digest) = &task.tool_digest {
            roles.insert((format!("step:{}", task.provider), digest.clone()));
        }
    }
    roles
}

impl PreparedPlan {
    pub fn tool_pins(&self) -> BTreeMap<String, String> {
        let mut pins: BTreeMap<_, _> = self
            .tools
            .values()
            .map(|tool| {
                (
                    tool.path.to_string_lossy().into_owned(),
                    tool.digest.clone(),
                )
            })
            .collect();
        for task in self
            .plan
            .checks
            .iter()
            .flat_map(|check| check.tasks())
            .chain(
                self.context_preparations
                    .iter()
                    .flat_map(|plan| plan.build.tasks.iter()),
            )
        {
            if let (Some(path), Some(digest)) = (task.argv.first(), &task.tool_digest) {
                pins.insert(path.clone(), digest.clone());
            }
        }
        pins
    }
    pub fn toolchain_roles(&self) -> BTreeSet<(String, String)> {
        let built: BTreeSet<&str> = self
            .context_preparations
            .iter()
            .flat_map(|plan| plan.build.tasks.iter())
            .flat_map(|task| task.output_specs.iter().map(String::as_str))
            .collect();
        toolchain_roles(
            self.tools
                .values()
                .map(|tool| (tool.id.as_str(), tool.path.as_path(), tool.digest.as_str())),
            self.plan
                .checks
                .iter()
                .flat_map(|check| check.tasks())
                .chain(
                    self.context_preparations
                        .iter()
                        .flat_map(|plan| plan.build.tasks.iter()),
                ),
            &built,
        )
    }
    pub fn document(&self) -> Value {
        let plans:Vec<_>=self.plan.components.iter().filter_map(|component| {
            let checks:Vec<_>=self.plan.checks.iter().filter(|p| &p.component_id==component).map(|p| {
                let blocked=self.graph.blocked.get(&p.task_id).map(String::as_str).or_else(||p.blocked());
                let mut entry=json!({"task_id":p.task_id,"check":p.check.id,"kind":if blocked.is_some(){"blocked"}else if matches!(p.work,Work::NativePrepare{..}){"native_prepare"}else if p.task().is_some(){"process"}else{"internal"}});
                if let Some(reason)=blocked {entry["blocked"]=json!(reason);}
                // A declared suite that cannot run is listed beside the suites that will
                // (frozen plans list every suite task, runnable or blocked).
                if let Some(steps)=self.suite_steps.get(&p.task_id).filter(|steps|steps.iter().any(|(_,reason)|reason.is_some())) {entry["blocked_steps"]=json!(steps.iter().filter_map(|(step,reason)|reason.as_ref().map(|reason|json!({"task_id":step,"reason":reason}))).collect::<Vec<_>>());}
                if let Some(task)=p.task() {
                    entry["argv"]=json!(task.argv);
                    let steps:Vec<_>=self.probes.get(&p.task_id).into_iter().chain(p.tasks().iter()).collect();
                    entry["steps"]=json!(steps);
                    if !task.requires.is_empty(){entry["requires"]=json!(task.requires);}
                    if !task.work_dirs.is_empty(){entry["work_dirs"]=json!(task.work_dirs);}
                    if p.advisory(){entry["advisory"]=json!(true);}
                }
                if let Some(scope)=self.legacy_lines.get(&p.task_id) { entry["line_scope"]=json!(scope); }
                let conditions:BTreeMap<_,_>=self.validations.get(&p.task_id).into_iter().flatten().flat_map(|plan|plan.conditional_steps.iter()).collect();
                if !conditions.is_empty() {entry["conditional_steps"]=json!(conditions);}
                entry
            }).collect();
            (!checks.is_empty()).then(||json!({"component":component,"checks":checks}))
        }).collect();
        let mut mutating: BTreeMap<_, _> = self
            .plan
            .checks
            .iter()
            .filter(|p| !self.graph.blocked.contains_key(&p.task_id))
            .filter_map(|p| {
                let directories: BTreeSet<_> = p
                    .tasks()
                    .iter()
                    .filter(|t| t.mutating())
                    .flat_map(|t| t.work_dirs.iter().cloned())
                    .collect();
                (!directories.is_empty()).then_some((p.task_id.clone(), directories))
            })
            .collect();
        for preparation in &self.context_preparations {
            let directories: BTreeSet<_> = preparation
                .build
                .tasks
                .iter()
                .filter(|task| task.mutating())
                .flat_map(|task| task.work_dirs.iter().cloned())
                .collect();
            mutating.insert(
                format!(
                    "prepare:{}:{}",
                    preparation.build.id, preparation.build.variant
                ),
                directories,
            );
        }
        let edges: BTreeMap<_, _> = self
            .graph
            .edges
            .iter()
            .filter(|(id, _)| !self.graph.blocked.contains_key(*id))
            .collect();
        let owners: BTreeMap<_, _> = self
            .plan
            .checks
            .iter()
            .flat_map(|check| {
                check
                    .tasks()
                    .iter()
                    .map(move |task| (task.id.as_str(), check.task_id.as_str()))
            })
            .collect();
        let mut consumers = BTreeMap::<&str, BTreeSet<&str>>::new();
        for check in &self.plan.checks {
            if self.graph.blocked.contains_key(&check.task_id) {
                continue;
            }
            for task in check.tasks() {
                consumers
                    .entry(task.id.as_str())
                    .or_default()
                    .insert(&check.task_id);
                for dependency in &task.depends_on {
                    if let Some(owner) = owners.get(dependency.as_str())
                        && *owner != check.task_id
                    {
                        consumers
                            .entry(dependency.as_str())
                            .or_default()
                            .insert(&check.task_id);
                    }
                }
            }
        }
        for (consumer, dependencies) in &self.graph.edges {
            if self.graph.blocked.contains_key(consumer) {
                continue;
            }
            for dependency in dependencies {
                if !self.graph.blocked.contains_key(dependency) {
                    consumers
                        .entry(dependency)
                        .or_default()
                        .extend([dependency.as_str(), consumer.as_str()]);
                }
            }
        }
        let shared: BTreeMap<_, _> = consumers
            .into_iter()
            .filter(|(_, checks)| checks.len() > 1)
            .collect();
        let builds:Vec<_>=self.project.builds.iter().map(|build| {
            let linked:BTreeSet<_>=self.project.components.iter().filter(|c|self.plan.components.contains(&c.id) && c.build.as_deref()==Some(build.id.as_str())).map(|c|&c.id).collect();
            json!({"id":build.id,"system":build.system,"variant":build.variant,"directory":build.directory,"definition":build.project,"artifacts":build.artifacts,"linked_by":linked})
        }).collect();
        let mut document = json!({"schema_id":"ici.next.plan","schema_version":1,"root":self.project.root,"scope":{"components":self.plan.components,"languages":self.plan.languages,"profile":self.plan.profile,"require_full":self.request.require_full},"plans":plans,"graph":{"edges":edges,"shared":shared,"mutating":mutating},"builds":builds,"coverage_gaps":self.scope_gaps(),"limitations":self.plan.limitations});
        if !self.source_identity.tasks.is_empty() || self.source_identity.limitation.is_some() {
            document["source_identity"] = json!(self.source_identity);
        }
        if !self.context_preparations.is_empty() {
            document["preparations"]=json!(self.context_preparations.iter().map(|plan|json!({"component":plan.component_id,"build":plan.build.id,"system":plan.build.system,"variant":plan.build.variant,"direct":plan.direct.is_some(),"inputs":plan.inputs,"steps":plan.build.tasks,"generated_roots":plan.build.generated_roots,"effects":if plan.effects.is_null(){json!(["configure and build inside the owned shadow", "reset only verified single-link coverage counters inside that shadow"])}else{plan.effects.clone()}})).collect::<Vec<_>>());
            if self
                .plan
                .checks
                .iter()
                .any(|check| check.check.id == "cpp.artifact")
            {
                document["artifact_preparation"] = json!({"status":"planned","effects":["measure primary compiled binaries and libraries after build", "copy verified bytes through the shared release packager under the configured version directory", "write owned release manifests and environment scripts"],"limits":{"file_bytes":268435456u64,"total_input_bytes":536870912u64},"output_names":"resolved from measured primary build outputs before publication"});
            }
        }
        ici_execution::redaction::redact_value(&document)
    }
    /// Frozen `ici next plan` text (`crate::plan_text`).
    pub fn text(&self) -> String {
        self.frozen_text()
    }
}

/// Frozen BinaryCompatibilityEngine `_records` outcomes for a legacy run whose selection is empty
/// or names an unpublished artifact; other blocked reasons stay blocked.
fn legacy_binary_selection(
    component: &ici_config::Component,
    task_id: &str,
    reason: &str,
) -> Option<ici_domain::AnalysisOutput> {
    let required = component
        .settings
        .pointer("/engines/binary_compat/required")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if reason.starts_with("no existing executable/shared-library artifact targets")
        || reason.starts_with("binary compatibility requires existing verified")
    {
        return Some(crate::cpp::legacy_binary_compat_unavailable(
            task_id,
            required.then_some("no published executable or shared-library artifacts"),
        ));
    }
    // Frozen `BinaryCompatEngine._readelf`: selected artifacts with no resolvable readelf
    // raise "readelf capability is unavailable" (ERROR, NOT_RUN) instead of a blocked check.
    if reason.starts_with("readelf: ") {
        return Some(crate::cpp::legacy_binary_compat_unavailable(
            task_id,
            Some("readelf capability is unavailable"),
        ));
    }
    // Frozen `_records`: more selected artifacts than `max_artifacts` (bound_binary rejects an
    // empty selection before planning, and the schema bounds the key to 1..=64).
    if reason.starts_with("binary compatibility requires 1..max_artifacts") {
        let maximum = component
            .settings
            .pointer("/engines/binary_compat/max_artifacts")
            .and_then(Value::as_u64)
            .unwrap_or(64);
        return Some(crate::cpp::legacy_binary_compat_unavailable(
            task_id,
            Some(&format!(
                "binary artifact count exceeds configured maximum {maximum}"
            )),
        ));
    }
    let unpublished = [
        "is not an observed output",
        "is not in the linked build inventory",
        "is not an existing verified published or declared artifact",
    ];
    if unpublished.iter().any(|marker| reason.contains(marker)) {
        let value = reason.split('"').nth(1)?;
        return Some(crate::cpp::legacy_binary_compat_unavailable(
            task_id,
            Some(&format!("configured artifact was not published: {value}")),
        ));
    }
    None
}

#[cfg(test)]
mod toolchain_role_tests {
    use super::toolchain_roles;
    use ici_domain::TaskSpec;
    use std::{collections::BTreeSet, path::Path};

    /// A binary the run's preparation linked is not toolchain identity; a project-local tool the
    /// run did not build, and every other step's executable, still are.
    #[test]
    fn prepared_executables_leave_the_toolchain_identity_and_project_tools_stay() {
        let step = |provider: &str, executable: &str, digest: &str| {
            let mut task = TaskSpec::command(
                format!("t.{provider}"),
                provider,
                vec![executable.to_owned()],
                "/p",
            );
            task.tool_digest = Some(digest.into());
            task
        };
        let tasks = [
            step(
                "cpp-executable",
                "/p/.ici/build/bin/test-a",
                "sha256:binary",
            ),
            step("ctest", "/p/tools/ctest", "sha256:ctest"),
            step("gcov", "/usr/bin/gcov", "sha256:gcov"),
        ];
        // A directory output (a build tree) excludes nothing below it: only exact outputs.
        let built = BTreeSet::from(["/p/.ici/build/bin/test-a", "/p/tools"]);
        let roles = toolchain_roles(
            [
                ("ctest", Path::new("/p/tools/ctest"), "sha256:ctest"),
                (
                    "built",
                    Path::new("/p/.ici/build/bin/test-a"),
                    "sha256:binary",
                ),
            ]
            .into_iter(),
            tasks.iter(),
            &built,
        );
        let expected: BTreeSet<(String, String)> = [
            ("tool:ctest", "sha256:ctest"),
            ("step:ctest", "sha256:ctest"),
            ("step:gcov", "sha256:gcov"),
        ]
        .into_iter()
        .map(|(role, digest)| (role.to_owned(), digest.to_owned()))
        .collect();
        assert_eq!(roles, expected);
    }
}
