use crate::{
    commands::{CliError, Result},
    planning::PreparedPlan,
};
use ici_application::{
    identity::{InvocationIdentity, ScopeIdentity, workspace_environment, workspace_reference},
    plan::{PlannedCheck, engine_name, file_language},
    snapshot::{InputLimits, InputSnapshot},
};
use ici_config::Component;
use ici_domain::{AnalysisRequest, CheckRecord, Observation};
use ici_execution::Runner;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
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
    let retained = |path: &&String| {
        !generated_roots
            .iter()
            .any(|root| Path::new(path).starts_with(root))
    };
    sources.extend(original.sources.iter().filter(retained).cloned());
    configurations.extend(original.configurations.iter().filter(retained).cloned());
    InputSnapshot::take_partial_with_path_limits(
        &prepared.project.root,
        &sources.into_iter().collect::<Vec<_>>(),
        &configurations.into_iter().collect::<Vec<_>>(),
        InputLimits::default(),
        &path_limits,
    )
    .map_err(|error| CliError(error.to_string()))
}
/// The selected sources of a check that the snapshot left unread.
fn uncaptured_of(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
) -> (Vec<String>, usize) {
    let component = component(prepared, planned);
    let mut paths: Vec<&String> = component
        .files
        .iter()
        .filter(|path| file_language(path) == Some(planned.check.language.as_str()))
        .collect();
    if let Some(scope) = prepared.legacy_lines.get(&planned.task_id) {
        paths = scope.inputs.iter().collect();
    }
    let missing: Vec<String> = paths
        .iter()
        .filter_map(|path| snapshot.uncaptured.get(*path))
        .map(|source| source.reason())
        .collect();
    let captured = paths.len() - missing.len();
    (missing, captured)
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
        return Some(format!("artifact preparation refused: {}", first.reason()));
    }
    if matches!(
        planned.check.id.as_str(),
        "cpp.compile" | "python.compile-db"
    ) {
        return None;
    }
    let (missing, captured) = uncaptured_of(prepared, planned, snapshot);
    let reason = missing.into_iter().next()?;
    let per_file = matches!(
        planned.check.id.rsplit('.').next().unwrap_or(""),
        "line" | "complexity" | "cognitive" | "exception" | "security" | "resource"
    );
    let unit_steps = prepared.cpp.diagnostic_steps.contains_key(&planned.task_id);
    (!(unit_steps || per_file && captured > 0)).then_some(reason)
}
/// Findings of the captured files stand; the check is incomplete and claims no whole-scope metric.
pub fn mark_uncaptured(
    prepared: &PreparedPlan,
    planned: &PlannedCheck,
    snapshot: &InputSnapshot,
    output: &mut ici_domain::AnalysisOutput,
) {
    let (missing, _) = uncaptured_of(prepared, planned, snapshot);
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
        for directory in [component.root.as_str(), "."] {
            for name in ["pyproject.toml", "setup.cfg", "setup.py"] {
                let path = if directory == "." {
                    name.into()
                } else {
                    format!("{directory}/{name}")
                };
                if let Some(file) = snapshot.files.get(&path)
                    && !inputs.iter().any(|input| input.path == file.input.path)
                {
                    inputs.push(file.input.clone());
                }
            }
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

            for path in &snapshot.configurations {
                // Binary evidence is captured for integrity, but its content is code,
                // not policy. Fixing an object must not change the comparison scope.
                if code_inputs.contains(path) || settings_files.contains(path) {
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
        let complete = input_verification.stable
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
                .is_none_or(|provider| provider.scope_complete);
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
    let components: Vec<_> = prepared
        .project
        .components
        .iter()
        .map(|component| json!({"id":component.id,"settings":component.settings}))
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
