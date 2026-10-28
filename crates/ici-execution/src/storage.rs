//! Same-directory atomic result publication and locked, validated event append.
use crate::redaction::redact_text;
use ici_domain::{RunEvent, RunResult, Validate, read_events};
use nix::{
    errno::Errno,
    fcntl::{AtFlags, OFlag, open, openat, renameat},
    sys::stat::{Mode, SFlag, fstatat, mkdirat},
    unistd::{UnlinkatFlags, linkat, unlinkat},
};
use std::{
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    os::{fd::OwnedFd, unix::fs::OpenOptionsExt},
    path::{Component, Path},
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug)]
pub enum WriteMode {
    CreateNew,
    ReplaceRegular,
}
struct Stage<'a> {
    parent: &'a OwnedFd,
    name: OsString,
}
impl Drop for Stage<'_> {
    fn drop(&mut self) {
        let _ = unlinkat(
            self.parent,
            self.name.as_os_str(),
            UnlinkatFlags::NoRemoveDir,
        );
    }
}
/// No-follow directory descriptors prevent an ancestor symlink from redirecting a write.
/// Existing regular result files may be atomically replaced only when explicitly selected.
/// CreateNew additionally guarantees that a concurrent new destination is never overwritten.
pub fn atomic_write(path: &Path, bytes: &[u8], mode: WriteMode) -> io::Result<()> {
    atomic_write_with(path, mode, |file| file.write_all(bytes))
}
pub fn atomic_write_with(
    path: &Path,
    mode: WriteMode,
    write: impl FnOnce(&mut File) -> io::Result<()>,
) -> io::Result<()> {
    let (parent, name) = open_parent(path, true)?;
    refuse_non_regular(&parent, &name)?;
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    let temporary = OsString::from(format!(
        ".ici-{}-{}.partial",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    let fd = openat(
        &parent,
        temporary.as_os_str(),
        OFlag::O_WRONLY | OFlag::O_CREAT | OFlag::O_EXCL | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
        Mode::S_IRUSR | Mode::S_IWUSR,
    )?;
    let stage = Stage {
        parent: &parent,
        name: temporary,
    };
    let mut file = File::from(fd);
    write(&mut file)?;
    file.sync_all()?;
    // Recheck after the writer, allowing tests and callers to surface races safely.
    refuse_non_regular(&parent, &name)?;
    match mode {
        WriteMode::CreateNew => linkat(
            &parent,
            stage.name.as_os_str(),
            &parent,
            name.as_os_str(),
            AtFlags::empty(),
        )?,
        WriteMode::ReplaceRegular => {
            renameat(&parent, stage.name.as_os_str(), &parent, name.as_os_str())?
        }
    }
    File::from(parent.try_clone()?)
        .sync_all()
        .map_err(|error| {
            io::Error::other(format!(
                "destination committed but directory durability is unconfirmed: {error}"
            ))
        })?;
    drop(stage);
    Ok(())
}
pub fn write_result(path: &Path, result: &RunResult, mode: WriteMode) -> io::Result<()> {
    let mut json = result.to_json().map_err(io::Error::other)?;
    json.push('\n');
    atomic_write(path, json.as_bytes(), mode)
}
/// Validates the complete new batch before touching disk. A failed append attempts
/// rollback to its original length; any rollback failure is reported explicitly.
pub fn append_events(path: &Path, events: &[RunEvent]) -> io::Result<()> {
    if events.is_empty() {
        return Ok(());
    }
    for pair in events.windows(2) {
        if pair[0].run_id != pair[1].run_id || pair[0].seq.checked_add(1) != Some(pair[1].seq) {
            return Err(io::Error::other(
                "event batch has mixed runs or non-contiguous sequence",
            ));
        }
    }
    let mut lines = Vec::new();
    for event in events {
        event.validate().map_err(io::Error::other)?;
        let mut event = event.clone();
        event.message = redact_text(&event.message);
        lines.extend_from_slice(event.to_json().map_err(io::Error::other)?.as_bytes());
        lines.push(b'\n');
    }
    const MAX_STREAM: u64 = 64 * 1024 * 1024;
    if lines.len() as u64 > MAX_STREAM {
        return Err(io::Error::other("event batch exceeds 64 MiB"));
    }
    let (parent, name) = open_parent(path, true)?;
    refuse_non_regular(&parent, &name)?;
    let fd = openat(
        &parent,
        name.as_os_str(),
        OFlag::O_RDWR | OFlag::O_CREAT | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
        Mode::S_IRUSR | Mode::S_IWUSR,
    )?;
    let mut file = File::from(fd);
    if !file.metadata()?.is_file() {
        return Err(io::Error::other(
            "event destination changed to non-regular file",
        ));
    }
    lock_bounded(&file, Duration::from_secs(2))?;
    let original = file.metadata()?.len();
    if original + lines.len() as u64 > MAX_STREAM {
        return Err(io::Error::other("event stream exceeds 64 MiB"));
    }
    let mut previous = String::new();
    (&mut file)
        .take(MAX_STREAM + 1)
        .read_to_string(&mut previous)?;
    if previous.len() as u64 != original {
        return Err(io::Error::other("event stream changed while locked"));
    }
    if !previous.is_empty() && !previous.ends_with('\n') {
        return Err(io::Error::other("event stream has a truncated final line"));
    }
    let (old, notes) = read_events(&previous).map_err(io::Error::other)?;
    if !notes.is_empty() {
        return Err(io::Error::other(
            "event stream is incomplete; refusing to append",
        ));
    }
    if old.iter().any(|event| event.run_id != events[0].run_id) {
        return Err(io::Error::other("event stream belongs to a different run"));
    }
    let mut last = old.last();
    for event in events {
        if let Some(previous) = last
            && (event.run_id != previous.run_id || previous.seq.checked_add(1) != Some(event.seq))
        {
            return Err(io::Error::other(
                "event run_id or sequence does not continue",
            ));
        }
        last = Some(event);
    }
    file.seek(SeekFrom::End(0))?;
    if let Err(error) = file.write_all(&lines).and_then(|_| file.sync_all()) {
        if let Err(rollback) = file.set_len(original).and_then(|_| file.sync_all()) {
            return Err(io::Error::other(format!(
                "event append failed: {error}; rollback failed: {rollback}"
            )));
        }
        return Err(error);
    }
    Ok(())
}
fn refuse_non_regular(parent: &OwnedFd, name: &OsString) -> io::Result<()> {
    match fstatat(parent, name.as_os_str(), AtFlags::AT_SYMLINK_NOFOLLOW) {
        Ok(stat)
            if (stat.st_mode & SFlag::S_IFMT.bits()) == SFlag::S_IFREG.bits()
                && stat.st_nlink == 1 =>
        {
            Ok(())
        }
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "destination is not a single-link regular file",
        )),
        Err(Errno::ENOENT) => Ok(()),
        Err(error) => Err(error.into()),
    }
}
fn open_parent(path: &Path, create: bool) -> io::Result<(OwnedFd, OsString)> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut parts: Vec<_> = absolute.components().collect();
    let Some(Component::Normal(name)) = parts.pop() else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "destination needs a file name",
        ));
    };
    let mut directory = open(
        Path::new("/"),
        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC,
        Mode::empty(),
    )?;
    for component in parts {
        match component {
            Component::RootDir | Component::CurDir => continue,
            Component::Normal(part) => {
                match openat(
                    &directory,
                    part,
                    OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
                    Mode::empty(),
                ) {
                    Ok(next) => directory = next,
                    Err(Errno::ENOENT) if create => {
                        match mkdirat(&directory, part, Mode::S_IRWXU) {
                            Ok(()) | Err(Errno::EEXIST) => {}
                            Err(error) => return Err(error.into()),
                        }
                        directory = openat(
                            &directory,
                            part,
                            OFlag::O_RDONLY
                                | OFlag::O_DIRECTORY
                                | OFlag::O_NOFOLLOW
                                | OFlag::O_CLOEXEC,
                            Mode::empty(),
                        )?;
                    }
                    Err(error) => return Err(error.into()),
                }
            }
            _ => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "parent traversal is not allowed",
                ));
            }
        }
    }
    Ok((directory, name.to_os_string()))
}

pub(crate) fn open_lock_file(path: &Path) -> io::Result<File> {
    let (parent, name) = open_parent(path, true)?;
    refuse_non_regular(&parent, &name)?;
    let fd = openat(
        &parent,
        name.as_os_str(),
        OFlag::O_RDWR | OFlag::O_CREAT | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
        Mode::S_IRUSR | Mode::S_IWUSR,
    )?;
    let file = File::from(fd);
    if !file.metadata()?.is_file() {
        return Err(io::Error::other("lock path is not a regular file"));
    }
    Ok(file)
}
fn lock_bounded(file: &File, timeout: Duration) -> io::Result<()> {
    let started = Instant::now();
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(()),
            Err(std::fs::TryLockError::WouldBlock) => {}
            Err(std::fs::TryLockError::Error(error)) => return Err(error),
        }
        if started.elapsed() >= timeout {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "event file is locked",
            ));
        }
        thread::sleep(Duration::from_millis(5));
    }
}

/// Read only a bounded regular result, then validate its domain/wire schema.
pub fn read_result(path: &Path) -> io::Result<RunResult> {
    RunResult::from_json(&read_text(path)?)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}
pub fn read_event_stream(path: &Path) -> io::Result<(Vec<RunEvent>, Vec<String>)> {
    match read_text(path) {
        Ok(text) => {
            read_events(&text).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            Ok((vec![], vec![format!("{} does not exist", path.display())]))
        }
        Err(error) => Err(error),
    }
}
fn read_text(path: &Path) -> io::Result<String> {
    const MAX_FILE: u64 = 64 * 1024 * 1024;
    // Reading an explicitly selected saved result may follow a link; writing never does.
    // Nonblocking open prevents a substituted FIFO from hanging before metadata validation.
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NONBLOCK | nix::libc::O_CLOEXEC)
        .open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() > MAX_FILE {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "saved result is not a bounded regular file",
        ));
    }
    let mut text = String::new();
    file.take(MAX_FILE + 1).read_to_string(&mut text)?;
    if text.len() as u64 > MAX_FILE {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "saved result exceeds size limit",
        ));
    }
    Ok(text)
}

pub(crate) fn prepare_directory(path: &Path) -> io::Result<()> {
    // Walk/create the actual directory with no-follow directory descriptors;
    // the synthetic final component is never opened or created.
    let _ = open_parent(&path.join(".ici-directory-probe"), true)?;
    Ok(())
}
