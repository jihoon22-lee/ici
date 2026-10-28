use ici_domain::{Profile, ScopeKind};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SchemaFamily {
    Automatic,
    SparseV2,
    NextV1,
    Legacy,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Origin {
    pub file: String,
    pub line: usize,
    pub column: usize,
    pub key: String,
    /// defaults, discovered, root, component, local, or global_preference.
    pub layer: String,
}
impl Origin {
    pub fn defaults(key: impl Into<String>) -> Self {
        Self {
            file: "<built-in defaults>".into(),
            line: 1,
            column: 1,
            key: key.into(),
            layer: "defaults".into(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, thiserror::Error)]
#[error("{file}:{line}:{column}: {key}: {message}", file=.origin.file, line=.origin.line, column=.origin.column, key=.origin.key)]
pub struct ConfigError {
    pub message: String,
    pub origin: Origin,
}
impl ConfigError {
    pub fn new(message: impl Into<String>, origin: Origin) -> Self {
        Self {
            message: message.into(),
            origin,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CheckSetting {
    pub enabled: bool,
    pub required: bool,
    pub exemptions: BTreeMap<String, String>,
    pub settings: Value,
}
impl CheckSetting {
    /// An absent key inherits catalog policy; an explicit false remains false.
    pub fn required_or(&self, catalog_default: bool) -> bool {
        if self.settings.get("required").is_some() {
            self.required
        } else {
            catalog_default
        }
    }
    pub fn enabled_or(&self, catalog_default: bool) -> bool {
        if self.settings.get("enabled").is_some() {
            self.enabled
        } else {
            catalog_default
        }
    }
}

impl Default for CheckSetting {
    fn default() -> Self {
        Self {
            enabled: true,
            required: false,
            exemptions: BTreeMap::new(),
            settings: serde_json::json!({}),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ToolSetting {
    pub id: String,
    pub source: Option<String>,
    pub path: Option<String>,
    pub config: Option<String>,
    pub settings: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Build {
    pub id: String,
    pub system: Option<String>,
    pub project: Option<String>,
    pub directory: Option<String>,
    pub variant: String,
    pub prepare: Option<String>,
    pub artifacts: Vec<String>,
    pub settings: Value,
    pub origin: Origin,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Component {
    pub id: String,
    /// Canonical workspace-relative path, with "." for the workspace itself.
    pub root: String,
    pub languages: Vec<String>,
    /// Sorted canonical workspace-relative source paths; no symlink traversal.
    pub files: Vec<String>,
    pub source_dirs: Vec<String>,
    pub sources: Vec<String>,
    pub include: Vec<String>,
    pub exclude: Vec<String>,
    pub vendor: Vec<String>,
    pub external: Vec<String>,
    pub required: bool,
    pub build: Option<String>,
    pub needs: Vec<String>,
    pub python_executable: Option<String>,
    pub python_type_provider: Option<String>,
    pub test_paths: Vec<String>,
    pub checks: BTreeMap<String, CheckSetting>,
    pub integrations: Vec<Value>,
    /// Validated source options retained for providers and legacy projections.
    pub settings: Value,
    pub declared_in: String,
    pub origin: Origin,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Suppression {
    pub fingerprint: String,
    pub rule: String,
    pub path: String,
    pub component: String,
    pub reason: String,
    pub declared_in: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Publish {
    pub repo: String,
    pub api_url: String,
    pub server_url: String,
    pub branch: String,
    pub token_env: String,
    pub settings: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ResolvedProject {
    pub root: PathBuf,
    pub config_source: Option<PathBuf>,
    pub schema_family: SchemaFamily,
    pub profile: Profile,
    pub scope_kind: ScopeKind,
    pub workspace_name: String,
    pub components: Vec<Component>,
    pub builds: Vec<Build>,
    pub tools: BTreeMap<String, ToolSetting>,
    pub checks: BTreeMap<String, CheckSetting>,
    pub suppressions: Vec<Suppression>,
    pub publish: Option<Publish>,
    /// Validated settings after defaults and permitted overrides; no known keys dropped.
    pub effective: Value,
    pub origins: BTreeMap<String, Origin>,
    pub configuration_files: Vec<PathBuf>,
    pub limitations: Vec<String>,
}
