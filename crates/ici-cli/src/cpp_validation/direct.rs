//! Measured descriptor-free test binaries; one process is one source-located suite.
use super::*;
pub(super) struct DirectBinding<'a> {
    pub project: &'a ResolvedProject,
    pub component: &'a Component,
    pub build: &'a Build,
    pub kind: ValidationKind,
    pub id: &'a str,
    pub run_request: &'a RunRequest,
    pub environment: &'a BTreeMap<String, String>,
    pub run_label: &'a str,
}
pub(super) fn bind(binding: DirectBinding<'_>) -> Result<BoundCppValidation, String> {
    let DirectBinding {
        project,
        component,
        build,
        kind,
        id,
        run_request,
        environment,
        run_label,
    } = binding;
    let suites = build
        .settings
        .get("direct_test_executables")
        .and_then(Value::as_object)
        .filter(|suites| !suites.is_empty() && suites.len() <= 64)
        .ok_or("the direct build has no measured bounded source-to-executable test inventory")?;
    let mut sources = build
        .settings
        .get("direct_compiled_sources")
        .and_then(Value::as_array)
        .ok_or("direct compiled source inventory is absent")?
        .iter()
        .map(|source| {
            source
                .as_str()
                .map(str::to_owned)
                .ok_or("direct source path is not text")
        })
        .collect::<Result<Vec<_>, _>>()?;
    sources.sort();
    sources.dedup();
    let mut tools = BTreeMap::new();
    let mut plans = vec![];
    let mut physical = BTreeMap::new();
    let shared =
        kind == ValidationKind::Gcov && selected(project, component, run_request, "cpp.test");
    let suite_prefix = if shared {
        format!("{}.cpp.test", component.id)
    } else if kind == ValidationKind::Gcov {
        format!("{id}.tests")
    } else {
        id.into()
    };
    for (index, (source, path)) in suites.iter().enumerate() {
        ici_domain::validation::relative_path(source, false).map_err(|error| error.to_string())?;
        let path = path
            .as_str()
            .ok_or("direct test executable path is not text")?;
        ici_domain::validation::relative_path(path, false).map_err(|error| error.to_string())?;
        let directory = project.root.join(
            build
                .directory
                .as_deref()
                .ok_or("direct test build has no owned directory")?,
        );
        let binary = project.root.join(path);
        if !binary.starts_with(&directory)
            || crate::export_context::compiled_artifact_kind(&binary)? != Some("executable")
        {
            return Err(
                "direct test inventory does not select a measured executable in its declared build"
                    .into(),
            );
        }
        regular_file(&project.root, &binary)?;
        regular_file(&project.root, &project.root.join(source))?;
        let executable = resolve(
            project,
            environment,
            "cpp-executable",
            Some(binary),
            &project.root,
        )?;
        let options = ValidationOptions {
            executable_test_source: Some(source.clone()),
            build_dir: Some(directory),
            build_roots: vec![project.root.clone()],
            output_limit_bytes: Some(1024 * 1024),
            ..Default::default()
        };
        let mut request = ValidationRequest {
            kind: if matches!(kind, ValidationKind::Ctest | ValidationKind::Gcov) {
                ValidationKind::CppExecutable
            } else {
                kind
            },
            task_id: format!("{suite_prefix}.case{index}"),
            component_id: component.id.clone(),
            root: project.root.clone(),
            cwd: project.root.clone(),
            owned_run_dir: project
                .root
                .join(".ici/rust-v1/runs")
                .join(run_label)
                .join(&component.id)
                .join("cpp-direct"),
            tool: executable.clone(),
            targets: vec![source.clone()],
            source_files: sources.clone(),
            functions: vec![],
            options,
        };
        if matches!(
            kind,
            ValidationKind::Sanitizer | ValidationKind::ThreadSanitizer
        ) {
            request.options.sanitizer_suite_kind = Some(ValidationKind::CppExecutable);
            request.options.instrumented_artifacts = vec![path.into()];
            let readelf = resolve(project, environment, "readelf", None, &project.root)?;
            request
                .options
                .auxiliary_tools
                .insert("readelf".into(), readelf.clone());
            tools.insert(format!("{}:readelf", component.id), readelf);
        }
        physical.insert(format!("{}.suite", request.task_id), source.clone());
        tools.insert(format!("{}:direct:{index}", component.id), executable);
        if !shared {
            plans.push(make_plan(request, component)?);
        }
    }
    if kind == ValidationKind::Gcov {
        let directory = project.root.join(
            build
                .directory
                .as_deref()
                .ok_or("direct coverage build directory absent")?,
        );
        let targets = crate::files::tree(&project.root, &directory)
            .map_err(|error| error.to_string())?
            .into_iter()
            .filter(|entry| entry.file && entry.contained && entry.relative.ends_with(".gcno"))
            .map(|entry| entry.relative)
            .collect::<Vec<_>>();
        if targets.is_empty() {
            return Err(
                "direct coverage has no measured notes; prepare the coverage variant first".into(),
            );
        }
        for target in &targets {
            regular_file(&project.root, &project.root.join(target))?;
        }
        let tool = resolve(project, environment, "gcov", None, &project.root)?;
        tools.insert(format!("{}:gcov", component.id), tool.clone());
        let cfg = component.settings.pointer("/engines/test");
        let options = ValidationOptions {
            shared_test_task_id: physical.keys().next().cloned(),
            shared_test_kind: Some(ValidationKind::CppExecutable),
            gcov_capability_probe: true,
            shared_cpp_test_sources: physical,
            build_dir: Some(directory),
            build_roots: vec![project.root.clone()],
            min_line_cov: number(cfg, "min_line_cov"),
            min_file_cov: number(cfg, "min_file_cov"),
            min_branch_cov: number(cfg, "min_branch_cov"),
            min_func_cov: number(cfg, "min_func_cov"),
            min_tem_score: number(cfg, "min_tem_score"),
            min_changed_line_cov: number(cfg, "min_changed_line_cov"),
            max_coverage_regression: number(cfg, "max_coverage_regression"),
            changed_lines: strings(cfg.and_then(|cfg| cfg.get("changed_lines"))),
            min_file_statements: cfg
                .and_then(|cfg| cfg.get("min_file_statements"))
                .and_then(Value::as_u64)
                .unwrap_or(5),
            ..Default::default()
        };
        let coverage_sources = sources
            .into_iter()
            .filter(|source| !suites.contains_key(source))
            .collect::<Vec<_>>();
        if coverage_sources.is_empty() {
            return Err("direct GCOV coverage has no non-main production sources".into());
        }
        plans.push(make_plan(
            ValidationRequest {
                kind,
                task_id: id.into(),
                component_id: component.id.clone(),
                root: project.root.clone(),
                cwd: project.root.clone(),
                owned_run_dir: project
                    .root
                    .join(".ici/rust-v1/runs")
                    .join(run_label)
                    .join(&component.id)
                    .join("cpp-direct"),
                tool,
                targets,
                source_files: coverage_sources,
                functions: vec![],
                options,
            },
            component,
        )?);
    }
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
