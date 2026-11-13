//! Explicit native preparation and minimal compilation exports. Planning performs no processes.
mod artifact_facts;
pub mod cmake_analysis;
mod direct;
mod preparation_failure;
mod public_export;
pub use artifact_facts::{compiled_artifact_kind, compiled_artifacts};
pub use direct::{DirectPreparation, plan_legacy_direct_preparation};
pub(crate) use direct::{
    LEGACY_DIRECT_FAILURE, NO_DECLARED_EXECUTABLE, NO_DECLARED_OBJECT, UNVERIFIED_RELEASE_BINARY,
    legacy_unreadable_sources,
};
use ici_config::{Build, Component, ResolvedProject};
use ici_cpp::{BuildLease, BuildPlan, BuildRequest, CompilationContext, ContextRequest};
use ici_domain::{AnalysisOutput, sha256};
use ici_execution::{Cancellation, Scheduler};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
pub use preparation_failure::{PreparationFailure, PreparationUnavailable};
pub use public_export::{
    ExportMetadata, cmake_unity_build, compilation_export_payload, render_compilation_export,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

#[derive(Clone, Debug)]
pub struct ContextPreparationPlan {
    pub root: PathBuf,
    pub component_id: String,
    pub build_id: String,
    pub build: BuildPlan,
    pub tools: BTreeMap<String, ResolvedTool>,
    pub inputs: Vec<String>,
    pub selected_sources: Vec<String>,
    pub declared_build: Option<Build>,
    pub descriptor: String,
    pub effects: Value,
    pub direct: Option<DirectPreparation>,
    /// Frozen legacy `prepare_cmake_compilation_context` only configures the release shadow
    /// (it builds only to generate missing sources), so a `verify` whose release build fails
    /// still analyses the configured database. A selected `cpp.artifact` (the BuildEngine) then
    /// reports the build failure itself and `cpp.binary-compat` stays blocked.
    pub configured_context_on_build_failure: bool,
    /// Frozen legacy `prepare_cmake_compilation_context` owns this preparation: the canonical
    /// release analysis database, whose cache metadata, generation and unity diagnostics the
    /// context carries ([`cmake_analysis`]) whatever the database holds.
    pub analysis_database: bool,
    /// Nothing selected consumes this preparation's build (no `cpp.artifact`/`cpp.binary-compat`
    /// release artifacts): like frozen it only configures, and builds only to generate a stale
    /// source the database names under its own shadow.
    pub analysis_only: bool,
    /// The project has the database frozen `_select_database` picks (a configured
    /// `project.compile_database` or a root/`build/` `compile_commands.json`): it stays the
    /// analysis context, and this release build only serves the BuildEngine's artifacts.
    pub existing_database: bool,
    /// Frozen `core/cmake._capture_artifact_manifest` runs on this successful build session
    /// (a legacy `verify` CMake/qmake/Make session of a published variant): its
    /// `_linked_artifact_paths` bounds fail the session like a failed build.
    pub legacy_manifest_bounds: bool,
}
#[derive(Clone)]
pub struct PreparedContext {
    pub plan: ContextPreparationPlan,
    pub context: CompilationContext,
    /// Workspace-relative measured ELF outputs of an otherwise undeclared legacy build.
    pub artifacts: Vec<String>,
    /// Workspace-relative outputs of the legacy ABI inventory only, never published: PE/Mach-O
    /// outputs and the linked executables of CMake's own compiler checks under `CMakeFiles/`.
    pub non_elf: Vec<String>,
    /// Workspace-relative `!<arch>` static libraries of an otherwise undeclared legacy build:
    /// frozen manifests list them (kind static-library), so `binary_compat` checks one only when
    /// `engines.binary_compat.artifacts` names it (the non-ELF finding); never published.
    pub archives: Vec<String>,
    pub test_executable: Option<String>,
    /// Actual source-to-executable mapping for descriptor-free native test suites.
    pub test_executables: BTreeMap<String, String>,
    /// Descriptor-free test sources whose own translation unit failed to compile, with the
    /// compiler's bounded stderr. Their executables were not linked; the other tests still run.
    pub test_compile_failures: BTreeMap<String, String>,
    /// Those of `test_compile_failures` whose compiler did not finish (frozen TestEngine
    /// "incomplete": timed out or output past the bound; "terminated": killed by a signal),
    /// with whether the coverage variant compiled them.
    pub test_compile_unfinished: BTreeMap<String, Value>,
    pub evidence: Option<Value>,
    /// The failed build step of a configured-only legacy release context (frozen never builds it).
    pub build_failure: Option<String>,
    /// The frozen BuildEngine adapter failure of that build step (`<backend> build failed: …`),
    /// which a selected `cpp.artifact` reports while the source checks analyse the database.
    pub build_failure_output: Option<Box<AnalysisOutput>>,
    /// Frozen BuildSession `ToolEvidence` of the adapter processes this preparation ran
    /// (`make configure`/`make clean`/`make build`, `cmake configure`/`cmake build`,
    /// `qmake configure`/`qmake build`), in run order.
    pub process_evidence: Vec<Value>,
    /// The frozen `session.cmake_version` of a legacy CMake session (`Some(None)`: probed, no
    /// parseable version); `None` when this preparation ran no `cmake --version` probe.
    pub cmake_version: Option<Option<(u32, u32)>>,
    /// The frozen `cmake_context` additions of an analysis-database preparation.
    pub analysis: Option<cmake_analysis::CmakeAnalysis>,
    _lease: Arc<BuildLease>,
}

/// Frozen `parse_cmake_version` (core/cmake.py:70-78): `re.search(r"cmake version (\d+)\.(\d+)",
/// stdout)`, `None` when absent.
pub fn frozen_cmake_version(stdout: &str) -> Option<(u32, u32)> {
    static VERSION: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"cmake version (\d+)\.(\d+)").expect("constant regex")
    });
    // Python `int()` reads any Unicode decimal digit.
    let number = |text: &str| -> Option<u32> {
        text.chars().try_fold(0u32, |value, character| {
            let digit = unicode_decimal(character)?;
            value.checked_mul(10)?.checked_add(digit)
        })
    };
    let capture = VERSION.captures(stdout)?;
    Some((number(&capture[1])?, number(&capture[2])?))
}

/// The value of a Unicode `Nd` digit (Python `int()`): `Nd` digits come in runs that start at
/// their zero, so the value is the count of digits just below it, modulo ten.
fn unicode_decimal(character: char) -> Option<u32> {
    static DIGIT: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"^\d$").expect("constant regex"));
    let digit = |code: u32| {
        char::from_u32(code).is_some_and(|c| DIGIT.is_match(c.encode_utf8(&mut [0; 4])))
    };
    let code = character as u32;
    if !digit(code) {
        return None;
    }
    let below = (1..=code).take_while(|step| digit(code - step)).count() as u32;
    Some(below % 10)
}

const NON_ELF_MAGICS: [&[u8]; 5] = [
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
];

pub fn plan_preparation(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
    ici_binary: &Path,
) -> Result<ContextPreparationPlan, PreparationUnavailable> {
    let directory = crate::files::under(&project.root, &component.root);
    // A direct-compile overlay of an earlier variant is not a declared native build.
    let declared = crate::cpp::linked_build(project, component)
        .is_some_and(|build| build.system.as_deref() != Some("direct-gcc"));
    let selected = if declared {
        None
    } else {
        // Without a declared build, only a selectable root descriptor (CMake, qmake or an
        // explicitly enabled Make build) means there is something to prepare.
        Some(
            select_descriptor(
                &project.root,
                &directory,
                None,
                make_table(project, component),
            )
            .map_err(PreparationUnavailable::NothingToPrepare)?,
        )
    };
    plan_selected_preparation(project, component, environment, ici_binary).map_err(|reason| {
        PreparationUnavailable::Misconfigured {
            reason,
            descriptor: selected.or_else(|| {
                crate::cpp::linked_build(project, component).and_then(|build| build.project.clone())
            }),
        }
    })
}

fn plan_selected_preparation(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
    ici_binary: &Path,
) -> Result<ContextPreparationPlan, String> {
    let declared = crate::cpp::linked_build(project, component).cloned();
    let directory = crate::files::under(&project.root, &component.root);
    let (system, mut descriptor) = if let Some(build) = &declared {
        let system = build
            .system
            .as_deref()
            .ok_or_else(|| format!("build {:?} must select cmake, qmake or make", build.id))?
            .to_owned();
        let descriptor = if let Some(path) = &build.project {
            path.clone()
        } else {
            select_descriptor(
                &project.root,
                &directory,
                Some(&system),
                make_table(project, component),
            )?
        };
        (system, descriptor)
    } else {
        let descriptor = select_descriptor(
            &project.root,
            &directory,
            None,
            make_table(project, component),
        )?;
        let system = if Path::new(&descriptor)
            .file_name()
            .is_some_and(|name| name == "CMakeLists.txt")
        {
            "cmake"
        } else if descriptor.ends_with(".pro") {
            "qmake"
        } else {
            "make"
        };
        (system.into(), descriptor)
    };
    let descriptor_path = project.root.join(&descriptor);
    if descriptor_path.is_dir() {
        descriptor = select_descriptor(
            &project.root,
            &descriptor_path,
            Some(&system),
            make_table(project, component),
        )?;
    }
    let descriptor_path = project.root.join(&descriptor);
    if !fs::symlink_metadata(&descriptor_path).is_ok_and(|metadata| metadata.is_file())
        || descriptor_path
            .canonicalize()
            .map_err(|error| error.to_string())?
            != descriptor_path
        || !descriptor_path.starts_with(&project.root)
    {
        return Err(
            "native descriptor must be a contained regular file without symlink traversal".into(),
        );
    }
    let build_id = declared
        .as_ref()
        .map(|build| build.id.clone())
        .unwrap_or_else(|| format!("legacy-{}", &sha256(&component.id)[7..23]));
    let variant = declared
        .as_ref()
        .map(|build| build.variant.clone())
        .unwrap_or_else(|| {
            component
                .settings
                .get("native_requested_variant")
                .and_then(Value::as_str)
                .unwrap_or("release")
                .into()
        });
    let mut tools = BTreeMap::new();
    for name in match system.as_str() {
        "cmake" => vec!["cmake"],
        "qmake" => vec!["qmake", "make"],
        "make" => vec![],
        _ => return Err("build system must be cmake, qmake or make".into()),
    } {
        let tool = if name == "qmake"
            && project.schema_family == ici_config::SchemaFamily::Legacy
            && !project.tools.contains_key("qmake")
        {
            // Frozen `_configure_qmake`: `shutil.which("qmake6") or shutil.which("qmake")`.
            resolve(project, environment, "qmake6", None, &directory)
                .map(|mut tool| {
                    tool.id = "qmake".into();
                    tool
                })
                .or_else(|_| resolve(project, environment, name, None, &directory))?
        } else {
            resolve(project, environment, name, None, &directory)?
        };
        tools.insert(name.into(), tool);
    }
    let make = make_table(project, component);
    if system == "make"
        && make
            .and_then(|value| value.get("enabled"))
            .and_then(Value::as_bool)
            != Some(true)
    {
        return Err(
            "Make preparation requires build.make.enabled=true and explicit shell-free argv".into(),
        );
    }
    // Frozen `prepare_cmake_compilation_context` configures the release analysis database with
    // `-DCMAKE_UNITY_BUILD=OFF` (cmake.py `analysis_database`); its BuildEngine release build
    // does not. A legacy release preparation that only publishes release artifacts (`ici
    // build`) keeps the project's own unity setting; under `ici verify` the analysis database is
    // configured first and the deep BuildEngine reuses its cached setting.
    let analysis_database = project.schema_family == ici_config::SchemaFamily::Legacy
        && system == "cmake"
        && variant == "release"
        && (component.settings.get("native_release_artifacts") != Some(&Value::Bool(true))
            || component.settings.get("native_analysis_database") == Some(&Value::Bool(true)));
    let mut request = BuildRequest {
        id: build_id.clone(),
        shadow_owner_id: declared
            .as_ref()
            .and_then(|build| build.settings.get("make_shadow_owner"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        system: system.clone(),
        project: descriptor.clone(),
        variant,
        jobs: 2,
        ici_binary: Some(ici_binary.into()),
        environment: BTreeMap::new(),
        // Frozen legacy `qmake_configure_argv` keeps the mkspec's default CONFIG in every variant.
        qmake_default_config: project.schema_family == ici_config::SchemaFamily::Legacy,
        extra_cmake_args: if analysis_database {
            vec!["-DCMAKE_UNITY_BUILD=OFF".into()]
        } else {
            vec![]
        },
        ..Default::default()
    };
    // Every frozen legacy BuildSession (`_configure_cmake`: release analysis database and
    // BuildEngine, test, sanitize, thread-sanitize) probes `cmake --version` before configuring
    // and passes frozen's `-D` order; the parsed version decides the ctest flags of the ctest
    // variants (core/cmake.py:193-201). The link-reachability configure is not a BuildSession.
    let legacy_cmake = project.schema_family == ici_config::SchemaFamily::Legacy
        && system == "cmake"
        && request.variant != "link-reachability";
    request.cmake_version_probe = legacy_cmake;
    request.frozen_cmake_define_order = legacy_cmake;
    if system == "make" {
        // Frozen make_plan: validated contract, cwd = workdir, variant-shared shadow, configured
        // jobs substituted into whole {jobs} tokens.
        let mut settings = ici_cpp::make_settings(
            make,
            &request.variant,
            Path::new(&descriptor)
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("Makefile"),
        )
        .map_err(|error| format!("Make configuration failed: {}", error.message))?;
        request.project = settings.workdir.clone();
        for argv in [
            &mut settings.configure_argv,
            &mut settings.clean_argv,
            &mut settings.build_argv,
        ] {
            if let Some(first) = argv.first_mut() {
                let selected = first.clone();
                let tool = resolve(
                    project,
                    environment,
                    Path::new(&selected)
                        .file_name()
                        .and_then(|name| name.to_str())
                        .ok_or("invalid Make executable")?,
                    selected
                        .contains('/')
                        .then(|| crate::files::under(&project.root, &selected)),
                    &directory,
                )
                .map_err(|error| {
                    format!("configured executable is unavailable: {selected}: {error}")
                })?;
                *first = tool.path.to_string_lossy().into_owned();
                tools.insert(format!("make-command:{}", tool.id), tool);
            }
        }
        request.configure_argv = settings.configure_argv.clone();
        request.build_argv = settings.build_argv.clone();
        request.jobs = settings.jobs as usize;
        request.make = Some(settings);
    }
    // Frozen `_cpp_linker_dead_symbols` builds the reachability shadow with `--parallel 1`: a
    // failing compile ends it before later targets, project custom commands included.
    if project.schema_family == ici_config::SchemaFamily::Legacy
        && request.variant == "link-reachability"
    {
        request.jobs = 1;
    }
    for (field, names) in [
        (&mut request.compiler_c, ["cc", "gcc", "clang"]),
        (&mut request.compiler_cxx, ["c++", "g++", "clang++"]),
    ] {
        if let Some(name) = names.into_iter().find(|name| {
            project
                .tools
                .get(*name)
                .and_then(|tool| tool.path.as_ref())
                .is_some()
        }) {
            let tool = resolve(project, environment, name, None, &directory)?;
            *field = tool.path.to_string_lossy().into_owned();
            tools.insert(format!("compiler:{name}"), tool);
        }
    }
    request.tools = tools
        .iter()
        .map(|(id, tool)| (id.clone(), tool.path.to_string_lossy().into_owned()))
        .collect();
    let mut build = if request.variant == "link-reachability" {
        ici_cpp::plan_linker_build(&project.root, &request)
    } else {
        ici_cpp::plan_build(&project.root, &request)
    }
    .map_err(|error| error.to_string())?;
    if system == "make" {
        // Read-only preflight: plan/doctor name a shadow that preparation would refuse.
        ici_cpp::check_shadow_adoptable(&project.root, &build)
            .map_err(|error| format!("Make configuration failed: {}", error.message))?;
    }
    let mut inputs =
        crate::files::build_inputs(&project.root, &directory).map_err(|error| error.to_string())?;
    inputs.retain(|path| {
        !build
            .generated_roots
            .iter()
            .any(|owned| Path::new(path).starts_with(owned))
    });
    for task in &mut build.tasks {
        let executable = Path::new(&task.argv[0]);
        if let Some(tool) = tools.values().find(|tool| tool.path == executable) {
            task.tool_digest = Some(tool.digest.clone());
        } else if executable == ici_binary {
            task.tool_digest = Some(
                ici_execution::process::executable_digest(ici_binary)
                    .map_err(|error| error.to_string())?,
            );
        } else {
            return Err(format!(
                "preparation executable {} has no measured identity",
                executable.display()
            ));
        }
        task.input_refs = inputs
            .iter()
            .map(|path| project.root.join(path).to_string_lossy().into_owned())
            .collect();
        task.analysis_unit_ids = vec![format!("{}.cpp", component.id)];
    }
    let effects = json!({"owned_roots":build.generated_roots,"reset":{"glob":format!("{}/**/*.gcda",build.shadow),"policy":"single-link regular files without symlink traversal under the held BuildLease"},"native_tasks":build.tasks.iter().map(|task|json!({"id":task.id,"argv":task.argv,"cwd":task.cwd,"work_dirs":task.work_dirs})).collect::<Vec<_>>(),"limitations":build.limitations});
    Ok(ContextPreparationPlan {
        root: project.root.clone(),
        component_id: component.id.clone(),
        build_id,
        build,
        tools,
        inputs,
        selected_sources: component
            .files
            .iter()
            .filter(|path| ici_cpp::is_translation_unit(path))
            .cloned()
            .collect(),
        declared_build: declared,
        descriptor,
        effects,
        direct: None,
        configured_context_on_build_failure: false,
        analysis_database,
        analysis_only: analysis_database
            && component.settings.get("native_release_artifacts") != Some(&Value::Bool(true)),
        existing_database: false,
        legacy_manifest_bounds: false,
    })
}

pub fn execute_preparation(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
) -> Result<PreparedContext, String> {
    execute_preparation_typed(plan, scheduler, cancellation, events)
        .map_err(|error| error.to_string())
}
fn incomplete(error: impl std::fmt::Display) -> PreparationFailure {
    PreparationFailure::Incomplete(error.to_string())
}
pub fn execute_preparation_typed(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
) -> Result<PreparedContext, PreparationFailure> {
    execute_preparation_shared(plan, scheduler, cancellation, events, None)
}
/// [`execute_preparation_typed`] inside a build transaction an earlier context already holds.
pub fn execute_preparation_shared(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
    shared: Option<Arc<BuildLease>>,
) -> Result<PreparedContext, PreparationFailure> {
    if plan.direct.is_some() {
        direct::execute(plan, scheduler, cancellation, events)
    } else {
        execute_native_preparation(plan, scheduler, cancellation, events, shared)
    }
}

impl PreparedContext {
    /// Frozen `make_plan` gives every legacy Make variant the one configured shadow. A later
    /// variant of the same run builds inside the transaction this context already holds (the
    /// earlier variant's consumers keep it alive), instead of waiting for its own lock.
    pub fn shared_make_lease(&self, plan: &ContextPreparationPlan) -> Option<Arc<BuildLease>> {
        (plan.build.system == "make"
            && self.plan.build.system == "make"
            && self.plan.root == plan.root
            && self.plan.build.shadow == plan.build.shadow)
            .then(|| self._lease.clone())
    }
}
fn execute_native_preparation(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
    shared: Option<Arc<BuildLease>>,
) -> Result<PreparedContext, PreparationFailure> {
    let lease = match shared {
        Some(lease) => lease,
        None => Arc::new(
            BuildLease::acquire(
                &plan.root,
                &plan.build,
                cancellation,
                Duration::from_secs(120),
            )
            .map_err(incomplete)?,
        ),
    };
    let shadow = lease.prepare(&plan.build).map_err(incomplete)?;
    // Only this verified leased shadow is eligible for resetting accumulated runtime counters.
    for entry in crate::files::tree(&plan.root, &shadow).map_err(incomplete)? {
        if entry.relative.ends_with(".gcda") {
            let path = plan.root.join(&entry.relative);
            let metadata = fs::symlink_metadata(&path).map_err(incomplete)?;
            if !entry.contained
                || !metadata.is_file()
                || metadata.nlink() != 1
                || path.canonicalize().map_err(incomplete)? != path
            {
                return Err(incomplete(
                    "owned coverage reset refuses redirected or multiply linked gcda files",
                ));
            }
            fs::remove_file(path).map_err(incomplete)?;
        }
    }
    // Frozen `prepare_cmake_compilation_context` decides from the configured tree alone: an
    // analysis-database preparation configures first, reads that context and cache, and only
    // then builds (analysis-only: just to generate a missing source; a consumed release build:
    // always).
    let compile_id = format!("build:{}:{}:compile", plan.build.id, plan.build.variant);
    let mut batch: Vec<_> = plan
        .build
        .tasks
        .iter()
        .filter(|task| !(plan.analysis_database && task.id == compile_id))
        .cloned()
        .collect();
    let mut process_evidence = vec![];
    let mut cmake_version = None;
    let version_id = format!("build:{}:{}:version", plan.build.id, plan.build.variant);
    // (configured context, its cache metadata, whether it names a source still to generate).
    let mut configured: Option<(CompilationContext, cmake_analysis::CacheMetadata, bool)> = None;
    let mut generation_failed = false;
    loop {
        let outcomes = scheduler
            .run_with_start(&batch, cancellation, |task| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    Some(&plan.component_id),
                    if configured.is_some() {
                        "CMake build of the configured analysis database"
                    } else {
                        "explicit native preparation"
                    },
                )
            })
            .map_err(|error| {
                PreparationFailure::Incomplete(format!(
                    "{}: {error}",
                    crate::preparation::PREPARATION_NOT_STARTED
                ))
            })?;
        for outcome in outcomes.outcomes {
            if let Some(record) = adapter_process_evidence(plan, &outcome) {
                process_evidence.push(record);
            }
            events.emit(
                "task.completed",
                Some(&outcome.task_id),
                Some(&plan.component_id),
                &outcome.state_label(),
            );
            // Frozen's failed generation build keeps the configured context and is a diagnostic.
            if plan.analysis_only && outcome.task_id == compile_id {
                generation_failed = !outcome.prerequisite_succeeded();
                continue;
            }
            if outcome.task_id == version_id {
                // Frozen keeps configuring whatever the probe returned; only its stdout counts.
                cmake_version = Some(frozen_cmake_version(&String::from_utf8_lossy(
                    &outcome.stdout,
                )));
                continue;
            }
            if !outcome.prerequisite_succeeded() {
                let detail = format!(
                    "native preparation {} failed ({}, timed_out={}, truncated={}): {} {}",
                    outcome.task_id,
                    outcome.ending(),
                    outcome.timed_out,
                    outcome.truncated,
                    outcome.error.unwrap_or_default(),
                    String::from_utf8_lossy(&outcome.stderr)
                        .chars()
                        .take(2048)
                        .collect::<String>()
                );
                if plan.build.system == "make" {
                    // Frozen Make adapter: a failed configure is a configuration error; a failed
                    // clean or build means the build did not produce a complete artifact set.
                    let step = outcome.task_id.rsplit(':').next().unwrap_or("compile");
                    let (step, configuration) = match step {
                        "configure" => ("configure", true),
                        "clean" => ("clean", false),
                        _ => ("build", false),
                    };
                    let mut output = crate::cpp::build_failure(
                        &outcome.task_id,
                        &plan.descriptor,
                        &plan.descriptor,
                        configuration,
                        &format!("Make {step} command did not complete successfully"),
                        &detail,
                    );
                    output.observation.exit_code = outcome.exit_code;
                    output.observation.timed_out = outcome.timed_out;
                    output.observation.truncated = outcome.truncated;
                    // The session's processes up to the failed one (frozen `BuildSession` evidence).
                    output.extra["process_evidence"] = json!(process_evidence);
                    return Err(PreparationFailure::Observed {
                        phase: format!("Make {step}"),
                        output: Box::new(output),
                    });
                }
                if matches!(plan.build.system.as_str(), "cmake" | "qmake") {
                    // Frozen CMake/qmake adapter (core/cmake.py `_fail`): '<backend> <step> failed:'
                    // plus the first 200 characters of stderr; a failed configure is a configuration
                    // error, a failed build means no complete artifact set.
                    let step = outcome.task_id.rsplit(':').next().unwrap_or("compile");
                    let configuration = step == "configure";
                    let message = format!(
                        "{} {} failed: {}",
                        plan.build.system,
                        if configuration { "configure" } else { "build" },
                        String::from_utf8_lossy(&outcome.stderr)
                            .chars()
                            .take(200)
                            .collect::<String>()
                    );
                    let mut output = crate::cpp::build_failure(
                        &outcome.task_id,
                        &plan.descriptor,
                        &plan.descriptor,
                        configuration,
                        &message,
                        &detail,
                    );
                    output.observation.exit_code = outcome.exit_code;
                    output.observation.timed_out = outcome.timed_out;
                    output.observation.truncated = outcome.truncated;
                    output.extra["process_evidence"] = json!(process_evidence);
                    if !configuration && plan.configured_context_on_build_failure {
                        // The analysis database is frozen's own preparation: the configured context
                        // and cache, and its generation build (this build) failed when that context
                        // named a source still to generate. Whatever the failed build left behind
                        // is not read.
                        let (context, analysis) = match configured.clone() {
                            Some((context, metadata, needed)) => {
                                let analysis = cmake_analysis::analyse(metadata, &context, needed);
                                (context, Some(analysis))
                            }
                            None => (prepared_context(plan), None),
                        };
                        if analysis.is_some()
                            || context.database_path.is_some()
                                && !context.units.is_empty()
                                && !context
                                    .diagnostics
                                    .iter()
                                    .chain(context.units.iter().flat_map(|unit| &unit.diagnostics))
                                    .any(|diagnostic| diagnostic.level == "error")
                        {
                            // Frozen analyses the configured database; nothing built is claimed.
                            return Ok(PreparedContext {
                                plan: plan.clone(),
                                context,
                                artifacts: vec![],
                                non_elf: vec![],
                                archives: vec![],
                                test_executable: None,
                                test_executables: BTreeMap::new(),
                                test_compile_failures: BTreeMap::new(),
                                test_compile_unfinished: BTreeMap::new(),
                                evidence: None,
                                build_failure: Some(detail),
                                build_failure_output: Some(Box::new(output)),
                                process_evidence,
                                cmake_version,
                                analysis,
                                _lease: lease.clone(),
                            });
                        }
                    }
                    return Err(PreparationFailure::Observed {
                        phase: format!("{} {step}", plan.build.system),
                        output: Box::new(output),
                    });
                }
                return Err(PreparationFailure::Incomplete(detail));
            }
        }
        // Configured: frozen's `_needs_generation_build` over this context decides the build.
        if plan.analysis_database && configured.is_none() {
            let context = prepared_context(plan);
            let metadata = cmake_analysis::configured_metadata(&plan.root, &plan.build.shadow);
            let needed = cmake_analysis::needs_generation_build(&context, &plan.build.shadow);
            configured = Some((context, metadata, needed));
            if !plan.analysis_only || needed {
                let mut build = plan
                    .build
                    .tasks
                    .iter()
                    .find(|task| task.id == compile_id)
                    .cloned()
                    .ok_or_else(|| incomplete("the CMake preparation has no build task"))?;
                build.depends_on.clear();
                batch = vec![build];
                continue;
            }
        }
        break;
    }
    if plan.build.system == "qmake" {
        lease.finalize_qmake(&plan.build).map_err(incomplete)?;
    }
    // Frozen keeps the configured context unless its generation build succeeded (then reloads).
    let (context, analysis) = match configured {
        Some((configured, metadata, needed)) => {
            let context = if needed && !generation_failed {
                prepared_context(plan)
            } else {
                configured
            };
            let analysis = cmake_analysis::analyse(metadata, &context, generation_failed);
            (context, Some(analysis))
        }
        None => (prepared_context(plan), None),
    };
    if plan.build.system != "make"
        && analysis.is_none()
        && (context.database_path.is_none()
            || context.units.is_empty()
            || context
                .diagnostics
                .iter()
                .chain(context.units.iter().flat_map(|unit| &unit.diagnostics))
                .any(|diagnostic| diagnostic.level == "error"))
    {
        return Err(PreparationFailure::Incomplete(format!(
            "prepared compilation evidence is incomplete: {:?}",
            context.diagnostics
        )));
    }
    // Frozen `_capture_artifact_manifest` after the session's build: a shadow tree past
    // `_linked_artifact_paths`' bounds fails the session (`_fail`) with 'artifact manifest
    // validation failed: …', which every consumer reports as that session's build failure
    // (artifact-manifest-bounds-oracle.json).
    if plan.legacy_manifest_bounds
        && !plan.analysis_only
        && let Err(reason) = crate::cpp::legacy_linked_artifact_bounds(&shadow)
    {
        let message = format!("artifact manifest validation failed: {reason}");
        let mut output = crate::cpp::build_failure(
            &compile_id,
            &plan.descriptor,
            &plan.descriptor,
            false,
            &message,
            &message,
        );
        output.observation.exit_code = Some(0);
        output.extra["process_evidence"] = json!(process_evidence);
        if plan.configured_context_on_build_failure {
            // The BuildEngine release session failed; frozen's configure-only analysis
            // database is unaffected, so the source checks still analyse it.
            return Ok(PreparedContext {
                plan: plan.clone(),
                context,
                artifacts: vec![],
                non_elf: vec![],
                archives: vec![],
                test_executable: None,
                test_executables: BTreeMap::new(),
                test_compile_failures: BTreeMap::new(),
                test_compile_unfinished: BTreeMap::new(),
                evidence: None,
                build_failure: Some(message),
                build_failure_output: Some(Box::new(output)),
                process_evidence,
                cmake_version,
                analysis,
                _lease: lease.clone(),
            });
        }
        return Err(PreparationFailure::Observed {
            phase: format!("{} build", plan.build.system),
            output: Box::new(output),
        });
    }
    let mut artifacts = vec![];
    let mut non_elf = vec![];
    let mut archives = vec![];
    let mut executables = vec![];
    // An analysis-only shadow publishes no build outputs, even after a generation build.
    let scanned = if plan.analysis_only {
        vec![]
    } else {
        crate::files::tree(&plan.root, &shadow).map_err(incomplete)?
    };
    // Frozen's legacy CMake manifest also selects the linked executables CMake's own compiler
    // checks leave under `CMakeFiles/`: a legacy ABI inventory, never published artifacts.
    let legacy_inventory = plan.configured_context_on_build_failure;
    for entry in scanned {
        let build_metadata = build_metadata_path(&entry.relative, &plan.build.shadow);
        if build_metadata && !legacy_inventory {
            continue;
        }
        if !entry.file || !entry.contained {
            continue;
        }
        let path = plan.root.join(&entry.relative);
        let metadata = fs::symlink_metadata(&path).map_err(incomplete)?;
        if !metadata.is_file()
            || metadata.nlink() != 1
            || path.canonicalize().map_err(incomplete)? != path
        {
            continue;
        }
        let mut bytes = Vec::with_capacity(20);
        fs::File::open(&path)
            .map_err(incomplete)?
            .take(20)
            .read_to_end(&mut bytes)
            .map_err(incomplete)?;
        // Frozen `_linked_artifact_paths` also lists PE (MZ) and Mach-O outputs; they stay a
        // legacy ABI inventory and never become published build artifacts.
        if !build_metadata
            && NON_ELF_MAGICS.iter().any(|magic| bytes.starts_with(magic))
            && ![".o", ".obj", ".gcno", ".gcda"]
                .iter()
                .any(|suffix| entry.relative.ends_with(suffix))
        {
            non_elf.push(entry.relative.clone());
            continue;
        }
        if !build_metadata
            && (entry.relative.ends_with(".a") || entry.relative.ends_with(".lib"))
            && bytes.starts_with(b"!<arch>\n")
        {
            archives.push(entry.relative.clone());
            continue;
        }
        if bytes.len() == 20 && &bytes[..4] == b"\x7fELF" {
            let kind = match bytes[5] {
                1 => u16::from_le_bytes([bytes[16], bytes[17]]),
                2 => u16::from_be_bytes([bytes[16], bytes[17]]),
                _ => continue,
            };
            if build_metadata {
                if matches!(kind, 2 | 3) {
                    non_elf.push(entry.relative.clone());
                }
                continue;
            }
            if matches!(kind, 2 | 3) {
                artifacts.push(entry.relative.clone());
                if metadata.permissions().mode() & 0o111 != 0 {
                    executables.push(entry.relative);
                }
            }
        }
    }
    let qtest = if plan.build.system == "qmake" {
        let bytes = ici_execution::storage::read_bounded_regular(
            &plan.root.join(&plan.descriptor),
            1024 * 1024,
        )
        .map_err(incomplete)?;
        String::from_utf8_lossy(&bytes)
            .lines()
            .map(|line| line.split('#').next().unwrap_or(""))
            .any(|line| {
                line.split_once("+=").is_some_and(|(left, right)| {
                    left.trim() == "CONFIG"
                        && right.split_whitespace().any(|token| token == "testcase")
                })
            })
    } else {
        false
    };
    let test_executable = (qtest && executables.len() == 1).then(|| executables[0].clone());
    Ok(PreparedContext {
        plan: plan.clone(),
        context,
        artifacts,
        non_elf,
        archives,
        test_executable,
        test_executables: BTreeMap::new(),
        test_compile_failures: BTreeMap::new(),
        test_compile_unfinished: BTreeMap::new(),
        evidence: None,
        build_failure: None,
        build_failure_output: None,
        process_evidence,
        cmake_version,
        analysis,
        _lease: lease.clone(),
    })
}

/// Frozen `core/cmake.py::_record`/`_run_make_command`: one `ToolEvidence` per adapter process,
/// named by backend and phase. Native-only steps (the qmake capability probe) record nothing.
fn adapter_process_evidence(
    plan: &ContextPreparationPlan,
    outcome: &ici_execution::TaskOutcome,
) -> Option<Value> {
    let task = plan
        .build
        .tasks
        .iter()
        .find(|task| task.id == outcome.task_id)?;
    let phase = task
        .id
        .strip_prefix(&format!("build:{}:{}:", plan.build.id, plan.build.variant))?;
    let name = match (plan.build.system.as_str(), phase) {
        ("make", "configure") => "make configure",
        ("make", "clean") => "make clean",
        ("make", "compile") => "make build",
        ("cmake", "version") => "cmake --version",
        ("cmake", "configure") => "cmake configure",
        ("cmake", "compile") => "cmake build",
        ("qmake", "configure") => "qmake configure",
        ("qmake", "compile") => "qmake build",
        _ => return None,
    };
    Some(json!({
        "name": name,
        "path": task.argv.first().cloned().unwrap_or_default(),
        "version": "",
        "argv": task.argv,
        "returncode": crate::cpp::frozen_returncode(outcome),
        "timed_out": outcome.timed_out,
        "truncated": outcome.truncated,
        "error": "",
    }))
}

/// The owned build's recorded compilation context. A handwritten Make backend need not write a
/// compilation database; then no database is claimed (and none is discovered elsewhere).
fn prepared_context(plan: &ContextPreparationPlan) -> CompilationContext {
    if plan.build.system == "make"
        && !fs::symlink_metadata(plan.root.join(&plan.build.compilation_database))
            .is_ok_and(|metadata| metadata.is_file())
    {
        return CompilationContext {
            variant: plan.build.variant.clone(),
            selected_sources: plan.selected_sources.clone(),
            ..Default::default()
        };
    }
    ici_cpp::load_compilation_context(
        &plan.root,
        &ContextRequest {
            database: Some(plan.build.compilation_database.clone()),
            variant: Some(plan.build.variant.clone()),
            selected_sources: plan.selected_sources.clone(),
            ..Default::default()
        },
    )
}

pub fn for_consumer(
    context: &PreparedContext,
    plan: &ContextPreparationPlan,
) -> Result<PreparedContext, String> {
    let original = &context.plan;
    if original.root != plan.root
        || original.build.id != plan.build.id
        || original.build.variant != plan.build.variant
        || original.build.owner_digest != plan.build.owner_digest
        || original.descriptor != plan.descriptor
    {
        return Err(
            "consumer alias does not identify the same owned native build transaction".into(),
        );
    }
    let mut alias = context.clone();
    alias.plan = plan.clone();
    alias.context = prepared_context(plan);
    Ok(alias)
}

pub fn overlay(project: &ResolvedProject, prepared: &[PreparedContext]) -> ResolvedProject {
    let mut overlay = project.clone();
    let mut projected: BTreeMap<(String, String, String), String> = BTreeMap::new();
    let mut ordered = prepared.iter().collect::<Vec<_>>();
    ordered.sort_by_key(|context| (&context.plan.component_id, &context.plan.build.variant));
    for context in ordered {
        let plan = &context.plan;
        let original = project
            .builds
            .iter()
            .find(|build| build.id == plan.build_id);
        let preferred = original
            .map(|build| build.variant.as_str())
            .unwrap_or("release");
        let primary = plan.build.variant == preferred;
        let key = (
            plan.build_id.clone(),
            plan.build.variant.clone(),
            plan.build.owner_digest.clone(),
        );
        let id = if let Some(id) = projected.get(&key) {
            id.clone()
        } else if primary
            && (plan.declared_build.is_some()
                || !overlay.builds.iter().any(|build| build.id == plan.build_id))
        {
            plan.build_id.clone()
        } else {
            let digest = sha256(format!("{}:{}", plan.build_id, plan.build.variant));
            let base = format!("native-{}", &digest[7..31]);
            let mut id = base.clone();
            let mut suffix = 1usize;
            while overlay.builds.iter().any(|build| build.id == id) {
                suffix += 1;
                id = format!("{base}-{suffix}");
            }
            id
        };
        projected.insert(key, id.clone());
        let mut build = plan.declared_build.clone().unwrap_or_else(|| Build {
            id: id.clone(),
            system: Some(plan.build.system.clone()),
            project: Some(plan.descriptor.clone()),
            directory: None,
            variant: plan.build.variant.clone(),
            prepare: Some("explicit".into()),
            artifacts: vec![],
            settings: json!({}),
            origin: project
                .components
                .iter()
                .find(|component| component.id == plan.component_id)
                .expect("prepared component")
                .origin
                .clone(),
        });
        build.id = id.clone();
        build.directory = Some(plan.build.shadow.clone());
        build.variant = plan.build.variant.clone();
        if plan.declared_build.is_none() {
            build.artifacts = context
                .artifacts
                .iter()
                .filter_map(|path| {
                    path.strip_prefix(&format!("{}/", plan.build.shadow))
                        .map(str::to_owned)
                })
                .collect();
            let non_elf: Vec<_> = context
                .non_elf
                .iter()
                .filter_map(|path| {
                    path.strip_prefix(&format!("{}/", plan.build.shadow))
                        .map(str::to_owned)
                })
                .collect();
            if !non_elf.is_empty() {
                build.settings["legacy_non_elf_artifacts"] = json!(non_elf);
            }
            let archives: Vec<_> = context
                .archives
                .iter()
                .filter_map(|path| {
                    path.strip_prefix(&format!("{}/", plan.build.shadow))
                        .map(str::to_owned)
                })
                .collect();
            if !archives.is_empty() {
                build.settings["legacy_archive_artifacts"] = json!(archives);
            }
        }
        if let Some(executable) = &context.test_executable {
            let relative = executable
                .strip_prefix(&format!("{}/", plan.build.shadow))
                .unwrap_or(executable);
            build.settings["test_argv"] = json!([relative]);
        }
        if plan.build.system == "make" && id != plan.build_id {
            // The projected variant build shares the configured Make shadow of its source build.
            build.settings["make_shadow_owner"] = json!(plan.build_id);
        }
        if !context.test_executables.is_empty() || !context.test_compile_failures.is_empty() {
            build.settings["direct_test_executables"] = json!(context.test_executables);
            build.settings["direct_compiled_sources"] = json!(plan.selected_sources);
        }
        if !context.test_compile_failures.is_empty() {
            build.settings["direct_test_compile_failures"] = json!(context.test_compile_failures);
        }
        if !context.test_compile_unfinished.is_empty() {
            build.settings["direct_test_compile_unfinished"] =
                json!(context.test_compile_unfinished);
        }
        if let Some(version) = context.cmake_version {
            // `[major, minor]`, or null for an unparseable `cmake --version`.
            build.settings["legacy_cmake_version"] = json!(version.map(|(a, b)| [a, b]));
        }
        if let Some(existing) = overlay.builds.iter_mut().find(|existing| existing.id == id) {
            *existing = build;
        } else {
            overlay.builds.push(build);
        }
        for component in &mut overlay.components {
            let original_component = project
                .components
                .iter()
                .find(|original| original.id == component.id)
                .expect("overlay component");
            if component.id != plan.component_id
                && !(plan.declared_build.is_some()
                    && original_component.build.as_deref() == Some(plan.build_id.as_str()))
            {
                continue;
            }
            component.settings["native_build_variants"][&plan.build.variant] = json!({"build_id":id,"directory":plan.build.shadow,"artifacts":context.artifacts,"test_executable":context.test_executable,"test_executables":context.test_executables,"database":plan.build.compilation_database});
            if plan.build.variant == "link-reachability" {
                component.settings["native_build_variants"][&plan.build.variant]["plan"] =
                    json!(plan.build);
            }
            // Every plan over this project reads the same configured context (frozen's).
            if let Some(analysis) = context
                .analysis
                .as_ref()
                .filter(|analysis| !analysis.stale_units.is_empty())
            {
                component.settings["legacy_preparation_stale_units"] = json!(analysis.stale_units);
            }
            // A legacy direct compile (test, sanitizer or the BuildEngine release binary) is
            // never the analysis context: frozen compiles those executables per engine and
            // keeps lint/compile_db/dead on the project's own (absent) database.
            let direct_runtime = plan.direct.is_some();
            if (primary || component.build.is_none()) && !direct_runtime {
                component.build = Some(id.clone());
                // The BuildEngine's release build: its artifacts are linked, the project's own
                // database stays the analysis context (frozen `_select_database`).
                if plan.existing_database {
                    component.settings["legacy_project_database"] = json!(true);
                // A handwritten Make backend need not write a compilation database; only an
                // observed database becomes an analysis input.
                } else if context.context.database_path.is_some() {
                    component.settings["project"]["compile_database"] =
                        json!(plan.root.join(&plan.build.compilation_database));
                }
            }
        }
    }
    overlay
}

/// Frozen BuildEngine `_has_build_descriptor`: a root native descriptor that no configured
/// adapter selected (for example a Makefile without `build.make.enabled = true`). Returns the
/// frozen descriptor label used in its "has no configured adapter" error and the file name.
pub fn unadapted_descriptor(directory: &Path) -> Option<(String, String)> {
    let regular = |path: &Path| fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_file());
    if let Some(name) = ["Makefile", "makefile", "GNUmakefile"]
        .into_iter()
        .find(|name| regular(&directory.join(name)))
    {
        return Some((name.into(), name.into()));
    }
    let mut names = fs::read_dir(directory)
        .ok()?
        .filter_map(Result::ok)
        .filter(|entry| regular(&entry.path()))
        .filter_map(|entry| entry.file_name().into_string().ok())
        .collect::<Vec<_>>();
    names.sort();
    names
        .into_iter()
        .find(|name| {
            name.ends_with(".pro")
                || name.starts_with("Makefile.")
                || name.ends_with(".mk")
                || name.ends_with(".make")
        })
        .map(|name| ("A build descriptor".into(), name))
}

/// The legacy `build.make` table: component-scoped settings first, else the project effective
/// configuration (component settings copy only language/engine tables).
pub(crate) fn make_table<'a>(
    project: &'a ResolvedProject,
    component: &'a Component,
) -> Option<&'a Value> {
    component
        .settings
        .pointer("/build/make")
        .or_else(|| project.effective.pointer("/build/make"))
}
fn select_descriptor(
    root: &Path,
    directory: &Path,
    system: Option<&str>,
    make: Option<&Value>,
) -> Result<String, String> {
    let regular = |path: &Path| fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_file());
    if system.is_none_or(|system| system == "cmake") && regular(&directory.join("CMakeLists.txt")) {
        return relative(root, &directory.join("CMakeLists.txt"));
    }
    if system.is_none_or(|system| system == "qmake") {
        let mut projects = fs::read_dir(directory)
            .map_err(|error| error.to_string())?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            // Frozen `root.glob("*.pro")` also matches a file named only `.pro` (a dot-name with
            // no extension for `Path::extension`).
            .filter(|path| {
                path.file_name()
                    .is_some_and(|name| name.to_string_lossy().ends_with(".pro"))
                    && regular(path)
            })
            .collect::<Vec<_>>();
        projects.sort();
        if let Some(path) = projects.first() {
            return relative(root, path);
        }
    }
    if system.is_none_or(|system| system == "make")
        && make
            .and_then(|make| make.get("enabled"))
            .and_then(Value::as_bool)
            == Some(true)
    {
        for name in ["Makefile", "makefile", "GNUmakefile"] {
            if regular(&directory.join(name)) {
                return relative(root, &directory.join(name));
            }
        }
    }
    Err("no selected root CMake/qmake descriptor or explicitly enabled Make build".into())
}
fn resolve(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
    id: &str,
    explicit: Option<PathBuf>,
    cwd: &Path,
) -> Result<ResolvedTool, String> {
    ToolRequest {
        id: id.into(),
        explicit: explicit.or_else(|| {
            project
                .tools
                .get(id)
                .and_then(|tool| tool.path.as_deref())
                .map(|path| crate::files::under(&project.root, path))
        }),
        project_root: cwd.into(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(|error| error.to_string())
}
fn relative(root: &Path, path: &Path) -> Result<String, String> {
    path.strip_prefix(root)
        .map_err(|error| error.to_string())?
        .to_str()
        .map(str::to_owned)
        .ok_or_else(|| "native path is not UTF-8".into())
}

fn build_metadata_path(path: &str, shadow: &str) -> bool {
    path.strip_prefix(shadow)
        .and_then(|path| path.strip_prefix('/'))
        .is_some_and(|path| {
            matches!(
                path.split('/').next(),
                Some("CMakeFiles" | "CMakeTmp" | ".cmake")
            )
        })
}
