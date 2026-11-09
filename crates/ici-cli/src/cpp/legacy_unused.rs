//! Frozen `run_cpp_unused_functions` execution loop for the legacy dead projection: the
//! configurations run one at a time in frozen order under one 600 s budget, each guarded before
//! and after its compiler run, and the first failure stops the scope (exact evidence is atomic).
use super::legacy_boundaries::executable_identity;
use ici_domain::TaskSpec;
use ici_execution::TaskOutcome;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

/// Frozen `_GLOBAL_TIMEOUT_SECONDS`.
const GLOBAL_BUDGET: Duration = Duration::from_secs(600);
/// Frozen `_UNIT_TIMEOUT_SECONDS`.
const UNIT_TIMEOUT: Duration = Duration::from_secs(120);
/// Test budgets by project root, so a replaced budget never reaches another project's run.
static BUDGET_OVERRIDES: Mutex<BTreeMap<PathBuf, Duration>> = Mutex::new(BTreeMap::new());

fn canonical(root: &Path) -> PathBuf {
    std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf())
}

/// Test hook: replaces the 600 s legacy unused-function budget for runs of the project at
/// `root` (`None` restores it). The command line has no way to reach it.
#[doc(hidden)]
pub fn set_legacy_unused_budget_for_tests(root: &Path, budget: Option<Duration>) {
    let mut overrides = BUDGET_OVERRIDES
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    match budget {
        Some(budget) => overrides.insert(canonical(root), budget),
        None => overrides.remove(&canonical(root)),
    };
}

fn budget(root: &Path) -> Duration {
    BUDGET_OVERRIDES
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get(&canonical(root))
        .copied()
        .unwrap_or(GLOBAL_BUDGET)
}

/// One configuration to run: its task (database order) and the source snapshot the scope was
/// planned from.
pub(super) struct Unit<'a> {
    pub source: &'a str,
    pub task: &'a TaskSpec,
    pub snapshot: Option<&'a [u8]>,
}

/// What a guarded sequential run produced.
#[derive(Default)]
pub(super) struct Run {
    pub outcomes: Vec<TaskOutcome>,
    /// Frozen `_compiler_result_failure` guards that fail after a completed run, by task id
    /// (`<reason>: <source>`, without the `C++ unused-function ` prefix).
    pub after: BTreeMap<String, String>,
    /// The located stop that ended the scope before a configuration ran (`_run_unit` guards
    /// and the global budget): `(source, message)`.
    pub stop: Option<(String, String)>,
}

/// Frozen `_PreparedUnit` identities, taken for every configuration before the first runs.
struct Prepared {
    executable: Option<(PathBuf, [i128; 6])>,
    directory: Option<[u64; 3]>,
}

fn executable(task: &TaskSpec) -> Option<(PathBuf, [i128; 6])> {
    let path = std::fs::canonicalize(task.argv.first()?).ok()?;
    let identity = executable_identity(&path).ok()?;
    Some((path, identity))
}

/// Frozen `_directory_identity`: the resolved directory must be the recorded one.
fn directory(task: &TaskSpec) -> Option<[u64; 3]> {
    use std::os::unix::fs::MetadataExt;
    let path = Path::new(&task.cwd);
    let resolved = std::fs::canonicalize(path).ok()?;
    let details = std::fs::metadata(&resolved).ok()?;
    (resolved == path && details.is_dir())
        .then(|| [details.dev(), details.ino(), u64::from(details.mode())])
}

fn source_unchanged(root: &Path, source: &str, snapshot: Option<&[u8]>) -> bool {
    std::fs::read(root.join(source)).ok().is_some_and(|bytes| {
        Some(bytes.as_slice()) == snapshot && std::str::from_utf8(&bytes).is_ok()
    })
}

/// Frozen loop: before each configuration the budget, then the compiler identity, working
/// directory and source snapshot; a configuration gets `min(120 s, remaining)`; a run whose
/// process failed (judged by the caller) or whose guards fail afterwards stops the scope.
pub(super) fn run_sequential(
    root: &Path,
    units: &[Unit<'_>],
    mut failed: impl FnMut(&TaskOutcome) -> bool,
    mut run: impl FnMut(TaskSpec) -> Result<TaskOutcome, String>,
) -> Result<Run, String> {
    let prepared: Vec<_> = units
        .iter()
        .map(|unit| Prepared {
            executable: executable(unit.task),
            directory: directory(unit.task),
        })
        .collect();
    let deadline = Instant::now() + budget(root);
    let mut result = Run::default();
    for (index, (unit, identity)) in units.iter().zip(&prepared).enumerate() {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let stop = |message: String| Some((unit.source.to_owned(), message));
        if remaining.is_zero() {
            result.stop = stop(format!(
                "C++ unused-function global budget left {} configuration(s) unexamined",
                units.len() - index
            ));
            break;
        }
        if identity.executable.is_none() || executable(unit.task) != identity.executable {
            result.stop =
                stop("C++ unused-function compiler identity changed before execution".into());
            break;
        }
        if identity.directory.is_none() || directory(unit.task) != identity.directory {
            result.stop =
                stop("C++ unused-function working directory changed before execution".into());
            break;
        }
        if !source_unchanged(root, unit.source, unit.snapshot) {
            result.stop = stop("C++ unused-function source changed before execution".into());
            break;
        }
        let mut task = unit.task.clone();
        task.timeout_seconds = Some(remaining.min(UNIT_TIMEOUT).as_secs_f64());
        let outcome = run(task)?;
        let after = if executable(unit.task) != identity.executable {
            Some("compiler identity changed during execution")
        } else if directory(unit.task) != identity.directory {
            Some("working directory changed during execution")
        } else if !source_unchanged(root, unit.source, unit.snapshot) {
            Some("source changed during compiler analysis")
        } else {
            None
        };
        let stopped = failed(&outcome) || after.is_some();
        if let Some(reason) = after {
            result.after.insert(
                outcome.task_id.clone(),
                format!("{reason}: {}", unit.source),
            );
        }
        result.outcomes.push(outcome);
        if stopped {
            break;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn task(id: &str, root: &Path) -> TaskSpec {
        TaskSpec::command(
            id,
            "compiler",
            vec!["/bin/true".into()],
            root.canonicalize().unwrap().display().to_string(),
        )
    }

    fn ran(task: &TaskSpec) -> TaskOutcome {
        let mut outcome = TaskOutcome::empty(&task.id);
        outcome.exit_code = Some(0);
        outcome
    }

    /// Frozen `run_cpp_unused_functions`: a spent budget stops at the next configuration and
    /// names how many are left (the loop checks `remaining <= 0` before each one).
    #[test]
    fn a_spent_budget_names_the_unexamined_configurations() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("a.cpp"), "int a;\n").unwrap();
        let tasks = [
            task("t0", root.path()),
            task("t1", root.path()),
            task("t2", root.path()),
        ];
        let units: Vec<_> = tasks
            .iter()
            .map(|task| Unit {
                source: "a.cpp",
                task,
                snapshot: Some(b"int a;\n".as_slice()),
            })
            .collect();
        set_legacy_unused_budget_for_tests(root.path(), Some(Duration::from_millis(200)));
        let mut seen = vec![];
        let result = run_sequential(
            root.path(),
            &units,
            |_| false,
            |task| {
                seen.push(task.timeout_seconds.unwrap());
                std::thread::sleep(Duration::from_millis(250));
                Ok(ran(&task))
            },
        );
        set_legacy_unused_budget_for_tests(root.path(), None);
        let result = result.unwrap();
        assert_eq!(result.outcomes.len(), 1);
        assert!(seen[0] <= 0.2, "{seen:?}");
        assert_eq!(
            result.stop,
            Some((
                "a.cpp".into(),
                "C++ unused-function global budget left 2 configuration(s) unexamined".into()
            ))
        );
    }

    /// Frozen `_UNIT_TIMEOUT_SECONDS`: a configuration never gets more than 120 s.
    #[test]
    fn a_configuration_gets_at_most_120_seconds() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("a.cpp"), "int a;\n").unwrap();
        let task = task("t0", root.path());
        let units = [Unit {
            source: "a.cpp",
            task: &task,
            snapshot: Some(b"int a;\n".as_slice()),
        }];
        let mut seen = None;
        let result = run_sequential(
            root.path(),
            &units,
            |_| false,
            |task| {
                seen = task.timeout_seconds;
                Ok(ran(&task))
            },
        )
        .unwrap();
        assert_eq!(seen, Some(120.0));
        assert!(result.stop.is_none() && result.after.is_empty());
    }
}
