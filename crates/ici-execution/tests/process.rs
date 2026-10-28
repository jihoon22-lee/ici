use ici_domain::{TaskSpec, TaskState};
use ici_execution::process::group_members;
use ici_execution::{Cancellation, Runner};
use std::{
    collections::BTreeMap,
    fs,
    path::Path,
    thread,
    time::{Duration, Instant},
};

fn task(root: &Path, mode: &str, args: &[&str]) -> TaskSpec {
    let python = std::env::var("ICI_TEST_PYTHON").unwrap_or_else(|_| "/usr/bin/python3".into());
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/rust-parity/execution/child.py")
        .canonicalize()
        .unwrap();
    let mut argv = vec![python, fixture.to_string_lossy().into_owned(), mode.into()];
    argv.extend(args.iter().map(|arg| (*arg).to_string()));
    TaskSpec::command("fixture", "fixture", argv, root.to_string_lossy())
}
#[test]
fn captures_raw_binary_and_separates_completed_nonzero_exit() {
    let root = tempfile::tempdir().unwrap();
    let runner = Runner::default();
    let output = runner.run(&task(root.path(), "binary", &[]), &Cancellation::default());
    assert!(output.completed(), "{output:?}");
    assert_eq!(output.stdout, b"\0\xffpartial\xfe");
    assert_eq!(output.stderr, b"\x80stderr");
    let output = runner.run(
        &task(root.path(), "echo", &["finding", "diagnostic", "1"]),
        &Cancellation::default(),
    );
    assert_eq!(output.state, TaskState::Succeeded);
    assert_eq!(output.exit_code, Some(1));
    assert!(!output.prerequisite_succeeded());
}
#[test]
fn output_flood_is_bounded_and_never_complete() {
    let root = tempfile::tempdir().unwrap();
    let mut specification = task(root.path(), "flood", &[]);
    specification.output_limit_bytes = Some(4096);
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert_eq!(output.stdout.len(), 4096);
    assert_eq!(output.stderr.len(), 4096);
    assert!(output.truncated);
    assert!(!output.completed());
    assert_eq!(output.exit_code, Some(0));
}
#[test]
fn timeout_escalates_and_verifies_group_empty() {
    let root = tempfile::tempdir().unwrap();
    let mut specification = task(root.path(), "ignore-term", &[]);
    specification.timeout_seconds = Some(0.2);
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert!(output.timed_out, "{output:?}");
    assert!(output.cleanup.escalated, "{output:?}");
    assert!(output.cleanup.is_clean(), "{output:?}");
    assert!(output.duration_seconds < 3.0);
    assert_eq!(output.signal, Some(9));
}
#[test]
fn cancellation_before_spawn_and_during_execution_stays_cancelled() {
    let root = tempfile::tempdir().unwrap();
    let pidfile = root.path().join("pid");
    let specification = task(root.path(), "sleep", &[pidfile.to_str().unwrap()]);
    let cancellation = Cancellation::default();
    cancellation.cancel();
    let output = Runner::default().run(&specification, &cancellation);
    assert_eq!(output.state, TaskState::Cancelled);
    assert!(!pidfile.exists());
    let cancellation = Cancellation::default();
    let signal = cancellation.clone();
    let join = thread::spawn(move || Runner::default().run(&specification, &signal));
    let started = Instant::now();
    while !pidfile.exists() && started.elapsed() < Duration::from_secs(3) {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(pidfile.exists());
    cancellation.cancel();
    let output = join.join().unwrap();
    assert_eq!(output.state, TaskState::Cancelled, "{output:?}");
    assert!(output.cleanup.is_clean());
    assert!(!output.completed());
}
#[test]
fn descendant_holding_pipes_is_stopped_after_leader_exits() {
    let root = tempfile::tempdir().unwrap();
    let pidfile = root.path().join("pids");
    let specification = task(root.path(), "descendant", &[pidfile.to_str().unwrap()]);
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert!(output.duration_seconds < 3.0, "{output:?}");
    assert_eq!(output.exit_code, Some(0));
    assert_eq!(output.state, TaskState::Failed);
    assert!(output.cleanup.interrupted);
    assert!(output.cleanup.is_clean());
    let group: i32 = fs::read_to_string(pidfile)
        .unwrap()
        .split_whitespace()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(group_members(group), Some(vec![]));
}
#[test]
fn signal_and_start_errors_are_not_empty_success() {
    let root = tempfile::tempdir().unwrap();
    let runner = Runner::default();
    let output = runner.run(&task(root.path(), "signal", &[]), &Cancellation::default());
    assert_eq!(output.signal, Some(15));
    assert_eq!(output.state, TaskState::Failed);
    let mut specification = task(root.path(), "binary", &[]);
    specification.cwd = root.path().join("absent").to_string_lossy().into_owned();
    let output = runner.run(&specification, &Cancellation::default());
    assert!(output.error.is_some());
    assert_eq!(output.exit_code, None);
    specification.cwd = root.path().to_string_lossy().into_owned();
    specification.argv[0] = root
        .path()
        .join("missing-tool")
        .to_string_lossy()
        .into_owned();
    let output = runner.run(&specification, &Cancellation::default());
    assert!(output.error.is_some());
    assert!(!output.completed());
}
#[test]
fn environment_is_snapshotted_allowlisted_and_bundle_paths_removed() {
    let root = tempfile::tempdir().unwrap();
    let mut specification = task(root.path(), "environment", &[]);
    specification
        .env_overlay
        .insert("CUSTOM_PROJECT_VALUE".into(), "explicit".into());
    specification
        .env_overlay
        .insert("ICI_BUNDLE_ROOT".into(), "/bundle".into());
    let runner = Runner::with_environment(BTreeMap::from([
        ("PATH".into(), "/bundle/bin:/project/bin:/usr/bin".into()),
        ("PYTHONPATH".into(), "/bundle/app:/project/lib".into()),
        ("ICI_BUNDLE_ROOT".into(), "/bundle".into()),
        ("HOST_SECRET".into(), "must-not-inherit".into()),
        ("VIRTUAL_ENV".into(), "/project/venv".into()),
    ]));
    let output = runner.run(&specification, &Cancellation::default());
    let values: BTreeMap<String, String> = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(values["PATH"], "/project/bin:/usr/bin");
    assert_eq!(values["PYTHONPATH"], "/project/lib");
    assert_eq!(values["VIRTUAL_ENV"], "/project/venv");
    assert_eq!(values["CUSTOM_PROJECT_VALUE"], "explicit");
    assert!(!values.contains_key("HOST_SECRET"));
    assert!(!values.contains_key("ICI_BUNDLE_ROOT"));
    let other = Runner::with_environment(BTreeMap::from([("PATH".into(), "/changed".into())]));
    assert_ne!(
        runner.environment_digest(&specification),
        other.environment_digest(&specification)
    );
}
#[test]
fn argv_is_not_interpreted_as_a_shell_and_work_dirs_are_declared() {
    let root = tempfile::tempdir().unwrap();
    let text = "$(touch SHOULD_NOT_EXIST); * $HOME";
    let mut specification = task(root.path(), "echo", &[text, "", "0"]);
    specification.work_dirs = vec![".ici/rust/work".into()];
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert_eq!(output.stdout, text.as_bytes());
    assert!(root.path().join(".ici/rust/work").is_dir());
    assert!(!root.path().join("SHOULD_NOT_EXIST").exists());
    specification.work_dirs = vec!["../escape".into()];
    assert!(
        Runner::default()
            .run(&specification, &Cancellation::default())
            .error
            .is_some()
    );
}
#[test]
fn declared_absolute_work_directory_is_supported_without_following_symlinks() {
    let root = tempfile::tempdir().unwrap();
    let outputs = tempfile::tempdir().unwrap();
    let declared = outputs.path().join("declared/work");
    let mut specification = task(root.path(), "binary", &[]);
    specification.work_dirs = vec![declared.to_string_lossy().into_owned()];
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert!(output.completed(), "{output:?}");
    assert!(declared.is_dir());
    let linked = root.path().join("linked");
    std::os::unix::fs::symlink(outputs.path(), &linked).unwrap();
    specification.work_dirs = vec!["linked/must-not-create".into()];
    let output = Runner::default().run(&specification, &Cancellation::default());
    assert!(!output.completed());
    assert!(!outputs.path().join("must-not-create").exists());
}
#[test]
fn executable_changed_at_same_path_is_not_executed() {
    use ici_execution::process::executable_digest;
    let root = tempfile::tempdir().unwrap();
    let executable = root.path().join("tool");
    fs::copy("/usr/bin/true", &executable).unwrap();
    let digest = executable_digest(&executable).unwrap();
    let mut specification = TaskSpec::command(
        "pinned",
        "fixture",
        vec![executable.to_string_lossy().into_owned()],
        root.path().to_string_lossy(),
    );
    specification.tool_digest = Some(digest);
    let runner = Runner::default();
    assert!(
        runner
            .run(&specification, &Cancellation::default())
            .completed()
    );
    fs::copy("/usr/bin/false", &executable).unwrap();
    let output = runner.run(&specification, &Cancellation::default());
    assert_eq!(output.exit_code, None);
    assert_eq!(output.cleanup.group, None);
    assert!(output.error.unwrap().contains("bytes changed"));
}
#[test]
fn substituted_fifo_executable_fails_without_blocking_hash_or_spawn() {
    let root = tempfile::tempdir().unwrap();
    let fifo = root.path().join("tool");
    nix::unistd::mkfifo(
        &fifo,
        nix::sys::stat::Mode::S_IRUSR | nix::sys::stat::Mode::S_IWUSR,
    )
    .unwrap();
    let mut specification = TaskSpec::command(
        "pinned",
        "fixture",
        vec![fifo.to_string_lossy().into_owned()],
        root.path().to_string_lossy(),
    );
    specification.tool_digest = Some(ici_domain::sha256("old bytes"));
    let started = Instant::now();
    let outcome = Runner::default().run(&specification, &Cancellation::default());
    assert!(started.elapsed().as_secs_f64() < 1.0);
    assert_eq!(outcome.cleanup.group, None);
    assert!(outcome.error.unwrap().contains("regular file"));
}
