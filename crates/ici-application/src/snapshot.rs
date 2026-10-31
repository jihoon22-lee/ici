//! Immutable in-process source inputs. External tools still read the project, so callers compare
//! a second inventory after execution and refuse complete evidence if inputs drifted.
use crate::{ApplicationError, Result};
use ici_domain::{SourceInput, SourceSnapshot, canonical_json, sha256, validation};
use nix::{
    fcntl::{OFlag, open, openat},
    sys::stat::Mode,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::Read,
    os::{fd::OwnedFd, unix::fs::MetadataExt},
    path::{Component, Path},
};

#[derive(Clone, Copy, Debug)]
pub struct InputLimits {
    pub file_bytes: usize,
    pub total_bytes: usize,
}
impl Default for InputLimits {
    fn default() -> Self {
        Self {
            file_bytes: 16 * 1024 * 1024,
            total_bytes: 512 * 1024 * 1024,
        }
    }
}
#[derive(Clone, Debug)]
pub struct CapturedFile {
    pub input: SourceInput,
    pub digest: String,
}
#[derive(Clone, Debug)]
pub struct InputSnapshot {
    pub files: BTreeMap<String, CapturedFile>,
    pub sources: BTreeSet<String>,
    pub configurations: BTreeSet<String>,
    pub digest: String,
    pub total_bytes: usize,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct InputDrift {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
}
impl InputDrift {
    pub fn stable(&self) -> bool {
        self.added.is_empty() && self.removed.is_empty() && self.changed.is_empty()
    }
}
impl InputSnapshot {
    /// Keep original user inputs frozen while accepting only declared generated build inputs.
    /// The returned drift excludes those explicit effects and still records every user change.
    pub fn after_preparation(
        &self,
        after: &Self,
        generated_roots: &[String],
        limits: InputLimits,
    ) -> Result<(Self, InputDrift)> {
        self.after_preparation_with_path_limits(after, generated_roots, limits, &BTreeMap::new())
    }
    pub fn after_preparation_with_path_limits(
        &self,
        after: &Self,
        generated_roots: &[String],
        limits: InputLimits,
        path_limits: &BTreeMap<String, usize>,
    ) -> Result<(Self, InputDrift)> {
        validate_limits(limits, path_limits)?;
        for root in generated_roots {
            validation::relative_path(root, false)
                .map_err(|error| ApplicationError(error.to_string()))?;
        }
        let generated = |path: &str| {
            generated_roots.iter().any(|root| {
                path == root
                    || path
                        .strip_prefix(root)
                        .is_some_and(|tail| tail.starts_with('/'))
            })
        };
        let mut drift = self.difference(after);
        drift.added.retain(|path| !generated(path));
        drift.removed.retain(|path| !generated(path));
        drift.changed.retain(|path| !generated(path));
        let mut files: BTreeMap<_, _> = self
            .files
            .iter()
            .filter(|(path, _)| !generated(path))
            .map(|(path, file)| (path.clone(), file.clone()))
            .collect();
        files.extend(
            after
                .files
                .iter()
                .filter(|(path, _)| generated(path))
                .map(|(path, file)| (path.clone(), file.clone())),
        );
        let sources = self
            .sources
            .iter()
            .filter(|path| !generated(path))
            .chain(after.sources.iter().filter(|path| generated(path)))
            .cloned()
            .collect();
        let configurations = self
            .configurations
            .iter()
            .filter(|path| !generated(path))
            .chain(after.configurations.iter().filter(|path| generated(path)))
            .cloned()
            .collect();
        let mut total_bytes = 0usize;
        for (path, file) in &files {
            if file.input.bytes.len() > path_limits.get(path).copied().unwrap_or(limits.file_bytes)
            {
                return Err(ApplicationError(
                    "generated input exceeds per-file limit".into(),
                ));
            }
            total_bytes = total_bytes
                .checked_add(file.input.bytes.len())
                .ok_or_else(|| ApplicationError("input byte count overflow".into()))?;
            if total_bytes > limits.total_bytes {
                return Err(ApplicationError(
                    "generated inputs exceed total snapshot limit".into(),
                ));
            }
        }
        let identity: Vec<_> = files
            .iter()
            .map(|(path, file)| (path, &file.digest))
            .collect();
        let digest =
            sha256(canonical_json(&identity).map_err(|error| ApplicationError(error.to_string()))?);
        Ok((
            Self {
                files,
                sources,
                configurations,
                digest,
                total_bytes,
            },
            drift,
        ))
    }
    pub fn take(
        root: &Path,
        sources: &[String],
        configurations: &[String],
        limits: InputLimits,
    ) -> Result<Self> {
        Self::take_with_path_limits(root, sources, configurations, limits, &BTreeMap::new())
    }
    pub fn take_with_path_limits(
        root: &Path,
        sources: &[String],
        configurations: &[String],
        limits: InputLimits,
        path_limits: &BTreeMap<String, usize>,
    ) -> Result<Self> {
        validate_limits(limits, path_limits)?;
        let root = root
            .canonicalize()
            .map_err(|e| ApplicationError(format!("snapshot root: {e}")))?;
        let root_fd =
            directory(&root).map_err(|e| ApplicationError(format!("snapshot root: {e}")))?;
        let sources: BTreeSet<_> = sources.iter().cloned().collect();
        let configurations: BTreeSet<_> = configurations.iter().cloned().collect();
        let mut files = BTreeMap::new();
        let mut total_bytes: usize = 0;
        for path in sources.union(&configurations) {
            validation::relative_path(path, false)
                .map_err(|e| ApplicationError(format!("snapshot {path}: {e}")))?;
            let limit = path_limits
                .get(path)
                .copied()
                .unwrap_or(limits.file_bytes)
                .min(limits.total_bytes.saturating_sub(total_bytes));
            let bytes = if sources.contains(path) || path_limits.contains_key(path) {
                read_relative(&root_fd, path, limit)
            } else {
                // Explicit/native configuration readers permit a symlink to a regular file.
                // Capture its bounded bytes and compare again after execution; source traversal
                // and all artifact/cache writes retain the stronger no-follow policy.
                ici_execution::storage::read_bounded_regular(&root.join(path), limit)
            }
            .map_err(|e| ApplicationError(format!("snapshot {path}: {e}")))?;
            total_bytes = total_bytes
                .checked_add(bytes.len())
                .ok_or_else(|| ApplicationError("input byte count overflow".into()))?;
            let digest = sha256(&bytes);
            files.insert(
                path.clone(),
                CapturedFile {
                    input: SourceInput {
                        path: path.clone(),
                        bytes,
                    },
                    digest,
                },
            );
        }
        let identity: Vec<_> = files
            .iter()
            .map(|(path, captured)| (path, &captured.digest))
            .collect();
        let digest =
            sha256(canonical_json(&identity).map_err(|e| ApplicationError(e.to_string()))?);
        Ok(Self {
            files,
            sources,
            configurations,
            digest,
            total_bytes,
        })
    }
    pub fn source(&self, commit: Option<String>, dirty: bool) -> SourceSnapshot {
        SourceSnapshot {
            digest: self.digest.clone(),
            files: self.sources.iter().cloned().collect(),
            generated: vec![],
            external_inputs: vec![],
            commit,
            dirty,
        }
    }
    pub fn difference(&self, after: &Self) -> InputDrift {
        InputDrift {
            added: after
                .files
                .keys()
                .filter(|p| !self.files.contains_key(*p))
                .cloned()
                .collect(),
            removed: self
                .files
                .keys()
                .filter(|p| !after.files.contains_key(*p))
                .cloned()
                .collect(),
            changed: self
                .files
                .iter()
                .filter(|(path, before)| {
                    after
                        .files
                        .get(*path)
                        .is_some_and(|now| before.digest != now.digest)
                })
                .map(|(p, _)| p.clone())
                .collect(),
        }
    }
}
fn validate_limits(limits: InputLimits, path_limits: &BTreeMap<String, usize>) -> Result<()> {
    if limits.file_bytes == 0 || limits.total_bytes == 0 {
        return Err(ApplicationError("input limits must be positive".into()));
    }
    for (path, limit) in path_limits {
        validation::relative_path(path, false)
            .map_err(|error| ApplicationError(error.to_string()))?;
        if *limit == 0 || *limit > limits.total_bytes {
            return Err(ApplicationError(format!("invalid input limit for {path}")));
        }
    }
    Ok(())
}
pub(crate) fn directory(path: &Path) -> std::io::Result<OwnedFd> {
    if !path.is_absolute() {
        return Err(std::io::Error::other("input directory must be absolute"));
    }
    let mut fd = open(
        Path::new("/"),
        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC,
        Mode::empty(),
    )?;
    for component in path.components() {
        match component {
            Component::RootDir => {}
            Component::Normal(part) => {
                fd = openat(
                    &fd,
                    part,
                    OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
                    Mode::empty(),
                )?;
            }
            _ => return Err(std::io::Error::other("snapshot root must be canonical")),
        }
    }
    Ok(fd)
}
fn read_relative(root: &OwnedFd, path: &str, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut directory = root.try_clone()?;
    let mut parts = Path::new(path).components().peekable();
    while let Some(component) = parts.next() {
        let Component::Normal(part) = component else {
            return Err(std::io::Error::other(
                "source path must be relative without traversal",
            ));
        };
        if parts.peek().is_some() {
            directory = openat(
                &directory,
                part,
                OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
                Mode::empty(),
            )?;
        } else {
            let fd = openat(
                &directory,
                part,
                OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
                Mode::empty(),
            )?;
            let mut file = File::from(fd);
            let before = file.metadata()?;
            if !before.is_file() {
                return Err(std::io::Error::other("input is not a regular file"));
            }
            if before.len() > limit as u64 {
                return Err(std::io::Error::other(
                    "input exceeds file or aggregate byte limit",
                ));
            }
            let mut bytes = vec![];
            (&mut file)
                .take((limit as u64).saturating_add(1))
                .read_to_end(&mut bytes)?;
            let after = file.metadata()?;
            if bytes.len() > limit {
                return Err(std::io::Error::other(
                    "input exceeds file or aggregate byte limit",
                ));
            }
            if before.len() != after.len()
                || before.mtime() != after.mtime()
                || before.mtime_nsec() != after.mtime_nsec()
                || before.ctime() != after.ctime()
                || before.ctime_nsec() != after.ctime_nsec()
                || bytes.len() as u64 != after.len()
            {
                return Err(std::io::Error::other("input changed while snapshotting"));
            }
            return Ok(bytes);
        }
    }
    Err(std::io::Error::other("input path is empty"))
}
