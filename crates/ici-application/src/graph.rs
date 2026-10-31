use crate::{
    ApplicationError, Result,
    plan::{PlannedCheck, Work, validate_steps},
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct TaskGraph {
    pub edges: BTreeMap<String, Vec<String>>,
    pub blocked: BTreeMap<String, String>,
    pub layers: Vec<Vec<String>>,
}
impl TaskGraph {
    /// Keep check identities until execution. The runner alone can verify the inherited environment
    /// and executable bytes needed to share work safely.
    pub fn build(checks: &[PlannedCheck]) -> Result<Self> {
        let mut graph = Self::default();
        let mut ids = BTreeSet::new();
        let mut producers = BTreeMap::new();
        let mut outputs = BTreeMap::new();
        let mut step_owners = BTreeMap::new();
        for planned in checks {
            if matches!(
                planned.work,
                Work::Steps { .. } | Work::NativePrepare { .. }
            ) {
                validate_steps(&planned.task_id, planned.tasks())?;
            }
            if !ids.insert(planned.task_id.clone()) {
                return Err(ApplicationError(format!(
                    "{} was selected twice",
                    planned.task_id
                )));
            }
            for capability in &planned.check.provides {
                if let Some(previous) = producers.insert(capability, &planned.task_id) {
                    return Err(ApplicationError(format!(
                        "input {capability:?} is produced by both {previous} and {}",
                        planned.task_id
                    )));
                }
            }
            if let Some(reason) = planned.blocked() {
                graph.blocked.insert(planned.task_id.clone(), reason.into());
            }
            for task in planned.tasks() {
                if let Some(previous) =
                    step_owners.insert(task.id.as_str(), planned.task_id.as_str())
                    && previous != planned.task_id
                {
                    return Err(ApplicationError(format!(
                        "native step {} belongs to both {previous} and {}",
                        task.id, planned.task_id
                    )));
                }
                for output in &task.output_specs {
                    let path = normalized(&std::path::Path::new(&task.cwd).join(output));
                    if let Some(previous) = outputs.insert(path, &planned.task_id)
                        && previous != &planned.task_id
                    {
                        return Err(ApplicationError(format!(
                            "output {output:?} is claimed by both {previous} and {}",
                            planned.task_id
                        )));
                    }
                }
            }
        }
        for planned in checks {
            let mut deps = BTreeSet::new();
            let mut missing = vec![];
            for need in &planned.check.needs {
                if let Some(producer) = producers.get(need) {
                    deps.insert((*producer).clone());
                } else {
                    missing.push(format!("needs {need}, which no selected check produces"));
                }
            }
            for task in planned.tasks() {
                for dependency in &task.depends_on {
                    let owner = if ids.contains(dependency) {
                        dependency.as_str()
                    } else if let Some(owner) = step_owners.get(dependency.as_str()) {
                        *owner
                    } else {
                        return Err(ApplicationError(format!(
                            "{} depends on unknown task {dependency}",
                            planned.task_id
                        )));
                    };
                    if owner == planned.task_id && step_owners.contains_key(dependency.as_str()) {
                        continue;
                    }
                    deps.insert(owner.to_owned());
                }
            }
            if deps.contains(&planned.task_id) {
                return Err(ApplicationError(format!(
                    "{} consumes its own output",
                    planned.task_id
                )));
            }
            if !deps.is_empty() {
                graph
                    .edges
                    .insert(planned.task_id.clone(), deps.into_iter().collect());
            }
            if !missing.is_empty() && !graph.blocked.contains_key(&planned.task_id) {
                graph
                    .blocked
                    .insert(planned.task_id.clone(), missing.join("; "));
            }
        }
        loop {
            let newly_blocked: Vec<_> = checks
                .iter()
                .filter(|c| !graph.blocked.contains_key(&c.task_id))
                .filter_map(|c| {
                    graph
                        .edges
                        .get(&c.task_id)?
                        .iter()
                        .find(|d| graph.blocked.contains_key(*d))
                        .map(|d| (c.task_id.clone(), format!("prerequisite {d} did not run")))
                })
                .collect();
            if newly_blocked.is_empty() {
                break;
            }
            graph.blocked.extend(newly_blocked);
        }
        // Validate cycles across all selected checks, including blocked ones; a missing tool cannot
        // hide an invalid graph until a later machine happens to have it installed.
        let mut done = BTreeSet::new();
        while done.len() < checks.len() {
            let ready: Vec<_> = checks
                .iter()
                .filter(|c| !done.contains(&c.task_id))
                .filter(|c| {
                    graph
                        .edges
                        .get(&c.task_id)
                        .is_none_or(|deps| deps.iter().all(|d| done.contains(d)))
                })
                .map(|c| c.task_id.clone())
                .collect();
            if ready.is_empty() {
                return Err(ApplicationError(format!(
                    "the task graph contains a cycle: {}",
                    ids.difference(&done)
                        .cloned()
                        .collect::<Vec<_>>()
                        .join(", ")
                )));
            }
            done.extend(ready.iter().cloned());
            let runnable: Vec<_> = ready
                .into_iter()
                .filter(|id| !graph.blocked.contains_key(id))
                .collect();
            if !runnable.is_empty() {
                graph.layers.push(runnable);
            }
        }
        Ok(graph)
    }
}

fn normalized(path: &std::path::Path) -> std::path::PathBuf {
    let mut result = std::path::PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                if !result.pop() && !path.is_absolute() {
                    result.push("..");
                }
            }
            other => result.push(other.as_os_str()),
        }
    }
    result
}
