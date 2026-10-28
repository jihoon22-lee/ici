use ici_domain::{TaskSpec, TaskState, Validate, canonical_json, sha256};
use nix::{
    errno::Errno,
    fcntl::{FcntlArg, OFlag, fcntl},
    sys::signal::{Signal, killpg},
    unistd::{Pid, getpgrp},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    env, fs,
    io::{self, Read},
    os::{
        fd::AsFd,
        unix::{
            fs::OpenOptionsExt,
            process::{CommandExt, ExitStatusExt},
        },
    },
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

pub const DEFAULT_OUTPUT_LIMIT: usize = 8 * 1024 * 1024;
pub const MAX_OUTPUT_LIMIT: usize = 64 * 1024 * 1024;

#[derive(Clone, Debug, Default)]
pub struct Cancellation(Arc<AtomicBool>);
impl Cancellation {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
    pub fn flag(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.0)
    }
}

/// Linux process-group evidence. None means unverified, not an empty group.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Cleanup {
    pub group: Option<i32>,
    pub survivors: Option<Vec<i32>>,
    pub interrupted: bool,
    pub escalated: bool,
}
impl Cleanup {
    pub fn is_clean(&self) -> bool {
        self.survivors.as_ref().is_some_and(Vec::is_empty)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TaskOutcome {
    pub task_id: String,
    /// Succeeded means process completed; exit-code interpretation belongs to a provider.
    pub state: TaskState,
    pub exit_code: Option<i32>,
    pub signal: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
    pub cancelled: bool,
    pub truncated: bool,
    pub duration_seconds: f64,
    pub error: Option<String>,
    pub cleanup: Cleanup,
}
impl TaskOutcome {
    pub fn blocked(id: &str, reason: impl Into<String>) -> Self {
        let mut outcome = Self::empty(id);
        outcome.state = TaskState::Blocked;
        outcome.error = Some(reason.into());
        outcome
    }
    pub fn empty(id: &str) -> Self {
        Self {
            task_id: id.into(),
            state: TaskState::Failed,
            exit_code: None,
            signal: None,
            stdout: vec![],
            stderr: vec![],
            timed_out: false,
            cancelled: false,
            truncated: false,
            duration_seconds: 0.0,
            error: None,
            cleanup: Cleanup::default(),
        }
    }
    pub fn completed(&self) -> bool {
        self.state == TaskState::Succeeded && !self.timed_out && !self.cancelled && !self.truncated
    }
    pub fn prerequisite_succeeded(&self) -> bool {
        self.completed() && self.exit_code == Some(0)
    }
}

#[derive(Clone, Debug)]
pub struct Runner {
    inherited_env: BTreeMap<String, String>,
    bundle_root: Option<PathBuf>,
    pub poll_interval: Duration,
    pub terminate_grace: Duration,
    pub drain_grace: Duration,
}
impl Default for Runner {
    fn default() -> Self {
        Self::with_environment(
            env::vars_os()
                .filter_map(|(key, value)| {
                    Some((key.into_string().ok()?, value.into_string().ok()?))
                })
                .collect(),
        )
    }
}
impl Runner {
    /// Snapshot once; never read ambient environment while a task is running.
    pub fn with_environment(environment: BTreeMap<String, String>) -> Self {
        let bundle_root = environment.get("ICI_BUNDLE_ROOT").map(PathBuf::from);
        let inherited_env = environment
            .into_iter()
            .filter(|(key, _)| inherit_key(key))
            .collect();
        Self {
            inherited_env,
            bundle_root,
            poll_interval: Duration::from_millis(10),
            terminate_grace: Duration::from_millis(250),
            drain_grace: Duration::from_millis(250),
        }
    }
    pub fn environment(&self, task: &TaskSpec) -> BTreeMap<String, String> {
        let mut environment = self.inherited_env.clone();
        environment.extend(task.env_overlay.clone());
        for key in [
            "ICI_BUNDLE_ROOT",
            "SHIV_ROOT",
            "SHIV_ENTRY_POINT",
            "SHIV_MODULE",
        ] {
            environment.remove(key);
        }
        if let Some(bundle) = &self.bundle_root {
            for key in [
                "PATH",
                "PYTHONPATH",
                "LD_LIBRARY_PATH",
                "LIBRARY_PATH",
                "CPATH",
                "CPLUS_INCLUDE_PATH",
            ] {
                if let Some(value) = environment.get(key) {
                    let paths: Vec<_> = env::split_paths(value)
                        .filter(|path| !path.starts_with(bundle))
                        .collect();
                    if let Ok(joined) = env::join_paths(paths) {
                        environment.insert(key.into(), joined.to_string_lossy().into_owned());
                    } else {
                        environment.remove(key);
                    }
                }
            }
        }
        environment
    }
    pub fn environment_digest(&self, task: &TaskSpec) -> String {
        sha256(canonical_json(&self.environment(task)).expect("environment is serializable"))
    }
    /// Pin must match bytes of an explicit executable, not just its name/version.
    pub fn verify_tool(&self, task: &TaskSpec) -> io::Result<()> {
        if let Some(expected) = &task.tool_digest {
            let path = task
                .argv
                .first()
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing executable"))?;
            if !Path::new(path).is_absolute() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "pinned executable must be an absolute resolved path",
                ));
            }
            if &executable_digest(Path::new(path))? != expected {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "executable bytes changed after tool resolution",
                ));
            }
        }
        Ok(())
    }
    pub fn run(&self, task: &TaskSpec, cancellation: &Cancellation) -> TaskOutcome {
        let started = Instant::now();
        let mut outcome = TaskOutcome::empty(&task.id);
        if cancellation.is_cancelled() {
            outcome.state = TaskState::Cancelled;
            outcome.cancelled = true;
            return outcome;
        }
        let limit = task.output_limit_bytes.unwrap_or(DEFAULT_OUTPUT_LIMIT);
        if let Err(error) = task.validate() {
            outcome.error = Some(format!("invalid task: {error}"));
            return outcome;
        }
        if limit > MAX_OUTPUT_LIMIT {
            outcome.error = Some(format!(
                "output limit exceeds {MAX_OUTPUT_LIMIT} bytes per stream"
            ));
            return outcome;
        }
        let timeout = match Duration::try_from_secs_f64(task.timeout_seconds.unwrap_or(300.0)) {
            Ok(timeout) => timeout,
            Err(error) => {
                outcome.error = Some(format!("invalid timeout: {error}"));
                return outcome;
            }
        };
        if let Err(error) = prepare_dirs(task) {
            outcome.error = Some(format!("cannot prepare work directories: {error}"));
            return outcome;
        }
        let mut command = Command::new(&task.argv[0]);
        command
            .args(&task.argv[1..])
            .current_dir(&task.cwd)
            .env_clear()
            .envs(self.environment(task))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0);
        if let Err(error) = self.verify_tool(task) {
            outcome.error = Some(format!("tool identity validation failed: {error}"));
            outcome.duration_seconds = started.elapsed().as_secs_f64();
            return outcome;
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                outcome.error = Some(format!("cannot start process: {error}"));
                return outcome;
            }
        };
        let group = child.id() as i32;
        outcome.cleanup.group = Some(group);
        let mut stdout = child.stdout.take();
        let mut stderr = child.stderr.take();
        let setup = stdout
            .as_ref()
            .map(nonblocking)
            .transpose()
            .and_then(|_| stderr.as_ref().map(nonblocking).transpose());
        if let Err(error) = setup {
            outcome.error = Some(format!("cannot configure bounded capture: {error}"));
            stop_group(group, self.terminate_grace, &mut outcome.cleanup);
            let _ = reap_bounded(&mut child, self.terminate_grace);
            outcome.duration_seconds = started.elapsed().as_secs_f64();
            return outcome;
        }
        let mut exited = None;
        let mut exit_seen = None;
        let mut stopped = false;
        loop {
            let mut capture_error = None;
            drain(
                &mut stdout,
                &mut outcome.stdout,
                limit,
                &mut outcome.truncated,
                &mut capture_error,
            );
            drain(
                &mut stderr,
                &mut outcome.stderr,
                limit,
                &mut outcome.truncated,
                &mut capture_error,
            );
            if let Some(error) = capture_error {
                outcome.error = Some(error);
            }
            if exited.is_none() {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        exited = Some(status);
                        exit_seen = Some(Instant::now());
                    }
                    Ok(None) => {}
                    Err(error) => {
                        outcome.error = Some(format!("cannot poll process: {error}"));
                    }
                }
            }
            if cancellation.is_cancelled() {
                outcome.cancelled = true;
            }
            if started.elapsed() >= timeout {
                outcome.timed_out = true;
            }
            if !stopped && (outcome.cancelled || outcome.timed_out || outcome.error.is_some()) {
                stop_group(group, self.terminate_grace, &mut outcome.cleanup);
                stopped = true;
            }
            if exited.is_some() && stdout.is_none() && stderr.is_none() {
                let members = group_members(group);
                if members.as_ref().is_none_or(|items| !items.is_empty()) && !stopped {
                    stop_group(group, self.terminate_grace, &mut outcome.cleanup);
                } else if !stopped {
                    outcome.cleanup.survivors = members;
                }
                break;
            }
            // A finished leader can leave live descendants holding the pipe. Do not join a
            // blocking reader thread or accept the leader's success while they still run.
            if !stopped && exit_seen.is_some_and(|time| time.elapsed() >= self.drain_grace) {
                stop_group(group, self.terminate_grace, &mut outcome.cleanup);
                stopped = true;
            }
            if stopped {
                if exited.is_none() {
                    match reap_bounded(&mut child, self.terminate_grace) {
                        Ok(status) => exited = status,
                        Err(error) => outcome.error = Some(format!("cannot reap process: {error}")),
                    }
                    if exited.is_none() {
                        outcome.error =
                            Some("direct child did not exit within cleanup grace".into());
                    }
                }
                let drain_started = Instant::now();
                while (stdout.is_some() || stderr.is_some())
                    && drain_started.elapsed() < self.drain_grace
                {
                    drain(
                        &mut stdout,
                        &mut outcome.stdout,
                        limit,
                        &mut outcome.truncated,
                        &mut outcome.error,
                    );
                    drain(
                        &mut stderr,
                        &mut outcome.stderr,
                        limit,
                        &mut outcome.truncated,
                        &mut outcome.error,
                    );
                    if stdout.is_some() || stderr.is_some() {
                        thread::sleep(self.poll_interval);
                    }
                }
                outcome.truncated |= stdout.is_some() || stderr.is_some();
                break;
            }
            thread::sleep(self.poll_interval);
        }
        if let Some(status) = exited {
            outcome.exit_code = status.code();
            outcome.signal = status.signal();
        }
        if !outcome.cleanup.is_clean() && outcome.error.is_none() {
            outcome.error = Some("process-group cleanup was not verified clean".into());
        }
        if outcome.cleanup.interrupted
            && !outcome.cancelled
            && !outcome.timed_out
            && outcome.error.is_none()
        {
            outcome.error = Some(
                "descendants remained after the command leader exited; cleanup interrupted them"
                    .into(),
            );
        }
        outcome.state = if outcome.cancelled {
            TaskState::Cancelled
        } else if outcome.timed_out
            || outcome.truncated
            || outcome.signal.is_some()
            || outcome.error.is_some()
            || outcome.cleanup.interrupted
        {
            TaskState::Failed
        } else {
            TaskState::Succeeded
        };
        outcome.duration_seconds = started.elapsed().as_secs_f64();
        outcome
    }
}

fn inherit_key(key: &str) -> bool {
    matches!(
        key,
        "PATH"
            | "HOME"
            | "USER"
            | "LOGNAME"
            | "TMPDIR"
            | "TMP"
            | "TEMP"
            | "LANG"
            | "LC_ALL"
            | "LC_CTYPE"
            | "TZ"
            | "TERM"
            | "NO_COLOR"
            | "XDG_CONFIG_HOME"
            | "XDG_CACHE_HOME"
            | "XDG_DATA_HOME"
            | "VIRTUAL_ENV"
            | "CONDA_PREFIX"
            | "PYTHONPATH"
            | "PYTHONNOUSERSITE"
            | "PYTHONDONTWRITEBYTECODE"
            | "LD_LIBRARY_PATH"
            | "LIBRARY_PATH"
            | "CPATH"
            | "CPLUS_INCLUDE_PATH"
            | "CC"
            | "CXX"
            | "AR"
            | "CFLAGS"
            | "CXXFLAGS"
            | "LDFLAGS"
            | "PKG_CONFIG_PATH"
            | "QTDIR"
            | "QT_PLUGIN_PATH"
            | "QT_QPA_PLATFORM"
            | "CARGO_HOME"
            | "RUSTUP_HOME"
            | "RUSTUP_TOOLCHAIN"
            | "RUSTUP_AUTO_INSTALL"
            | "CARGO_NET_OFFLINE"
            | "CARGO_BUILD_JOBS"
            | "CARGO_TARGET_DIR"
            | "RUSTFLAGS"
            | "RUSTDOCFLAGS"
            | "NODE_OPTIONS"
            | "NODE_PATH"
            | "NPM_CONFIG_USERCONFIG"
    ) || key.starts_with("LC_")
}
fn nonblocking(fd: &impl AsFd) -> nix::Result<()> {
    let flags = OFlag::from_bits_truncate(fcntl(fd, FcntlArg::F_GETFL)?);
    fcntl(fd, FcntlArg::F_SETFL(flags | OFlag::O_NONBLOCK)).map(|_| ())
}
fn drain<R: Read>(
    stream: &mut Option<R>,
    bytes: &mut Vec<u8>,
    limit: usize,
    truncated: &mut bool,
    error: &mut Option<String>,
) {
    let mut buffer = [0; 8192];
    for _ in 0..8 {
        let Some(reader) = stream.as_mut() else {
            return;
        };
        match reader.read(&mut buffer) {
            Ok(0) => {
                *stream = None;
                return;
            }
            Ok(count) => {
                let keep = count.min(limit.saturating_sub(bytes.len()));
                bytes.extend_from_slice(&buffer[..keep]);
                *truncated |= keep < count;
            }
            Err(problem) if problem.kind() == io::ErrorKind::WouldBlock => return,
            Err(problem) if problem.kind() == io::ErrorKind::Interrupted => continue,
            Err(problem) => {
                *error = Some(format!("cannot capture process output: {problem}"));
                *stream = None;
                return;
            }
        }
    }
}
fn prepare_dirs(task: &TaskSpec) -> io::Result<()> {
    let cwd = Path::new(&task.cwd);
    if !cwd.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "cwd is not a directory",
        ));
    }
    for raw in &task.work_dirs {
        let relative = Path::new(raw);
        if relative.components().any(|part| {
            !matches!(
                part,
                Component::Normal(_) | Component::CurDir | Component::RootDir
            )
        }) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "work directory must not contain parent traversal",
            ));
        }
        let path = if relative.is_absolute() {
            relative.to_path_buf()
        } else {
            cwd.join(relative)
        };
        crate::storage::prepare_directory(&path)?;
    }
    Ok(())
}
/// Live members only; zombie descendants hold no pipes or locks.
pub fn group_members(group: i32) -> Option<Vec<i32>> {
    let entries = fs::read_dir("/proc").ok()?;
    let mut members = vec![];
    for entry in entries {
        let entry = entry.ok()?;
        let name = entry.file_name();
        let Some(pid) = name.to_str().and_then(|name| name.parse::<i32>().ok()) else {
            continue;
        };
        let stat = match fs::read_to_string(entry.path().join("stat")) {
            Ok(stat) => stat,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(_) => return None,
        };
        let fields: Vec<_> = stat.rsplit_once(") ")?.1.split_whitespace().collect();
        if fields.len() < 3 {
            return None;
        }
        if fields[0] != "Z" && fields[2].parse::<i32>().ok()? == group {
            members.push(pid);
        }
    }
    members.sort_unstable();
    Some(members)
}
fn signal_group(group: i32, signal: Signal) -> bool {
    if group <= 0 || Pid::from_raw(group) == getpgrp() {
        return false;
    }
    matches!(
        killpg(Pid::from_raw(group), signal),
        Ok(()) | Err(Errno::ESRCH)
    )
}
fn stop_group(group: i32, grace: Duration, cleanup: &mut Cleanup) {
    let before = group_members(group);
    cleanup.interrupted = before.as_ref().is_none_or(|members| !members.is_empty());
    let _ = signal_group(group, Signal::SIGTERM);
    let start = Instant::now();
    while start.elapsed() < grace {
        if group_members(group).as_ref().is_some_and(Vec::is_empty) {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    let remaining = group_members(group);
    if remaining.as_ref().is_none_or(|members| !members.is_empty()) {
        cleanup.escalated = signal_group(group, Signal::SIGKILL);
    }
    let start = Instant::now();
    while start.elapsed() < grace {
        if group_members(group).as_ref().is_some_and(Vec::is_empty) {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    cleanup.survivors = group_members(group);
}

fn reap_bounded(
    child: &mut std::process::Child,
    grace: Duration,
) -> io::Result<Option<std::process::ExitStatus>> {
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(Some(status));
        }
        if started.elapsed() >= grace {
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(5));
    }
}

pub fn executable_digest(path: &Path) -> io::Result<String> {
    let mut file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NONBLOCK | nix::libc::O_CLOEXEC)
        .open(path)?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "executable is not a regular file",
        ));
    }
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("sha256:{:x}", hash.finalize()))
}
