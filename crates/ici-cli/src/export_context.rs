//! Explicit native preparation and minimal compilation exports. Planning performs no processes.
use ici_config::{Build, Component, ResolvedProject};
use ici_cpp::{BuildLease, BuildPlan, BuildRequest, CompilationContext, ContextRequest};
use ici_domain::sha256;
use ici_execution::{Cancellation, Scheduler};
use ici_providers::resolver::{ResolvedTool, ToolRequest};
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
}
#[derive(Clone)]
pub struct PreparedContext {
    pub plan: ContextPreparationPlan,
    pub context: CompilationContext,
    /// Workspace-relative measured ELF outputs of an otherwise undeclared legacy build.
    pub artifacts: Vec<String>,
    pub test_executable: Option<String>,
    _lease: Arc<BuildLease>,
}

pub fn plan_preparation(
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
            select_descriptor(&project.root, &directory, Some(&system), component)?
        };
        (system, descriptor)
    } else {
        let descriptor = select_descriptor(&project.root, &directory, None, component)?;
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
        descriptor = select_descriptor(&project.root, &descriptor_path, Some(&system), component)?;
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
        let tool = resolve(project, environment, name, None, &directory)?;
        tools.insert(name.into(), tool);
    }
    let make = component.settings.pointer("/build/make");
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
    let mut request = BuildRequest {
        id: build_id.clone(),
        system: system.clone(),
        project: descriptor.clone(),
        variant,
        jobs: 2,
        ici_binary: Some(ici_binary.into()),
        environment: BTreeMap::new(),
        ..Default::default()
    };
    if system == "make" {
        request.project = make
            .and_then(|value| value.get("workdir"))
            .and_then(Value::as_str)
            .unwrap_or(&component.root)
            .into();
        request.configure_argv = strings(make.and_then(|value| value.get("configure_argv")));
        let field = match request.variant.as_str() {
            "coverage" => "coverage_build_argv",
            "sanitize" => "sanitize_build_argv",
            "thread-sanitize" => "thread_sanitize_build_argv",
            _ => "build_argv",
        };
        request.build_argv = strings(make.and_then(|value| value.get(field)));
        if request.build_argv.is_empty() && field != "build_argv" {
            return Err(format!(
                "build.make.{field} is required for this instrumented variant"
            ));
        }
        for argv in [&mut request.configure_argv, &mut request.build_argv] {
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
                )?;
                *first = tool.path.to_string_lossy().into_owned();
                tools.insert(format!("make-command:{}", tool.id), tool);
            }
        }
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
    let mut build =
        ici_cpp::plan_build(&project.root, &request).map_err(|error| error.to_string())?;
    let inputs =
        crate::files::build_inputs(&project.root, &directory).map_err(|error| error.to_string())?;
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
    let effects = json!({"owned_roots":build.generated_roots,"reset":{"glob":format!("{}/**/*.gcda",build.shadow),"policy":"single-link regular files without symlink traversal under the held BuildLease"},"native_tasks":build.tasks.iter().map(|task|json!({"id":task.id,"argv":task.argv,"cwd":task.cwd,"work_dirs":task.work_dirs})).collect::<Vec<_>>()});
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
    })
}

pub fn execute_preparation(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
) -> Result<PreparedContext, String> {
    let lease = BuildLease::acquire(
        &plan.root,
        &plan.build,
        cancellation,
        Duration::from_secs(120),
    )
    .map_err(|error| error.to_string())?;
    let shadow = lease
        .prepare(&plan.build)
        .map_err(|error| error.to_string())?;
    // Only this verified leased shadow is eligible for resetting accumulated runtime counters.
    for entry in crate::files::tree(&plan.root, &shadow).map_err(|error| error.to_string())? {
        if entry.relative.ends_with(".gcda") {
            let path = plan.root.join(&entry.relative);
            let metadata = fs::symlink_metadata(&path).map_err(|error| error.to_string())?;
            if !entry.contained
                || !metadata.is_file()
                || metadata.nlink() != 1
                || path.canonicalize().map_err(|error| error.to_string())? != path
            {
                return Err(
                    "owned coverage reset refuses redirected or multiply linked gcda files".into(),
                );
            }
            fs::remove_file(path).map_err(|error| error.to_string())?;
        }
    }
    let outcomes = scheduler
        .run_with_start(&plan.build.tasks, cancellation, |task| {
            events.emit(
                "task.started",
                Some(&task.id),
                Some(&plan.component_id),
                "explicit native preparation",
            )
        })
        .map_err(|error| error.to_string())?;
    for outcome in outcomes.outcomes {
        events.emit(
            "task.completed",
            Some(&outcome.task_id),
            Some(&plan.component_id),
            &format!("{:?}", outcome.state),
        );
        if !outcome.prerequisite_succeeded() {
            return Err(format!(
                "native preparation {} failed (exit {:?}, timed_out={}, truncated={}): {} {}",
                outcome.task_id,
                outcome.exit_code,
                outcome.timed_out,
                outcome.truncated,
                outcome.error.unwrap_or_default(),
                String::from_utf8_lossy(&outcome.stderr)
                    .chars()
                    .take(2048)
                    .collect::<String>()
            ));
        }
    }
    if plan.build.system == "qmake" {
        lease
            .finalize_qmake(&plan.build)
            .map_err(|error| error.to_string())?;
    }
    let context = ici_cpp::load_compilation_context(
        &plan.root,
        &ContextRequest {
            database: Some(plan.build.compilation_database.clone()),
            variant: Some(plan.build.variant.clone()),
            selected_sources: plan.selected_sources.clone(),
            ..Default::default()
        },
    );
    if plan.build.system != "make"
        && (context.database_path.is_none()
            || context.units.is_empty()
            || context
                .diagnostics
                .iter()
                .chain(context.units.iter().flat_map(|unit| &unit.diagnostics))
                .any(|diagnostic| diagnostic.level == "error"))
    {
        return Err(format!(
            "prepared compilation evidence is incomplete: {:?}",
            context.diagnostics
        ));
    }
    let mut artifacts = vec![];
    let mut executables = vec![];
    for entry in crate::files::tree(&plan.root, &shadow).map_err(|error| error.to_string())? {
        if build_metadata_path(&entry.relative, &plan.build.shadow) {
            continue;
        }
        if !entry.file || !entry.contained {
            continue;
        }
        let path = plan.root.join(&entry.relative);
        let metadata = fs::symlink_metadata(&path).map_err(|error| error.to_string())?;
        if !metadata.is_file()
            || metadata.nlink() != 1
            || path.canonicalize().map_err(|error| error.to_string())? != path
        {
            continue;
        }
        let mut bytes = [0u8; 20];
        let mut file = fs::File::open(&path).map_err(|error| error.to_string())?;
        if file.read_exact(&mut bytes).is_ok() && &bytes[..4] == b"\x7fELF" {
            let kind = match bytes[5] {
                1 => u16::from_le_bytes([bytes[16], bytes[17]]),
                2 => u16::from_be_bytes([bytes[16], bytes[17]]),
                _ => continue,
            };
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
        .map_err(|error| error.to_string())?;
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
        test_executable,
        _lease: Arc::new(lease),
    })
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
    alias.context = ici_cpp::load_compilation_context(
        &plan.root,
        &ContextRequest {
            database: Some(plan.build.compilation_database.clone()),
            variant: Some(plan.build.variant.clone()),
            selected_sources: plan.selected_sources.clone(),
            ..Default::default()
        },
    );
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
        }
        if let Some(executable) = &context.test_executable {
            let relative = executable
                .strip_prefix(&format!("{}/", plan.build.shadow))
                .unwrap_or(executable);
            build.settings["test_argv"] = json!([relative]);
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
            component.settings["native_build_variants"][&plan.build.variant] = json!({"build_id":id,"directory":plan.build.shadow,"artifacts":context.artifacts,"test_executable":context.test_executable,"database":plan.build.compilation_database});
            if primary || component.build.is_none() {
                component.build = Some(id.clone());
                component.settings["project"]["compile_database"] =
                    json!(plan.root.join(&plan.build.compilation_database));
            }
        }
    }
    overlay
}

fn select_descriptor(
    root: &Path,
    directory: &Path,
    system: Option<&str>,
    component: &Component,
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
            .filter(|path| {
                path.extension().is_some_and(|extension| extension == "pro") && regular(path)
            })
            .collect::<Vec<_>>();
        projects.sort();
        if let Some(path) = projects.first() {
            return relative(root, path);
        }
    }
    if system.is_none_or(|system| system == "make")
        && component
            .settings
            .pointer("/build/make/enabled")
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
fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
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

/// Measured primary-build inputs for the shared release packager. Aliases copy canonical bytes.
pub fn compiled_artifacts(
    project: &ResolvedProject,
    component: &Component,
    contexts: &[PreparedContext],
) -> Result<Vec<crate::artifact_build::ArtifactSource>, String> {
    let preferred = crate::cpp::linked_build(project, component)
        .map(|build| build.variant.as_str())
        .unwrap_or("release");
    let candidates = contexts
        .iter()
        .filter(|context| {
            context.plan.component_id == component.id && context.plan.build.variant == preferred
        })
        .collect::<Vec<_>>();
    let context = candidates
        .first()
        .ok_or("the primary compiled build context was not prepared")?;
    if candidates.iter().any(|candidate| {
        candidate.plan.root != context.plan.root
            || candidate.plan.build.owner_digest != context.plan.build.owner_digest
    }) {
        return Err("primary compiled build context is ambiguous".into());
    }
    if context.plan.root != project.root {
        return Err("compiled producer belongs to another workspace".into());
    }
    let shadow = project.root.join(&context.plan.build.shadow);
    let entries = crate::files::tree(&project.root, &shadow).map_err(|error| error.to_string())?;
    let explicit = context
        .plan
        .declared_build
        .as_ref()
        .filter(|build| !build.artifacts.is_empty());
    let allowed = if let Some(build) = explicit {
        let view = Component {
            root: context.plan.build.shadow.clone(),
            ..component.clone()
        };
        Some(
            ici_config::matching_source_paths(
                &view,
                &entries
                    .iter()
                    .map(|entry| entry.relative.clone())
                    .collect::<Vec<_>>(),
                &build.artifacts,
            )
            .map_err(|error| error.to_string())?
            .into_iter()
            .collect::<std::collections::BTreeSet<_>>(),
        )
    } else {
        None
    };
    let mut records = BTreeMap::new();
    let mut total = 0u64;
    let mut alias_sources = std::collections::BTreeSet::new();
    for entry in &entries {
        let path = project.root.join(&entry.relative);
        if entry.contained
            && allowed
                .as_ref()
                .is_none_or(|allowed| allowed.contains(&entry.relative))
            && fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_symlink())
        {
            let canonical = path.canonicalize().map_err(|error| error.to_string())?;
            if canonical.starts_with(&shadow) {
                alias_sources.insert(relative(&project.root, &canonical)?);
            }
        }
    }
    for entry in &entries {
        if !entry.file
            || !entry.contained
            || allowed.as_ref().is_some_and(|allowed| {
                !allowed.contains(&entry.relative) && !alias_sources.contains(&entry.relative)
            })
            || allowed.is_none() && build_metadata_path(&entry.relative, &context.plan.build.shadow)
        {
            continue;
        }
        let path = project.root.join(&entry.relative);
        if fs::symlink_metadata(&path)
            .map_err(|error| error.to_string())?
            .is_symlink()
        {
            continue;
        }
        let Some((bytes, metadata)) = artifact_bytes(&path)? else {
            continue;
        };
        let Some(kind) = artifact_bytes_kind(&bytes)? else {
            continue;
        };
        total = total
            .checked_add(metadata.len())
            .ok_or("compiled artifact size overflow")?;
        if total > 512 * 1024 * 1024 || records.len() >= 4096 {
            return Err("compiled artifacts exceed 4096 files or 512 MiB aggregate".into());
        }
        records.insert(
            entry.relative.clone(),
            crate::artifact_build::ArtifactSource {
                path: entry.relative.clone(),
                kind: kind.into(),
                sha256: sha256(&bytes),
                size: metadata.len(),
                mode: metadata.permissions().mode() & 0o777,
                destination_name: None,
            },
        );
    }
    let mut aliases = vec![];
    for entry in entries {
        let path = project.root.join(&entry.relative);
        if !entry.contained
            || allowed
                .as_ref()
                .is_some_and(|allowed| !allowed.contains(&entry.relative))
            || allowed.is_none() && build_metadata_path(&entry.relative, &context.plan.build.shadow)
            || !fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_symlink())
        {
            continue;
        }
        let target = path.canonicalize().map_err(|error| error.to_string())?;
        if !target.starts_with(&shadow) {
            continue;
        }
        let source = relative(&project.root, &target)?;
        if let Some(record) = records.get(&source) {
            let mut alias = record.clone();
            alias.destination_name = Some(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .ok_or("compiled alias name is not UTF-8")?
                    .into(),
            );
            aliases.push(alias);
        }
    }
    let mut outputs = records
        .into_iter()
        .filter(|(path, _)| {
            allowed
                .as_ref()
                .is_none_or(|allowed| allowed.contains(path))
        })
        .map(|(_, record)| record)
        .collect::<Vec<_>>();
    outputs.extend(aliases);
    if outputs.len() > 4096 {
        return Err("compiled artifacts and aliases exceed 4096 records".into());
    }
    Ok(outputs)
}

/// Classify linked ELF programs/libraries and bounded native archives from their actual bytes.
pub fn compiled_artifact_kind(path: &Path) -> Result<Option<&'static str>, String> {
    match artifact_bytes(path)? {
        Some((bytes, _)) => artifact_bytes_kind(&bytes),
        None => Ok(None),
    }
}
fn artifact_bytes(path: &Path) -> Result<Option<(Vec<u8>, fs::Metadata)>, String> {
    let before = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !before.is_file() || path.canonicalize().map_err(|error| error.to_string())? != path {
        return Err("compiled artifact must be a regular file without symlink traversal".into());
    }
    let mut header = [0u8; 20];
    let length = usize::try_from(before.len().min(20)).map_err(|error| error.to_string())?;
    let mut file = fs::File::open(path).map_err(|error| error.to_string())?;
    file.read_exact(&mut header[..length])
        .map_err(|error| error.to_string())?;
    if !header[..length].starts_with(b"\x7fELF") && !header[..length].starts_with(b"!<arch>\n") {
        if header[..length].starts_with(b"!<thin>\n") {
            return Err("thin archives reference external members and cannot be published as standalone libraries".into());
        }
        return Ok(None);
    }
    if header[..length].starts_with(b"\x7fELF") && length >= 18 {
        let kind = match header[5] {
            1 => u16::from_le_bytes([header[16], header[17]]),
            2 => u16::from_be_bytes([header[16], header[17]]),
            _ => return Err("ELF data encoding is unsupported".into()),
        };
        if !matches!(kind, 2 | 3) {
            return Ok(None);
        }
    }
    if before.nlink() != 1 {
        return Err("compiled artifacts must not be multiply linked".into());
    }
    let bytes = ici_execution::storage::read_bounded_regular(path, 256 * 1024 * 1024)
        .map_err(|error| error.to_string())?;
    let after = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    let signature = |metadata: &fs::Metadata| {
        (
            metadata.dev(),
            metadata.ino(),
            metadata.len(),
            metadata.mode(),
            metadata.nlink(),
            metadata.mtime(),
            metadata.mtime_nsec(),
            metadata.ctime(),
            metadata.ctime_nsec(),
        )
    };
    if signature(&before) != signature(&after)
        || path.canonicalize().map_err(|error| error.to_string())? != path
    {
        return Err("compiled artifact changed while capturing producer evidence".into());
    }
    Ok(Some((bytes, after)))
}
fn artifact_bytes_kind(bytes: &[u8]) -> Result<Option<&'static str>, String> {
    if bytes.starts_with(b"!<arch>\n") {
        let mut offset = 8usize;
        let mut members = 0usize;
        while offset < bytes.len() {
            let end = offset.checked_add(60).ok_or("archive header overflow")?;
            let header = bytes
                .get(offset..end)
                .ok_or("native archive member header is truncated")?;
            if header[58..60] != [0x60, b'\n'] {
                return Err("native archive member trailer is invalid".into());
            }
            let size = std::str::from_utf8(&header[48..58])
                .map_err(|_| "native archive size is not ASCII")?
                .trim()
                .parse::<usize>()
                .map_err(|_| "native archive member size is invalid")?;
            offset = end
                .checked_add(size)
                .and_then(|end| end.checked_add(size % 2))
                .ok_or("archive member size overflow")?;
            members += 1;
            if offset > bytes.len() || members > 100000 {
                return Err("native archive exceeds its member or byte bounds".into());
            }
        }
        return Ok(Some("static-library"));
    }
    if !bytes.starts_with(b"\x7fELF") {
        return Ok(None);
    }
    let class = *bytes.get(4).ok_or("ELF header is truncated")?;
    let little = match bytes.get(5) {
        Some(1) => true,
        Some(2) => false,
        _ => return Err("ELF data encoding is unsupported".into()),
    };
    let number = |offset, width| read_number(bytes, offset, width, little);
    let (header_size, phoff, phsize, phnum, entry_size) = match class {
        1 => (52, number(28, 4)?, number(42, 2)?, number(44, 2)?, 8),
        2 => (64, number(32, 8)?, number(54, 2)?, number(56, 2)?, 16),
        _ => return Err("ELF class is unsupported".into()),
    };
    if bytes.len() < header_size || number(20, 4)? != 1 {
        return Err("ELF header version or size is invalid".into());
    }
    let kind = number(16, 2)?;
    if !matches!(kind, 2 | 3) {
        return Ok(None);
    }
    if phnum == 0 || phnum > 4096 || phsize < if class == 1 { 32 } else { 56 } || phsize > 4096 {
        return Err("ELF program header geometry is invalid or exceeds 4096 records".into());
    }
    let end = phoff
        .checked_add(
            phsize
                .checked_mul(phnum)
                .ok_or("ELF program table overflow")?,
        )
        .ok_or("ELF program table overflow")?;
    if end > bytes.len() as u64 {
        return Err("ELF program header table is truncated".into());
    }
    let mut executable = kind == 2;
    for index in 0..phnum {
        let start = usize::try_from(phoff + index * phsize).map_err(|error| error.to_string())?;
        let segment = number(start, 4)?;
        let offset = number(
            start + if class == 1 { 4 } else { 8 },
            if class == 1 { 4 } else { 8 },
        )?;
        let size = number(
            start + if class == 1 { 16 } else { 32 },
            if class == 1 { 4 } else { 8 },
        )?;
        let end = offset.checked_add(size).ok_or("ELF segment overflow")?;
        if end > bytes.len() as u64 {
            return Err("ELF file segment is truncated".into());
        }
        if segment == 3 {
            executable = true;
        }
        if segment == 2 {
            if size > 1024 * 1024 || size % entry_size != 0 {
                return Err("ELF dynamic table exceeds its bounds".into());
            }
            let mut position = offset;
            while position < end {
                let width = if class == 1 { 4 } else { 8 };
                let position_usize =
                    usize::try_from(position).map_err(|error| error.to_string())?;
                let tag = number(position_usize, width)?;
                let value = number(position_usize + width, width)?;
                if tag == 0 {
                    break;
                }
                if tag == 0x6fff_fffb && value & 0x0800_0000 != 0 {
                    executable = true;
                }
                position += entry_size;
            }
        }
    }
    Ok(Some(if executable {
        "executable"
    } else {
        "shared-library"
    }))
}
fn read_number(bytes: &[u8], offset: usize, width: usize, little: bool) -> Result<u64, String> {
    let slice = bytes
        .get(
            offset
                ..offset
                    .checked_add(width)
                    .ok_or("binary integer offset overflow")?,
        )
        .ok_or("binary integer is truncated")?;
    let mut value = 0u64;
    if little {
        for byte in slice.iter().rev() {
            value = (value << 8) | u64::from(*byte);
        }
    } else {
        for byte in slice {
            value = (value << 8) | u64::from(*byte);
        }
    }
    Ok(value)
}
