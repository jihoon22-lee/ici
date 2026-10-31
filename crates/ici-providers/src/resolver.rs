use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ToolSource {
    Explicit,
    Project,
    Pack,
    Path,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResolvedTool {
    pub id: String,
    pub path: PathBuf,
    pub source: ToolSource,
    pub digest: String,
    /// Plan does not probe. Version evidence is filled by an explicit execution task.
    pub version: Option<String>,
    pub configuration: Vec<PathBuf>,
}
#[derive(Clone, Debug)]
pub struct ToolRequest {
    pub id: String,
    pub explicit: Option<PathBuf>,
    pub project_root: PathBuf,
    pub pack_root: Option<PathBuf>,
    pub path_env: Option<String>,
}
#[derive(Debug, thiserror::Error)]
#[error("{id}: {reason}")]
pub struct ResolutionError {
    pub id: String,
    pub reason: String,
}
fn error(id: &str, reason: impl Into<String>) -> ResolutionError {
    ResolutionError {
        id: id.into(),
        reason: reason.into(),
    }
}
#[derive(Deserialize)]
struct PackManifest {
    schema_id: String,
    schema_version: u32,
    tools: BTreeMap<String, PackTool>,
}
#[derive(Deserialize)]
struct PackTool {
    path: String,
    sha256: String,
    version: String,
    license: String,
    target: String,
}

impl ToolRequest {
    /// Existence/content checks only: no process, network, install, or environment mutation.
    pub fn resolve(&self) -> Result<ResolvedTool, ResolutionError> {
        if self.id.is_empty() || self.id.contains(['/', '\\', '\0']) {
            return Err(error(&self.id, "invalid tool identifier"));
        }
        if let Some(path) = &self.explicit {
            let path = if path.is_absolute() {
                path.clone()
            } else {
                self.project_root.join(path)
            };
            return self.accept(path, ToolSource::Explicit, None);
        }
        let executable = match self.id.as_str() {
            "clippy" => "cargo",
            other => other,
        };
        let project_candidates = if matches!(executable, "eslint" | "tsc") {
            vec![self.project_root.join("node_modules/.bin").join(executable)]
        } else if matches!(
            executable,
            "ruff"
                | "ty"
                | "mypy"
                | "pytest"
                | "coverage"
                | "python"
                | "mutmut"
                | "cosmic-ray"
                | "mutpy"
        ) {
            vec![
                self.project_root.join(".venv/bin").join(executable),
                self.project_root.join("venv/bin").join(executable),
            ]
        } else {
            vec![]
        };
        for path in project_candidates {
            if fs::symlink_metadata(&path).is_ok() {
                return self.accept(path, ToolSource::Project, None);
            }
        }
        if let Some(root) = &self.pack_root {
            let manifest_path = root.join("manifest.json");
            if manifest_path.exists() {
                let raw = fs::read(&manifest_path)
                    .map_err(|e| error(&self.id, format!("tool pack manifest: {e}")))?;
                let manifest: PackManifest = serde_json::from_slice(&raw)
                    .map_err(|e| error(&self.id, format!("invalid tool pack manifest: {e}")))?;
                if manifest.schema_id != "ici.native-tools" || manifest.schema_version != 1 {
                    return Err(error(&self.id, "unsupported tool pack manifest"));
                }
                if let Some(tool) = manifest.tools.get(&self.id) {
                    ici_domain::validation::relative_path(&tool.path, false)
                        .map_err(|e| error(&self.id, e.to_string()))?;
                    if tool.license.trim().is_empty() || tool.target != "x86_64-unknown-linux-musl"
                    {
                        return Err(error(&self.id, "tool pack license/target is not supported"));
                    }
                    let path = root.join(&tool.path);
                    let canonical_root = root
                        .canonicalize()
                        .map_err(|e| error(&self.id, e.to_string()))?;
                    let canonical_path = path
                        .canonicalize()
                        .map_err(|e| error(&self.id, e.to_string()))?;
                    if !canonical_path.starts_with(canonical_root) {
                        return Err(error(
                            &self.id,
                            "tool pack executable escapes its installation",
                        ));
                    }
                    let mut resolved = self.accept(path, ToolSource::Pack, Some(&tool.sha256))?;
                    resolved.version = Some(tool.version.clone());
                    resolved.configuration.push(manifest_path.clone());
                    return Ok(resolved);
                }
            }
        }
        if let Some(paths) = &self.path_env {
            for directory in std::env::split_paths(paths) {
                // Empty PATH components mean cwd to the shell; implicit cwd execution is excluded.
                if directory.as_os_str().is_empty() {
                    continue;
                }
                let path = directory.join(executable);
                if path.is_file() && executable_file(&path) {
                    return self.accept(path, ToolSource::Path, None);
                }
            }
        }
        Err(error(
            &self.id,
            "not found; prepare the project tool or install the verified native tool pack",
        ))
    }
    fn accept(
        &self,
        path: PathBuf,
        source: ToolSource,
        expected: Option<&str>,
    ) -> Result<ResolvedTool, ResolutionError> {
        if !path.is_file() || !executable_file(&path) {
            return Err(error(
                &self.id,
                format!(
                    "selected executable is missing or not executable: {}",
                    path.display()
                ),
            ));
        }
        // Preserve argv[0] dispatch for rustup/cargo and other multicall symlinks.
        let path = if path.is_absolute() {
            path
        } else {
            self.project_root.join(path)
        };
        let canonical = path
            .canonicalize()
            .map_err(|e| error(&self.id, e.to_string()))?;
        let digest = executable_digest(&canonical)
            .map_err(|e| error(&self.id, format!("cannot hash selected executable: {e}")))?;
        if let Some(expected) = expected
            && digest != expected
            && digest.strip_prefix("sha256:") != Some(expected)
        {
            return Err(error(&self.id, "native tool pack checksum mismatch"));
        }
        Ok(ResolvedTool {
            id: self.id.clone(),
            path,
            source,
            digest,
            version: None,
            configuration: vec![],
        })
    }
}
fn executable_file(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TypeProvider {
    Mypy,
    Ty,
}
/// A configured checker retains its own semantics. No transparent mypy-to-ty replacement.
pub fn type_provider(root: &Path, explicit: Option<&str>) -> Result<TypeProvider, ResolutionError> {
    match ici_config::discover_type_provider(root, root, explicit)
        .map_err(|problem| error("python.type", problem.to_string()))?
        .as_deref()
    {
        Some("mypy") => Ok(TypeProvider::Mypy),
        Some("ty") | None => Ok(TypeProvider::Ty),
        Some(other) => Err(error(
            "python.type",
            format!("unsupported type provider {other}"),
        )),
    }
}

pub use ici_execution::process::executable_digest;
