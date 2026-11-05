//! Host tool PATH mirrors for the non-ignored CI twins of actual-tool replays.
//!
//! A frozen capture ran on a host whose `/usr/bin` lacked some tools (no clang-tidy, Clazy or
//! clang++). A twin reproduces that tool situation on any host — the GitHub runner has
//! `/usr/bin/clang-tidy`, `clazy` and a different g++ — by running with a PATH of symlink mirrors
//! of `/usr/bin` and `/bin` that leave out every entry whose name starts with one of the given
//! prefixes. Everything else (g++, cmake, make, binutils) is the host's own.
#![allow(dead_code)]

use std::{fs, path::Path, process::Command};

/// The tools a capture host without LLVM tools lacked: clang-tidy*, clazy*, clang*.
pub const NO_LLVM: [&str; 2] = ["clang", "clazy"];

pub struct HostPath {
    _mirror: tempfile::TempDir,
    pub path: String,
    /// The mirror directory standing for `/usr/bin`.
    pub usr_bin: String,
}

/// A PATH over mirrors of `/usr/bin` and `/bin` without entries starting with `skipped`.
pub fn mirror(skipped: &[&str]) -> HostPath {
    let mirror = tempfile::tempdir().unwrap();
    let mut directories = vec![];
    for source in ["/usr/bin", "/bin"] {
        let target = mirror
            .path()
            .join(source.trim_start_matches('/').replace('/', "-"));
        fs::create_dir(&target).unwrap();
        for entry in fs::read_dir(source).unwrap().flatten() {
            let name = entry.file_name();
            let text = name.to_string_lossy();
            if skipped.iter().any(|prefix| text.starts_with(prefix)) {
                continue;
            }
            let _ = std::os::unix::fs::symlink(entry.path(), target.join(&name));
        }
        directories.push(target.display().to_string());
    }
    HostPath {
        usr_bin: directories[0].clone(),
        path: directories.join(":"),
        _mirror: mirror,
    }
}

/// The first `--version` line of a host tool, if it runs.
pub fn version(executable: &Path) -> Option<String> {
    let output = Command::new(executable).arg("--version").output().ok()?;
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .next()
        .map(str::to_owned)
}
