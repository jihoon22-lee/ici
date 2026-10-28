use crate::{
    OwnedArtifactSpec, StepPrerequisite, ValidationError, ValidationKind, ValidationPlan,
    ValidationRequest,
};
use ici_domain::{TaskKind, TaskSpec, TaskState, Validate};
use ici_execution::TaskOutcome;
use std::{collections::BTreeMap, path::Path};

pub const METADATA_SCRIPT: &str = "import json,sys,sysconfig,platform;print(json.dumps({'version':list(sys.version_info[:3]),'implementation':sys.implementation.name,'platform':sys.platform,'machine':platform.machine(),'soabi':sysconfig.get_config_var('SOABI'),'ext_suffix':sysconfig.get_config_var('EXT_SUFFIX')}))";
pub const IMPORT_SCRIPT: &str = r#"import sys
_ici_baseline = set(sys.modules)
import json as _ici_json
import importlib as _ici_importlib
_ici_modules = _ici_json.loads(sys.argv[2])
for _ici_name in set(sys.modules) - _ici_baseline:
    sys.modules.pop(_ici_name, None)
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
_ici_results = []
for _ici_name in _ici_modules:
    try:
        _ici_importlib.import_module(_ici_name)
        _ici_results.append({'module': _ici_name, 'ok': True})
    except Exception as _ici_error:
        _ici_results.append({'module': _ici_name, 'ok': False, 'error': type(_ici_error).__name__ + ': ' + str(_ici_error)})
print('\n' + _ici_json.dumps({'imports': _ici_results}))
sys.exit(0 if all(_ici_item['ok'] for _ici_item in _ici_results) else 1)"#;

pub fn plan(request: &ValidationRequest) -> Result<ValidationPlan, ValidationError> {
    for path in [
        &request.root,
        &request.cwd,
        &request.owned_run_dir,
        &request.tool.path,
    ] {
        if path.components().any(|part| {
            !matches!(
                part,
                std::path::Component::RootDir | std::path::Component::Normal(_)
            )
        }) {
            return Err(ValidationError(
                "validation paths must be normalized".into(),
            ));
        }
    }
    if !request.root.is_absolute()
        || !request.cwd.is_absolute()
        || !request.owned_run_dir.is_absolute()
        || !request.tool.path.is_absolute()
    {
        return Err(ValidationError(
            "validation roots, run directory and executable must be absolute".into(),
        ));
    }
    if !request.cwd.starts_with(&request.root) || !request.owned_run_dir.starts_with(&request.root)
    {
        return Err(ValidationError(
            "validation cwd/run directory must lie inside workspace".into(),
        ));
    }
    if !request.options.timeout_seconds.is_finite() || request.options.timeout_seconds <= 0.0 {
        return Err(ValidationError(
            "validation timeout must be positive and finite".into(),
        ));
    }
    if request
        .options
        .output_limit_bytes
        .is_some_and(|limit| limit == 0 || limit > 16 * 1024 * 1024)
    {
        return Err(ValidationError(
            "validation output limit must be 1..16 MiB".into(),
        ));
    }
    for threshold in [
        request.options.min_line_cov,
        request.options.min_file_cov,
        request.options.min_branch_cov,
        request.options.min_func_cov,
        request.options.min_changed_line_cov,
        request.options.max_coverage_regression,
    ]
    .into_iter()
    .flatten()
    {
        if !threshold.is_finite() || !(0.0..=100.0).contains(&threshold) {
            return Err(ValidationError(
                "coverage minimum must lie between 0 and 100".into(),
            ));
        }
    }
    if request.options.min_file_statements == 0
        || request
            .options
            .min_tem_score
            .is_some_and(|value| !value.is_finite() || !(0.0..=5.0).contains(&value))
    {
        return Err(ValidationError(
            "invalid coverage statement floor or TEM minimum".into(),
        ));
    }
    crate::policy::changed_lines(&request.options.changed_lines, &request.source_files)?;
    if request.options.repeat_runs > 3
        || !matches!(request.options.quality_mode.as_str(), "report" | "warn")
        || !request.options.quality_timeout_seconds.is_finite()
        || request.options.quality_timeout_seconds <= 0.0
        || !(1..=1000).contains(&request.options.max_slow_tests)
        || request
            .options
            .slow_threshold_seconds
            .is_some_and(|v| !v.is_finite() || v < 0.0)
    {
        return Err(ValidationError(
            "quality runs exceed 3 or slow threshold is invalid".into(),
        ));
    }
    for path in &request.targets {
        ici_domain::validation::relative_path(path, true)
            .map_err(|e| ValidationError(e.to_string()))?;
    }
    for path in &request.source_files {
        ici_domain::validation::relative_path(path, false)
            .map_err(|e| ValidationError(e.to_string()))?;
    }
    let mut plan = ValidationPlan {
        kind: request.kind,
        tasks: vec![],
        artifacts: vec![],
        artifact_patterns: vec![],
        runtime_plans: vec![],
        optional_task_ids: vec![],
        conditional_steps: BTreeMap::new(),
        prerequisites: BTreeMap::new(),
        context: request.clone(),
        limitations: vec!["dynamic evidence is never reused from a previous run".into()],
        source_coordinates: std::sync::Arc::default(),
        artifact_headers: BTreeMap::new(),
    };
    if request.kind == ValidationKind::Gcov {
        for target in &request.targets {
            let relative = Path::new(target).with_extension("gcda");
            let exists = match std::fs::symlink_metadata(request.root.join(&relative)) {
                Ok(_) => true,
                Err(error) => error.kind() != std::io::ErrorKind::NotFound,
            };
            let relative = relative.to_string_lossy().into_owned();
            if exists
                && !plan
                    .context
                    .options
                    .gcov_preexisting_data
                    .contains(&relative)
            {
                plan.context.options.gcov_preexisting_data.push(relative);
            }
        }
    }
    if !request.options.runtimes.is_empty() {
        return crate::runtime::plan(request, plan);
    }
    if !request.options.required_interpreters.is_empty() {
        return Err(ValidationError(
            "required_interpreters requires a configured runtime matrix".into(),
        ));
    }
    match request.kind {
        ValidationKind::PythonTest
        | ValidationKind::PythonCoverage
        | ValidationKind::PythonSanitize => python_suite(&mut plan)?,
        ValidationKind::PythonRuntime => {
            let mut metadata = task(
                request,
                "metadata",
                vec!["-I".into(), "-c".into(), METADATA_SCRIPT.into()],
            );
            metadata.kind = TaskKind::Probe;
            metadata.timeout_seconds = Some(5.0);
            metadata.output_limit_bytes = Some(64 * 1024);
            let metadata_id = metadata.id.clone();
            plan.tasks.push(metadata);
            if request.options.compile_sources {
                if request.source_files.is_empty() {
                    return Err(ValidationError(
                        "runtime compile requires explicit source files".into(),
                    ));
                }
                let mut args = vec![
                    "-B".into(),
                    "-m".into(),
                    "compileall".into(),
                    "-q".into(),
                    "-f".into(),
                ];
                args.extend(
                    request
                        .source_files
                        .iter()
                        .map(|path| request.root.join(path).to_string_lossy().into_owned()),
                );
                let mut compile = task(request, "compile", args);
                compile.env_overlay.insert(
                    "PYTHONPYCACHEPREFIX".into(),
                    request
                        .owned_run_dir
                        .join("pycache")
                        .to_string_lossy()
                        .into_owned(),
                );
                compile
                    .env_overlay
                    .insert("PYTHON_COLORS".into(), "0".into());
                compile
                    .env_overlay
                    .insert("PYTHONHASHSEED".into(), "0".into());
                compile.work_dirs.push(
                    request
                        .owned_run_dir
                        .join("pycache")
                        .to_string_lossy()
                        .into_owned(),
                );
                compile.timeout_seconds = Some(120.0);
                plan.prerequisites.insert(
                    compile.id.clone(),
                    vec![StepPrerequisite {
                        task_id: metadata_id,
                        accepted_exits: vec![0],
                        require_test_evidence: false,
                    }],
                );
                plan.tasks.push(compile);
            }
        }
        ValidationKind::PythonImports => {
            if request.options.modules.is_empty() || request.options.modules.len() > 256 {
                return Err(ValidationError(
                    "import-only requires 1..256 explicitly declared modules".into(),
                ));
            }
            for module in &request.options.modules {
                if module.split('.').any(|part| {
                    part.is_empty() || !part.chars().all(|c| c == '_' || c.is_alphanumeric())
                }) {
                    return Err(ValidationError(format!("invalid declared import {module}")));
                }
            }
            let mut imports = task(
                request,
                "imports",
                vec![
                    "-I".into(),
                    "-B".into(),
                    "-c".into(),
                    IMPORT_SCRIPT.into(),
                    request.cwd.to_string_lossy().into_owned(),
                    serde_json::to_string(&request.options.modules).expect("strings serialize"),
                ],
            );
            imports.timeout_seconds = Some(30.0);
            imports.output_limit_bytes = Some(1024 * 1024);
            imports.kind = TaskKind::Test;
            plan.tasks.push(imports);
            plan.limitations.push("explicit imports execute module initialization; module main entry points are not invoked".into());
        }
        ValidationKind::Ctest
        | ValidationKind::Qtest
        | ValidationKind::Sanitizer
        | ValidationKind::ThreadSanitizer
        | ValidationKind::Gcov
        | ValidationKind::BinaryCompatibility => crate::cpp::plan(&mut plan)?,
        ValidationKind::Integration => crate::integration::plan(&mut plan)?,
    }
    for task in &plan.tasks {
        task.validate()
            .map_err(|e| ValidationError(e.to_string()))?;
    }
    Ok(plan)
}
fn python_suite(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    if request.targets.is_empty() {
        return Err(ValidationError(
            "pytest requires an explicit test scope".into(),
        ));
    }
    let with_coverage =
        request.options.collect_coverage || request.kind == ValidationKind::PythonCoverage;
    let data = request.owned_run_dir.join("coverage.data");
    let suite_id = request
        .options
        .shared_test_task_id
        .clone()
        .unwrap_or_else(|| format!("{}.suite", request.task_id));
    if request.options.shared_test_task_id.is_none() {
        let mut args = if request.kind == ValidationKind::PythonSanitize {
            vec!["-W".into(), "error::ResourceWarning".into()]
        } else {
            vec![]
        };
        if with_coverage {
            args.extend([
                "-m".into(),
                "coverage".into(),
                "run".into(),
                "--branch".into(),
                "--data-file".into(),
                data.to_string_lossy().into_owned(),
            ]);
        }
        args.extend([
            "-m".into(),
            "pytest".into(),
            "-p".into(),
            "no:cacheprovider".into(),
            "-v".into(),
            if request.kind == ValidationKind::PythonSanitize {
                "--tb=long".into()
            } else {
                "--tb=short".into()
            },
            "--color=no".into(),
        ]);
        if request.kind == ValidationKind::PythonSanitize {
            args.extend(["-o".into(), "addopts=".into()]);
        }
        if request
            .options
            .slow_threshold_seconds
            .is_some_and(|threshold| threshold > 0.0)
        {
            args.push("--durations=0".into());
        }
        args.extend(
            request
                .targets
                .iter()
                .map(|target| request.root.join(target).to_string_lossy().into_owned()),
        );
        let mut suite = task(request, "suite", args.clone());
        suite.id = suite_id.clone();
        suite.kind = TaskKind::Test;
        if with_coverage {
            suite.output_specs.push(data.to_string_lossy().into_owned());
        }
        plan.tasks.push(suite);
        let mut previous = suite_id.clone();
        for index in 0..request.options.repeat_runs.saturating_sub(1) {
            // Quality repeats must not overwrite the primary coverage database.
            let mut repeat_args = args.clone();
            if with_coverage {
                repeat_args.drain(..6);
            }
            let mut repeat = task(request, &format!("repeat{}", index + 1), repeat_args);
            repeat.kind = TaskKind::Test;
            repeat.timeout_seconds = Some(request.options.quality_timeout_seconds);
            plan.prerequisites.insert(
                repeat.id.clone(),
                vec![StepPrerequisite {
                    task_id: previous,
                    accepted_exits: vec![0, 1],
                    require_test_evidence: true,
                }],
            );
            previous = repeat.id.clone();
            plan.optional_task_ids.push(repeat.id.clone());
            plan.tasks.push(repeat);
        }
        if request.kind == ValidationKind::PythonTest && request.options.mutation_enabled {
            if let Some(tool) = &request.options.mutation_tool {
                let argv = if request.options.mutation_argv.is_empty() {
                    vec![tool.path.to_string_lossy().into_owned(), "--version".into()]
                } else {
                    request.options.mutation_argv.clone()
                };
                if std::path::Path::new(&argv[0]) != tool.path {
                    return Err(ValidationError(
                        "mutation probe argv must match selected executable identity".into(),
                    ));
                }
                let mut probe = task(request, "mutation", vec![]);
                probe.argv = argv;
                probe.tool_digest = Some(tool.digest.clone());
                probe.kind = TaskKind::Probe;
                probe.timeout_seconds = Some(request.options.quality_timeout_seconds);
                probe.output_limit_bytes = Some(8192);
                plan.optional_task_ids.push(probe.id.clone());
                plan.prerequisites.insert(
                    probe.id.clone(),
                    vec![StepPrerequisite {
                        task_id: previous,
                        accepted_exits: vec![0, 1],
                        require_test_evidence: false,
                    }],
                );
                plan.tasks.push(probe);
            } else {
                plan.limitations.push("mutation capability was requested but no explicit external tool was available; base test gate is unchanged".into());
            }
        }
    } else if request.kind != ValidationKind::PythonCoverage {
        return Err(ValidationError(
            "shared suite references are only valid for coverage readers".into(),
        ));
    }
    if request.kind == ValidationKind::PythonCoverage {
        let report = request.owned_run_dir.join("coverage.json");
        let mut reporting = task(
            request,
            "report",
            vec![
                "-m".into(),
                "coverage".into(),
                "json".into(),
                "--data-file".into(),
                data.to_string_lossy().into_owned(),
                "-o".into(),
                report.to_string_lossy().into_owned(),
            ],
        );
        reporting.kind = TaskKind::Prepare;
        reporting
            .input_refs
            .push(data.to_string_lossy().into_owned());
        reporting
            .output_specs
            .push(report.to_string_lossy().into_owned());
        plan.prerequisites.insert(
            reporting.id.clone(),
            vec![StepPrerequisite {
                task_id: suite_id,
                accepted_exits: vec![0, 1],
                require_test_evidence: true,
            }],
        );
        plan.artifacts.push(OwnedArtifactSpec {
            id: "coverage-json".into(),
            path: report,
            producer_task_id: reporting.id.clone(),
            max_bytes: 32 * 1024 * 1024,
            required: true,
        });
        plan.tasks.push(reporting);
    }
    crate::python_coverage::configure(plan)?;
    Ok(())
}
pub(crate) fn task(request: &ValidationRequest, suffix: &str, args: Vec<String>) -> TaskSpec {
    let mut argv = vec![request.tool.path.to_string_lossy().into_owned()];
    argv.extend(args);
    let mut task = TaskSpec::command(
        format!("{}.{}", request.task_id, suffix),
        request.kind.provider(),
        argv,
        request.cwd.to_string_lossy(),
    );
    task.kind = TaskKind::Analyze;
    task.cacheable = false;
    task.resource_keys
        .push(format!("project-execution:{}", request.cwd.display()));
    if let Some(build) = &request.options.build_dir {
        task.resource_keys
            .push(format!("build:{}", build.display()));
    }
    task.tool_digest = Some(request.tool.digest.clone());
    task.timeout_seconds = Some(request.options.timeout_seconds);
    task.output_limit_bytes = Some(
        request
            .options
            .output_limit_bytes
            .unwrap_or(8 * 1024 * 1024) as usize,
    );
    task.env_overlay = request.options.environment.clone();
    task.env_overlay
        .insert("PYTHONDONTWRITEBYTECODE".into(), "1".into());
    task.env_overlay
        .insert("OMP_NUM_THREADS".into(), "2".into());
    task.input_refs = request
        .source_files
        .iter()
        .chain(&request.targets)
        .map(|path| request.root.join(path).to_string_lossy().into_owned())
        .collect();
    task
}
pub(crate) fn completed(outcome: &TaskOutcome) -> bool {
    outcome.state == TaskState::Succeeded
        && !outcome.cancelled
        && !outcome.timed_out
        && !outcome.truncated
        && outcome.signal.is_none()
        && outcome.exit_code.is_some()
}
pub fn ready_steps(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<Vec<String>, ValidationError> {
    if plan.kind == ValidationKind::Gcov && !plan.context.options.gcov_preexisting_data.is_empty() {
        return Err(ValidationError(format!(
            "GCOV counters existed before this run and may contain accumulated coverage; user data was preserved: {}",
            plan.context.options.gcov_preexisting_data.join(", ")
        )));
    }
    let mut ready = vec![];
    for (index, task) in plan.tasks.iter().enumerate() {
        if !crate::python_coverage::condition_ready(plan, &task.id, outcomes)? {
            continue;
        }
        if plan.kind == ValidationKind::BinaryCompatibility
            && plan
                .artifact_headers
                .get(&plan.context.targets[index])
                .is_some_and(|header| header.magic != b"\x7fELF")
        {
            continue;
        }
        if outcomes.contains_key(&task.id) {
            continue;
        }
        let mut pending = false;
        for requirement in plan.prerequisites.get(&task.id).into_iter().flatten() {
            let prerequisite =
                crate::python_coverage::prerequisite(plan, &requirement.task_id, outcomes)?;
            let Some(outcome) = outcomes.get(&prerequisite) else {
                pending = true;
                break;
            };
            // Each optional quality attempt remains sequential, but a failed earlier attempt
            // must not suppress later independent observations or the mutation capability probe.
            if plan.optional_task_ids.contains(&task.id)
                && requirement.task_id.contains(".repeat")
                && plan.optional_task_ids.contains(&requirement.task_id)
            {
                continue;
            }
            if !completed(outcome)
                || !requirement
                    .accepted_exits
                    .contains(&outcome.exit_code.unwrap_or(-1))
            {
                if plan.optional_task_ids.contains(&task.id) {
                    pending = true;
                    break;
                }
                return Err(ValidationError(format!(
                    "{} prerequisite {} did not complete under its exit contract",
                    task.id, requirement.task_id
                )));
            }
            if requirement.require_test_evidence {
                let text = std::str::from_utf8(&outcome.stdout).map_err(|_| {
                    ValidationError("pytest prerequisite output is not UTF-8".into())
                })?;
                let parsed = crate::python::parse_pytest(text, &plan.context)?;
                if parsed.collection_error || parsed.cases.is_empty() {
                    return Err(ValidationError(
                        "coverage requires a completed nonempty pytest suite".into(),
                    ));
                }
            }
            if requirement.task_id.ends_with(".inventory")
                && plan.context.options.sanitizer_suite_kind == Some(ValidationKind::Ctest)
            {
                crate::sanitizer_suite::inventory(plan, outcome)?;
            }
            if requirement.task_id.ends_with(".instrumentation") {
                crate::cpp::instrumentation(plan, outcome)?;
            }
            if plan.kind == ValidationKind::Gcov {
                crate::cpp::completed_test(plan, &requirement.task_id, outcome)?;
            }
        }
        if !pending {
            ready.push(task.id.clone());
        }
    }
    Ok(ready)
}
pub(crate) fn relative(path: &str, root: &Path, cwd: &Path) -> Result<String, ValidationError> {
    let candidate = if Path::new(path).is_absolute() {
        Path::new(path).to_path_buf()
    } else {
        cwd.join(path)
    };
    // Tool locations are often relative to an out-of-source build directory (../main.cpp).
    // Normalize those lexical coordinates without opening source paths or following symlinks.
    let mut normalized = std::path::PathBuf::new();
    for part in candidate.components() {
        match part {
            std::path::Component::RootDir => normalized.push(part.as_os_str()),
            std::path::Component::Normal(value) => normalized.push(value),
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir if normalized.pop() => {}
            _ => {
                return Err(ValidationError(format!(
                    "external validation location {path}"
                )));
            }
        }
    }
    let relative = normalized
        .strip_prefix(root)
        .map_err(|_| ValidationError(format!("external validation location {path}")))?
        .to_string_lossy()
        .replace('\\', "/");
    ici_domain::validation::relative_path(&relative, false)
        .map_err(|e| ValidationError(e.to_string()))?;
    Ok(relative)
}
