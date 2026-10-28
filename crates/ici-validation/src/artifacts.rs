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
    sync::Mutex,
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
    starts: Mutex<BTreeMap<String, SystemTime>>,
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
            starts: Mutex::default(),
        })
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    /// Call from the scheduler start callback immediately before this step is launched.
    pub fn mark_started(&self, task_id: &str) {
        self.starts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .insert(task_id.into(), SystemTime::now());
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
    pub outputs: BTreeMap<String, OutputWitness>,
}
#[derive(Clone, Debug)]
pub struct OutputWitness {
    pub path: PathBuf,
    pub size: u64,
    pub producer_task_id: String,
    pub fresh: bool,
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
    for pattern in &plan.artifact_patterns {
        let Some(outcome) = outcomes.get(&pattern.producer_task_id) else {
            continue;
        };
        if !crate::planning::completed(outcome) {
            captured.errors.insert(
                pattern.id_prefix.clone(),
                "artifact producer did not complete".into(),
            );
            continue;
        }
        let mut entries = std::fs::read_dir(run.path())
            .map_err(error)?
            .take(16385)
            .collect::<Result<Vec<_>, _>>()
            .map_err(error)?;
        if entries.len() > 16384 {
            return Err(ValidationError(
                "owned artifact directory inventory exceeds limit".into(),
            ));
        }
        entries.sort_by_key(|entry| entry.file_name());
        let mut count = 0usize;
        let mut total = 0u64;
        for entry in entries {
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                continue;
            };
            if !name.ends_with(&pattern.suffix) {
                continue;
            }
            count += 1;
            if count > pattern.max_files {
                return Err(ValidationError(
                    "artifact report count exceeds bound".into(),
                ));
            }
            let spec = OwnedArtifactSpec {
                id: format!("{}:{name}", pattern.id_prefix),
                path: run.path().join(name),
                producer_task_id: pattern.producer_task_id.clone(),
                max_bytes: pattern.max_bytes_each,
                required: true,
            };
            match read_spec(&spec, run) {
                Ok(bytes) => {
                    total = total
                        .checked_add(bytes.len() as u64)
                        .ok_or_else(|| ValidationError("artifact report size overflow".into()))?;
                    if total > pattern.max_bytes_total {
                        return Err(ValidationError(
                            "aggregate artifact report byte limit exceeded".into(),
                        ));
                    }
                    let digest = sha256(&bytes);
                    captured.files.insert(
                        spec.id.clone(),
                        CapturedArtifact {
                            id: spec.id,
                            producer_task_id: spec.producer_task_id,
                            bytes,
                            digest,
                        },
                    );
                }
                Err(error) => {
                    captured.errors.insert(spec.id, error.to_string());
                }
            }
        }
        if count == 0 {
            captured.errors.insert(
                pattern.id_prefix.clone(),
                "producer wrote no matching fresh report".into(),
            );
        }
    }
    if plan.kind == crate::ValidationKind::Integration {
        let producer = format!("{}.case", plan.context.task_id);
        let started = run
            .starts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .get(&producer)
            .copied();
        for (index, assertion) in plan.context.options.outputs.iter().enumerate() {
            let id = format!("integration-output:{index}");
            let path = if assertion.path.is_absolute() {
                assertion.path.clone()
            } else {
                plan.context.cwd.join(&assertion.path)
            };
            let result = (|| -> Result<OutputWitness, ValidationError> {
                let started = started.ok_or_else(|| {
                    ValidationError("integration case start timestamp is absent".into())
                })?;
                if !path.starts_with(&plan.context.root) {
                    return Err(ValidationError("declared output escapes workspace".into()));
                }
                let outcome = outcomes
                    .get(&producer)
                    .filter(|outcome| crate::planning::completed(outcome))
                    .ok_or_else(|| {
                        ValidationError("integration output producer did not complete".into())
                    })?;
                let file = open_nofollow(&path)?;
                let metadata = file.metadata().map_err(error)?;
                if !metadata.is_file() {
                    return Err(ValidationError(
                        "declared output is not a regular file".into(),
                    ));
                }
                let fresh = metadata.modified().map_err(error)? >= started;
                Ok(OutputWitness {
                    path: path.clone(),
                    size: metadata.len(),
                    producer_task_id: outcome.task_id.clone(),
                    fresh,
                })
            })();
            match result {
                Ok(witness) => {
                    captured.outputs.insert(id, witness);
                }
                Err(error) => {
                    captured.errors.insert(id, error.to_string());
                }
            }
        }
    }
    if plan.kind == crate::ValidationKind::Gcov {
        let producer = plan
            .context
            .options
            .shared_test_task_id
            .as_ref()
            .ok_or_else(|| ValidationError("gcov shared test owner absent".into()))?;
        let started = run
            .starts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .get(producer)
            .copied();
        for target in &plan.context.targets {
            let key = format!("gcov-data:{target}");
            let path = plan.context.root.join(target).with_extension("gcda");
            let result = (|| -> Result<OutputWitness, ValidationError> {
                let started = started.ok_or_else(|| {
                    ValidationError("instrumented suite start timestamp absent".into())
                })?;
                let metadata = open_nofollow(&path)?.metadata().map_err(error)?;
                if !metadata.is_file() || metadata.len() == 0 {
                    return Err(ValidationError(
                        "instrumented suite wrote no regular gcda evidence".into(),
                    ));
                }
                Ok(OutputWitness {
                    path,
                    size: metadata.len(),
                    producer_task_id: producer.clone(),
                    fresh: metadata.modified().map_err(error)? >= started,
                })
            })();
            match result {
                Ok(witness) => {
                    captured.outputs.insert(key, witness);
                }
                Err(error) => {
                    captured.errors.insert(key, error.to_string());
                }
            }
        }
    }
    run.validate(plan)?;
    Ok(captured)
}
fn open_nofollow(path: &Path) -> Result<File, ValidationError> {
    if !path.is_absolute() {
        return Err(ValidationError("artifact path must be absolute".into()));
    }
    let mut parts = path.components().collect::<Vec<_>>();
    let Some(Component::Normal(name)) = parts.pop() else {
        return Err(ValidationError("artifact path needs file name".into()));
    };
    let mut directory = open(Path::new("/"), directory_flags(), Mode::empty()).map_err(error)?;
    for part in parts {
        match part {
            Component::RootDir => {}
            Component::Normal(name) => {
                directory =
                    openat(&directory, name, directory_flags(), Mode::empty()).map_err(error)?
            }
            _ => return Err(ValidationError("artifact path is not normalized".into())),
        }
    }
    let descriptor = openat(
        &directory,
        name,
        OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
        Mode::empty(),
    )
    .map_err(error)?;
    Ok(File::from(descriptor))
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
