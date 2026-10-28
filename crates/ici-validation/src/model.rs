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
    PythonSanitize,
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
            Self::PythonSanitize => "python-resource-warnings",
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
pub struct RuntimeSpec {
    pub configured: String,
    pub tool: Option<ResolvedTool>,
    pub required: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct ValidationOptions {
    pub collect_coverage: bool,
    /// Same owned run directory and source scope must be used by both logical checks.
    pub shared_test_task_id: Option<String>,
    pub shared_test_kind: Option<ValidationKind>,
    /// Total runs including the primary suite, capped at 3. Zero disables repeats.
    pub repeat_runs: usize,
    pub slow_threshold_seconds: Option<f64>,
    pub quality_mode: String,
    pub quality_timeout_seconds: f64,
    pub max_slow_tests: usize,
    pub min_line_cov: Option<f64>,
    pub min_file_cov: Option<f64>,
    pub min_branch_cov: Option<f64>,
    pub min_func_cov: Option<f64>,
    pub min_tem_score: Option<f64>,
    pub min_file_statements: u64,
    pub changed_lines: Vec<String>,
    pub min_changed_line_cov: Option<f64>,
    pub coverage_required: bool,
    pub function_facts_complete: bool,
    pub runtimes: Vec<RuntimeSpec>,
    pub required_interpreters: Vec<String>,
    pub mutation_enabled: bool,
    pub mutation_tool: Option<ResolvedTool>,
    pub mutation_argv: Vec<String>,
    pub auxiliary_tools: BTreeMap<String, ResolvedTool>,
    pub elf_policy: ici_cpp::ElfPolicy,
    pub allow_non_elf: bool,
    pub max_artifacts: usize,
    pub build_roots: Vec<PathBuf>,
    pub modules: Vec<String>,
    pub requires_python: Option<String>,
    pub compile_sources: bool,
    pub argv: Vec<String>,
    pub expected_exit: i32,
    /// A declared named case uses the historical single-contract finding/grouping.
    pub integration_case_name: Option<String>,
    pub integration_required: bool,
    pub stdout_contains: Vec<String>,
    pub stderr_contains: Vec<String>,
    pub stdout_not_contains: Vec<String>,
    pub stderr_not_contains: Vec<String>,
    pub outputs: Vec<OutputAssertion>,
    pub requires: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub timeout_seconds: f64,
    pub output_limit_bytes: Option<u64>,
    pub build_dir: Option<PathBuf>,
}
impl Default for ValidationOptions {
    fn default() -> Self {
        Self {
            collect_coverage: false,
            shared_test_task_id: None,
            shared_test_kind: None,
            repeat_runs: 0,
            slow_threshold_seconds: None,
            quality_mode: "report".into(),
            quality_timeout_seconds: 300.0,
            max_slow_tests: 50,
            min_line_cov: None,
            min_file_cov: None,
            min_branch_cov: None,
            min_func_cov: None,
            min_tem_score: None,
            min_file_statements: 5,
            changed_lines: vec![],
            min_changed_line_cov: None,
            coverage_required: false,
            function_facts_complete: false,
            runtimes: vec![],
            required_interpreters: vec![],
            mutation_enabled: false,
            mutation_tool: None,
            mutation_argv: vec![],
            auxiliary_tools: BTreeMap::new(),
            elf_policy: ici_cpp::ElfPolicy::default(),
            allow_non_elf: false,
            max_artifacts: 64,
            build_roots: vec![],
            modules: vec![],
            requires_python: None,
            compile_sources: false,
            argv: vec![],
            expected_exit: 0,
            integration_case_name: None,
            integration_required: true,
            stdout_contains: vec![],
            stderr_contains: vec![],
            stdout_not_contains: vec![],
            stderr_not_contains: vec![],
            outputs: vec![],
            requires: vec![],
            environment: BTreeMap::new(),
            timeout_seconds: 1800.0,
            output_limit_bytes: None,
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
pub struct ArtifactPattern {
    pub id_prefix: String,
    pub suffix: String,
    pub producer_task_id: String,
    pub max_files: usize,
    pub max_bytes_each: u64,
    pub max_bytes_total: u64,
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
    #[serde(default)]
    pub artifact_patterns: Vec<ArtifactPattern>,
    #[serde(default)]
    pub runtime_plans: Vec<RuntimePlan>,
    #[serde(default)]
    pub optional_task_ids: Vec<String>,
    /// Dependencies use provider exit contracts; the generic scheduler's exit=0 rule is insufficient.
    pub prerequisites: BTreeMap<String, Vec<StepPrerequisite>>,
    pub context: ValidationRequest,
    pub limitations: Vec<String>,
    #[serde(skip)]
    pub source_coordinates: std::sync::Arc<ici_providers::SourceCoordinates>,
    #[serde(skip)]
    pub artifact_headers: BTreeMap<String, ArtifactHeader>,
}
#[derive(Clone, Debug)]
pub struct ArtifactHeader {
    pub magic: Vec<u8>,
    pub size: u64,
    pub(crate) signature: [i128; 7],
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RuntimePlan {
    pub configured: String,
    pub required: bool,
    pub plan: Option<Box<ValidationPlan>>,
}
