use crate::{ApplicationError, Result, request::RunRequest};
use ici_config::{Component, ResolvedProject, SchemaFamily};
use ici_domain::{CheckDefinition, Profile, TaskSpec, Validate, builtin_catalog};
use serde::{Deserialize, Serialize};

/// Every selected check must be bound explicitly; a null registry tool never implies internal work.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Work {
    Internal,
    Process {
        task: Box<TaskSpec>,
        /// Findings remain measured; required advisory work must still complete.
        #[serde(default)]
        advisory: bool,
    },
    /// Ordered native steps behind one logical check. Provider exit contracts decide whether a
    /// completed failing test may feed coverage; no implicit work is hidden from plan/doctor.
    Steps {
        tasks: Vec<TaskSpec>,
        #[serde(default)]
        advisory: bool,
    },
    Blocked {
        reason: String,
    },
}
impl Work {
    pub fn process(task: TaskSpec) -> Self {
        Self::Process {
            task: Box::new(task),
            advisory: false,
        }
    }
    pub fn process_advisory(task: TaskSpec) -> Self {
        Self::Process {
            task: Box::new(task),
            advisory: true,
        }
    }
    pub fn steps(tasks: Vec<TaskSpec>, advisory: bool) -> Self {
        Self::Steps { tasks, advisory }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlannedCheck {
    pub component_id: String,
    pub task_id: String,
    pub check: CheckDefinition,
    pub work: Work,
}
impl PlannedCheck {
    pub fn task(&self) -> Option<&TaskSpec> {
        self.tasks().first()
    }
    pub fn tasks(&self) -> &[TaskSpec] {
        match &self.work {
            Work::Process { task, .. } => std::slice::from_ref(task.as_ref()),
            Work::Steps { tasks, .. } => tasks,
            _ => &[],
        }
    }
    pub fn analysis_units(&self) -> Vec<String> {
        let mut units: Vec<_> = self
            .tasks()
            .iter()
            .flat_map(|task| task.analysis_unit_ids.iter().cloned())
            .collect();
        if units.is_empty() {
            units.push(format!("{}.{}", self.component_id, self.check.language));
        }
        units.sort();
        units.dedup();
        units
    }
    pub fn variants(&self) -> Vec<String> {
        let mut variants: Vec<_> = self
            .tasks()
            .iter()
            .filter_map(|task| task.variant.clone())
            .collect();
        variants.sort();
        variants.dedup();
        variants
    }
    pub fn blocked(&self) -> Option<&str> {
        match &self.work {
            Work::Blocked { reason } => Some(reason),
            _ => None,
        }
    }
    pub fn advisory(&self) -> bool {
        matches!(
            self.work,
            Work::Process { advisory: true, .. } | Work::Steps { advisory: true, .. }
        )
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Omitted {
    pub component_id: String,
    pub check_id: String,
    pub reason: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    pub profile: Profile,
    pub components: Vec<String>,
    pub languages: Vec<String>,
    pub checks: Vec<PlannedCheck>,
    pub omitted: Vec<Omitted>,
    pub coverage_gaps: Vec<String>,
    pub limitations: Vec<String>,
}

/// Bindings inspect declared inputs and tool existence only; callers must not run version probes.
pub fn plan(
    project: &ResolvedProject,
    request: &RunRequest,
    mut bind: impl FnMut(&Component, &CheckDefinition, &str) -> Result<Work>,
) -> Result<Plan> {
    let scope = request.scope(project)?;
    let profile = request.profile.unwrap_or(project.profile);
    let catalog = builtin_catalog();
    let mut result = Plan {
        profile,
        components: scope.iter().map(|c| c.id.clone()).collect(),
        languages: request.requested_languages(&scope),
        coverage_gaps: if request.require_full {
            request.coverage_gaps(project, &scope)
        } else {
            vec![]
        },
        checks: vec![],
        omitted: vec![],
        limitations: project.limitations.clone(),
    };
    if request.static_only {
        result.limitations.push(
            "static selection: tests, runtime checks and build artifact checks omitted".into(),
        );
    }
    for component in scope {
        let mut selected = 0;
        for definition in &catalog.checks {
            let applies = component.languages.contains(&definition.language)
                || (definition.id == "integration" && !component.integrations.is_empty());
            if !applies || !request.wants(definition) {
                continue;
            }
            let (enabled, required) = configured_check(project, component, definition);
            let omitted = if !definition.profiles.contains(&profile) {
                Some(format!(
                    "not in profile '{}'",
                    serde_json::to_value(profile)
                        .expect("enum")
                        .as_str()
                        .expect("string")
                ))
            } else if !enabled {
                Some("disabled".into())
            } else {
                None
            };
            if let Some(reason) = omitted {
                result.omitted.push(Omitted {
                    component_id: component.id.clone(),
                    check_id: definition.id.clone(),
                    reason,
                });
                continue;
            }
            let mut check = definition.clone();
            check.required = required;
            for capability in check.needs.iter_mut().chain(check.provides.iter_mut()) {
                *capability = format!("{}.{capability}", component.id);
            }
            let task_id = format!("{}.{}", component.id, check.id);
            let has_sources = check.language == "integration"
                || component
                    .files
                    .iter()
                    .any(|f| file_language(f) == Some(check.language.as_str()));
            let work = if has_sources {
                bind(component, &check, &task_id)?
            } else {
                Work::Blocked {
                    reason: format!("no sources in scope for {}", check.language),
                }
            };
            let work = match work {
                Work::Process { task, advisory } => {
                    task.validate()
                        .map_err(|e| ApplicationError(e.to_string()))?;
                    if task.id != task_id {
                        return Err(ApplicationError(format!(
                            "{task_id}: process task identity disagrees"
                        )));
                    }
                    if profile == Profile::Fast && task.mutating() {
                        Work::Blocked {
                            reason: "profile 'fast' does not run prepare tasks".into(),
                        }
                    } else {
                        Work::Process { task, advisory }
                    }
                }
                Work::Steps { tasks, advisory } => {
                    validate_steps(&task_id, &tasks)?;
                    if profile == Profile::Fast && tasks.iter().any(TaskSpec::mutating) {
                        Work::Blocked {
                            reason: "profile 'fast' does not run prepare tasks".into(),
                        }
                    } else {
                        Work::Steps { tasks, advisory }
                    }
                }
                Work::Blocked { reason } if reason.trim().is_empty() => {
                    return Err(ApplicationError(format!(
                        "{task_id}: blocked work needs a reason"
                    )));
                }
                other => other,
            };
            result.checks.push(PlannedCheck {
                component_id: component.id.clone(),
                task_id,
                check,
                work,
            });
            selected += 1;
        }
        if selected == 0 {
            result.limitations.push(format!(
                "{}: no checks selected for its requested languages and profile",
                component.id
            ));
        }
    }
    if result.checks.is_empty() {
        return Err(ApplicationError(
            "no component selected a check; a run that checks nothing cannot pass".into(),
        ));
    }
    Ok(result)
}

pub fn validate_steps(logical_id: &str, tasks: &[TaskSpec]) -> Result<()> {
    if tasks.is_empty() {
        return Err(ApplicationError(format!(
            "{logical_id}: native steps must not be empty"
        )));
    }
    let mut positions = std::collections::BTreeMap::new();
    for (index, task) in tasks.iter().enumerate() {
        task.validate()
            .map_err(|error| ApplicationError(error.to_string()))?;
        if task.id != logical_id && !task.id.starts_with(&format!("{logical_id}.")) {
            return Err(ApplicationError(format!(
                "{} is not owned by logical check {logical_id}",
                task.id
            )));
        }
        if positions.insert(&task.id, index).is_some() {
            return Err(ApplicationError(format!(
                "duplicate native step {}",
                task.id
            )));
        }
    }
    for (index, task) in tasks.iter().enumerate() {
        if task.depends_on.iter().any(|dependency| {
            positions
                .get(dependency)
                .is_some_and(|position| *position >= index)
        }) {
            return Err(ApplicationError(format!(
                "{} depends on a later ordered native step",
                task.id
            )));
        }
    }
    Ok(())
}

/// Honor legacy engine controls and explicit sparse compatibility keys through one selection path.
/// Built-in legacy defaults do not silently disable newly defined next checks.
pub fn configured_check(
    project: &ResolvedProject,
    component: &Component,
    definition: &CheckDefinition,
) -> (bool, bool) {
    let mut enabled = true;
    let mut required = definition.required;
    if let Some(engine) = engine_name(&definition.id) {
        let settings = component
            .settings
            .get("engines")
            .and_then(|engines| engines.get(engine));
        let declared = |field: &str| {
            project.schema_family == SchemaFamily::Legacy
                || project
                    .origins
                    .get(&format!("engines.{engine}.{field}"))
                    .is_some_and(|origin| origin.layer != "defaults")
        };
        if declared("enabled") {
            enabled = settings
                .and_then(|settings| settings.get("enabled"))
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(true);
        }
        if declared("required") {
            required = settings
                .and_then(|settings| settings.get("required"))
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(required);
        }
    }
    if let Some(setting) = component
        .checks
        .get(&definition.id)
        .or_else(|| project.checks.get(&definition.id))
    {
        enabled = setting.enabled_or(enabled);
        required = setting.required_or(required);
    }
    (enabled, required)
}

pub fn engine_name(check_id: &str) -> Option<&'static str> {
    match check_id {
        "python.line" | "cpp.line" => Some("line"),
        "python.lint" | "python.format" | "cpp.diagnostics" | "cpp.tidy" | "rust.clippy"
        | "javascript.lint" | "typescript.lint" => Some("lint"),
        "python.type" | "typescript.type" => Some("type"),
        "python.test" | "python.coverage" | "cpp.test" | "cpp.coverage" => Some("test"),
        "python.compat" | "python.compat-runtime" => Some("python_compat"),
        "python.complexity" | "cpp.complexity" => Some("complexity"),
        "python.cognitive" | "cpp.cognitive" => Some("cognitive"),
        "python.cycle" | "cpp.cycle" => Some("cycle"),
        "python.dup" | "cpp.dup" => Some("dup"),
        "python.exception" | "cpp.exception" => Some("exception"),
        "python.resource" => Some("resource"),
        "python.security" => Some("security"),
        "python.dead" => Some("dead"),
        "cpp.compile" => Some("compile_db"),
        "cpp.artifact" => Some("build"),
        "cpp.sanitize" => Some("sanitize"),
        "cpp.tsan" => Some("thread_sanitize"),
        "cpp.binary-compat" => Some("binary_compat"),
        "integration" => Some("integration"),
        _ => None,
    }
}

pub fn file_language(path: &str) -> Option<&'static str> {
    match path.rsplit('.').next()? {
        "py" => Some("python"),
        "rs" => Some("rust"),
        "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
        "ts" | "tsx" | "mts" | "cts" => Some("typescript"),
        "c" | "cc" | "cpp" | "cxx" | "h" | "hh" | "hpp" | "hxx" => Some("cpp"),
        _ => None,
    }
}
