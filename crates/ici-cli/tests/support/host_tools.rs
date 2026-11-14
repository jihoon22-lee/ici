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

/// The mirror directories live in one `TempDir` owned here: the PATH is valid while the value
/// lives, and dropping it removes every mirror (no process-lifetime state).
pub struct HostPath {
    _mirror: tempfile::TempDir,
    pub path: String,
    /// The mirror directory standing for `/usr/bin`.
    pub usr_bin: String,
    /// Each mirror directory and the directory it mirrors, in PATH order.
    pub sources: Vec<(String, String)>,
}

impl HostPath {
    /// `text` with each of this mirror's own directory prefixes (`<mirror>/`) spelled as the
    /// directory it mirrors, only where a path token starts (not after a path character), and
    /// how many were mapped; every mirror entry is a symlink to the entry of the same name there.
    /// Panics when the mirror root still occurs afterwards.
    pub fn unmirror(&self, text: &str) -> (String, usize) {
        let mut mapped = 0;
        let mut text = text.to_owned();
        for (mirror, source) in &self.sources {
            let prefix = format!("{mirror}/");
            let mut out = String::with_capacity(text.len());
            let mut rest = text.as_str();
            while let Some(index) = rest.find(&prefix) {
                let before = rest[..index].chars().last().or_else(|| out.chars().last());
                let boundary = before.is_none_or(|before| {
                    !(before.is_alphanumeric() || matches!(before, '/' | '.' | '-' | '_' | '~'))
                });
                out.push_str(&rest[..index]);
                if boundary {
                    out.push_str(&format!("{source}/"));
                    mapped += 1;
                } else {
                    out.push_str(&prefix);
                }
                rest = &rest[index + prefix.len()..];
            }
            out.push_str(rest);
            text = out;
        }
        let root = self._mirror.path().display().to_string();
        assert!(
            !text.contains(&root),
            "the mirror root {root} is left after mapping: {text}"
        );
        (text, mapped)
    }

    /// Whether `path` (`<source>/<name>`) is reachable through this PATH: its mirror entry is a
    /// symlink to exactly `path`.
    pub fn mirrors(&self, path: &str) -> bool {
        self.sources.iter().any(|(mirror, source)| {
            path.strip_prefix(&format!("{source}/"))
                .filter(|name| !name.is_empty() && !name.contains('/'))
                .is_some_and(|name| {
                    fs::read_link(Path::new(mirror).join(name))
                        .is_ok_and(|target| target == Path::new(path))
                })
        })
    }
}

/// A PATH over mirrors of `/usr/bin` and `/bin` without entries starting with `skipped`.
pub fn mirror(skipped: &[&str]) -> HostPath {
    mirror_of(&["/usr/bin", "/bin"], skipped)
}

/// A PATH over mirrors of `sources`, in order, without entries starting with `skipped`.
pub fn mirror_of(sources: &[&str], skipped: &[&str]) -> HostPath {
    let mirror = tempfile::tempdir().unwrap();
    let mut directories = vec![];
    for (index, source) in sources.iter().enumerate() {
        let name = match *source {
            "/usr/bin" | "/bin" => source.trim_start_matches('/').replace('/', "-"),
            _ => format!("dir-{index}"),
        };
        let target = mirror.path().join(name);
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
        usr_bin: sources
            .iter()
            .position(|source| *source == "/usr/bin")
            .map_or_else(String::new, |index| directories[index].clone()),
        path: directories.join(":"),
        sources: directories
            .iter()
            .cloned()
            .zip(sources.iter().map(|source| (*source).to_owned()))
            .collect(),
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
