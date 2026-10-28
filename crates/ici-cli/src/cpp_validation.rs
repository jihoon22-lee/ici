//! Existing declared C++ runtime artifacts. Binding is read-only and never builds a project.
use ici_application::{plan::Work, request::RunRequest};
use ici_config::{Build, Component, ResolvedProject};
use ici_domain::CheckDefinition;
use ici_providers::resolver::{ResolvedTool, ToolRequest};
use ici_validation::{ValidationKind, ValidationOptions, ValidationPlan, ValidationRequest};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub struct BoundCppValidation {
    pub work: Work,
    pub plans: Vec<ValidationPlan>,
    pub tools: BTreeMap<String, ResolvedTool>,
}
fn blocked(reason: String) -> BoundCppValidation {
    BoundCppValidation {
        work: Work::Blocked { reason },
        plans: vec![],
        tools: BTreeMap::new(),
    }
}
pub fn bind(
    project: &ResolvedProject,
    component: &Component,
    check: &CheckDefinition,
    id: &str,
    request: &RunRequest,
    environment: &BTreeMap<String, String>,
    run_label: &str,
) -> Option<BoundCppValidation> {
    let kind = match check.id.as_str() {
        "cpp.test" => ValidationKind::Ctest,
        "cpp.coverage" => ValidationKind::Gcov,
        "cpp.sanitize" => ValidationKind::Sanitizer,
        "cpp.tsan" => ValidationKind::ThreadSanitizer,
        "cpp.binary-compat" => ValidationKind::BinaryCompatibility,
        _ => return None,
    };
    Some(
        match bind_inner(
            project,
            component,
            kind,
            id,
            request,
            environment,
            run_label,
        ) {
            Ok(bound) => bound,
            Err(reason) => blocked(reason),
        },
    )
}
fn bind_inner(
    project: &ResolvedProject,
    component: &Component,
    kind: ValidationKind,
    id: &str,
    run_request: &RunRequest,
    environment: &BTreeMap<String, String>,
    run_label: &str,
) -> Result<BoundCppValidation, String> {
    let variant = match kind {
        ValidationKind::Gcov => Some("coverage"),
        ValidationKind::Ctest if selected(project, component, run_request, "cpp.coverage") => {
            Some("coverage")
        }
        ValidationKind::Sanitizer => Some("sanitize"),
        ValidationKind::ThreadSanitizer => Some("thread-sanitize"),
        _ => None,
    };
    let build = variant
        .and_then(|variant| {
            component
                .settings
                .get("native_build_variants")
                .and_then(|variants| variants.get(variant))
        })
        .and_then(|variant| variant.get("build_id"))
        .and_then(Value::as_str)
        .and_then(|id| project.builds.iter().find(|build| build.id == id))
        .or_else(|| crate::cpp::linked_build(project, component))
        .ok_or_else(|| "C++ runtime checks require a declared component build".to_string())?;
    let directory = crate::files::under(&project.root, build.directory.as_deref().unwrap_or("."));
    regular_directory(&project.root, &directory)?;
    let artifacts = declared_artifacts(project, component, build)?;
    let sources: Vec<_> = component
        .files
        .iter()
        .filter(|path| ici_application::plan::file_language(path) == Some("cpp"))
        .cloned()
        .collect();
    let mut tools = BTreeMap::new();
    let owned = project
        .root
        .join(".ici/rust-v1/runs")
        .join(run_label)
        .join(&component.id)
        .join("cpp");
    let initial_tool = if matches!(kind, ValidationKind::Ctest | ValidationKind::Gcov) {
        let field = if kind == ValidationKind::Gcov {
            "coverage_test_argv"
        } else {
            "test_argv"
        };
        let argv = suite_argv(component, build, field);
        if let Some(selected) = argv.first() {
            executable(
                project,
                environment,
                build,
                &artifacts,
                selected,
                &directory,
            )?
        } else {
            resolve(project, environment, "ctest", None, &directory)?
        }
    } else {
        resolve(project, environment, "readelf", None, &directory)?
    };
    let mut request = ValidationRequest {
        kind,
        task_id: id.into(),
        component_id: component.id.clone(),
        root: project.root.clone(),
        cwd: directory.clone(),
        owned_run_dir: owned.clone(),
        tool: initial_tool,
        targets: vec![],
        source_files: sources,
        functions: vec![],
        options: ValidationOptions {
            build_dir: Some(directory.clone()),
            build_roots: vec![project.root.clone(), directory.clone()],
            ..Default::default()
        },
    };
    let mut plans = vec![];
    match kind {
        ValidationKind::BinaryCompatibility => {
            let cfg = component
                .settings
                .pointer("/engines/binary_compat")
                .cloned()
                .unwrap_or_default();
            let selected = strings(cfg.get("artifacts"));
            request.targets = artifacts
                .iter()
                .filter(|path| {
                    selected.is_empty()
                        || selected
                            .iter()
                            .any(|value| artifact_matches(build, path, value))
                })
                .cloned()
                .collect();
            for value in &selected {
                if !request
                    .targets
                    .iter()
                    .any(|path| artifact_matches(build, path, value))
                {
                    return Err(format!(
                        "configured ABI artifact {value:?} is not an existing declared artifact"
                    ));
                }
            }
            if request.targets.is_empty() {
                return Err("no existing declared artifacts for binary compatibility; prepare the declared build first".into());
            }
            request.options.elf_policy = policy(&cfg)?;
            request.options.allow_non_elf = cfg
                .get("allow_non_elf")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            request.options.max_artifacts = cfg
                .get("max_artifacts")
                .and_then(Value::as_u64)
                .unwrap_or(64) as usize;
            tools.insert(format!("{}:readelf", component.id), request.tool.clone());
        }
        ValidationKind::Gcov => {
            let mut suite = request.clone();
            suite.task_id = format!("{id}.tests");
            configure_suite(
                project,
                component,
                build,
                environment,
                &artifacts,
                "coverage_test_argv",
                &mut suite,
            )?;
            let test_selected = selected(project, component, run_request, "cpp.test");
            let shared = test_selected
                && (suite_argv(component, build, "coverage_test_argv").is_empty()
                    || suite_argv(component, build, "coverage_test_argv")
                        == suite_argv(component, build, "test_argv"));
            request.options.shared_test_kind = Some(suite.kind);
            request.options.shared_test_task_id = Some(if shared {
                format!("{}.cpp.test.suite", component.id)
            } else {
                format!("{}.suite", suite.task_id)
            });
            tools.insert(
                format!("{}:coverage-suite", component.id),
                suite.tool.clone(),
            );
            if !shared {
                plans.push(make_plan(suite, component)?);
            }
            request.tool = resolve(project, environment, "gcov", None, &directory)?;
            tools.insert(format!("{}:gcov", component.id), request.tool.clone());
            request.targets = crate::files::tree(&project.root, &directory)
                .map_err(|error| error.to_string())?
                .into_iter()
                .filter(|entry| entry.file && entry.contained && entry.relative.ends_with(".gcno"))
                .map(|entry| entry.relative)
                .collect();
            for target in &request.targets {
                regular_file(&project.root, &project.root.join(target))?;
            }
            if request.targets.is_empty() {
                return Err("no existing .gcno notes under the declared build; prepare its coverage variant first".into());
            }
            let cfg = component.settings.pointer("/engines/test");
            request.options.min_line_cov = number(cfg, "min_line_cov");
            request.options.min_file_cov = number(cfg, "min_file_cov");
            request.options.min_branch_cov = number(cfg, "min_branch_cov");
            request.options.min_func_cov = number(cfg, "min_func_cov");
            request.options.min_file_statements = cfg
                .and_then(|cfg| cfg.get("min_file_statements"))
                .and_then(Value::as_u64)
                .unwrap_or(5);
        }
        ValidationKind::Ctest => {
            configure_suite(
                project,
                component,
                build,
                environment,
                &artifacts,
                "test_argv",
                &mut request,
            )?;
            tools.insert(format!("{}:cpp-suite", component.id), request.tool.clone());
        }
        ValidationKind::Sanitizer | ValidationKind::ThreadSanitizer => {
            let field = if kind == ValidationKind::Sanitizer {
                "sanitize_test_argv"
            } else {
                "thread_sanitize_test_argv"
            };
            let mut argv = suite_argv(component, build, field);
            if argv.is_empty() && variant == Some(build.variant.as_str()) {
                argv = suite_argv(component, build, "test_argv");
            }
            validate_argv(&argv)?;
            if argv.is_empty() && build.system.as_deref() == Some("cmake") {
                let inventory = directory.join("CTestTestfile.cmake");
                regular_file(&project.root, &inventory)?;
                request.tool = resolve(project, environment, "ctest", None, &directory)?;
                request.options.sanitizer_suite_kind = Some(ValidationKind::Ctest);
                request.targets = vec![relative(&project.root, &inventory)?];
                for path in &artifacts {
                    let absolute = project.root.join(path);
                    if crate::export_context::compiled_artifact_kind(&absolute)?
                        == Some("executable")
                    {
                        let artifact = resolve(
                            project,
                            environment,
                            "instrumented-artifact",
                            Some(absolute),
                            &directory,
                        )?;
                        tools.insert(
                            format!(
                                "{}:{}:artifact{}",
                                component.id,
                                kind.provider(),
                                request.options.instrumented_artifacts.len()
                            ),
                            artifact,
                        );
                        request.options.instrumented_artifacts.push(path.clone());
                    }
                }
                if request.options.instrumented_artifacts.is_empty() {
                    return Err("CMake sanitizer suite has no declared executable artifacts to prove instrumentation".into());
                }
            } else {
                if argv.is_empty() {
                    return Err(format!(
                        "{field} must select an existing declared instrumented executable"
                    ));
                }
                request.tool = executable(
                    project,
                    environment,
                    build,
                    &artifacts,
                    &argv[0],
                    &directory,
                )?;
                request.options.argv =
                    std::iter::once(request.tool.path.to_string_lossy().into_owned())
                        .chain(argv.into_iter().skip(1))
                        .collect();
                request.targets = vec![relative(&project.root, &request.tool.path)?];
                if build.system.as_deref() == Some("qmake") {
                    request.options.sanitizer_suite_kind = Some(ValidationKind::Qtest);
                    request.options.instrumented_artifacts = request.targets.clone();
                }
            }
            let readelf = resolve(project, environment, "readelf", None, &directory)?;
            request
                .options
                .auxiliary_tools
                .insert("readelf".into(), readelf.clone());
            tools.insert(format!("{}:readelf", component.id), readelf);
            tools.insert(
                format!("{}:{}:instrumented-suite", component.id, kind.provider()),
                request.tool.clone(),
            );
        }
        _ => unreachable!(),
    }
    plans.push(make_plan(request, component)?);
    let tasks = plans
        .iter()
        .flat_map(|plan| plan.tasks.iter().cloned())
        .collect();
    Ok(BoundCppValidation {
        work: Work::steps(tasks, false),
        plans,
        tools,
    })
}
fn make_plan(request: ValidationRequest, component: &Component) -> Result<ValidationPlan, String> {
    let mut plan = ici_validation::plan(&request).map_err(|error| error.to_string())?;
    for task in &mut plan.tasks {
        task.analysis_unit_ids = vec![format!("{}.cpp", component.id)];
        task.work_dirs
            .push(request.owned_run_dir.to_string_lossy().into_owned());
    }
    Ok(plan)
}
fn configure_suite(
    project: &ResolvedProject,
    component: &Component,
    build: &Build,
    environment: &BTreeMap<String, String>,
    artifacts: &[String],
    field: &str,
    request: &mut ValidationRequest,
) -> Result<(), String> {
    let mut argv = suite_argv(component, build, field);
    if argv.is_empty() && field == "coverage_test_argv" {
        argv = suite_argv(component, build, "test_argv");
    }
    validate_argv(&argv)?;
    if !argv.is_empty() {
        request.kind = ValidationKind::Qtest;
        request.tool = executable(
            project,
            environment,
            build,
            artifacts,
            &argv[0],
            &request.cwd,
        )?;
        request.targets = vec![relative(&project.root, &request.tool.path)?];
        request.options.argv = std::iter::once(request.tool.path.to_string_lossy().into_owned())
            .chain(argv.into_iter().skip(1))
            .collect();
    } else if build.system.as_deref() == Some("cmake") {
        let inventory = request.cwd.join("CTestTestfile.cmake");
        regular_file(&project.root, &inventory).map_err(|error| {
            format!("CTest inventory is unavailable: {error}; prepare the declared build first")
        })?;
        request.kind = ValidationKind::Ctest;
        request.tool = resolve(project, environment, "ctest", None, &request.cwd)?;
        request.targets = vec![relative(&project.root, &inventory)?];
    } else {
        return Err(format!(
            "{field} must name an existing declared QTest executable for this build system"
        ));
    }
    Ok(())
}
fn suite_argv(component: &Component, build: &Build, field: &str) -> Vec<String> {
    let modern = strings(build.settings.get(field));
    if !modern.is_empty() {
        modern
    } else {
        strings(component.settings.pointer(&format!("/build/make/{field}")))
    }
}
fn executable(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
    build: &Build,
    artifacts: &[String],
    selected: &str,
    cwd: &Path,
) -> Result<ResolvedTool, String> {
    let candidates: Vec<_> = artifacts
        .iter()
        .filter(|path| {
            artifact_matches(build, path, selected)
                || project.root.join(path).to_str() == Some(selected)
        })
        .collect();
    if candidates.len() != 1 {
        return Err(format!(
            "suite executable {selected:?} must identify exactly one existing declared artifact"
        ));
    }
    resolve(
        project,
        environment,
        "cpp-suite",
        Some(project.root.join(candidates[0])),
        cwd,
    )
}
fn artifact_matches(build: &Build, path: &str, selected: &str) -> bool {
    path == selected
        || path
            .strip_prefix(
                build
                    .directory
                    .as_deref()
                    .filter(|path| *path != ".")
                    .map(|directory| format!("{directory}/"))
                    .as_deref()
                    .unwrap_or(""),
            )
            .is_some_and(|tail| {
                tail == selected
                    || format!("{}/{tail}", build.id) == selected
                    || format!("{}:project:{tail}", build.id) == selected
            })
}
pub fn declared_artifacts(
    project: &ResolvedProject,
    component: &Component,
    build: &Build,
) -> Result<Vec<String>, String> {
    let directory = build.directory.as_deref().unwrap_or(".");
    let base = crate::files::under(&project.root, directory);
    let entries = crate::files::tree(&project.root, &base).map_err(|error| error.to_string())?;
    let candidates = entries
        .iter()
        .filter(|entry| entry.file && entry.contained)
        .map(|entry| entry.relative.clone())
        .collect::<Vec<_>>();
    let view = Component {
        root: directory.into(),
        ..component.clone()
    };
    let paths = ici_config::matching_source_paths(&view, &candidates, &build.artifacts)
        .map_err(|error| error.to_string())?;
    for path in &paths {
        regular_file(&project.root, &project.root.join(path))?;
    }
    Ok(paths)
}
fn resolve(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
    id: &str,
    explicit: Option<PathBuf>,
    cwd: &Path,
) -> Result<ResolvedTool, String> {
    let explicit = explicit.or_else(|| {
        project
            .tools
            .get(id)
            .and_then(|tool| tool.path.as_ref())
            .map(|path| crate::files::under(&project.root, path))
    });
    ToolRequest {
        id: id.into(),
        explicit,
        project_root: cwd.into(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(|error| error.to_string())
}
fn regular_directory(root: &Path, path: &Path) -> Result<(), String> {
    if !path.is_dir()
        || path.canonicalize().map_err(|error| error.to_string())? != path
        || !path.starts_with(root)
    {
        return Err(format!(
            "{} must be a contained existing build directory without symlink traversal",
            path.display()
        ));
    }
    Ok(())
}
fn regular_file(root: &Path, path: &Path) -> Result<(), String> {
    if !std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_file())
        || path.canonicalize().map_err(|error| error.to_string())? != path
        || !path.starts_with(root)
    {
        return Err(format!(
            "{} must be a contained existing regular artifact without symlink traversal",
            path.display()
        ));
    }
    Ok(())
}
fn relative(root: &Path, path: &Path) -> Result<String, String> {
    path.strip_prefix(root)
        .map_err(|error| error.to_string())?
        .to_str()
        .map(str::to_owned)
        .ok_or_else(|| "artifact path is not UTF-8".into())
}
fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn number(value: Option<&Value>, key: &str) -> Option<f64> {
    value
        .and_then(|value| value.get(key))
        .and_then(Value::as_f64)
}
fn policy(cfg: &Value) -> Result<ici_cpp::ElfPolicy, String> {
    let mut value =
        serde_json::to_value(ici_cpp::ElfPolicy::default()).map_err(|error| error.to_string())?;
    let keys: BTreeSet<_> = value
        .as_object()
        .expect("policy map")
        .keys()
        .cloned()
        .collect();
    for key in keys {
        if let Some(selected) = cfg.get(&key) {
            value[&key] = selected.clone();
        }
    }
    serde_json::from_value(value).map_err(|error| error.to_string())
}

fn validate_argv(argv: &[String]) -> Result<(), String> {
    if argv.len() > 256
        || argv
            .iter()
            .any(|token| token.len() > 8192 || token.contains('\0'))
    {
        return Err("suite argv exceeds 256 bounded literal tokens".into());
    }
    Ok(())
}
fn selected(
    project: &ResolvedProject,
    component: &Component,
    request: &RunRequest,
    id: &str,
) -> bool {
    ici_domain::builtin_catalog()
        .checks
        .iter()
        .find(|check| check.id == id)
        .is_some_and(|check| {
            request.wants(check)
                && check
                    .profiles
                    .contains(&request.profile.unwrap_or(project.profile))
                && ici_application::plan::configured_check(project, component, check).0
        })
}
