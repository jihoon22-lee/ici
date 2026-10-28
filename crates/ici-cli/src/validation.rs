//! Bind project runtime checks without using the core interpreter or running discovery probes.
use ici_application::{
    plan::{Work, configured_check},
    request::RunRequest,
};
use ici_config::{Component, ResolvedProject};
use ici_domain::{CheckDefinition, Profile, builtin_catalog};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
use ici_validation::{ValidationKind, ValidationOptions, ValidationPlan, ValidationRequest};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

pub struct BoundValidation {
    pub work: Work,
    pub plans: Vec<ValidationPlan>,
    pub tool: Option<ResolvedTool>,
}
fn blocked(reason: impl Into<String>) -> BoundValidation {
    BoundValidation {
        work: Work::Blocked {
            reason: reason.into(),
        },
        plans: vec![],
        tool: None,
    }
}
fn selected(
    project: &ResolvedProject,
    component: &Component,
    request: &RunRequest,
    id: &str,
) -> bool {
    builtin_catalog()
        .checks
        .iter()
        .find(|check| check.id == id)
        .is_some_and(|check| {
            request.wants(check)
                && check
                    .profiles
                    .contains(&request.profile.unwrap_or(project.profile))
                && configured_check(project, component, check).0
        })
}
pub fn bind(
    project: &ResolvedProject,
    component: &Component,
    check: &CheckDefinition,
    id: &str,
    request: &RunRequest,
    environment: &BTreeMap<String, String>,
    run_label: &str,
) -> Option<BoundValidation> {
    let kind = match check.id.as_str() {
        "python.test" => ValidationKind::PythonTest,
        "python.coverage" => ValidationKind::PythonCoverage,
        "python.compat-runtime" => ValidationKind::PythonRuntime,
        _ => return None,
    };
    let cwd = if component.root == "." {
        project.root.clone()
    } else {
        project.root.join(&component.root)
    };
    let (tool_id, explicit) = match component.python_executable.as_deref() {
        Some(name) if !name.contains('/') => (name.to_owned(), None),
        Some(path) => (
            "python".into(),
            Some(if Path::new(path).is_absolute() {
                PathBuf::from(path)
            } else {
                project.root.join(path)
            }),
        ),
        None => (
            "python".into(),
            project
                .tools
                .get("python")
                .and_then(|tool| tool.path.as_ref())
                .map(|path| {
                    if Path::new(path).is_absolute() {
                        PathBuf::from(path)
                    } else {
                        project.root.join(path)
                    }
                }),
        ),
    };
    let tool = match (ToolRequest {
        id: tool_id,
        explicit,
        project_root: cwd.clone(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    })
    .resolve()
    {
        Ok(mut tool) => {
            tool.id = "python".into();
            tool
        }
        Err(failure) => {
            return Some(blocked(format!(
                "project Python: {failure}; set components.{}.python.executable",
                component.id
            )));
        }
    };
    let sources: Vec<_> = component
        .files
        .iter()
        .filter(|file| file.ends_with(".py"))
        .cloned()
        .collect();
    let supporting = match ici_config::component_source_inventory(project, component) {
        Ok(files) => files
            .into_iter()
            .filter(|path| path.ends_with(".py"))
            .collect::<Vec<_>>(),
        Err(failure) => return Some(blocked(failure.to_string())),
    };
    let targets = match test_targets(project, component, &supporting) {
        Ok(targets) => targets,
        Err(failure) => return Some(blocked(failure)),
    };
    let test = component.settings.pointer("/engines/test");
    let quality = test.and_then(|test| test.get("quality"));
    let quality_enabled = request.profile.unwrap_or(project.profile) == Profile::Deep
        && quality
            .and_then(|value| value.get("enabled"))
            .and_then(Value::as_bool)
            .unwrap_or(true);
    let mut options = ValidationOptions {
        collect_coverage: kind == ValidationKind::PythonTest
            && selected(project, component, request, "python.coverage"),
        shared_test_task_id: (kind == ValidationKind::PythonCoverage
            && selected(project, component, request, "python.test"))
        .then(|| format!("{}.python.test.suite", component.id)),
        repeat_runs: if quality_enabled {
            quality
                .and_then(|value| value.get("repeat_runs"))
                .and_then(Value::as_u64)
                .unwrap_or(1) as usize
        } else {
            0
        },
        slow_threshold_seconds: quality_enabled.then(|| {
            quality
                .and_then(|value| value.get("slow_test_threshold"))
                .and_then(Value::as_f64)
                .unwrap_or(1.0)
        }),
        quality_mode: quality
            .and_then(|value| value.get("mode"))
            .and_then(Value::as_str)
            .unwrap_or("report")
            .into(),
        compile_sources: kind == ValidationKind::PythonRuntime,
        ..Default::default()
    };
    options.quality_timeout_seconds = quality
        .and_then(|value| value.get("timeout"))
        .and_then(Value::as_f64)
        .unwrap_or(300.0);
    options.max_slow_tests = quality
        .and_then(|value| value.get("max_slow_tests"))
        .and_then(Value::as_u64)
        .unwrap_or(50) as usize;
    let legacy = project.schema_family == ici_config::SchemaFamily::Legacy;
    let threshold = |key: &str, default: f64| {
        test.and_then(|value| value.get(key))
            .and_then(Value::as_f64)
            .filter(|_| {
                legacy
                    || project
                        .origins
                        .get(&format!("engines.test.{key}"))
                        .is_some_and(|origin| origin.layer != "defaults")
            })
            .or_else(|| legacy.then_some(default))
    };
    options.min_line_cov = threshold("min_line_cov", 80.0);
    options.min_file_cov = threshold("min_file_cov", 80.0);
    options.min_branch_cov = threshold("min_branch_cov", 80.0);
    options.min_func_cov = threshold("min_func_cov", 90.0);
    options.min_file_statements = test
        .and_then(|value| value.get("min_file_statements"))
        .and_then(Value::as_u64)
        .unwrap_or(5);
    options.coverage_required = test
        .and_then(|value| value.get("coverage_required"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if let Some(requirement) = component
        .settings
        .pointer("/python/requires_python")
        .and_then(Value::as_str)
    {
        options.requires_python = Some(requirement.into());
    }
    let owned_run_dir = project
        .root
        .join(".ici/rust-v1/runs")
        .join(run_label)
        .join(&component.id);
    let context = ValidationRequest {
        kind,
        task_id: id.into(),
        component_id: component.id.clone(),
        root: project.root.clone(),
        cwd,
        owned_run_dir: owned_run_dir.clone(),
        tool: tool.clone(),
        targets,
        source_files: sources,
        functions: vec![],
        options,
    };
    let mut plans = match ici_validation::plan(&context) {
        Ok(plan) => vec![plan],
        Err(failure) => return Some(blocked(failure.to_string())),
    };
    if kind == ValidationKind::PythonRuntime {
        let modules: Vec<_> = component
            .settings
            .pointer("/engines/python_compat/imports")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        if !modules.is_empty() {
            let mut imports = context;
            imports.task_id = format!("{id}.imports");
            imports.kind = ValidationKind::PythonImports;
            imports.options.modules = modules;
            imports.options.compile_sources = false;
            match ici_validation::plan(&imports) {
                Ok(plan) => plans.push(plan),
                Err(failure) => return Some(blocked(failure.to_string())),
            }
        }
    }
    let mut tasks: Vec<_> = plans
        .iter()
        .flat_map(|plan| plan.tasks.iter().cloned())
        .collect();
    for task in &mut tasks {
        task.work_dirs
            .push(owned_run_dir.to_string_lossy().into_owned());
        task.input_refs.extend(
            supporting
                .iter()
                .map(|path| project.root.join(path).to_string_lossy().into_owned()),
        );
        task.input_refs.sort();
        task.input_refs.dedup();
        task.analysis_unit_ids = vec![format!("{}.python", component.id)];
    }
    // Preserve the same exact task records in observers and displayed execution work.
    for plan in &mut plans {
        for task in &mut plan.tasks {
            task.work_dirs
                .push(owned_run_dir.to_string_lossy().into_owned());
            task.input_refs.extend(
                supporting
                    .iter()
                    .map(|path| project.root.join(path).to_string_lossy().into_owned()),
            );
            task.input_refs.sort();
            task.input_refs.dedup();
            task.analysis_unit_ids = vec![format!("{}.python", component.id)];
        }
    }
    Some(BoundValidation {
        work: Work::steps(tasks, false),
        plans,
        tool: Some(tool),
    })
}
fn test_targets(
    project: &ResolvedProject,
    component: &Component,
    files: &[String],
) -> std::result::Result<Vec<String>, String> {
    let under_component = |path: &str| {
        if component.root == "." {
            path.to_owned()
        } else if path == "." {
            component.root.clone()
        } else {
            format!("{}/{path}", component.root)
        }
    };
    if component.test_paths.is_empty() {
        let tests = under_component("tests");
        return Ok(vec![if project.root.join(&tests).is_dir() {
            tests
        } else {
            component.root.clone()
        }]);
    }
    let mut candidates = std::collections::BTreeSet::new();
    for file in files {
        candidates.insert(file.clone());
        let mut parent = Path::new(file).parent();
        while let Some(directory) = parent {
            if directory.as_os_str().is_empty() {
                break;
            }
            candidates.insert(directory.to_string_lossy().into_owned());
            parent = directory.parent();
        }
    }
    let mut selected = std::collections::BTreeSet::new();
    for pattern in &component.test_paths {
        let pattern = pattern.strip_prefix("./").unwrap_or(pattern);
        let path = under_component(pattern);
        let absolute = project.root.join(&path);
        if absolute.exists() {
            if path != "."
                && absolute.canonicalize().map_err(|error| error.to_string())? != absolute
            {
                return Err(format!("test target {path} traverses a symlink"));
            }
            selected.insert(path);
        } else {
            let matches = ici_config::matching_source_paths(
                component,
                &candidates.iter().cloned().collect::<Vec<_>>(),
                &[pattern.into()],
            )
            .map_err(|error| error.to_string())?;
            if matches.is_empty() {
                return Err(format!("test target {pattern} matched no existing input"));
            }
            selected.extend(matches);
        }
    }
    let targets: Vec<_> = selected
        .iter()
        .filter(|path| {
            !selected.iter().any(|parent| {
                *parent != **path
                    && path
                        .strip_prefix(parent.as_str())
                        .is_some_and(|tail| tail.starts_with('/'))
            })
        })
        .cloned()
        .collect();
    Ok(targets)
}
