//! Per-process fixture directories for test statics that live as long as the test binary.
//!
//! A `static` (`OnceLock`) value is never dropped, so a `tempfile::TempDir` it holds is never
//! removed: every run of the test binary left its directory in /tmp (a tmpfs whose inodes ran
//! out). Such a fixture lives in `<CARGO_TARGET_TMPDIR>/ici-test-process-fixtures/<kind>/` instead,
//! on disk inside target/ (pruned with it), and the next process of the same kind removes it once
//! its owner is gone. Boundaries:
//! - Only this test-only namespace is created or swept; never /tmp, `.tmp*` or other target
//!   directories. A namespace component that is a symlink is refused.
//! - A fixture is `<pid>-<start>` (the owner's pid and its /proc start time) and is swept only
//!   with an ownership mark (`OWNER`, written last, naming the kind, pid and start time) that
//!   matches its name. Without a complete mark (unknown owner, or a fixture still being created)
//!   it is kept, so a sweep that overlaps another run's creation never deletes it.
//! - An owner is dead only when /proc has no such pid or the pid now has another start time (the
//!   recorded process is gone). A live owner, an unreadable /proc entry or any other doubt keeps
//!   the fixture. A symlink entry is kept and never followed.
//! - So the fixtures of a kind never outnumber its live processes plus the last finished one.

#![allow(dead_code)]

use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

const NAMESPACE: &str = "ici-test-process-fixtures";
const OWNER: &str = "OWNER";

/// A fresh, empty directory of `kind` owned by this process for the rest of its life.
pub fn process_dir(kind: &str) -> PathBuf {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR"));
    let base = namespace(root, kind).unwrap();
    sweep(&base, kind).unwrap();
    let pid = std::process::id();
    let start = start_time(pid).expect("this process's /proc start time");
    let directory = base.join(format!("{pid}-{start}"));
    fs::create_dir(&directory).unwrap();
    // The mark comes last and atomically: until it exists the fixture is not sweepable.
    let pending = directory.join(format!(".{OWNER}.pending"));
    fs::write(&pending, mark(kind, pid, start)).unwrap();
    fs::rename(&pending, directory.join(OWNER)).unwrap();
    directory
}

/// `<root>/ici-test-process-fixtures/<kind>`, created as real directories; a symlink is refused.
fn namespace(root: &Path, kind: &str) -> Result<PathBuf, String> {
    assert!(
        !kind.is_empty() && kind.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'),
        "{kind}"
    );
    let mut path = root.to_path_buf();
    for part in [NAMESPACE, kind] {
        path.push(part);
        match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_dir() => {}
            Ok(_) => return Err(format!("{} is not a real directory", path.display())),
            Err(error) if error.kind() == ErrorKind::NotFound => {
                if let Err(error) = fs::create_dir(&path)
                    && error.kind() != ErrorKind::AlreadyExists
                {
                    return Err(error.to_string());
                }
                if !fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_dir())
                {
                    return Err(format!("{} is not a real directory", path.display()));
                }
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(path)
}

fn mark(kind: &str, pid: u32, start: u64) -> String {
    format!("ici test process fixture v1\nkind={kind}\npid={pid}\nstart={start}\n")
}

/// The /proc start time (clock ticks after boot, field 22 of `stat`) of a live `pid`.
fn start_time(pid: u32) -> Result<u64, ErrorKind> {
    let text = fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|error| error.kind())?;
    // Fields after the parenthesized command name start at field 3.
    text.rsplit_once(')')
        .and_then(|(_, rest)| rest.split_whitespace().nth(19))
        .and_then(|field| field.parse().ok())
        .ok_or(ErrorKind::InvalidData)
}

/// Removes the fixtures of `kind` under `base` whose marked owner is certainly gone.
fn sweep(base: &Path, kind: &str) -> Result<(), String> {
    for entry in fs::read_dir(base)
        .map_err(|error| error.to_string())?
        .flatten()
    {
        let path = entry.path();
        // A symlink (or anything but a real directory) is never followed or removed.
        if !fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_dir()) {
            continue;
        }
        let Some((pid, start)) = entry.file_name().to_str().and_then(|name| {
            let (pid, start) = name.split_once('-')?;
            Some((pid.parse::<u32>().ok()?, start.parse::<u64>().ok()?))
        }) else {
            continue;
        };
        let owner = path.join(OWNER);
        let marked = fs::symlink_metadata(&owner).is_ok_and(|metadata| metadata.is_file())
            && fs::read_to_string(&owner).is_ok_and(|text| text == mark(kind, pid, start));
        if !marked {
            continue;
        }
        let gone = match start_time(pid) {
            Err(ErrorKind::NotFound) => true,
            Ok(now) => now != start,
            Err(_) => false,
        };
        if gone {
            // A parallel sweep may remove it first; `remove_dir_all` does not follow symlinks.
            let _ = fs::remove_dir_all(&path);
        }
    }
    Ok(())
}

/// Controls of the sweep, in a namespace of their own: a live owner (pid 1 with its start time)
/// and this process's own fixture are kept; a dead owner (no such pid) and a recorded owner whose
/// pid now has another start time are removed; an unmarked fixture, a wrong mark, another kind's
/// mark, a non-pid name and a symlink entry (whose target holds a valid dead mark) are kept and
/// the symlink target is untouched; a symlinked namespace is refused.
#[test]
fn a_process_dir_sweep_removes_only_marked_fixtures_whose_owner_is_gone() {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "ici-test-process-fixture-controls-{}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let kind = "controls";
    let base = namespace(&root, kind).unwrap();
    let own = std::process::id();
    let own_start = start_time(own).unwrap();
    let init_start = start_time(1).unwrap();
    let fixture = |name: &str, marked: Option<String>| {
        let directory = base.join(name);
        fs::create_dir_all(directory.join("inner")).unwrap();
        if let Some(text) = marked {
            fs::write(directory.join(OWNER), text).unwrap();
        }
    };
    fixture(&format!("1-{init_start}"), Some(mark(kind, 1, init_start)));
    fixture(
        &format!("{own}-{own_start}"),
        Some(mark(kind, own, own_start)),
    );
    fixture(&format!("{}-5", u32::MAX), Some(mark(kind, u32::MAX, 5)));
    fixture(
        &format!("1-{}", init_start + 1),
        Some(mark(kind, 1, init_start + 1)),
    );
    fixture(&format!("{}-6", u32::MAX - 1), None);
    fixture(
        &format!("{}-7", u32::MAX - 2),
        Some(mark(kind, u32::MAX - 2, 8)),
    );
    fixture(
        &format!("{}-9", u32::MAX - 3),
        Some(mark("other", u32::MAX - 3, 9)),
    );
    fixture("not-a-pid", Some(mark(kind, u32::MAX, 5)));
    let outside = root.join("outside");
    fs::create_dir_all(outside.join("inner")).unwrap();
    fs::write(outside.join(OWNER), mark(kind, u32::MAX - 4, 3)).unwrap();
    std::os::unix::fs::symlink(&outside, base.join(format!("{}-3", u32::MAX - 4))).unwrap();
    sweep(&base, kind).unwrap();
    let mut left: Vec<String> = fs::read_dir(&base)
        .unwrap()
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    left.sort();
    let mut expected = vec![
        format!("1-{init_start}"),
        format!("{own}-{own_start}"),
        format!("{}-6", u32::MAX - 1),
        format!("{}-7", u32::MAX - 2),
        format!("{}-9", u32::MAX - 3),
        format!("{}-3", u32::MAX - 4),
        "not-a-pid".to_owned(),
    ];
    expected.sort();
    assert_eq!(left, expected);
    assert!(outside.join("inner").is_dir() && outside.join(OWNER).is_file());
    // A symlinked namespace is refused, not followed.
    let elsewhere = root.join("elsewhere");
    fs::create_dir_all(elsewhere.join(NAMESPACE)).unwrap();
    let linked = root.join("linked");
    fs::create_dir_all(&linked).unwrap();
    std::os::unix::fs::symlink(elsewhere.join(NAMESPACE), linked.join(NAMESPACE)).unwrap();
    assert!(namespace(&linked, kind).is_err());
    assert!(!elsewhere.join(NAMESPACE).join(kind).exists());
    fs::remove_dir_all(&root).unwrap();
}
