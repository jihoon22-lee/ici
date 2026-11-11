use crate::{
    commands::{CliError, Result},
    planning::PreparedPlan,
};
use ici_application::{
    identity::{InvocationIdentity, ScopeIdentity, workspace_environment, workspace_reference},
    plan::{PlannedCheck, engine_name, file_language},
    snapshot::{InputLimits, InputSnapshot, UncapturedCause, UncapturedSource},
};
use ici_config::Component;
use ici_domain::{AnalysisRequest, CheckRecord, Observation};
use ici_execution::Runner;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub fn capture(prepared: &PreparedPlan) -> Result<InputSnapshot> {
    capture_preserving(prepared, &empty_snapshot(), &BTreeMap::new(), &[])
}
/// Keep checking original physical inputs when preparation changes provider selection.
/// Declared generated roots may replace their prior inputs during preparation only.
pub fn capture_preserving(
    prepared: &PreparedPlan,
    original: &InputSnapshot,
    original_limits: &BTreeMap<String, usize>,
    generated_roots: &[String],
) -> Result<InputSnapshot> {
    let mut path_limits = binary_limits(prepared);
    path_limits.extend(original_limits.clone());
    let mut sources: BTreeSet<_> = prepared
        .project
        .components
        .iter()
        .filter(|component| prepared.plan.components.contains(&component.id))
        .flat_map(|component| component.files.iter().cloned())
        .collect();
    sources.extend(
        prepared
            .legacy_lines
            .values()
            .flat_map(|scope| scope.inputs.iter().cloned()),
    );
    for plans in prepared.validations.values() {
        for task in plans.iter().flat_map(|plan| &plan.tasks) {
            for reference in &task.input_refs {
                if let Ok(relative) = Path::new(reference).strip_prefix(&prepared.project.root)
                    && reference.ends_with(".py")
                    && Path::new(reference).is_file()
                {
                    sources.insert(relative.to_string_lossy().into_owned());
                }
            }
        }
    }
    for context in prepared.cpp.contexts.values() {
        for unit in &context.units {
            if prepared.project.root.join(&unit.source).is_file() {
                sources.insert(unit.source.clone());
            }
        }
    }
    let mut configurations: BTreeSet<String> = prepared
        .project
        .configuration_files
        .iter()
        .filter_map(|path| {
            path.strip_prefix(&prepared.project.root)
                .ok()
                .map(|p| p.to_string_lossy().into_owned())
        })
        .collect();
    configurations.extend(prepared.cpp.evidence_input_limits().into_keys());
    configurations.extend(python_binary_artifacts(prepared));
    configurations.extend(python_wheels(prepared));
    for build in prepared.artifact_builds.values() {
        for reference in &build.task.input_refs {
            if let Ok(relative) = Path::new(reference).strip_prefix(&prepared.project.root) {
                let relative = relative.to_string_lossy().into_owned();
                if path_limits.contains_key(&relative) {
                    sources.remove(&relative);
                    configurations.insert(relative);
                } else {
                    sources.insert(relative);
                }
            }
        }
    }
    for preparation in &prepared.context_preparations {
        configurations.extend(
            preparation
                .inputs
                .iter()
                .filter(|path| !sources.contains(*path))
                .cloned(),
        );
    }
    for context in prepared.cpp.contexts.values() {
        // A legacy database that could not be read is the frozen `database-missing` verdict of
        // the engines that use it, not an unreadable input of the whole run.
        if !prepared.legacy_projection || context.database_digest.is_some() {
            configurations.extend(context.database_path.iter().cloned());
        }
        configurations.extend(
            context
                .response_files
                .iter()
                .map(|input| input.path.clone()),
        );
    }
    for provider in prepared.providers.values() {
        for reference in &provider.task.input_refs {
            let path = Path::new(reference);
            let path = if path.is_absolute() {
                path.to_owned()
            } else {
                Path::new(&provider.task.cwd).join(path)
            };
            if let Ok(relative) = path.strip_prefix(&prepared.project.root) {
                let relative = relative.to_string_lossy().into_owned();
                if !sources.contains(&relative) {
                    configurations.insert(relative);
                }
            }
        }
    }
    for component in prepared
        .project
        .components
        .iter()
        .filter(|component| prepared.plan.components.contains(&component.id))
    {
        let component_root = if component.root == "." {
            prepared.project.root.clone()
        } else {
            prepared.project.root.join(&component.root)
        };
        for directory in [&prepared.project.root, &component_root] {
            for name in [
                "pyproject.toml",
                "setup.cfg",
                "setup.py",
                "pytest.ini",
                ".pytest.ini",
                "tox.ini",
            ] {
                let path = directory.join(name);
                if path.is_file()
                    && let Ok(relative) = path.strip_prefix(&prepared.project.root)
                {
                    configurations.insert(relative.to_string_lossy().into_owned());
                }
            }
        }
    }
    // What a Python run's pytest and coverage may read as configuration, by their own discovery.
    for plans in prepared.validations.values() {
        for plan in plans {
            if let Some(configuration) = &plan.tool_configuration {
                configurations.extend(
                    configuration
                        .read
                        .iter()
                        .filter(|path| prepared.project.root.join(path).is_file())
                        .cloned(),
                );
            }
        }
    }
    let retained = |path: &&String| {
        !generated_roots
            .iter()
            .any(|root| Path::new(path).starts_with(root))
    };
    sources.extend(original.sources.iter().filter(retained).cloned());
    configurations.extend(original.configurations.iter().filter(retained).cloned());
    // Unread: the outside links of the selection, and any other link a selected check's inputs
    // name (never read through).
    let unread: BTreeMap<String, UncapturedSource> = prepared
        .project
        .components
        .iter()
        .flat_map(|component| {
            component
                .outside_links
                .iter()
                .map(move |link| (component, link))
        })
        .filter(|(component, link)| {
            selected_link(prepared, component, link)
                || sources.contains(&link.link)
                || configurations.contains(&link.link)
        })
        .map(|(component, link)| {
            (
                link.link.clone(),
                UncapturedSource {
                    path: link.link.clone(),
                    cause: UncapturedCause::OutsideComponentRoot,
                    limit_bytes: 0,
                    detail: Some(link.reason(&component.id)),
                },
            )
        })
        .collect();
    sources.retain(|path| !unread.contains_key(path));
    configurations.retain(|path| !unread.contains_key(path));
    InputSnapshot::take_partial_with_path_limits(
        &prepared.project.root,
        &sources.into_iter().collect::<Vec<_>>(),
        &configurations.into_iter().collect::<Vec<_>>(),
        InputLimits::default(),
        &path_limits,
        unread,
    )
    .map_err(|error| CliError(error.to_string()))
}
/// A link of a selected component in a selected language: that scope is incomplete. A link the
/// selection leaves out is outside the run unless a selected check's inputs name it.
pub fn selected_link(
    prepared: &PreparedPlan,
    component: &Component,
    link: &ici_config::OutsideLink,
) -> bool {
    prepared.plan.components.contains(&component.id)
        && prepared.plan.languages.contains(&link.language)
}
/// What a check reads that the snapshot left unread: (reasons for its own per-file scope, reasons
/// for anything else in its input closure, captured files of its per-file scope). The closure is
/// its component sources of its language (or its legacy line scope), the inputs its provider,
/// validation and replay tasks declare, and its recorded C++ code evidence.
fn uncaptured_of(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
) -> (Vec<String>, Vec<String>, usize) {
    let component = component(prepared, planned);
    let mut scope: BTreeSet<String> = component
        .files
        .iter()
        .filter(|path| file_language(path) == Some(planned.check.language.as_str()))
        .cloned()
        .collect();
    if let Some(lines) = prepared.legacy_lines.get(&planned.task_id) {
        scope = lines.inputs.iter().cloned().collect();
    }
    scope.extend(
        component
            .outside_links
            .iter()
            .filter(|link| link.language == planned.check.language)
            .map(|link| link.link.clone()),
    );
    let root = &prepared.project.root;
    let relative = |reference: &String| -> String {
        Path::new(reference)
            .strip_prefix(root)
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_else(|_| reference.clone())
    };
    let task_inputs = prepared
        .providers
        .get(&planned.task_id)
        .map(|provider| &provider.task.input_refs)
        .into_iter()
        .flatten()
        .chain(
            prepared
                .validations
                .get(&planned.task_id)
                .into_iter()
                .flatten()
                .flat_map(|plan| &plan.tasks)
                .flat_map(|task| &task.input_refs),
        )
        .chain(
            prepared
                .cpp
                .diagnostics
                .get(&planned.task_id)
                .into_iter()
                .flatten()
                .flat_map(|provider| &provider.task.input_refs),
        )
        .map(relative)
        .chain(prepared.cpp.evidence_code_inputs_for(&planned.task_id));
    let others: BTreeSet<String> = task_inputs.filter(|path| !scope.contains(path)).collect();
    let reasons = |paths: &BTreeSet<String>| -> Vec<String> {
        paths
            .iter()
            .filter_map(|path| snapshot.uncaptured.get(path))
            .map(|source| source.reason())
            .collect()
    };
    let own = reasons(&scope);
    let captured = scope.len() - own.len();
    (own, reasons(&others), captured)
}
/// Why a check cannot run on a partial snapshot. Artifact preparation never does; a check that
/// reads the database alone is unaffected; one that analyzes files one by one still runs over the
/// captured ones ([`mark_uncaptured`]); one relating files or handing them to a tool stays blocked.
pub fn uncaptured_block(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
) -> Option<String> {
    let first = snapshot.uncaptured.values().next()?;
    if matches!(
        planned.work,
        ici_application::plan::Work::NativePrepare { .. }
    ) {
        // Any other cause (an input size limit) still refuses every preparation, whichever
        // source sorts first.
        if let Some(other) = snapshot
            .uncaptured
            .values()
            .find(|source| source.cause != UncapturedCause::OutsideComponentRoot)
        {
            return Some(format!("artifact preparation refused: {}", other.reason()));
        }
        if outside_link_preparation(
            &prepared.project,
            (&prepared.plan.components, &prepared.plan.languages),
            &prepared.artifact_builds,
            planned,
        ) == Some(PreparationPolicy::Independent)
        {
            return None;
        }
        return Some(format!("artifact preparation refused: {}", first.reason()));
    }
    if matches!(
        planned.check.id.as_str(),
        "cpp.compile" | "python.compile-db"
    ) {
        return None;
    }
    // Next `cpp.diagnostics` refuses its affected units step by step.
    if prepared.cpp.diagnostic_steps.contains_key(&planned.task_id) {
        return None;
    }
    let (own, others, captured) = uncaptured_of(prepared, planned, snapshot);
    if let Some(reason) = others.into_iter().next() {
        return Some(reason);
    }
    let reason = own.into_iter().next()?;
    let per_file = matches!(planned.work, ici_application::plan::Work::Internal)
        && matches!(
            planned.check.id.rsplit('.').next().unwrap_or(""),
            "line" | "complexity" | "cognitive" | "exception" | "security" | "resource"
        );
    (!(per_file && captured > 0)).then_some(reason)
}
#[derive(Debug, PartialEq, Eq)]
pub enum PreparationPolicy {
    /// Reads nothing an outside link left unread; it may run.
    Independent,
    Refused(String),
}
/// Schema 1 `python.artifact` beside a selected link outside its component root (decisions.md
/// "component root 밖을 가리키는 선택 symlink"): the link's own component is refused with the link;
/// another component may package only when it provably reads its own complete selected sources
/// and the regular project metadata alone ([`ArtifactBuildPlan::reads_only_own_sources`]),
/// otherwise it is refused like every preparation of a partial snapshot. `None` when the rule
/// does not apply (another family or check, or no outside link in the workspace).
///
/// [`ArtifactBuildPlan::reads_only_own_sources`]: crate::artifact_build::ArtifactBuildPlan::reads_only_own_sources
pub fn outside_link_preparation(
    project: &ici_config::ResolvedProject,
    (components, languages): (&[String], &[String]),
    builds: &BTreeMap<String, crate::artifact_build::ArtifactBuildPlan>,
    planned: &PlannedCheck,
) -> Option<PreparationPolicy> {
    let selected = |component: &Component, link: &ici_config::OutsideLink| {
        components.contains(&component.id) && languages.contains(&link.language)
    };
    if project.schema_family != ici_config::SchemaFamily::NextV1
        || planned.check.id != "python.artifact"
        || !matches!(
            planned.work,
            ici_application::plan::Work::NativePrepare { .. }
        )
    {
        return None;
    }
    let first = project
        .components
        .iter()
        .flat_map(|component| {
            component
                .outside_links
                .iter()
                .filter(move |link| selected(component, link))
                .map(|link| link.reason(&component.id))
        })
        .next()?;
    let Some(component) = project
        .components
        .iter()
        .find(|component| component.id == planned.component_id)
    else {
        return Some(PreparationPolicy::Refused(format!(
            "artifact preparation refused: {first}"
        )));
    };
    if let Some(link) = component
        .outside_links
        .iter()
        .find(|link| selected(component, link))
    {
        return Some(PreparationPolicy::Refused(link.reason(&component.id)));
    }
    Some(
        match builds
            .get(&planned.task_id)
            .filter(|build| build.reads_only_own_sources(project, component))
        {
            Some(_) => PreparationPolicy::Independent,
            None => PreparationPolicy::Refused(format!("artifact preparation refused: {first}")),
        },
    )
}
/// Findings of the captured files stand; the check is incomplete and claims no whole-scope metric.
pub fn mark_uncaptured(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
    output: &mut ici_domain::AnalysisOutput,
) {
    let (missing, _, _) = uncaptured_of(prepared, planned, snapshot);
    if missing.is_empty() || planned.check.id == "cpp.compile" {
        return;
    }
    output.observation.measurements.clear();
    if output.observation.state == ici_domain::TaskState::Succeeded {
        output.observation.state = ici_domain::TaskState::Failed;
    }
    output.status = "ERROR".into();
    output.summary = missing[0].clone();
    output.observation.limitations.splice(0..0, missing.clone());
    if !output.extra.is_object() {
        output.extra = json!({});
    }
    output.extra["uncaptured_sources"] = json!(missing);
}
pub fn binary_limits(prepared: &PreparedPlan) -> BTreeMap<String, usize> {
    prepared
        .artifact_builds
        .values()
        .flat_map(|build| build.binary_inputs.iter())
        .map(|path| (path.clone(), 256 * 1024 * 1024))
        .chain(prepared.cpp.evidence_input_limits())
        .chain(
            python_binary_artifacts(prepared)
                .into_iter()
                .chain(python_wheels(prepared))
                .map(|path| (path, 256 * 1024 * 1024)),
        )
        .collect()
}
/// The wheels frozen legacy `python_compat` would inspect (`wheel_globs`, wheel_inputs.rs).
fn python_wheels(prepared: &PreparedPlan) -> BTreeSet<String> {
    prepared
        .plan
        .checks
        .iter()
        .filter(|check| check.check.id == "python.compat")
        .filter_map(|check| {
            let options = analysis_options(prepared, check, prepared.legacy_projection);
            match crate::wheel_inputs::for_options(&prepared.project.root, &options)? {
                crate::wheel_inputs::Discovery::Wheels(wheels) => Some(wheels),
                crate::wheel_inputs::Discovery::Refused(_) => None,
            }
        })
        .flatten()
        .collect()
}
fn python_binary_artifacts(prepared: &PreparedPlan) -> BTreeSet<String> {
    prepared
        .plan
        .checks
        .iter()
        .filter(|check| check.check.id == "python.binary-compat")
        .flat_map(|check| {
            let component = component(prepared, check);
            match crate::cpp_validation::abi_scope(&prepared.project, component) {
                Ok(Some(scope)) if scope.implicit && scope.targets.is_empty() => scope.artifacts,
                Ok(Some(scope)) => scope.targets,
                _ => vec![],
            }
        })
        .collect()
}
pub fn code_evidence_paths(prepared: &PreparedPlan) -> BTreeSet<String> {
    prepared
        .plan
        .checks
        .iter()
        .flat_map(|check| prepared.cpp.evidence_code_inputs_for(&check.task_id))
        .chain(
            prepared
                .legacy_lines
                .values()
                .flat_map(|scope| scope.inputs.iter().cloned()),
        )
        .chain(
            prepared
                .artifact_builds
                .values()
                .flat_map(|build| build.binary_inputs.iter().cloned()),
        )
        .chain(python_binary_artifacts(prepared))
        .collect()
}
pub struct InputVerification {
    pub stable: bool,
    /// Includes original physical code inputs retained through preparation rebinding.
    pub code_paths: BTreeSet<String>,
}
pub fn empty_snapshot() -> InputSnapshot {
    InputSnapshot {
        files: BTreeMap::new(),
        uncaptured: BTreeMap::new(),
        sources: BTreeSet::new(),
        configurations: BTreeSet::new(),
        digest: ici_domain::sha256("[]"),
        total_bytes: 0,
    }
}
pub fn component<'a>(prepared: &'a PreparedPlan, planned: &PlannedCheck) -> &'a Component {
    prepared
        .project
        .components
        .iter()
        .find(|component| component.id == planned.component_id)
        .expect("plan component belongs to resolved project")
}
/// Frozen `AnalysisUnit.variant`: the variant of the component's build, if it declares one.
fn unit_variant(prepared: &PreparedPlan, planned: &PlannedCheck) -> Option<String> {
    let build = component(prepared, planned).build.as_deref()?;
    prepared
        .project
        .builds
        .iter()
        .find(|candidate| candidate.id == build)
        .map(|candidate| candidate.variant.clone())
}
pub fn source_roots(component: &Component, workspace: &Path) -> Vec<String> {
    let mut roots = vec![];
    for directory in &component.source_dirs {
        let path = workspace.join(&component.root).join(directory);
        if path.is_dir()
            && let Ok(path) = path.canonicalize()
            && let Ok(relative) = path.strip_prefix(workspace)
        {
            roots.push(if relative.as_os_str().is_empty() {
                ".".into()
            } else {
                relative.to_string_lossy().into_owned()
            });
        }
    }
    if roots.is_empty() {
        roots.push(component.root.clone());
    }
    roots.dedup();
    roots
}
pub fn analysis_request(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
    legacy: bool,
) -> AnalysisRequest {
    let component = component(prepared, planned);
    let mut paths: Vec<_> = component
        .files
        .iter()
        .filter(|path| file_language(path) == Some(planned.check.language.as_str()))
        .cloned()
        .collect();
    if let Some(scope) = prepared.legacy_lines.get(&planned.task_id) {
        paths = scope.inputs.clone();
    }
    let mut inputs: Vec<_> = paths
        .iter()
        .filter_map(|path| snapshot.files.get(path).map(|file| file.input.clone()))
        .collect();
    let mut options = analysis_options(prepared, planned, legacy);
    if planned.check.id == "python.compat" {
        match crate::wheel_inputs::for_options(&prepared.project.root, &options) {
            Some(crate::wheel_inputs::Discovery::Wheels(wheels)) => {
                for path in wheels {
                    if let Some(file) = snapshot.files.get(&path)
                        && !inputs.iter().any(|input| input.path == path)
                    {
                        inputs.push(file.input.clone());
                    }
                }
            }
            Some(crate::wheel_inputs::Discovery::Refused(refusal)) => {
                options["wheel_discovery_error"] = refusal;
            }
            None => {}
        }
    }
    if matches!(
        planned.check.id.as_str(),
        "python.compat" | "python.compat-runtime"
    ) {
        let mut symlinked = vec![];
        for directory in [component.root.as_str(), "."] {
            for name in ["pyproject.toml", "setup.cfg", "setup.py"] {
                let path = if directory == "." {
                    name.into()
                } else {
                    format!("{directory}/{name}")
                };
                if name == "pyproject.toml"
                    && std::fs::symlink_metadata(prepared.project.root.join(&path))
                        .is_ok_and(|entry| entry.is_symlink())
                {
                    symlinked.push(path.clone());
                }
                if let Some(file) = snapshot.files.get(&path)
                    && !inputs.iter().any(|input| input.path == file.input.path)
                {
                    inputs.push(file.input.clone());
                }
            }
        }
        if !symlinked.is_empty() {
            options["metadata_symlinks"] = json!(symlinked);
        }
        if !legacy {
            options["metadata_location"] = json!(true);
        }
    }
    AnalysisRequest {
        task_id: planned.task_id.clone(),
        check_id: if planned.check.id == "python.annotations" {
            "python.type".into()
        } else {
            planned.check.id.clone()
        },
        component_id: planned.component_id.clone(),
        language: planned.check.language.clone(),
        inputs,
        options,
        target_version: "3.14".into(),
    }
}
pub fn analysis_options(prepared: &PreparedPlan, planned: &PlannedCheck, legacy: bool) -> Value {
    let component = component(prepared, planned);
    let paths: Vec<_> = component
        .files
        .iter()
        .filter(|path| file_language(path) == Some(planned.check.language.as_str()))
        .cloned()
        .collect();
    let mut options = engine_name(&planned.check.id)
        .and_then(|engine| {
            component
                .settings
                .get("engines")
                .and_then(|engines| engines.get(engine))
        })
        .cloned()
        .unwrap_or_else(|| json!({}));
    if !options.is_object() {
        options = json!({});
    }
    options["component_root"] = json!(component.root);
    options["source_roots"] = json!(source_roots(component, &prepared.project.root));
    options["source_paths"] = json!(paths);
    options["gated_paths"] = json!(paths);
    if legacy && let Some(scope) = prepared.legacy_lines.get(&planned.task_id) {
        options["source_paths"] = json!(scope.source_paths);
        options["gated_paths"] = json!(scope.gated_paths);
        options["line_scope_order"] = json!(scope.scope_order);
        if prepared.project.schema_family == ici_config::SchemaFamily::Legacy {
            options["extra_targets"] = json!(false);
        }
    }
    options["legacy_projection"] = json!(legacy);
    options
}
pub fn check_records(
    prepared: &PreparedPlan,
    observations: &[Observation],
    snapshot: &InputSnapshot,
    outputs: &BTreeMap<String, ici_domain::AnalysisOutput>,
    runner: &Runner,
    input_verification: &InputVerification,
    core_known: bool,
) -> Result<Vec<CheckRecord>> {
    let mut records = vec![];
    let mut code_inputs = code_evidence_paths(prepared);
    code_inputs.extend(input_verification.code_paths.iter().cloned());
    let settings_files = settings_files(prepared);
    let resolved_settings = resolved_settings(prepared)?;
    for planned in &prepared.plan.checks {
        let component = component(prepared, planned);
        let observation = observations
            .iter()
            .find(|observation| observation.task_id == planned.task_id)
            .ok_or_else(|| CliError(format!("missing observation for {}", planned.task_id)))?;
        let task = planned.task();
        let mut configuration = BTreeMap::new();
        if let Some(evidence) = prepared.native_configuration.get(&planned.task_id) {
            for (path, digest) in &evidence.files {
                configuration.insert(
                    workspace_reference(&path.to_string_lossy(), &prepared.project.root),
                    digest.clone(),
                );
            }
        }
        let configuration_digests = prepared
            .cpp
            .evidence_configuration_digests_for(&planned.task_id);
        for path in prepared.cpp.evidence_configurations_for(&planned.task_id) {
            if let Some(file) = snapshot.files.get(&path) {
                let digest = configuration_digests
                    .get(&path)
                    .unwrap_or(&file.digest)
                    .clone();
                configuration.insert(path, digest);
            }
        }
        let options = analysis_options(prepared, planned, prepared.legacy_projection);
        let mut context =
            json!({"options":options,"build":component.build,"externals":component.external});
        if let Some(configuration) = prepared.cpp.observed_configuration_for(&planned.task_id) {
            context["cpp_observed_configuration"] = configuration;
        }
        let mut files: BTreeSet<_> = component
            .files
            .iter()
            .filter(|file| file_language(file) == Some(planned.check.language.as_str()))
            .cloned()
            .collect();
        files.extend(prepared.cpp.evidence_code_inputs_for(&planned.task_id));
        if let Some(scope) = prepared.legacy_lines.get(&planned.task_id) {
            files.extend(scope.inputs.iter().cloned());
        }
        if let Some(plans) = prepared.validations.get(&planned.task_id) {
            context["validation"] = json!(
                plans
                    .iter()
                    .map(|plan| {
                        let mut options = json!(plan.context.options);
                        options["environment"] = json!(workspace_environment(
                            &plan.context.options.environment,
                            &prepared.project.root
                        ));
                        json!({"kind":plan.kind,"options":options,"sources":plan.context.source_files,"targets":plan.context.targets})
                    })
                    .collect::<Vec<_>>()
            );
            let invocations: Vec<_> = planned
                .tasks()
                .iter()
                .map(|task| {
                    let mut invocation = InvocationIdentity::new(
                        task,
                        &runner.environment(task),
                        &prepared.project.root,
                    );
                    for plan in plans {
                        invocation = invocation.with_owned_directory(
                            &plan.context.owned_run_dir,
                            &prepared.project.root,
                        );
                    }
                    for reference in &task.input_refs {
                        if reference.ends_with(".py")
                            && let Ok(relative) =
                                Path::new(reference).strip_prefix(&prepared.project.root)
                        {
                            files.insert(relative.to_string_lossy().into_owned());
                        }
                    }
                    invocation
                })
                .collect();
            context["steps"] = json!(invocations);
            let mut nodes = BTreeSet::new();
            if let Some(output) = outputs.get(&planned.task_id) {
                test_nodes(&output.extra, &mut nodes);
            }
            for plan in plans {
                // A shared suite step is `<check>.suite`, or a frozen next suite step
                // `<check>.<build>-ctest` of the check that ran it.
                if let Some(shared) = plan
                    .context
                    .options
                    .shared_test_task_id
                    .as_deref()
                    .and_then(|step| {
                        step.strip_suffix(".suite").or_else(|| {
                            outputs.keys().map(String::as_str).find(|check| {
                                step.strip_prefix(*check)
                                    .is_some_and(|rest| rest.starts_with('.'))
                            })
                        })
                    })
                    && let Some(output) = outputs.get(shared)
                {
                    test_nodes(&output.extra, &mut nodes);
                }
            }
            context["test_nodes"] = json!(nodes);
            context["resolved_settings"] = json!(resolved_settings);

            // A Python run is scoped by the configuration its tools read, not by every
            // configuration file in the project: a new ruff.toml does not move a pytest scope.
            let read = python_validation_configuration(plans);
            for path in &snapshot.configurations {
                // Binary evidence is captured for integrity, but its content is code,
                // not policy. Fixing an object must not change the comparison scope.
                if code_inputs.contains(path) || settings_files.contains(path) {
                    continue;
                }
                if let Some(read) = &read
                    && !read.contains(path)
                {
                    continue;
                }
                if let Some(file) = snapshot.files.get(path) {
                    configuration
                        .entry(path.clone())
                        .or_insert_with(|| file.digest.clone());
                }
            }
        }
        if matches!(
            planned.work,
            ici_application::plan::Work::Steps { .. }
                | ici_application::plan::Work::NativePrepare { .. }
        ) && !prepared.validations.contains_key(&planned.task_id)
        {
            context["steps"] = json!(
                planned
                    .tasks()
                    .iter()
                    .map(|task| InvocationIdentity::new(
                        task,
                        &runner.environment(task),
                        &prepared.project.root
                    ))
                    .collect::<Vec<_>>()
            );
        }
        if let Some(build) = prepared.artifact_builds.get(&planned.task_id) {
            context["artifact_scope"] =
                json!({"metadata":build.metadata,"output_paths":build.output_paths});
        }
        let units = planned.analysis_units();
        let variants = planned.variants();
        let scope = ScopeIdentity {
            check_id: planned.check.id.clone(),
            component_id: planned.component_id.clone(),
            language: planned.check.language.clone(),
            files: files.into_iter().collect(),
            source_roots: source_roots(component, &prepared.project.root),
            native_configuration: configuration,
            context,
            invocation: task
                .filter(|_| {
                    !matches!(
                        planned.work,
                        ici_application::plan::Work::Steps { .. }
                            | ici_application::plan::Work::NativePrepare { .. }
                    )
                })
                .map(|task| {
                    InvocationIdentity::new(task, &runner.environment(task), &prepared.project.root)
                }),
        };
        // A tool may read a configuration the snapshot cannot hold (above the project root, or
        // named by the run's environment outside it).
        let outside_configuration = prepared
            .validations
            .get(&planned.task_id)
            .into_iter()
            .flatten()
            .filter_map(|plan| plan.tool_configuration.as_ref())
            .any(|configuration| !configuration.unprovable.is_empty());
        let complete = !outside_configuration
            && input_verification.stable
            && core_known
            && observation.evidence_is_complete()
            && prepared.cpp.scope_complete(&planned.task_id)
            && !(matches!(planned.check.id.as_str(), "python.test" | "cpp.test")
                && outputs
                    .get(&planned.task_id)
                    .is_some_and(|output| test_errors(&output.extra)))
            && prepared
                .providers
                .get(&planned.task_id)
                .is_none_or(|provider| provider.scope_complete)
            // Next `cpp.diagnostics` providers refuse reuse only (closure unproven); their
            // evidence is per replayed unit. Every other C++ diagnostics provider keeps its
            // scope verdict.
            && (prepared.cpp.diagnostic_steps.contains_key(&planned.task_id)
                || prepared
                    .cpp
                    .diagnostics
                    .get(&planned.task_id)
                    .is_none_or(|providers| providers.iter().all(|provider| provider.scope_complete)));
        records.push(CheckRecord {
            task_id: planned.task_id.clone(),
            check_id: planned.check.id.clone(),
            component_id: planned.component_id.clone(),
            language: planned.check.language.clone(),
            scope_digest: scope
                .digest()
                .map_err(|error| CliError(error.to_string()))?,
            state: observation.state,
            evidence_complete: complete,
            analysis_unit_id: (units.len() == 1).then(|| units[0].clone()),
            // Frozen `check_scope`: the task's variant, else the single unit's build variant.
            variant: (variants.len() == 1)
                .then(|| variants[0].clone())
                .or_else(|| {
                    (units.len() == 1)
                        .then(|| unit_variant(prepared, planned))
                        .flatten()
                }),
        });
    }
    Ok(records)
}
/// The files ici read its settings from, whose meaning a validation scope takes from
/// `resolved_settings`: a comment or layout edit is not a policy change. Only the scope skips their
/// bytes; the snapshot keeps them for change detection and integrity. `pyproject.toml` stays
/// byte-exact because tools read it too.
fn settings_files(prepared: &PreparedPlan) -> BTreeSet<String> {
    prepared
        .project
        .configuration_files
        .iter()
        .filter(|path| path.file_name().is_none_or(|name| name != "pyproject.toml"))
        .filter_map(|path| path.strip_prefix(&prepared.project.root).ok())
        .map(|path| path.to_string_lossy().into_owned())
        .collect()
}
/// What those files resolve to, with a child file's content in its component's settings.
fn resolved_settings(prepared: &PreparedPlan) -> Result<String> {
    // `native_configs` lists every native tool configuration file found in the component. A
    // validation scope holds the bytes of the files its own tools read (`python_validation_
    // configuration`, or every configuration for C++), so a new ruff.toml must not move a
    // pytest scope through this list.
    let components: Vec<_> = prepared
        .project
        .components
        .iter()
        .map(|component| {
            let mut settings = component.settings.clone();
            if let Some(settings) = settings.as_object_mut() {
                settings.remove("native_configs");
            }
            json!({"id":component.id,"settings":settings})
        })
        .collect();
    ici_application::identity::digest(
        &json!({"effective":prepared.project.effective,"components":components}),
    )
    .map_err(|error| CliError(error.to_string()))
}
pub fn changed(
    before: &PreparedPlan,
    after: &PreparedPlan,
    old: &InputSnapshot,
    new: &InputSnapshot,
) -> bool {
    before.project.root != after.project.root
        || !old.difference(new).stable()
        || !old.uncaptured.keys().eq(new.uncaptured.keys())
        || before.tool_pins() != after.tool_pins()
        || before.cpp.configuration_identity() != after.cpp.configuration_identity()
        || before.plan.profile != after.plan.profile
        || ici_application::identity::policy_digest(&before.project).ok()
            != ici_application::identity::policy_digest(&after.project).ok()
        || before.plan.components != after.plan.components
        || before.plan.languages != after.plan.languages
        || before
            .tools
            .iter()
            .map(|(id, tool)| (id, &tool.digest))
            .collect::<BTreeMap<_, _>>()
            != after
                .tools
                .iter()
                .map(|(id, tool)| (id, &tool.digest))
                .collect::<BTreeMap<_, _>>()
        || before
            .native_configuration
            .iter()
            .map(|(id, evidence)| (id, &evidence.files))
            .collect::<BTreeMap<_, _>>()
            != after
                .native_configuration
                .iter()
                .map(|(id, evidence)| (id, &evidence.files))
                .collect::<BTreeMap<_, _>>()
}

fn test_nodes(value: &Value, nodes: &mut BTreeSet<String>) {
    if let Some(object) = value.as_object() {
        if let Some(outcomes) = object.get("test_outcomes").and_then(Value::as_object) {
            nodes.extend(
                outcomes
                    .iter()
                    .filter(|(_, verdict)| {
                        verdict.as_str().is_some_and(|verdict| {
                            matches!(verdict, "PASSED" | "FAILED" | "ERROR" | "XPASS")
                        })
                    })
                    .map(|(node, _)| node.clone()),
            );
        }
        for child in object.values() {
            if child.is_object() {
                test_nodes(child, nodes);
            }
        }
    }
}
fn test_errors(value: &Value) -> bool {
    value.as_object().is_some_and(|object| {
        object
            .get("test_outcomes")
            .and_then(Value::as_object)
            .is_some_and(|outcomes| outcomes.values().any(|verdict| verdict == "ERROR"))
            || object.values().any(test_errors)
    })
}

/// pytest 9 `findpaths.locate_config` candidates, in its order, in each directory it searches.
const PYTEST_CONFIGS: [&str; 7] = [
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
];
/// coverage 7 `config_files_to_try` after its first entry, which is `COVERAGE_RCFILE` when the
/// run's environment names one and `.coveragerc` otherwise. ici passes no `--rcfile`.
const COVERAGE_SHARED_CONFIGS: [&str; 4] =
    [".coveragerc.toml", "setup.cfg", "tox.ini", "pyproject.toml"];
/// pytest options whose next argument is their value, never a path pytest searches from.
const PYTEST_VALUE_OPTIONS: [&str; 22] = [
    "-p",
    "-k",
    "-m",
    "-o",
    "--override-ini",
    "-W",
    "--pythonwarnings",
    "--rootdir",
    "--confcutdir",
    "--basetemp",
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "--junit-xml",
    "--junitxml",
    "--log-file",
    "--tb",
    "--maxfail",
    "--durations",
    "--capture",
    "--import-mode",
    "-r",
];
/// pytest options that take no value: the argument after one is pytest's own.
const PYTEST_FLAGS: [&str; 24] = [
    "-v",
    "-vv",
    "-q",
    "-qq",
    "-x",
    "-s",
    "-l",
    "-ra",
    "-rA",
    "--verbose",
    "--quiet",
    "--exitfirst",
    "--collect-only",
    "--co",
    "--lf",
    "--last-failed",
    "--ff",
    "--failed-first",
    "--sw",
    "--stepwise",
    "--strict-markers",
    "--strict-config",
    "--disable-warnings",
    "--showlocals",
];
/// Marks a Python validation whose pytest may read a configuration above the project root.
pub const OUTSIDE_PYTEST_CONFIG: &str = "pytest may read a configuration file outside the project";
/// Value-less reasons: an environment value is never written into a result.
pub const OUTSIDE_COVERAGE_RCFILE: &str =
    "COVERAGE_RCFILE names a coverage configuration outside the project";
pub const OUTSIDE_PYTEST_ADDOPTS_CONFIG: &str =
    "PYTEST_ADDOPTS selects a pytest configuration outside the project";
pub const UNREAD_PYTEST_ADDOPTS: &str =
    "PYTEST_ADDOPTS cannot be split as pytest splits it (shlex)";
pub const AMBIGUOUS_PYTEST_ADDOPTS: &str = "PYTEST_ADDOPTS passes a path after an option ici does not know, which may be a pytest argument";

/// Whether pytest's `locate_config` takes this file as its configuration: `Some(true)` ends the
/// search there, `Some(false)` passes it, `None` (unreadable, oversized, malformed) cannot be
/// decided, so the search continues past it and the file stays among the inputs.
fn pytest_config(path: &Path) -> Option<bool> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("");
    let Ok(metadata) = std::fs::metadata(path) else {
        return Some(false);
    };
    if !metadata.is_file() {
        return Some(false);
    }
    if matches!(
        name,
        "pytest.toml" | ".pytest.toml" | "pytest.ini" | ".pytest.ini"
    ) {
        return Some(true);
    }
    if metadata.len() > 1 << 20 {
        return None;
    }
    let text = std::fs::read_to_string(path).ok()?;
    match name {
        "pyproject.toml" => {
            let document = text.parse::<toml_edit::DocumentMut>().ok()?;
            let Some(pytest) = document.get("tool").and_then(|tool| tool.get("pytest")) else {
                return Some(false);
            };
            // `[tool.pytest]` with its own keys (toml mode) or `[tool.pytest.ini_options]`.
            let table = pytest.as_table_like()?;
            Some(table.iter().next().is_some())
        }
        "tox.ini" => ini_section(&text, &["pytest"]),
        // `[pytest]` in setup.cfg makes pytest fail there: the search ends either way.
        "setup.cfg" => ini_section(&text, &["tool:pytest", "pytest"]),
        _ => Some(false),
    }
}

/// iniconfig's section rule: a line starting with `[` whose text before any `#`/`;` ends with
/// `]`. A name that matches only after trimming spaces is not decided.
fn ini_section(text: &str, names: &[&str]) -> Option<bool> {
    let mut undecided = false;
    for line in text.lines() {
        if !line.starts_with('[') {
            continue;
        }
        let mut header = line.trim_end();
        for comment in ['#', ';'] {
            header = header.split(comment).next().unwrap_or("").trim_end();
        }
        let Some(name) = header
            .strip_prefix('[')
            .and_then(|rest| rest.strip_suffix(']'))
        else {
            continue;
        };
        if names.contains(&name) {
            return Some(true);
        }
        undecided |= names.contains(&name.trim());
    }
    (!undecided).then_some(false)
}

/// `path` resolved against `cwd` without touching the file system, with `.` and `..` removed.
fn lexical(cwd: &Path, path: &Path) -> PathBuf {
    let mut resolved = PathBuf::new();
    for component in cwd.join(path).components() {
        match component {
            std::path::Component::ParentDir => {
                resolved.pop();
            }
            std::path::Component::CurDir => {}
            other => resolved.push(other),
        }
    }
    resolved
}

/// The project-relative path of a file a tool was told to read, or `None` when it lies outside
/// the project, also through a symbolic link.
fn contained(root: &Path, cwd: &Path, path: &Path) -> Option<String> {
    let resolved = lexical(cwd, path);
    let relative = resolved
        .strip_prefix(root)
        .ok()?
        .to_string_lossy()
        .into_owned();
    if resolved.exists() {
        let physical = resolved.canonicalize().ok()?;
        let root = root.canonicalize().ok()?;
        if !physical.starts_with(&root) {
            return None;
        }
    }
    (!relative.is_empty()).then_some(relative)
}

/// What `PYTEST_ADDOPTS` gives one pytest run, split as pytest splits it (`shlex.split`):
/// an explicit configuration (`-c`/`--config-file`, the last one wins) and the arguments pytest
/// takes as paths to search from. A path after an option ici does not know may be that option's
/// value or pytest's argument; that run's search is then not provable.
struct Addopts {
    explicit: Option<String>,
    arguments: Vec<String>,
}
fn pytest_addopts(value: &str, cwd: &Path, unprovable: &mut BTreeSet<String>) -> Addopts {
    let mut addopts = Addopts {
        explicit: None,
        arguments: vec![],
    };
    let Ok(tokens) = ici_cpp::split_posix(value) else {
        unprovable.insert(UNREAD_PYTEST_ADDOPTS.into());
        return addopts;
    };
    let mut positional = false;
    let mut index = 0;
    while index < tokens.len() {
        let token = tokens[index].as_str();
        index += 1;
        if positional || !token.starts_with('-') || token == "-" {
            let path = token.split("::").next().unwrap_or(token);
            let previous = index
                .checked_sub(2)
                .map(|previous| tokens[previous].as_str());
            let unknown_option = !positional
                && previous.is_some_and(|option| {
                    option.starts_with('-')
                        && !option.contains('=')
                        && !PYTEST_FLAGS.contains(&option)
                        && option != "--"
                });
            if unknown_option && cwd.join(path).exists() {
                unprovable.insert(AMBIGUOUS_PYTEST_ADDOPTS.into());
            }
            addopts.arguments.push(token.to_owned());
            continue;
        }
        if token == "--" {
            positional = true;
        } else if token == "-c" || token == "--config-file" {
            addopts.explicit = tokens.get(index).cloned();
            index += 1;
        } else if let Some(file) = token.strip_prefix("--config-file=") {
            addopts.explicit = Some(file.to_owned());
        } else if let Some(file) = token
            .strip_prefix("-c")
            .filter(|_| !token.starts_with("--"))
        {
            addopts.explicit = Some(file.to_owned());
        } else if PYTEST_VALUE_OPTIONS.contains(&token) {
            index += 1;
        }
    }
    addopts
}

/// The common ancestor pytest searches from (`get_common_ancestor`) and the directories of its
/// existing path arguments (`get_dirs_from_args`).
fn pytest_dirs(cwd: &Path, arguments: &[String]) -> (PathBuf, Vec<PathBuf>) {
    let dirs: Vec<PathBuf> = arguments
        .iter()
        .map(|argument| {
            lexical(
                cwd,
                Path::new(argument.split("::").next().unwrap_or(argument)),
            )
        })
        .filter(|path| path.exists())
        .map(|path| {
            if path.is_dir() {
                path
            } else {
                path.parent().map_or(path.clone(), Path::to_path_buf)
            }
        })
        .collect();
    let mut ancestor: Option<PathBuf> = None;
    for dir in &dirs {
        ancestor = Some(match ancestor {
            None => dir.clone(),
            Some(current) => current
                .ancestors()
                .find(|candidate| dir.starts_with(candidate))
                .map_or(current.clone(), Path::to_path_buf),
        });
    }
    (ancestor.unwrap_or_else(|| cwd.to_owned()), dirs)
}

/// The pytest and coverage runs of one Python validation: where they run, what they are given,
/// and the environment each runs with (`Runner::environment`).
pub struct PythonRuns<'a> {
    pub root: &'a Path,
    pub cwd: &'a Path,
    pub targets: &'a [String],
    pub pytest: Vec<BTreeMap<String, String>>,
    pub coverage: Vec<BTreeMap<String, String>>,
}

/// The project files a Python validation's tools may read as configuration and why it may read
/// one the snapshot cannot hold, decided from the environment each run gets (the snapshot the
/// runner executes it with). `None` for a plan that is not a Python run.
pub fn python_tool_configuration(
    root: &Path,
    plan: &ici_validation::ValidationPlan,
    runner: &Runner,
) -> Option<ici_validation::ToolConfiguration> {
    use ici_validation::ValidationKind;
    let coverage = match plan.kind {
        ValidationKind::PythonTest | ValidationKind::PythonSanitize => false,
        ValidationKind::PythonCoverage => true,
        ValidationKind::PythonRuntime => {
            return Some(ici_validation::ToolConfiguration {
                read: BTreeSet::from(["pyproject.toml".to_owned()]),
                unprovable: vec![],
            });
        }
        _ => return None,
    };
    let runs = |module: &str| {
        plan.tasks
            .iter()
            .filter(|task| {
                task.argv
                    .windows(2)
                    .any(|pair| pair[0] == "-m" && pair[1] == module)
            })
            .map(|task| runner.environment(task))
            .collect::<Vec<_>>()
    };
    // A coverage plan may run on the test check's shared pytest suite (`shared_test_task_id`),
    // planned from the same request: its pytest reads its configuration with that environment.
    let mut pytest = runs("pytest");
    if pytest.is_empty() {
        let mut suite = ici_domain::TaskSpec::command(
            "shared-suite",
            "pytest",
            vec!["pytest".into()],
            plan.context.cwd.to_string_lossy(),
        );
        suite.env_overlay = plan.context.options.environment.clone();
        pytest.push(runner.environment(&suite));
    }
    Some(python_configuration(&PythonRuns {
        root,
        cwd: &plan.context.cwd,
        targets: &plan.context.targets,
        pytest,
        coverage: if coverage { runs("coverage") } else { vec![] },
    }))
}

/// pytest 9 `determine_setup`/`locate_config` and coverage 7 `config_files_to_try`, followed as a
/// superset: every candidate on the paths pytest may walk is kept, present or not, and a walk
/// stops only at a file pytest certainly takes. This is not pytest's own resolution; the
/// differential test compares it with the file the real tools chose.
pub fn python_configuration(runs: &PythonRuns) -> ici_validation::ToolConfiguration {
    let (root, cwd) = (runs.root, runs.cwd);
    let relative = |path: &Path| {
        path.strip_prefix(root)
            .ok()
            .map(|path| path.to_string_lossy().into_owned())
    };
    let mut read = BTreeSet::new();
    let mut unprovable = BTreeSet::new();
    for environment in &runs.coverage {
        match environment
            .get("COVERAGE_RCFILE")
            .filter(|value| !value.is_empty() && *value != ".coveragerc")
        {
            Some(rcfile) => match contained(root, cwd, Path::new(rcfile)) {
                Some(path) => {
                    read.insert(path);
                }
                None => {
                    unprovable.insert(OUTSIDE_COVERAGE_RCFILE.to_owned());
                }
            },
            None => read.extend(relative(&cwd.join(".coveragerc"))),
        }
        read.extend(
            COVERAGE_SHARED_CONFIGS
                .iter()
                .filter_map(|name| relative(&cwd.join(name))),
        );
    }
    for environment in &runs.pytest {
        let addopts = environment
            .get("PYTEST_ADDOPTS")
            .map(|value| pytest_addopts(value, cwd, &mut unprovable))
            .unwrap_or(Addopts {
                explicit: None,
                arguments: vec![],
            });
        // `PYTEST_ADDOPTS` comes before the run's own arguments; pytest searches from all.
        let arguments: Vec<String> = addopts
            .arguments
            .iter()
            .chain(runs.targets)
            .cloned()
            .collect();
        let (ancestor, dirs) = pytest_dirs(cwd, &arguments);
        if let Some(explicit) = addopts.explicit {
            match contained(root, cwd, Path::new(&explicit)) {
                Some(path) => {
                    read.insert(path);
                }
                None => {
                    unprovable.insert(OUTSIDE_PYTEST_ADDOPTS_CONFIG.to_owned());
                }
            }
            continue;
        }
        let mut found = false;
        for directory in ancestor
            .ancestors()
            .take_while(|directory| directory.starts_with(root))
        {
            for name in PYTEST_CONFIGS {
                let path = directory.join(name);
                read.extend(relative(&path));
                found |= pytest_config(&path) == Some(true);
            }
            if found {
                break;
            }
        }
        if found {
            continue;
        }
        // Nothing there: pytest also tries each argument's own directory, then goes on above
        // the project, where the snapshot cannot follow.
        for dir in &dirs {
            for directory in dir
                .ancestors()
                .take_while(|directory| directory.starts_with(&ancestor) && *directory != ancestor)
            {
                read.extend(
                    PYTEST_CONFIGS
                        .iter()
                        .filter_map(|name| relative(&directory.join(name))),
                );
            }
        }
        let outside: Vec<_> = root
            .ancestors()
            .skip(1)
            .flat_map(|directory| PYTEST_CONFIGS.iter().map(move |name| directory.join(name)))
            .filter(|path| pytest_config(path) != Some(false))
            .collect();
        if !outside.is_empty() {
            unprovable.insert(format!(
                "{OUTSIDE_PYTEST_CONFIG}: {}",
                outside
                    .iter()
                    .map(|path| path.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
    }
    ici_validation::ToolConfiguration {
        read,
        unprovable: unprovable.into_iter().collect(),
    }
}

/// The configuration files a check's Python runs read, by project path, when every plan of the
/// check is a Python one; `None` keeps every configuration.
fn python_validation_configuration(
    plans: &[ici_validation::ValidationPlan],
) -> Option<BTreeSet<String>> {
    let mut read = BTreeSet::new();
    for plan in plans {
        read.extend(plan.tool_configuration.as_ref()?.read.iter().cloned());
    }
    Some(read)
}
