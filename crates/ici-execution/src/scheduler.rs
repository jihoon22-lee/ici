//! Deterministic dependency results with bounded parallel command execution.
use crate::{Cancellation, Runner, TaskOutcome};
use ici_domain::{TaskSpec, TaskState, Validate, canonical_json, sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io,
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ScheduleError {
    #[error("invalid task graph: {0}")]
    Invalid(String),
    #[error("cycle in task graph: {0}")]
    Cycle(String),
}
#[derive(Clone, Debug)]
pub struct ScheduleResult {
    /// Exactly one outcome per input task in original input order.
    pub outcomes: Vec<TaskOutcome>,
    pub reused_from: BTreeMap<String, String>,
    pub executed_task_ids: Vec<String>,
}
#[derive(Clone, Debug)]
pub struct Scheduler {
    pub runner: Runner,
    pub jobs: usize,
    /// Use the same per-workspace state directory across invocations for OS locks.
    pub lock_directory: Option<PathBuf>,
    pub lock_timeout: Duration,
}
impl Scheduler {
    pub fn new(runner: Runner, jobs: usize) -> Self {
        Self {
            runner,
            jobs: jobs.clamp(1, 64),
            lock_directory: None,
            lock_timeout: Duration::from_secs(120),
        }
    }
    pub fn run(
        &self,
        tasks: &[TaskSpec],
        cancellation: &Cancellation,
    ) -> Result<ScheduleResult, ScheduleError> {
        let ids = preflight(tasks)?;
        let invalid_tools: BTreeMap<_, _> = tasks
            .iter()
            .enumerate()
            .filter_map(|(index, task)| {
                self.runner
                    .verify_tool(task)
                    .err()
                    .map(|error| (index, error.to_string()))
            })
            .collect();
        let mut representatives = vec![];
        let mut seen = BTreeMap::new();
        let mut reused_from = BTreeMap::new();
        for (index, task) in tasks.iter().enumerate() {
            let key = sha256(
                canonical_json(&(
                    task.share_key()
                        .map_err(|e| ScheduleError::Invalid(e.to_string()))?,
                    self.runner.environment_digest(task),
                ))
                .map_err(|e| ScheduleError::Invalid(e.to_string()))?,
            );
            let representative =
                if task.tool_digest.is_some() && !invalid_tools.contains_key(&index) {
                    *seen.entry(key).or_insert(index)
                } else {
                    index
                };
            representatives.push(representative);
            if representative != index {
                reused_from.insert(task.id.clone(), tasks[representative].id.clone());
            }
        }
        let mut pending: BTreeSet<_> = (0..tasks.len())
            .filter(|&index| representatives[index] == index)
            .collect();
        let mut outcomes: Vec<Option<TaskOutcome>> = vec![None; tasks.len()];
        let mut active = BTreeSet::new();
        let mut held = BTreeSet::new();
        let mut executed = BTreeSet::new();
        let (tx, rx) = mpsc::channel();
        thread::scope(|scope| {
            while !pending.is_empty() || !active.is_empty() {
                for index in pending.clone() {
                    if active.len() >= self.jobs.clamp(1, 64) {
                        break;
                    }
                    let task = &tasks[index];
                    if cancellation.is_cancelled() {
                        let mut outcome = TaskOutcome::empty(&task.id);
                        outcome.state = TaskState::Cancelled;
                        outcome.cancelled = true;
                        outcomes[index] = Some(outcome);
                        pending.remove(&index);
                        continue;
                    }
                    if let Some(error) = invalid_tools.get(&index) {
                        let mut outcome = TaskOutcome::empty(&task.id);
                        outcome.error = Some(format!("tool identity validation failed: {error}"));
                        outcomes[index] = Some(outcome);
                        pending.remove(&index);
                        continue;
                    }
                    if let Some(missing) =
                        task.depends_on.iter().find(|dep| !ids.contains_key(*dep))
                    {
                        outcomes[index] = Some(TaskOutcome::blocked(
                            &task.id,
                            format!("missing prerequisite {missing}"),
                        ));
                        pending.remove(&index);
                        continue;
                    }
                    let dependencies: Vec<_> = task
                        .depends_on
                        .iter()
                        .map(|dep| representatives[ids[dep]])
                        .collect();
                    if let Some(failed) = dependencies.iter().find(|&&dep| {
                        outcomes[dep]
                            .as_ref()
                            .is_some_and(|result| !result.prerequisite_succeeded())
                    }) {
                        outcomes[index] = Some(TaskOutcome::blocked(
                            &task.id,
                            format!("prerequisite {} did not succeed", tasks[*failed].id),
                        ));
                        pending.remove(&index);
                        continue;
                    }
                    if dependencies.iter().any(|&dep| outcomes[dep].is_none())
                        || task.resource_keys.iter().any(|key| held.contains(key))
                    {
                        continue;
                    }
                    pending.remove(&index);
                    active.insert(index);
                    executed.insert(index);
                    held.extend(task.resource_keys.iter().cloned());
                    let sender = tx.clone();
                    scope.spawn(move || {
                        let outcome = std::panic::catch_unwind(|| {
                            let _locks = if let Some(directory) = &self.lock_directory {
                                match ResourceLocks::acquire(
                                    directory,
                                    &task.resource_keys,
                                    cancellation,
                                    self.lock_timeout,
                                ) {
                                    Ok(guard) => Some(guard),
                                    Err(error) => {
                                        let mut outcome = TaskOutcome::blocked(
                                            &task.id,
                                            format!("resource lock: {error}"),
                                        );
                                        if cancellation.is_cancelled() {
                                            outcome.state = TaskState::Cancelled;
                                            outcome.cancelled = true;
                                        }
                                        return outcome;
                                    }
                                }
                            } else {
                                None
                            };
                            self.runner.run(task, cancellation)
                        })
                        .unwrap_or_else(|_| {
                            let mut outcome = TaskOutcome::empty(&task.id);
                            outcome.error = Some("execution worker panicked".into());
                            outcome
                        });
                        let _ = sender.send((index, outcome));
                    });
                }
                if !active.is_empty() {
                    // Workers always report, including unwinds. This wait does not stop each
                    // runner's own cancellation/timeout loop or resource-lock polling.
                    if let Ok((index, outcome)) = rx.recv() {
                        active.remove(&index);
                        for key in &tasks[index].resource_keys {
                            held.remove(key);
                        }
                        outcomes[index] = Some(outcome);
                    }
                } else if !pending.is_empty() {
                    // A fail-closed fallback, never an empty successful result.
                    for index in std::mem::take(&mut pending) {
                        outcomes[index] = Some(TaskOutcome::blocked(
                            &tasks[index].id,
                            "no runnable prerequisite path",
                        ));
                    }
                }
            }
        });
        for index in 0..tasks.len() {
            if representatives[index] != index {
                let mut outcome = outcomes[representatives[index]]
                    .clone()
                    .expect("representative finished");
                outcome.task_id.clone_from(&tasks[index].id);
                outcomes[index] = Some(outcome);
            }
        }
        let executed_task_ids = executed
            .into_iter()
            .filter(|&index| {
                outcomes[index]
                    .as_ref()
                    .is_some_and(|outcome| outcome.cleanup.group.is_some())
            })
            .map(|index| tasks[index].id.clone())
            .collect();
        Ok(ScheduleResult {
            outcomes: outcomes
                .into_iter()
                .map(|item| item.expect("every task has an outcome"))
                .collect(),
            reused_from,
            executed_task_ids,
        })
    }
}
fn preflight(tasks: &[TaskSpec]) -> Result<BTreeMap<String, usize>, ScheduleError> {
    let mut ids = BTreeMap::new();
    for (index, task) in tasks.iter().enumerate() {
        task.validate()
            .map_err(|error| ScheduleError::Invalid(error.to_string()))?;
        if ids.insert(task.id.clone(), index).is_some() {
            return Err(ScheduleError::Invalid(format!(
                "duplicate task {}",
                task.id
            )));
        }
    }
    let mut remaining: BTreeSet<_> = (0..tasks.len()).collect();
    let mut done = BTreeSet::new();
    while !remaining.is_empty() {
        let ready: Vec<_> = remaining
            .iter()
            .copied()
            .filter(|&index| {
                tasks[index]
                    .depends_on
                    .iter()
                    .all(|dep| ids.get(dep).is_none_or(|dep| done.contains(dep)))
            })
            .collect();
        if ready.is_empty() {
            return Err(ScheduleError::Cycle(
                remaining
                    .iter()
                    .map(|&index| tasks[index].id.as_str())
                    .collect::<Vec<_>>()
                    .join(", "),
            ));
        }
        for index in ready {
            remaining.remove(&index);
            done.insert(index);
        }
    }
    Ok(ids)
}

/// OS locks are held by open descriptors, never by the existence of lock files.
pub struct ResourceLocks {
    _files: Vec<File>,
}
impl ResourceLocks {
    pub fn acquire(
        directory: &Path,
        resources: &[String],
        cancellation: &Cancellation,
        timeout: Duration,
    ) -> io::Result<Self> {
        if resources.is_empty() {
            return Ok(Self { _files: vec![] });
        }
        let started = Instant::now();
        let mut files = vec![];
        for resource in resources.iter().collect::<BTreeSet<_>>() {
            let name = sha256(resource).replace(':', "-");
            let file = crate::storage::open_lock_file(&directory.join(name))?;
            loop {
                if cancellation.is_cancelled() {
                    return Err(io::Error::new(
                        io::ErrorKind::Interrupted,
                        "cancelled waiting for resource",
                    ));
                }
                match file.try_lock() {
                    Ok(()) => break,
                    Err(std::fs::TryLockError::WouldBlock) => {}
                    Err(std::fs::TryLockError::Error(error)) => return Err(error),
                }
                if started.elapsed() >= timeout {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "resource lock timeout",
                    ));
                }
                thread::sleep(Duration::from_millis(10));
            }
            files.push(file);
        }
        Ok(Self { _files: files })
    }
}
