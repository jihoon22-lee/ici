use ici_domain::{TaskSpec, TaskState};
use ici_execution::scheduler::ResourceLocks;
use ici_execution::{Cancellation, Runner, Scheduler};
use std::{
    fs,
    path::Path,
    thread,
    time::{Duration, Instant},
};
fn task(root: &Path, id: &str, mode: &str, args: &[&str]) -> TaskSpec {
    let python = std::env::var("ICI_TEST_PYTHON").unwrap_or_else(|_| "/usr/bin/python3".into());
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/execution/child.py")
        .canonicalize()
        .unwrap();
    let mut argv = vec![python, fixture.to_string_lossy().into_owned(), mode.into()];
    argv.extend(args.iter().map(|arg| (*arg).into()));
    TaskSpec::command(id, "fixture", argv, root.to_string_lossy())
}
#[test]
fn duplicate_execution_has_original_consumer_ids_and_strict_bounds_identity() {
    let root = tempfile::tempdir().unwrap();
    let count = root.path().join("count");
    let mut first = task(root.path(), "first", "once", &[count.to_str().unwrap()]);
    first.tool_digest =
        Some(ici_execution::process::executable_digest(Path::new(&first.argv[0])).unwrap());
    let mut second = first.clone();
    second.id = "second".into();
    let mut third = first.clone();
    third.id = "third".into();
    third.output_limit_bytes = Some(42);
    let scheduled = Scheduler::new(Runner::default(), 2)
        .run(&[first, second, third], &Cancellation::default())
        .unwrap();
    assert_eq!(
        scheduled
            .outcomes
            .iter()
            .map(|r| r.task_id.as_str())
            .collect::<Vec<_>>(),
        ["first", "second", "third"]
    );
    assert_eq!(scheduled.reused_from["second"], "first");
    assert_eq!(scheduled.reused_from.len(), 1);
    assert_eq!(fs::read_to_string(count).unwrap().lines().count(), 2);
}
#[test]
fn missing_or_failed_prerequisites_block_without_running_and_cycle_fails_closed() {
    let root = tempfile::tempdir().unwrap();
    let marker = root.path().join("must-not-run");
    let failed = task(root.path(), "failed", "echo", &["", "", "9"]);
    let mut dependent = task(
        root.path(),
        "dependent",
        "once",
        &[marker.to_str().unwrap()],
    );
    dependent.depends_on = vec!["failed".into()];
    let mut missing = dependent.clone();
    missing.id = "missing".into();
    missing.depends_on = vec!["unselected".into()];
    let result = Scheduler::new(Runner::default(), 2)
        .run(
            &[dependent.clone(), missing, failed],
            &Cancellation::default(),
        )
        .unwrap();
    assert_eq!(result.outcomes[0].state, TaskState::Blocked);
    assert_eq!(result.outcomes[1].state, TaskState::Blocked);
    assert_eq!(result.outcomes[2].state, TaskState::Succeeded);
    assert!(!marker.exists());
    let mut other = dependent.clone();
    other.id = "failed".into();
    other.depends_on = vec!["dependent".into()];
    assert!(
        Scheduler::new(Runner::default(), 2)
            .run(&[dependent, other], &Cancellation::default())
            .is_err()
    );
    assert!(!marker.exists());
}
#[test]
fn resource_keys_serialize_and_jobs_bound_parallel_work() {
    let root = tempfile::tempdir().unwrap();
    let marker = root.path().join("exclusive");
    let output = root.path().join("order");
    let tasks: Vec<_> = (0..4)
        .map(|index| {
            let id = format!("task{index}");
            let mut task = task(
                root.path(),
                &id,
                "resource",
                &[marker.to_str().unwrap(), output.to_str().unwrap(), &id],
            );
            task.resource_keys = vec!["same-build-dir".into()];
            task
        })
        .collect();
    let result = Scheduler::new(Runner::default(), 4)
        .run(&tasks, &Cancellation::default())
        .unwrap();
    assert!(
        result
            .outcomes
            .iter()
            .all(|result| result.prerequisite_succeeded()),
        "{result:?}"
    );
    assert_eq!(
        fs::read_to_string(output).unwrap(),
        "task0\ntask1\ntask2\ntask3\n"
    );
    let active = root.path().join("active");
    fs::create_dir(&active).unwrap();
    let tasks: Vec<_> = (0..4)
        .map(|index| {
            let mut task = task(
                root.path(),
                &format!("parallel{index}"),
                "parallel",
                &[active.to_str().unwrap()],
            );
            task.env_overlay
                .insert("UNIQUE_INVOCATION".into(), index.to_string());
            task
        })
        .collect();
    let result = Scheduler::new(Runner::default(), 2)
        .run(&tasks, &Cancellation::default())
        .unwrap();
    let counts: Vec<usize> = result
        .outcomes
        .iter()
        .map(|result| {
            String::from_utf8_lossy(&result.stdout)
                .trim()
                .parse()
                .unwrap()
        })
        .collect();
    assert!(counts.iter().all(|&count| count <= 2));
    assert!(counts.contains(&2), "{counts:?}");
}
#[test]
fn cancellation_preserves_finished_work_and_marks_waiting_tasks() {
    let root = tempfile::tempdir().unwrap();
    let first = task(root.path(), "first", "echo", &["done", "", "0"]);
    let pid = root.path().join("pid");
    let mut sleeping = task(root.path(), "sleeping", "sleep", &[pid.to_str().unwrap()]);
    sleeping.depends_on = vec!["first".into()];
    let mut waiting = task(root.path(), "waiting", "binary", &[]);
    waiting.depends_on = vec!["sleeping".into()];
    let cancellation = Cancellation::default();
    let signal = cancellation.clone();
    let join = thread::spawn(move || {
        Scheduler::new(Runner::default(), 2)
            .run(&[first, sleeping, waiting], &signal)
            .unwrap()
    });
    let started = Instant::now();
    while !pid.exists() && started.elapsed() < Duration::from_secs(3) {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(pid.exists());
    cancellation.cancel();
    let result = join.join().unwrap();
    assert_eq!(result.outcomes[0].stdout, b"done");
    assert_eq!(result.outcomes[0].state, TaskState::Succeeded);
    assert_eq!(result.outcomes[1].state, TaskState::Cancelled);
    assert_eq!(result.outcomes[2].state, TaskState::Cancelled);
}
#[test]
fn os_locks_are_bounded_cancellable_and_released_on_drop() {
    let root = tempfile::tempdir().unwrap();
    let keys = vec!["build".to_string()];
    let guard = ResourceLocks::acquire(
        root.path(),
        &keys,
        &Cancellation::default(),
        Duration::from_secs(1),
    )
    .unwrap();
    let error = ResourceLocks::acquire(
        root.path(),
        &keys,
        &Cancellation::default(),
        Duration::from_millis(30),
    )
    .err()
    .unwrap();
    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
    let cancellation = Cancellation::default();
    cancellation.cancel();
    let error = ResourceLocks::acquire(root.path(), &keys, &cancellation, Duration::from_secs(1))
        .err()
        .unwrap();
    assert_eq!(error.kind(), std::io::ErrorKind::Interrupted);
    drop(guard);
    assert!(
        ResourceLocks::acquire(
            root.path(),
            &keys,
            &Cancellation::default(),
            Duration::from_secs(1)
        )
        .is_ok()
    );
}
#[test]
fn absent_tool_identity_is_never_shared() {
    let root = tempfile::tempdir().unwrap();
    let count = root.path().join("count");
    let first = task(root.path(), "one", "once", &[count.to_str().unwrap()]);
    let mut second = first.clone();
    second.id = "two".into();
    let result = Scheduler::new(Runner::default(), 2)
        .run(&[first, second], &Cancellation::default())
        .unwrap();
    assert!(result.reused_from.is_empty());
    assert_eq!(fs::read_to_string(count).unwrap().lines().count(), 2);
}
#[test]
fn kernel_releases_resource_lock_when_holder_is_terminated() {
    let root = tempfile::tempdir().unwrap();
    let keys = vec!["build".to_string()];
    let lockpath = root
        .path()
        .join(ici_domain::sha256("build").replace(':', "-"));
    let pidfile = root.path().join("pid");
    let mut holder = task(
        root.path(),
        "holder",
        "hold-lock",
        &[lockpath.to_str().unwrap(), pidfile.to_str().unwrap()],
    );
    holder.timeout_seconds = Some(0.4);
    let join = thread::spawn(move || Runner::default().run(&holder, &Cancellation::default()));
    let started = Instant::now();
    while !pidfile.exists() && started.elapsed() < Duration::from_secs(3) {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(pidfile.exists());
    assert!(
        ResourceLocks::acquire(
            root.path(),
            &keys,
            &Cancellation::default(),
            Duration::from_millis(20)
        )
        .is_err()
    );
    let outcome = join.join().unwrap();
    assert!(outcome.timed_out && outcome.cleanup.is_clean());
    assert!(
        ResourceLocks::acquire(
            root.path(),
            &keys,
            &Cancellation::default(),
            Duration::from_secs(1)
        )
        .is_ok()
    );
}
#[test]
fn replaced_tool_cannot_be_deduplicated_or_started() {
    use ici_execution::process::executable_digest;
    let root = tempfile::tempdir().unwrap();
    let executable = root.path().join("tool");
    fs::copy("/usr/bin/true", &executable).unwrap();
    let mut first = TaskSpec::command(
        "first",
        "fixture",
        vec![executable.to_string_lossy().into_owned()],
        root.path().to_string_lossy(),
    );
    first.tool_digest = Some(executable_digest(&executable).unwrap());
    let mut second = first.clone();
    second.id = "second".into();
    fs::copy("/usr/bin/false", &executable).unwrap();
    let result = Scheduler::new(Runner::default(), 2)
        .run(&[first, second], &Cancellation::default())
        .unwrap();
    assert!(result.reused_from.is_empty());
    assert!(result.executed_task_ids.is_empty());
    assert!(
        result
            .outcomes
            .iter()
            .all(|outcome| outcome.state == TaskState::Failed && outcome.exit_code.is_none())
    );
}
