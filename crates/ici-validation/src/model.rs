use ici_domain::TaskSpec;
use ici_providers::resolver::ResolvedTool;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValidationKind {
    PythonTest,
    PythonCoverage,
    PythonRuntime,
    PythonImports,
    Ctest,
    Qtest,
    Sanitizer,
    ThreadSanitizer,
    Integration,
    Gcov,
    BinaryCompatibility,
}
impl ValidationKind {
    pub fn provider(self) -> &'static str {
        match self {
            Self::PythonTest => "pytest",
            Self::PythonCoverage => "coverage",
            Self::PythonRuntime => "python-compat-runtime",
            Self::PythonImports => "python-imports",
            Self::Ctest => "ctest",
            Self::Qtest => "qtest",
            Self::Sanitizer => "sanitize",
            Self::ThreadSanitizer => "thread-sanitize",
            Self::Integration => "integration",
            Self::Gcov => "gcov",
            Self::BinaryCompatibility => "binarycompat",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FunctionFact {
    pub path: String,
    pub name: String,
    pub qualified_name: String,
    pub start_line: u32,
    /// CPython code object's first line: earliest decorator, otherwise the def line.
    #[serde(default)]
    pub entry_line: Option<u32>,
    pub body_start_line: u32,
    pub end_line: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct ValidationOptions {
    pub collect_coverage: bool,
    /// Same owned run directory and source scope must be used by both logical checks.
    pub shared_test_task_id: Option<String>,
    /// Total runs including the primary suite, capped at 3. Zero disables repeats.
    pub repeat_runs: usize,
    pub slow_threshold_seconds: Option<f64>,
    pub quality_mode: String,
    pub modules: Vec<String>,
    pub requires_python: Option<String>,
    pub compile_sources: bool,
    pub argv: Vec<String>,
    pub expected_exit: i32,
    pub stdout_contains: Vec<String>,
    pub stderr_contains: Vec<String>,
    pub stdout_not_contains: Vec<String>,
    pub stderr_not_contains: Vec<String>,
    pub outputs: Vec<OutputAssertion>,
    pub requires: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub timeout_seconds: f64,
    pub build_dir: Option<PathBuf>,
}
impl Default for ValidationOptions {
    fn default() -> Self {
        Self {
            collect_coverage: false,
            shared_test_task_id: None,
            repeat_runs: 0,
            slow_threshold_seconds: None,
            quality_mode: "report".into(),
            modules: vec![],
            requires_python: None,
            compile_sources: false,
            argv: vec![],
            expected_exit: 0,
            stdout_contains: vec![],
            stderr_contains: vec![],
            stdout_not_contains: vec![],
            stderr_not_contains: vec![],
            outputs: vec![],
            requires: vec![],
            environment: BTreeMap::new(),
            timeout_seconds: 1800.0,
            build_dir: None,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OutputAssertion {
    pub path: PathBuf,
    pub min_size: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ValidationRequest {
    pub kind: ValidationKind,
    pub task_id: String,
    pub component_id: String,
    pub root: PathBuf,
    pub cwd: PathBuf,
    pub owned_run_dir: PathBuf,
    pub tool: ResolvedTool,
    pub targets: Vec<String>,
    pub source_files: Vec<String>,
    pub functions: Vec<FunctionFact>,
    pub options: ValidationOptions,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OwnedArtifactSpec {
    pub id: String,
    pub path: PathBuf,
    pub producer_task_id: String,
    pub max_bytes: u64,
    pub required: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StepPrerequisite {
    pub task_id: String,
    pub accepted_exits: Vec<i32>,
    pub require_test_evidence: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ValidationPlan {
    pub kind: ValidationKind,
    pub tasks: Vec<TaskSpec>,
    pub artifacts: Vec<OwnedArtifactSpec>,
    /// Dependencies use provider exit contracts; the generic scheduler's exit=0 rule is insufficient.
    pub prerequisites: BTreeMap<String, Vec<StepPrerequisite>>,
    pub context: ValidationRequest,
    pub limitations: Vec<String>,
}
