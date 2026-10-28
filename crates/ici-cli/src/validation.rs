//! Bind project runtime checks without using the core interpreter or running discovery probes.
use ici_application::{
    plan::{Work, configured_check},
    request::RunRequest,
};
use ici_config::{Component, ResolvedProject};
use ici_domain::{CheckDefinition, Profile, builtin_catalog};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
use ici_validation::{
    RuntimeSpec, ValidationKind, ValidationOptions, ValidationPlan, ValidationRequest,
};
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
    if check.id == "python.artifact" {
        return Some(blocked(
            "Python artifact preparation requires the declared project build adapter",
        ));
    }
    if check.id == "integration" {
        return Some(bind_integration(
            project,
            component,
            id,
            environment,
            run_label,
        ));
    }
    let kind = match check.id.as_str() {
        "python.test" => ValidationKind::PythonTest,
        "python.coverage" => ValidationKind::PythonCoverage,
        "python.sanitize" => ValidationKind::PythonSanitize,
        "python.compat-runtime" => ValidationKind::PythonRuntime,
        _ => return None,
    };
    let cwd = if component.root == "." {
        project.root.clone()
    } else {
        project.root.join(&component.root)
    };
    let matrix = if kind == ValidationKind::PythonRuntime {
        match runtime_matrix(project, component, &cwd, environment) {
            Ok(matrix) => matrix,
            Err(reason) => return Some(blocked(reason)),
        }
    } else {
        vec![]
    };
    let tool = if matrix.is_empty() {
        match project_python(project, component, &cwd, environment) {
            Ok(tool) => tool,
            Err(reason) => return Some(blocked(reason)),
        }
    } else if let Some(tool) = matrix.iter().find_map(|runtime| runtime.tool.clone()) {
        tool
    } else {
        return Some(blocked(format!(
            "no configured project runtime is available: {}",
            matrix
                .iter()
                .map(|runtime| format!(
                    "{} ({})",
                    runtime.configured,
                    if runtime.required {
                        "required"
                    } else {
                        "optional"
                    }
                ))
                .collect::<Vec<_>>()
                .join(", ")
        )));
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
    let targets = if kind == ValidationKind::PythonRuntime {
        sources.clone()
    } else {
        match test_targets(project, component, &supporting) {
            Ok(targets) => targets,
            Err(failure) => return Some(blocked(failure)),
        }
    };
    let test = component.settings.pointer("/engines/test");
    let quality = test.and_then(|test| test.get("quality"));
    let quality_enabled = kind == ValidationKind::PythonTest
        && request.profile.unwrap_or(project.profile) == Profile::Deep
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
                .and_then(|value| value.get("repeat").or_else(|| value.get("repeat_runs")))
                .and_then(Value::as_u64)
                .unwrap_or(1) as usize
        } else {
            0
        },
        slow_threshold_seconds: quality_enabled.then(|| {
            quality
                .and_then(|value| {
                    value
                        .get("slow_threshold")
                        .or_else(|| value.get("slow_test_threshold"))
                })
                .and_then(Value::as_f64)
                .unwrap_or(1.0)
        }),
        quality_mode: quality
            .and_then(|value| value.get("mode"))
            .and_then(Value::as_str)
            .unwrap_or("report")
            .into(),
        compile_sources: kind == ValidationKind::PythonRuntime && !sources.is_empty(),
        runtimes: matrix,
        required_interpreters: if kind == ValidationKind::PythonRuntime {
            strings(
                component
                    .settings
                    .pointer("/engines/python_compat/required_interpreters"),
            )
        } else {
            vec![]
        },
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
    options.min_tem_score = threshold("min_tem_score", 4.0);
    options.max_coverage_regression = test
        .and_then(|value| value.get("max_coverage_regression"))
        .and_then(Value::as_f64);
    options.changed_lines = strings(test.and_then(|value| value.get("changed_lines")));
    options.min_changed_line_cov = threshold("min_changed_line_cov", 100.0)
        .or_else(|| (!options.changed_lines.is_empty()).then_some(100.0));
    if quality_enabled {
        let mutation = quality.and_then(|value| value.get("mutation"));
        options.mutation_enabled = mutation
            .and_then(Value::as_bool)
            .or_else(|| {
                mutation
                    .and_then(|value| value.get("enabled"))
                    .and_then(Value::as_bool)
            })
            .unwrap_or(false);
        if options.mutation_enabled {
            let command = strings(mutation.and_then(|value| value.get("command")));
            let selected = mutation
                .and_then(|value| value.get("tool"))
                .and_then(Value::as_str)
                .unwrap_or("auto");
            let (mutation_tool, mutation_argv) =
                mutation_probe(project, &cwd, environment, selected, &command);
            options.mutation_tool = mutation_tool;
            options.mutation_argv = mutation_argv;
        }
    }
    if matches!(
        kind,
        ValidationKind::PythonTest
            | ValidationKind::PythonCoverage
            | ValidationKind::PythonSanitize
    ) {
        // These are project-runtime search paths. They do not expose an ici tool pack to the suite.
        if let Some(directory) = tool.path.parent() {
            match std::env::join_paths(
                std::iter::once(directory.to_path_buf()).chain(
                    environment
                        .get("PATH")
                        .filter(|value| !value.is_empty())
                        .into_iter()
                        .flat_map(std::env::split_paths),
                ),
            ) {
                Ok(paths) => {
                    options
                        .environment
                        .insert("PATH".into(), paths.to_string_lossy().into_owned());
                }
                Err(error) => {
                    return Some(blocked(format!(
                        "project Python executable search path: {error}"
                    )));
                }
            }
        }
        let source_paths = crate::inputs::source_roots(component, &project.root)
            .into_iter()
            .map(|path| project.root.join(path))
            .chain(
                environment
                    .get("PYTHONPATH")
                    .into_iter()
                    .flat_map(std::env::split_paths),
            )
            .collect::<Vec<_>>();
        match std::env::join_paths(source_paths) {
            Ok(paths) => {
                options
                    .environment
                    .insert("PYTHONPATH".into(), paths.to_string_lossy().into_owned());
            }
            Err(error) => return Some(blocked(format!("project Python search path: {error}"))),
        }
    }
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

fn mutation_probe(
    project: &ResolvedProject,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    selected: &str,
    command: &[String],
) -> (Option<ResolvedTool>, Vec<String>) {
    let candidates = if let Some(executable) = command.first() {
        vec![executable.as_str()]
    } else if selected == "auto" {
        vec!["mutmut", "cosmic-ray", "mutpy"]
    } else {
        vec![selected]
    };
    for candidate in candidates {
        let explicit = if candidate.contains('/') {
            Some(if Path::new(candidate).is_absolute() {
                PathBuf::from(candidate)
            } else {
                project.root.join(candidate)
            })
        } else {
            project
                .tools
                .get(candidate)
                .and_then(|tool| tool.path.as_deref())
                .map(|path| {
                    if Path::new(path).is_absolute() {
                        PathBuf::from(path)
                    } else {
                        project.root.join(path)
                    }
                })
        };
        let tool = ToolRequest {
            id: if candidate.contains('/') {
                "mutation".into()
            } else {
                candidate.into()
            },
            explicit,
            project_root: cwd.into(),
            pack_root: None,
            path_env: Some(environment.get("PATH").cloned().unwrap_or_default()),
        }
        .resolve();
        if let Ok(tool) = tool {
            let argv = if command.is_empty() {
                vec![]
            } else {
                std::iter::once(tool.path.to_string_lossy().into_owned())
                    .chain(command[1..].iter().cloned())
                    .collect()
            };
            return (Some(tool), argv);
        }
    }
    (None, vec![])
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
fn configured_python(
    project: &ResolvedProject,
    cwd: &Path,
    configured: &str,
    environment: &BTreeMap<String, String>,
) -> Result<ResolvedTool, String> {
    let path = if configured.contains('/') {
        Some(if Path::new(configured).is_absolute() {
            PathBuf::from(configured)
        } else {
            project.root.join(configured)
        })
    } else {
        project
            .tools
            .get(configured)
            .and_then(|tool| tool.path.as_deref())
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    project.root.join(path)
                }
            })
    };
    let mut tool = ToolRequest {
        id: if configured.contains('/') {
            "python".into()
        } else {
            configured.into()
        },
        explicit: path,
        project_root: cwd.into(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(|failure| format!("project Python {configured:?}: {failure}"))?;
    tool.id = "python".into();
    Ok(tool)
}
fn project_python(
    project: &ResolvedProject,
    component: &Component,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<ResolvedTool, String> {
    configured_python(
        project,
        cwd,
        component.python_executable.as_deref().unwrap_or("python"),
        environment,
    )
}
fn runtime_matrix(
    project: &ResolvedProject,
    component: &Component,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<Vec<RuntimeSpec>, String> {
    let configured = strings(
        component
            .settings
            .pointer("/engines/python_compat/interpreters"),
    );
    let required = strings(
        component
            .settings
            .pointer("/engines/python_compat/required_interpreters"),
    );
    if configured.is_empty() {
        if !required.is_empty() {
            return Err(
                "required_interpreters must select explicitly configured interpreters".into(),
            );
        }
        return Ok(vec![]);
    }
    if configured.len() > 32 {
        return Err("runtime matrix exceeds 32 interpreters".into());
    }
    Ok(configured
        .iter()
        .map(|configured| RuntimeSpec {
            configured: configured.clone(),
            tool: configured_python(project, cwd, configured, environment).ok(),
            required: required.contains(configured),
        })
        .collect())
}
fn integration_owner(project: &ResolvedProject) -> Option<&str> {
    project
        .components
        .iter()
        .find(|component| component.root == ".")
        .or_else(|| project.components.first())
        .map(|component| component.id.as_str())
}
fn bind_integration(
    project: &ResolvedProject,
    component: &Component,
    id: &str,
    environment: &BTreeMap<String, String>,
    run_label: &str,
) -> BoundValidation {
    match integration_plans(project, component, id, environment, run_label) {
        Ok((plans, tool)) => {
            let tasks = plans
                .iter()
                .flat_map(|plan| plan.tasks.iter().cloned())
                .collect::<Vec<_>>();
            if tasks.is_empty() {
                return blocked("no integration cases were declared for this component");
            }
            BoundValidation {
                work: Work::steps(tasks, false),
                plans,
                tool,
            }
        }
        Err(reason) => blocked(reason),
    }
}
fn integration_plans(
    project: &ResolvedProject,
    component: &Component,
    id: &str,
    environment: &BTreeMap<String, String>,
    run_label: &str,
) -> Result<(Vec<ValidationPlan>, Option<ResolvedTool>), String> {
    let settings = component.settings.pointer("/engines/integration");
    let mut cases = component.integrations.clone();
    if integration_owner(project) == Some(&component.id) {
        cases.extend(
            settings
                .and_then(|settings| settings.get("cases"))
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .cloned(),
        );
    }
    let max_cases = settings
        .and_then(|settings| settings.get("max_cases"))
        .and_then(Value::as_u64)
        .unwrap_or(32);
    if max_cases == 0 || max_cases > 32 || cases.len() > max_cases as usize {
        return Err("integration case count exceeds the configured 1..32 maximum".into());
    }
    let max_output = settings
        .and_then(|settings| settings.get("max_output_bytes"))
        .and_then(Value::as_u64)
        .unwrap_or(65536);
    if !(1024..=8 * 1024 * 1024).contains(&max_output) {
        return Err("integration max_output_bytes must be 1024..8388608".into());
    }
    let cwd = if component.root == "." {
        project.root.clone()
    } else {
        project.root.join(&component.root)
    };
    let sources = ici_config::component_source_inventory(project, component)
        .map_err(|failure| failure.to_string())?
        .into_iter()
        .filter(|path| path.ends_with(".py"))
        .collect::<Vec<_>>();
    let mut names = std::collections::BTreeSet::new();
    let mut plans = vec![];
    let mut selected_tool = None;
    for (index, case) in cases.iter().enumerate() {
        let name = case
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "integration case name missing".to_string())?;
        if !names.insert(name) {
            return Err(format!("duplicate integration case {name:?}"));
        }
        let mut targets = integration_python_targets(
            project,
            settings.and_then(|settings| settings.get("python_targets")),
            environment,
        )?;
        targets.extend(integration_python_targets(
            project,
            case.get("python_targets"),
            environment,
        )?);
        let mut references = vec![];
        let mut argv = vec![];
        let mut executable = None;
        for (position, token) in strings(case.get("argv")).iter().enumerate() {
            if let Some(placeholder) = token
                .strip_prefix('{')
                .and_then(|text| text.strip_suffix('}'))
            {
                let (kind, identity) = placeholder
                    .split_once(':')
                    .ok_or_else(|| format!("unknown integration placeholder {token:?}"))?;
                let tool = match kind {
                    "python" => match targets.get(identity) {
                        Some(tool) => tool.clone(),
                        None if identity == "current" => {
                            project_python(project, component, &cwd, environment)?
                        }
                        None => return Err(format!("unknown Python target {identity:?}")),
                    },
                    "artifact" => {
                        let path = integration_artifact(project, component, identity)?;
                        references.push(path.to_string_lossy().into_owned());
                        if position != 0 {
                            argv.push(path.to_string_lossy().into_owned());
                            continue;
                        }
                        ToolRequest{id:"integration-artifact".into(),explicit:Some(path),project_root:cwd.clone(),pack_root:None,path_env:None}.resolve().map_err(|failure|format!("declared integration artifact {identity:?}: {failure}; build the declared artifact before execution"))?
                    }
                    _ => return Err(format!("unknown integration placeholder {token:?}")),
                };
                if position == 0 {
                    executable = Some(tool.clone());
                }
                argv.push(tool.path.to_string_lossy().into_owned());
            } else {
                if token.contains(['{', '}']) {
                    return Err(format!(
                        "integration placeholders must occupy whole tokens: {token:?}"
                    ));
                }
                argv.push(token.clone());
            }
        }
        let tool = executable.ok_or_else(|| {
            format!("integration {name:?} requires a typed Python or artifact executable")
        })?;
        let timeout = case
            .get("timeout_seconds")
            .and_then(Value::as_f64)
            .unwrap_or(30.0);
        if !(0.1..=300.0).contains(&timeout) {
            return Err(format!(
                "integration {name:?} timeout_seconds must be 0.1..300"
            ));
        }
        let outputs = case
            .get("output_artifacts")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|output| {
                let path = output
                    .get("path")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "integration output path missing".to_string())?;
                ici_domain::validation::relative_path(path, false)
                    .map_err(|failure| failure.to_string())?;
                Ok(ici_validation::OutputAssertion {
                    path: path.into(),
                    min_size: output.get("min_size").and_then(Value::as_u64).unwrap_or(1),
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        let overlay = case
            .get("env")
            .and_then(Value::as_object)
            .into_iter()
            .flatten()
            .map(|(name, value)| {
                value
                    .as_str()
                    .map(|value| (name.clone(), value.to_owned()))
                    .ok_or_else(|| format!("integration environment {name:?} is not a string"))
            })
            .collect::<Result<BTreeMap<_, _>, String>>()?;
        let context = ValidationRequest {
            kind: ValidationKind::Integration,
            task_id: format!("{id}.case{index}"),
            component_id: component.id.clone(),
            root: project.root.clone(),
            cwd: cwd.clone(),
            owned_run_dir: project
                .root
                .join(".ici/rust-v1/runs")
                .join(run_label)
                .join(&component.id),
            tool: tool.clone(),
            targets: vec![if component.declared_in.is_empty() {
                "ici.toml".into()
            } else {
                component.declared_in.clone()
            }],
            source_files: sources.clone(),
            functions: vec![],
            options: ValidationOptions {
                argv,
                expected_exit: case
                    .get("expected_exit")
                    .and_then(Value::as_i64)
                    .unwrap_or(0)
                    .try_into()
                    .map_err(|_| "integration expected_exit exceeds a 32-bit value".to_string())?,
                stdout_contains: strings(case.get("stdout_contains")),
                stderr_contains: strings(case.get("stderr_contains")),
                stdout_not_contains: strings(case.get("stdout_not_contains")),
                stderr_not_contains: strings(case.get("stderr_not_contains")),
                outputs,
                requires: strings(case.get("requires")),
                environment: overlay,
                timeout_seconds: timeout,
                output_limit_bytes: Some(max_output),
                integration_case_name: Some(name.into()),
                integration_required: case
                    .get("required")
                    .and_then(Value::as_bool)
                    .unwrap_or(true),
                ..Default::default()
            },
        };
        let mut plan = ici_validation::plan(&context).map_err(|failure| failure.to_string())?;
        for task in &mut plan.tasks {
            task.env_inherit = Some(strings(case.get("inherit_env")));
            task.input_refs.extend(references.clone());
            task.work_dirs
                .push(context.owned_run_dir.to_string_lossy().into_owned());
            task.analysis_unit_ids = vec![format!("{}.integration", component.id)];
            task.input_refs.sort();
            task.input_refs.dedup();
        }
        if selected_tool.is_none() {
            selected_tool = Some(tool);
        }
        plans.push(plan);
    }
    Ok((plans, selected_tool))
}
fn integration_python_targets(
    project: &ResolvedProject,
    value: Option<&Value>,
    environment: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, ResolvedTool>, String> {
    let Some(values) = value.and_then(Value::as_object) else {
        return Ok(BTreeMap::new());
    };
    if values.len() > 32 {
        return Err("integration Python targets exceed 32 declarations".into());
    }
    values
        .iter()
        .map(|(name, value)| {
            let configured = value
                .as_str()
                .filter(|value| !value.is_empty())
                .ok_or_else(|| format!("integration Python target {name:?} must be a path"))?;
            let path = if Path::new(configured).is_absolute() {
                PathBuf::from(configured)
            } else {
                project.root.join(configured)
            };
            let tool = ToolRequest {
                id: "python".into(),
                explicit: Some(path),
                project_root: project.root.clone(),
                pack_root: None,
                path_env: environment.get("PATH").cloned(),
            }
            .resolve()
            .map_err(|failure| format!("integration Python target {name:?}: {failure}"))?;
            Ok((name.clone(), tool))
        })
        .collect()
}
fn integration_artifact(
    project: &ResolvedProject,
    component: &Component,
    identity: &str,
) -> Result<PathBuf, String> {
    let (build_id, path) = identity
        .split_once('/')
        .ok_or_else(|| format!("artifact placeholder must name BUILD/PATH: {identity:?}"))?;
    let build = project
        .builds
        .iter()
        .find(|build| build.id == build_id)
        .ok_or_else(|| format!("artifact references undeclared build {build_id:?}"))?;
    ici_domain::validation::relative_path(path, false).map_err(|failure| failure.to_string())?;
    let directory = build.directory.as_deref().unwrap_or(".");
    let relative = if directory == "." {
        path.to_owned()
    } else {
        format!("{directory}/{path}")
    };
    let view = Component {
        root: directory.into(),
        ..component.clone()
    };
    let matched =
        ici_config::matching_source_paths(&view, std::slice::from_ref(&relative), &build.artifacts)
            .map_err(|failure| failure.to_string())?;
    if matched.is_empty() {
        return Err(format!(
            "integration artifact {identity:?} is not declared by build {build_id:?}"
        ));
    }
    let absolute = project.root.join(relative);
    let canonical=absolute.canonicalize().map_err(|failure|format!("declared integration artifact {identity:?} is unavailable: {failure}; prepare the declared build first"))?;
    if canonical != absolute
        || !canonical.starts_with(&project.root)
        || !std::fs::symlink_metadata(&absolute).is_ok_and(|metadata| metadata.is_file())
    {
        return Err(format!(
            "declared integration artifact {identity:?} must be a contained regular file without symlink traversal"
        ));
    }
    Ok(absolute)
}

/// Additional historical language scopes keep the frozen next catalog's 36 entries intact.
/// Modern projects opt in through an explicit check or legacy engine setting.
pub fn supplemental_checks(project: &ResolvedProject) -> Vec<CheckDefinition> {
    [
        (
            "python.sanitize",
            "sanitize",
            "Python ResourceWarning checks",
            vec![Profile::Standard, Profile::Deep],
            "python-resource-evidence",
        ),
        (
            "python.artifact",
            "build",
            "Declared Python artifacts",
            vec![Profile::Deep],
            "python-artifact-contract",
        ),
    ]
    .into_iter()
    .filter(|(id, engine, _, _, _)| {
        project.components.iter().any(|component| {
            component
                .languages
                .iter()
                .any(|language| language == "python")
                && (project.schema_family == ici_config::SchemaFamily::Legacy
                    || component.checks.contains_key(*id)
                    || project.checks.contains_key(*id)
                    || project.origins.iter().any(|(key, origin)| {
                        origin.layer != "defaults"
                            && (key == &format!("engines.{engine}")
                                || key.starts_with(&format!("engines.{engine}.")))
                    }))
        })
    })
    .map(|(id, _, title, profiles, provides)| CheckDefinition {
        id: id.into(),
        title: title.into(),
        language: "python".into(),
        tool: None,
        required: true,
        profiles,
        needs: vec![],
        provides: vec![provides.into()],
        is_static: false,
    })
    .collect()
}
