use crate::{
    commands::{CliError, Result},
    planning::PreparedPlan,
};
use ici_application::{
    identity::{InvocationIdentity, ScopeIdentity, workspace_reference},
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
    let path_limits = binary_limits(prepared);
    let mut sources: BTreeSet<_> = prepared
        .project
        .components
        .iter()
        .filter(|component| prepared.plan.components.contains(&component.id))
        .flat_map(|component| component.files.iter().cloned())
        .collect();
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
        configurations.extend(context.database_path.iter().cloned());
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
    InputSnapshot::take_with_path_limits(
        &prepared.project.root,
        &sources.into_iter().collect::<Vec<_>>(),
        &configurations.into_iter().collect::<Vec<_>>(),
        InputLimits::default(),
        &path_limits,
    )
    .map_err(|error| CliError(error.to_string()))
}
pub fn binary_limits(prepared: &PreparedPlan) -> BTreeMap<String, usize> {
    prepared
        .artifact_builds
        .values()
        .flat_map(|build| build.binary_inputs.iter())
        .map(|path| (path.clone(), 256 * 1024 * 1024))
        .collect()
}
pub fn empty_snapshot() -> InputSnapshot {
    InputSnapshot {
        files: BTreeMap::new(),
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
    let paths: Vec<_> = component
        .files
        .iter()
        .filter(|path| file_language(path) == Some(planned.check.language.as_str()))
        .cloned()
        .collect();
    let mut inputs: Vec<_> = paths
        .iter()
        .filter_map(|path| snapshot.files.get(path).map(|file| file.input.clone()))
        .collect();
    let options = analysis_options(prepared, planned, legacy);
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
        check_id: planned.check.id.clone(),
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
    options["legacy_projection"] = json!(legacy);
    options
}
pub fn check_records(
    prepared: &PreparedPlan,
    observations: &[Observation],
    snapshot: &InputSnapshot,
    outputs: &BTreeMap<String, ici_domain::AnalysisOutput>,
    runner: &Runner,
    inputs_stable: bool,
    core_known: bool,
) -> Result<Vec<CheckRecord>> {
    let mut records = vec![];
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
        let options = analysis_options(prepared, planned, prepared.legacy_projection);
        let mut context =
            json!({"options":options,"build":component.build,"externals":component.external});
        let mut files: BTreeSet<_> = component
            .files
            .iter()
            .filter(|file| file_language(file) == Some(planned.check.language.as_str()))
            .cloned()
            .collect();
        if let Some(plans) = prepared.validations.get(&planned.task_id) {
            context["validation"]=json!(plans.iter().map(|plan|json!({"kind":plan.kind,"options":plan.context.options,"sources":plan.context.source_files,"targets":plan.context.targets})).collect::<Vec<_>>());
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
                if let Some(shared) = plan
                    .context
                    .options
                    .shared_test_task_id
                    .as_deref()
                    .and_then(|step| step.strip_suffix(".suite"))
                    && let Some(output) = outputs.get(shared)
                {
                    test_nodes(&output.extra, &mut nodes);
                }
            }
            context["test_nodes"] = json!(nodes);

            for path in &snapshot.configurations {
                if let Some(file) = snapshot.files.get(path) {
                    configuration.insert(path.clone(), file.digest.clone());
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
        let complete = inputs_stable
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
            && prepared
                .cpp
                .diagnostics
                .get(&planned.task_id)
                .is_none_or(|providers| providers.iter().all(|provider| provider.scope_complete));
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
            variant: (variants.len() == 1).then(|| variants[0].clone()),
        });
    }
    Ok(records)
}
pub fn changed(
    before: &PreparedPlan,
    after: &PreparedPlan,
    old: &InputSnapshot,
    new: &InputSnapshot,
) -> bool {
    before.project.root != after.project.root
        || !old.difference(new).stable()
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
