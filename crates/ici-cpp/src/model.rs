use ici_domain::TaskSpec;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, thiserror::Error)]
#[error("{code}: {message}")]
pub struct CppError {
    pub code: String,
    pub message: String,
}
impl CppError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ContextDiagnostic {
    pub code: String,
    pub message: String,
    pub level: String,
    pub entry_index: Option<usize>,
    pub source: String,
}
impl ContextDiagnostic {
    pub fn error(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            level: "error".into(),
            entry_index: None,
            source: String::new(),
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct InputIdentity {
    pub path: String,
    pub sha256: String,
    pub size: u64,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Define {
    pub name: String,
    pub value: Option<String>,
    pub undefine: bool,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IncludePath {
    pub path: String,
    pub kind: String,
    pub scope: String,
    pub present: bool,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CompilationUnit {
    pub source: String,
    pub directory: String,
    pub original_argv: Vec<String>,
    pub argv: Vec<String>,
    pub compiler: String,
    pub language: String,
    pub standard: String,
    pub output: String,
    pub target: String,
    pub variant: String,
    pub defines: Vec<Define>,
    pub include_paths: Vec<IncludePath>,
    pub sysroot: String,
    pub sysroot_scope: String,
    pub configuration: String,
    pub response_files: Vec<InputIdentity>,
    pub diagnostics: Vec<ContextDiagnostic>,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ContextRequest {
    /// An explicit selection. Missing/broken selection never falls back to another database.
    pub database: Option<String>,
    pub variant: Option<String>,
    /// Additional explicitly declared build directories to inspect, never recursive guesses.
    pub build_directories: Vec<String>,
    pub selected_sources: Vec<String>,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CompilationContext {
    pub database_path: Option<String>,
    pub origin: String,
    pub database_digest: Option<String>,
    pub units: Vec<CompilationUnit>,
    pub diagnostics: Vec<ContextDiagnostic>,
    pub response_files: Vec<InputIdentity>,
    pub variant: String,
    pub selected_sources: Vec<String>,
    pub uncovered_sources: Vec<String>,
    pub unity_build: bool,
}
impl CompilationContext {
    pub fn complete(&self) -> bool {
        !self.units.is_empty()
            && self.uncovered_sources.is_empty()
            && !self
                .diagnostics
                .iter()
                .chain(self.units.iter().flat_map(|u| &u.diagnostics))
                .any(|d| d.level == "error")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReplayPurpose {
    Diagnostics,
    IncludeTrace,
    AstJson,
    Boundaries,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BuildRequest {
    pub id: String,
    pub system: String,
    pub project: String,
    pub variant: String,
    pub jobs: usize,
    pub ici_binary: Option<PathBuf>,
    pub compiler_c: String,
    pub compiler_cxx: String,
    pub tools: BTreeMap<String, String>,
    pub configure_argv: Vec<String>,
    pub build_argv: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub extra_cmake_args: Vec<String>,
}
impl Default for BuildRequest {
    fn default() -> Self {
        Self {
            id: "native".into(),
            system: "cmake".into(),
            project: ".".into(),
            variant: "release".into(),
            jobs: 2,
            ici_binary: None,
            compiler_c: String::new(),
            compiler_cxx: String::new(),
            tools: BTreeMap::new(),
            configure_argv: vec![],
            build_argv: vec![],
            environment: BTreeMap::new(),
            extra_cmake_args: vec![],
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BuildPlan {
    pub id: String,
    pub system: String,
    pub variant: String,
    pub shadow: String,
    pub owner_digest: String,
    pub tasks: Vec<TaskSpec>,
    pub compilation_database: String,
    pub capture_journal: Option<String>,
    pub generated_roots: Vec<String>,
}

/// Bounded same-binary qmake configure phase after native Makefile metadata was produced.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QmakeCaptureRequest {
    pub root: PathBuf,
    pub shadow: String,
    pub owner_digest: String,
    pub project: String,
    pub variant: String,
    pub qmake: String,
    pub ici_binary: PathBuf,
    pub journal: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplayCommand {
    pub argv: Vec<String>,
    pub cwd: PathBuf,
    pub source: PathBuf,
    pub configuration: String,
}
