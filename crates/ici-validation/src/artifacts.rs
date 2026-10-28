//! Artifact bytes must belong to a newly created, no-follow run directory.
use crate::{OwnedArtifactSpec, ValidationError, ValidationPlan};
use ici_domain::sha256;
use ici_execution::TaskOutcome;
use nix::{
    fcntl::{OFlag, open, openat},
    sys::stat::{Mode, fstat, mkdirat},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    os::fd::OwnedFd,
    path::{Component, Path, PathBuf},
    time::SystemTime,
};

/// A non-deserializable capability proves this process created the run directory afresh.
#[derive(Debug)]
pub struct OwnedRun {
    path: PathBuf,
    directory: OwnedFd,
    device: u64,
    inode: u64,
    not_before: SystemTime,
}
impl OwnedRun {
    /// Parent directories must already exist. Existing run directories are rejected.
    pub fn create(path: &Path) -> Result<Self, ValidationError> {
        let mut parts: Vec<_> = path.components().collect();
        let Some(Component::Normal(name)) = parts.pop() else {
            return Err(ValidationError(
                "run directory needs a new directory name".into(),
            ));
        };
        if !path.is_absolute() {
            return Err(ValidationError("run directory must be absolute".into()));
        }
        let mut directory =
            open(Path::new("/"), directory_flags(), Mode::empty()).map_err(error)?;
        for part in parts {
            match part {
                Component::RootDir => {}
                Component::Normal(name) => {
                    directory = openat(&directory, name, directory_flags(), Mode::empty())
                        .map_err(error)?;
                }
                _ => return Err(ValidationError("run directory must be normalized".into())),
            }
        }
        let not_before = SystemTime::now();
        mkdirat(&directory, name, Mode::S_IRWXU).map_err(error)?;
        let directory =
            openat(&directory, name, directory_flags(), Mode::empty()).map_err(error)?;
        let stat = fstat(&directory).map_err(error)?;
        Ok(Self {
            path: path.into(),
            directory,
            device: stat.st_dev,
            inode: stat.st_ino,
            not_before,
        })
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    fn validate(&self, plan: &ValidationPlan) -> Result<(), ValidationError> {
        if self.path != plan.context.owned_run_dir {
            return Err(ValidationError(
                "artifact run owner does not match plan".into(),
            ));
        }
        let stat = fstat(&self.directory).map_err(error)?;
        let current = std::fs::symlink_metadata(&self.path).map_err(error)?;
        use std::os::unix::fs::MetadataExt;
        if !current.is_dir()
            || current.dev() != self.device
            || current.ino() != self.inode
            || stat.st_dev != self.device
            || stat.st_ino != self.inode
        {
            return Err(ValidationError(
                "artifact run directory was replaced".into(),
            ));
        }
        Ok(())
    }
}
fn directory_flags() -> OFlag {
    OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC
}
fn error(error: impl std::fmt::Display) -> ValidationError {
    ValidationError(error.to_string())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CapturedArtifact {
    pub id: String,
    pub producer_task_id: String,
    pub bytes: Vec<u8>,
    pub digest: String,
}
#[derive(Clone, Debug, Default)]
pub struct CapturedArtifacts {
    pub files: BTreeMap<String, CapturedArtifact>,
    pub errors: BTreeMap<String, String>,
}
/// Capture only completed producers. No-follow regular-file reads enforce each byte limit.
pub fn capture_artifacts(
    plan: &ValidationPlan,
    run: &OwnedRun,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<CapturedArtifacts, ValidationError> {
    run.validate(plan)?;
    let mut captured = CapturedArtifacts::default();
    for spec in &plan.artifacts {
        if !spec.path.starts_with(run.path()) {
            return Err(ValidationError("artifact escapes its run directory".into()));
        }
        let Some(outcome) = outcomes.get(&spec.producer_task_id) else {
            continue;
        };
        if !crate::planning::completed(outcome) {
            captured
                .errors
                .insert(spec.id.clone(), "artifact producer did not complete".into());
            continue;
        }
        match read_spec(spec, run) {
            Ok(bytes) => {
                captured.files.insert(
                    spec.id.clone(),
                    CapturedArtifact {
                        id: spec.id.clone(),
                        producer_task_id: spec.producer_task_id.clone(),
                        digest: sha256(&bytes),
                        bytes,
                    },
                );
            }
            Err(error) => {
                captured.errors.insert(spec.id.clone(), error.to_string());
            }
        }
    }
    run.validate(plan)?;
    Ok(captured)
}
fn read_spec(spec: &OwnedArtifactSpec, run: &OwnedRun) -> Result<Vec<u8>, ValidationError> {
    let maximum = usize::try_from(spec.max_bytes).map_err(error)?;
    let relative = spec.path.strip_prefix(run.path()).map_err(error)?;
    let mut parts: Vec<_> = relative.components().collect();
    let Some(Component::Normal(name)) = parts.pop() else {
        return Err(ValidationError("artifact needs a regular file name".into()));
    };
    let mut directory = run.directory.try_clone().map_err(error)?;
    for part in parts {
        let Component::Normal(name) = part else {
            return Err(ValidationError("artifact path is not normalized".into()));
        };
        directory = openat(&directory, name, directory_flags(), Mode::empty()).map_err(error)?;
    }
    let descriptor = openat(
        &directory,
        name,
        OFlag::O_RDONLY | OFlag::O_NONBLOCK | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC,
        Mode::empty(),
    )
    .map_err(error)?;
    let mut file = File::from(descriptor);
    let before = file.metadata().map_err(error)?;
    use std::os::unix::fs::MetadataExt;
    if !before.is_file()
        || before.nlink() != 1
        || before.len() > maximum as u64
        || before.modified().map_err(error)? < run.not_before
    {
        return Err(ValidationError(
            "artifact is stale, linked, nonregular or larger than its bound".into(),
        ));
    }
    let mut bytes = vec![];
    file.by_ref()
        .take((maximum as u64).saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(error)?;
    let after = file.metadata().map_err(error)?;
    if bytes.len() > maximum
        || before.len() != after.len()
        || before.mtime() != after.mtime()
        || before.mtime_nsec() != after.mtime_nsec()
        || before.ctime() != after.ctime()
        || before.ctime_nsec() != after.ctime_nsec()
    {
        return Err(ValidationError(
            "artifact changed while being captured".into(),
        ));
    }
    Ok(bytes)
}
