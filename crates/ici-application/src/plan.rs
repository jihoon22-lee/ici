use crate::{ApplicationError, Result, request::RunRequest};
use ici_config::{Component, ResolvedProject};
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
        match &self.work {
            Work::Process { task, .. } => Some(task),
            _ => None,
        }
    }
    pub fn blocked(&self) -> Option<&str> {
        match &self.work {
            Work::Blocked { reason } => Some(reason),
            _ => None,
        }
    }
    pub fn advisory(&self) -> bool {
        matches!(self.work, Work::Process { advisory: true, .. })
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
            let setting = component
                .checks
                .get(&definition.id)
                .or_else(|| project.checks.get(&definition.id));
            let omitted = if !definition.profiles.contains(&profile) {
                Some(format!(
                    "not in profile '{}'",
                    serde_json::to_value(profile)
                        .expect("enum")
                        .as_str()
                        .expect("string")
                ))
            } else if setting.is_some_and(|s| !s.enabled) {
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
            if let Some(setting) = setting {
                check.required = setting.required_or(check.required);
            }
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
