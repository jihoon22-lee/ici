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
    /// A legacy build system's own test target (`make check`), read per test binary.
    MakeCheck,
    CppExecutable,
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
            Self::MakeCheck => "make-check",
            Self::CppExecutable => "cpp-executable",
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
    pub coverage_module_probe: bool,
    pub unittest_fallback: bool,
    /// Same owned run directory and source scope must be used by both logical checks.
    pub shared_test_task_id: Option<String>,
    pub shared_test_kind: Option<ValidationKind>,
    /// Physical suites contributing fresh counters to one bounded C++ coverage batch.
    pub shared_cpp_test_sources: BTreeMap<String, String>,
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
    pub max_coverage_regression: Option<f64>,
    pub function_facts_complete: bool,
    pub runtimes: Vec<RuntimeSpec>,
    pub required_interpreters: Vec<String>,
    pub mutation_enabled: bool,
    pub mutation_tool: Option<ResolvedTool>,
    pub mutation_argv: Vec<String>,
    /// The frozen `tool` label of the mutation capability target (`auto`, or the found tool).
    pub mutation_label: String,
    /// An explicit mutation probe command was configured (frozen runs it even when it is absent).
    pub mutation_command: bool,
    pub auxiliary_tools: BTreeMap<String, ResolvedTool>,
    pub elf_policy: ici_cpp::ElfPolicy,
    pub allow_non_elf: bool,
    pub max_artifacts: usize,
    pub build_roots: Vec<PathBuf>,
    /// Existing project counters cannot prove this run's coverage counts.
    pub gcov_preexisting_data: Vec<String>,
    /// A visible help/version probe chooses JSON or legacy text; never a failed-probe fallback.
    pub gcov_capability_probe: bool,
    pub sanitizer_suite_kind: Option<ValidationKind>,
    /// A `make check` transcript also carries QtTest xunit documents (frozen `_qmake_results`).
    pub make_check_qtest: bool,
    pub instrumented_artifacts: Vec<String>,
    /// One explicitly mapped source for a plain executable verdict, including shared GCOV evidence.
    pub executable_test_source: Option<String>,
    pub modules: Vec<String>,
    /// Declared source directories placed after the project root on the import probe path
    /// (frozen `sys.path[:0] = [root, *source_dirs]`); never ici's own runtime paths.
    pub import_roots: Vec<PathBuf>,
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
    /// Frozen `_missing_python_scope` with `engines.sanitize.required = false`: a missing Python
    /// ResourceWarning scope is a SKIP verdict instead of an ERROR.
    pub sanitize_scope_optional: bool,
    /// Frozen legacy `TestEngine`: a CTest case that did not run (disabled, skipped, missing
    /// executable) is a SKIP target of a measured run, stated as a limitation, not an error.
    pub ctest_not_run_measured: bool,
    /// Frozen legacy `core/cmake.run_tests` (CMake backend): ctest also writes its JUnit report
    /// (`--output-junit`, here `<owned run>/ici-ctest.xml`, `legacy_ctest_junit_path`) so the
    /// legacy view reads stdout verdicts exactly when frozen `parse_ctest_junit` refuses it.
    pub legacy_ctest_junit: bool,
    /// Frozen legacy sanitizer engines (`_append_sanitizer_diagnostics`): a report without a
    /// confirmed project frame is a `SanitizerDiagnosticLocation` ERROR scope of a measured run
    /// at its first external frame; native runs refuse the incomplete attribution instead.
    pub legacy_unlocated_sanitizer_scope: bool,
    /// Frozen legacy `ThreadSanitizeEngine._sanitizer_environment` appends `halt_on_error=1` to
    /// `TSAN_OPTIONS`: a run stops at its first report. Native runs keep `halt_on_error=0` so
    /// every race of the run is reported.
    pub legacy_tsan_halt_on_error: bool,
    /// Frozen legacy `TestEngine._find_coverage_cmd`: the coverage probe runs under the frozen
    /// 300 s / 1,000,000-character bounds, any failure falls through to the plain suite and the
    /// failure is a tool error (`python_coverage::LEGACY_PROBE_ERRORS`).
    pub legacy_coverage_probe: bool,
    /// Frozen legacy `PythonCompatibilityEngine` process bounds: version probe 10 s / 8192,
    /// compileall 60 s / 128,000 and import smoke 30 s / 128,000 output characters.
    pub legacy_runtime_bounds: bool,
    /// Frozen legacy `TestEngine._consume_cpp_coverage`: a gcov JSON report set frozen rejects
    /// (`GcovJsonError` incomplete_source_coverage, missing_data, aggregate_limit,
    /// inconsistent_report_set, ambiguous_source_path, read_error) is worded as frozen's
    /// `gcov JSON evidence rejected (<code>): <code>: <message>`, also when gcov's stderr names
    /// data it could not open (frozen reads no gcov stderr).
    pub legacy_gcov_source_wording: bool,
    /// Frozen next `expand_cpp_tests` step identity of one declared suite
    /// (`<task>.<build>-ctest`, `<task>.<build>-<qmake target>`); `None` keeps `<task>.suite`.
    pub suite_step_id: Option<String>,
    /// Frozen next `CtestProvider`/`QtestProvider` contract for a declared suite: the frozen
    /// argv (no `--parallel`), `process_fingerprint` finding identities and failure wording.
    pub frozen_next_suite: bool,
    /// Frozen next `expand_cpp_binary_compat` step of one ELF artifact (`suite_step_id` =
    /// `<task>.<build>-<n>`): the frozen readelf contract (workspace-root cwd, 60 s, 1,000,000
    /// output bytes), the fixed default loader policy, frozen failure wording, one
    /// per-artifact limitation and one `binary.checked` metric.
    pub frozen_next_elf: bool,
    /// Frozen next `plan_cpp_coverage`/`GcovProvider`: one gcov read of every note under the
    /// linked build after the declared suites, every report it wrote aggregated, frozen failure
    /// wording (no version probe, no shared suite step of its own).
    pub frozen_next_gcov: bool,
    /// Frozen next `expand_cpp_sanitizer` step of one declared suite under the variant build
    /// (`suite_step_id` = `<task>.<build>-ctest` / `<task>.<build>-<qmake target>`): the
    /// frozen `SanitizeProvider` run of `argv` in the component root (1800 s, 1,000,000
    /// output bytes, the variant options already in `environment`), no readelf probe — the
    /// plan gate checked the binaries' runtime markers — and frozen finding identities and
    /// failure wording.
    pub frozen_next_sanitizer: bool,
}
impl Default for ValidationOptions {
    fn default() -> Self {
        Self {
            sanitize_scope_optional: false,
            ctest_not_run_measured: false,
            legacy_ctest_junit: false,
            legacy_unlocated_sanitizer_scope: false,
            legacy_tsan_halt_on_error: false,
            legacy_coverage_probe: false,
            legacy_runtime_bounds: false,
            legacy_gcov_source_wording: false,
            suite_step_id: None,
            frozen_next_suite: false,
            frozen_next_elf: false,
            frozen_next_gcov: false,
            frozen_next_sanitizer: false,
            collect_coverage: false,
            coverage_module_probe: false,
            unittest_fallback: false,
            shared_test_task_id: None,
            shared_test_kind: None,
            shared_cpp_test_sources: BTreeMap::new(),
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
            max_coverage_regression: None,
            function_facts_complete: false,
            runtimes: vec![],
            required_interpreters: vec![],
            mutation_enabled: false,
            mutation_tool: None,
            mutation_argv: vec![],
            mutation_label: "auto".into(),
            mutation_command: false,
            auxiliary_tools: BTreeMap::new(),
            elf_policy: ici_cpp::ElfPolicy::default(),
            allow_non_elf: false,
            max_artifacts: 64,
            build_roots: vec![],
            gcov_preexisting_data: vec![],
            gcov_capability_probe: false,
            sanitizer_suite_kind: None,
            make_check_qtest: false,
            instrumented_artifacts: vec![],
            executable_test_source: None,
            modules: vec![],
            import_roots: vec![],
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
pub struct StepCondition {
    pub probe_task_id: String,
    pub coverage_available: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub missing_module: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gcov_format: Option<GcovFormat>,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GcovFormat {
    Json,
    Text,
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
    #[serde(default)]
    pub conditional_steps: BTreeMap<String, StepCondition>,
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
