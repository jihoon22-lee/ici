use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fmt, path::PathBuf};

pub const MAX_ARCHIVE_BYTES: u64 = 256 * 1024 * 1024;
pub const MAX_TOTAL_BYTES: u64 = 512 * 1024 * 1024;
pub const MAX_FILE_BYTES: u64 = 256 * 1024 * 1024;
pub const MAX_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
pub const MAX_FILES: usize = 8192;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BuildInputs {
    pub source_revision: String,
    pub source_tree_sha256: String,
    pub cargo_lock_sha256: String,
    pub rust_toolchain: String,
    pub target: String,
    pub profile: String,
    pub source_date_epoch: u64,
    pub rust_flags: Vec<String>,
    /// Measured binary requirements and actual tested host; this never infers support.
    pub platform_evidence: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FileRecord {
    pub size: u64,
    pub sha256: String,
    pub executable: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_id: String,
    pub schema_version: u32,
    pub version: String,
    pub target: String,
    pub build_inputs: BuildInputs,
    pub build_inputs_sha256: String,
    pub files: BTreeMap<String, FileRecord>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BundleInput {
    pub path: String,
    pub source: PathBuf,
    pub executable: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct BundleIdentity {
    pub path: PathBuf,
    pub sha256: String,
    pub size: u64,
    pub manifest: Manifest,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Installation {
    pub root: PathBuf,
    pub selected: String,
    pub previous: Option<String>,
    pub executable: PathBuf,
    pub manifest: Manifest,
    pub path_collisions: Vec<PathBuf>,
    pub guidance: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub code: &'static str,
    pub message: String,
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for Error {}
pub(crate) fn error(code: &'static str, message: impl Into<String>) -> Error {
    Error {
        code,
        message: message.into(),
    }
}
pub(crate) fn io_error(_: std::io::Error) -> Error {
    error(
        "filesystem",
        "Native distribution filesystem operation failed",
    )
}
pub(crate) fn digest(bytes: &[u8]) -> String {
    use sha2::Digest;
    format!("{:x}", sha2::Sha256::digest(bytes))
}
pub(crate) fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
pub(crate) fn valid_version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 80
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        && value != "."
        && value != ".."
}
pub(crate) fn safe_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 240
        && !value.starts_with('/')
        && value.split('/').count() <= 16
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_' | b'/'))
        && value
            .split('/')
            .all(|part| !matches!(part, "" | "." | ".."))
}
impl Manifest {
    pub fn validate(&self) -> Result<(), Error> {
        if self.schema_id != "ici.native-distribution" || self.schema_version != 1 {
            return Err(error(
                "manifest-schema",
                "Unsupported native distribution manifest",
            ));
        }
        if !valid_version(&self.version)
            || !matches!(
                self.target.as_str(),
                "x86_64-unknown-linux-gnu" | "x86_64-unknown-linux-musl"
            )
            || self.target != self.build_inputs.target
        {
            return Err(error(
                "manifest-identity",
                "Native distribution version or target is invalid",
            ));
        }
        if !valid_digest(&self.build_inputs.source_tree_sha256)
            || !valid_digest(&self.build_inputs.cargo_lock_sha256)
            || self.build_inputs.rust_toolchain != "1.99.1"
            || self.build_inputs.profile != "release"
            || self.build_inputs.source_revision.len() != 40
            || !self
                .build_inputs
                .source_revision
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
            || self.build_inputs_sha256
                != digest(
                    &serde_json::to_vec(&self.build_inputs).map_err(|_| {
                        error("manifest-inputs", "Build input serialization failed")
                    })?,
                )
        {
            return Err(error(
                "manifest-inputs",
                "Build input lock is inconsistent or unsupported",
            ));
        }
        if self.files.len() > MAX_FILES || self.files.is_empty() {
            return Err(error(
                "manifest-bound",
                "Native distribution file count exceeds its bound",
            ));
        }
        let mut total = 0u64;
        for (path, file) in &self.files {
            if !safe_path(path)
                || !valid_digest(&file.sha256)
                || file.size > MAX_FILE_BYTES
                || !(matches!(
                    path.as_str(),
                    "bin/ici"
                        | "tools/manifest.json"
                        | "tools/bin/ruff"
                        | "tools/bin/ty"
                        | "README.txt"
                ) || path.starts_with("licenses/"))
                || file.executable
                    != matches!(path.as_str(), "bin/ici" | "tools/bin/ruff" | "tools/bin/ty")
            {
                return Err(error(
                    "manifest-file",
                    "Native distribution contains an invalid or undeclared product path",
                ));
            }
            total = total
                .checked_add(file.size)
                .ok_or_else(|| error("manifest-bound", "Native distribution size overflow"))?;
        }
        if total > MAX_TOTAL_BYTES {
            return Err(error(
                "manifest-bound",
                "Native distribution exceeds its expanded byte bound",
            ));
        }
        for path in [
            "bin/ici",
            "tools/manifest.json",
            "tools/bin/ruff",
            "tools/bin/ty",
            "licenses/ici-MIT.txt",
            "licenses/Unicode-V3.txt",
            "licenses/ruff.txt",
            "licenses/ty.txt",
            "licenses/THIRD-PARTY-NOTICES.txt",
            "README.txt",
        ] {
            if !self.files.contains_key(path) {
                return Err(error(
                    "manifest-required",
                    "Native distribution omits a required executable or license",
                ));
            }
        }
        Ok(())
    }
}
