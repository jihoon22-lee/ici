//! Versioned native observation cache. Cached data never supplies its own execution identity.
use crate::{ApplicationError, Result};
use ici_domain::{
    AnalysisOutput, Observation, TaskSpec, Validate, canonical_json, sha256, validation,
};
use ici_execution::storage::{WriteMode, atomic_write};
use nix::{
    fcntl::{AtFlags, OFlag, openat},
    sys::stat::{Mode, SFlag, fstatat},
    unistd::{UnlinkatFlags, unlinkat},
};
use serde::{Deserialize, Serialize};
use std::{fs::File, io::Read, os::fd::AsRawFd, path::PathBuf};

const SCHEMA_ID: &str = "ici.native.observation";
const MAX_CACHE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CacheIdentity {
    pub core_digest: String,
    pub policy_digest: String,
    pub environment_digest: String,
    /// Provider parsing options/context, independently of process completion.
    pub interpretation_digest: String,
    pub input_digests: Vec<(String, String)>,
    /// False for unresolved native extends/plugins/header/dependency closure.
    pub inputs_complete: bool,
}
impl CacheIdentity {
    pub fn key(&self, task: &TaskSpec) -> Result<Option<String>> {
        if !self.inputs_complete || !task.cacheable || task.mutating() || task.tool_digest.is_none()
        {
            return Ok(None);
        }
        task.validate()
            .map_err(|e| ApplicationError(e.to_string()))?;
        for digest in [
            &self.core_digest,
            &self.policy_digest,
            &self.environment_digest,
            &self.interpretation_digest,
        ] {
            validation::digest(digest, "cache identity")
                .map_err(|e| ApplicationError(e.to_string()))?;
        }
        let mut inputs = self.input_digests.clone();
        inputs.sort();
        if inputs.windows(2).any(|pair| pair[0].0 == pair[1].0) {
            return Err(ApplicationError("duplicate cache input reference".into()));
        }
        for (_, digest) in &inputs {
            validation::digest(digest, "cache input")
                .map_err(|e| ApplicationError(e.to_string()))?;
        }
        if task
            .input_refs
            .iter()
            .any(|reference| !inputs.iter().any(|(name, _)| name == reference))
        {
            return Ok(None);
        }
        Ok(Some(sha256(
            canonical_json(&(
                SCHEMA_ID,
                1,
                &self.core_digest,
                &self.policy_digest,
                &self.environment_digest,
                &self.interpretation_digest,
                inputs,
                task.share_key()
                    .map_err(|e| ApplicationError(e.to_string()))?,
            ))
            .map_err(|e| ApplicationError(e.to_string()))?,
        )))
    }
}
#[derive(Clone, Debug)]
pub struct ObservationCache {
    pub directory: PathBuf,
    pub core_digest: String,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct CacheStats {
    pub entries: usize,
    pub valid_entries: usize,
    pub corrupt_entries: usize,
    pub bytes: u64,
    pub unrecognized: usize,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    schema_id: String,
    schema_version: u32,
    key: String,
    core_digest: String,
    observation: Observation,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    analysis: Option<AnalysisOutput>,
}
impl ObservationCache {
    fn name(key: &str) -> Result<String> {
        validation::digest(key, "cache key").map_err(|e| ApplicationError(e.to_string()))?;
        Ok(format!(
            "{}.json",
            key.strip_prefix("sha256:").expect("validated")
        ))
    }
    fn read_entry(&self, key: &str) -> Result<Option<Entry>> {
        validation::digest(&self.core_digest, "cache core identity")
            .map_err(|e| ApplicationError(e.to_string()))?;
        let name = Self::name(key)?;
        let directory = match crate::snapshot::directory(&self.directory) {
            Ok(directory) => directory,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(ApplicationError(format!("cache directory: {error}"))),
        };
        let fd = match openat(
            &directory,
            name.as_str(),
            OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
            Mode::empty(),
        ) {
            Ok(fd) => fd,
            Err(nix::errno::Errno::ENOENT) => return Ok(None),
            Err(error) => return Err(ApplicationError(format!("cache entry: {error}"))),
        };
        let mut file = File::from(fd);
        let metadata = file
            .metadata()
            .map_err(|e| ApplicationError(e.to_string()))?;
        if !metadata.is_file() || metadata.len() > MAX_CACHE_BYTES as u64 {
            return Err(ApplicationError(
                "cache entry is not a bounded regular file".into(),
            ));
        }
        let mut bytes = vec![];
        (&mut file)
            .take((MAX_CACHE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|e| ApplicationError(e.to_string()))?;
        if bytes.len() > MAX_CACHE_BYTES {
            return Err(ApplicationError("cache entry exceeds byte limit".into()));
        }
        let entry: Entry = serde_json::from_slice(&bytes)
            .map_err(|e| ApplicationError(format!("invalid cache entry: {e}")))?;
        if entry.schema_id != SCHEMA_ID
            || entry.schema_version != 1
            || entry.key != key
            || entry.core_digest != self.core_digest
        {
            return Err(ApplicationError(
                "cache entry identity does not match requested computation".into(),
            ));
        }
        entry
            .observation
            .validate()
            .map_err(|e| ApplicationError(e.to_string()))?;
        if !entry.observation.evidence_is_complete() {
            return Err(ApplicationError(
                "cache entry has incomplete evidence".into(),
            ));
        }
        if let Some(analysis) = &entry.analysis {
            validate_output(analysis)?;
            if analysis.observation != entry.observation {
                return Err(ApplicationError(
                    "cached rich output disagrees with its observation".into(),
                ));
            }
        }
        Ok(Some(entry))
    }
    pub fn get(&self, key: &str, task_id: &str) -> Result<Option<Observation>> {
        Ok(self.read_entry(key)?.map(|entry| {
            let mut observation = entry.observation;
            observation.task_id = task_id.into();
            observation
        }))
    }
    /// Old observation-only entries cannot invent the richer inspected targets.
    pub fn get_output(&self, key: &str, task_id: &str) -> Result<Option<AnalysisOutput>> {
        Ok(self
            .read_entry(key)?
            .and_then(|entry| entry.analysis)
            .map(|mut output| {
                output.observation.task_id = task_id.into();
                output
            }))
    }
    pub fn put_output(&self, key: &str, output: &AnalysisOutput) -> Result<bool> {
        validate_output(output)?;
        self.put_entry(key, &output.observation, Some(output))
    }
    pub fn put(&self, key: &str, observation: &Observation) -> Result<bool> {
        self.put_entry(key, observation, None)
    }
    fn put_entry(
        &self,
        key: &str,
        observation: &Observation,
        analysis: Option<&AnalysisOutput>,
    ) -> Result<bool> {
        validation::digest(&self.core_digest, "cache core identity")
            .map_err(|e| ApplicationError(e.to_string()))?;
        let name = Self::name(key)?;
        observation
            .validate()
            .map_err(|e| ApplicationError(e.to_string()))?;
        if !observation.evidence_is_complete() {
            return Ok(false);
        }
        let entry = Entry {
            schema_id: SCHEMA_ID.into(),
            schema_version: 1,
            key: key.into(),
            core_digest: self.core_digest.clone(),
            observation: observation.clone(),
            analysis: analysis.cloned(),
        };
        let bytes = canonical_json(&entry)
            .map_err(|e| ApplicationError(e.to_string()))?
            .into_bytes();
        if bytes.len() > MAX_CACHE_BYTES {
            return Ok(false);
        }
        atomic_write(
            &self.directory.join(name),
            &bytes,
            WriteMode::ReplaceRegular,
        )
        .map_err(|e| ApplicationError(format!("cache write: {e}")))?;
        Ok(true)
    }
    /// Inspect/unlink exact entry names through a stable no-follow directory descriptor. Unknown
    /// files and subdirectories are never removed by a native cache clear.
    pub fn inventory(&self, clear: bool) -> Result<CacheStats> {
        let directory = match crate::snapshot::directory(&self.directory) {
            Ok(directory) => directory,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(CacheStats::default());
            }
            Err(error) => return Err(ApplicationError(format!("cache directory: {error}"))),
        };
        let mut stats = CacheStats::default();
        let entries = std::fs::read_dir(format!("/proc/self/fd/{}", directory.as_raw_fd()))
            .map_err(|e| ApplicationError(e.to_string()))?;
        for entry in entries {
            let entry = entry.map_err(|e| ApplicationError(e.to_string()))?;
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                stats.unrecognized += 1;
                continue;
            };
            let Some(key) = name
                .strip_suffix(".json")
                .map(|stem| format!("sha256:{stem}"))
            else {
                stats.unrecognized += 1;
                continue;
            };
            if validation::digest(&key, "cache key").is_err() {
                stats.unrecognized += 1;
                continue;
            }
            let metadata = fstatat(&directory, name, AtFlags::AT_SYMLINK_NOFOLLOW)
                .map_err(|e| ApplicationError(e.to_string()))?;
            if metadata.st_mode & SFlag::S_IFMT.bits() != SFlag::S_IFREG.bits() {
                stats.unrecognized += 1;
                continue;
            }
            stats.entries += 1;
            if inventory_entry_valid(&directory, name, &key) {
                stats.valid_entries += 1;
            } else {
                stats.corrupt_entries += 1;
            }
            stats.bytes = stats.bytes.saturating_add(metadata.st_size.max(0) as u64);
            if clear {
                unlinkat(&directory, name, UnlinkatFlags::NoRemoveDir)
                    .map_err(|e| ApplicationError(e.to_string()))?;
            }
        }
        Ok(stats)
    }
}

fn inventory_entry_valid(directory: &std::os::fd::OwnedFd, name: &str, key: &str) -> bool {
    let read = || -> Option<Entry> {
        let fd = openat(
            directory,
            name,
            OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK | OFlag::O_CLOEXEC,
            Mode::empty(),
        )
        .ok()?;
        let file = File::from(fd);
        let metadata = file.metadata().ok()?;
        if !metadata.is_file() || metadata.len() > MAX_CACHE_BYTES as u64 {
            return None;
        }
        let mut bytes = vec![];
        file.take((MAX_CACHE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .ok()?;
        if bytes.len() > MAX_CACHE_BYTES {
            return None;
        }
        serde_json::from_slice(&bytes).ok()
    };
    let Some(entry) = read() else { return false };
    entry.schema_id == SCHEMA_ID
        && entry.schema_version == 1
        && entry.key == key
        && validation::digest(&entry.core_digest, "cache core identity").is_ok()
        && entry.observation.validate().is_ok()
        && entry.observation.evidence_is_complete()
        && entry.analysis.as_ref().is_none_or(|analysis| {
            validate_output(analysis).is_ok() && analysis.observation == entry.observation
        })
}

fn validate_output(output: &AnalysisOutput) -> Result<()> {
    output
        .observation
        .validate()
        .map_err(|error| ApplicationError(error.to_string()))?;
    for target in &output.targets {
        validation::relative_path(&target.file_path, false)
            .map_err(|error| ApplicationError(error.to_string()))?;
        if target.start_line == 0
            || target.end_line.is_some_and(|line| line < target.start_line)
            || target.start_column == Some(0)
            || target.end_column == Some(0)
            || (target.end_line.is_none_or(|line| line == target.start_line)
                && target
                    .start_column
                    .zip(target.end_column)
                    .is_some_and(|(start, end)| end < start))
            || !["PASS", "WARN", "FAIL", "ERROR", "SKIP"].contains(&target.status.as_str())
        {
            return Err(ApplicationError(
                "cached inspected target has an invalid position/status".into(),
            ));
        }
    }
    if !["PASS", "WARN", "FAIL", "ERROR", "SKIP"].contains(&output.status.as_str())
        || output.score.is_some_and(|value| !value.is_finite())
        || output.max_score.is_some_and(|value| !value.is_finite())
    {
        return Err(ApplicationError(
            "cached rich output has invalid status/score".into(),
        ));
    }
    Ok(())
}
