use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Read;
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
            "ruff" | "ty" | "mypy" | "pytest" | "coverage" | "python"
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
        let path = path
            .canonicalize()
            .map_err(|e| error(&self.id, e.to_string()))?;
        let digest = executable_digest(&path)
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
    if let Some(value) = explicit {
        return match value {
            "mypy" => Ok(TypeProvider::Mypy),
            "ty" => Ok(TypeProvider::Ty),
            _ => Err(error("python.type", "type_provider must be mypy or ty")),
        };
    }
    let project_path = root.join("pyproject.toml");
    let source = match fs::read_to_string(&project_path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => {
            return Err(error(
                "python.type",
                format!("{}: {e}", project_path.display()),
            ));
        }
    };
    let pyproject = source
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| error("python.type", format!("{}: {e}", project_path.display())))?;
    let native_table = |name: &str| {
        pyproject
            .get("tool")
            .and_then(|tool| tool.get(name))
            .is_some()
    };
    let mypy = root.join("mypy.ini").is_file()
        || root.join(".mypy.ini").is_file()
        || native_table("mypy")
        || fs::read_to_string(root.join("setup.cfg")).is_ok_and(|s| has_table(&s, "mypy"));
    let ty = root.join("ty.toml").is_file() || native_table("ty");
    match (mypy, ty) {
        (true, true) => Err(error(
            "python.type",
            "both mypy and ty configurations exist; choose python.type_provider explicitly",
        )),
        (true, false) => Ok(TypeProvider::Mypy),
        _ => Ok(TypeProvider::Ty),
    }
}
fn has_table(source: &str, name: &str) -> bool {
    source.lines().any(|line| {
        line.split('#')
            .next()
            .unwrap_or("")
            .trim()
            .strip_prefix('[')
            .and_then(|s| s.strip_suffix(']'))
            .is_some_and(|table| table == name || table.starts_with(&format!("{name}.")))
    })
}

pub fn executable_digest(path: &Path) -> std::io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("sha256:{:x}", hasher.finalize()))
}
