//! Standalone compilation export: read only unless the user explicitly selects preparation.
use crate::{
    commands::{CliError, CommandOutput, Result},
    export_context::{self, ExportMetadata},
    registry::Invocation,
};
use ici_config::ConfigurationInspection;
use ici_cpp::{CompilationContext, ContextRequest};
use ici_execution::{
    Cancellation, Runner, Scheduler,
    storage::{self, WriteMode},
};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
};
fn error(value: impl std::fmt::Display) -> CliError {
    CliError(ici_execution::redaction::redact_text(&value.to_string()))
}
fn failure(value: impl std::fmt::Display, code: i32) -> CommandOutput {
    CommandOutput {
        stderr: format!(
            "{}\n",
            ici_execution::redaction::redact_text(&value.to_string())
        ),
        exit_code: code,
        ..Default::default()
    }
}
fn database_override(value: &str) -> std::result::Result<String, String> {
    if value.is_empty()
        || value.contains('\\')
        || value.contains(':')
        || Path::new(value).is_absolute()
        || Path::new(value).components().any(|part| {
            matches!(
                part,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(
            "--database must identify a project-relative POSIX file without traversal".into(),
        );
    }
    let value = Path::new(value)
        .components()
        .filter_map(|part| {
            if let Component::Normal(part) = part {
                Some(part.to_string_lossy())
            } else {
                None
            }
        })
        .collect::<Vec<_>>()
        .join("/");
    if value.is_empty() {
        return Err("--database must identify a file below the project root".into());
    }
    Ok(value)
}
fn selected_request(
    view: &ConfigurationInspection,
    invocation: &Invocation,
    environment: &BTreeMap<String, String>,
) -> std::result::Result<ContextRequest, String> {
    let database = if let Some(value) = invocation.value("database") {
        Some(database_override(value)?)
    } else {
        view.effective
            .pointer("/project/compile_database")
            .and_then(Value::as_str)
            .map(|value| {
                view.resolve_path("project.compile_database", value, environment)
                    .map_err(|error| error.to_string())
            })
            .transpose()?
    };
    let mut build_directories = view
        .builds
        .iter()
        .filter_map(|build| build.directory.clone())
        .collect::<Vec<_>>();
    for key in [
        "/project/cpp_external_build_dirs",
        "/project/cpp_external_build_dir",
    ] {
        if let Some(value) = view.effective.pointer(key) {
            for value in value
                .as_array()
                .map(|items| items.iter().filter_map(Value::as_str).collect::<Vec<_>>())
                .unwrap_or_else(|| value.as_str().into_iter().collect())
            {
                build_directories.push(
                    view.resolve_path(
                        key.trim_start_matches('/').replace('/', ".").as_str(),
                        value,
                        environment,
                    )
                    .map_err(|error| error.to_string())?,
                );
            }
        }
    }
    build_directories.sort();
    build_directories.dedup();
    Ok(ContextRequest {
        database,
        build_directories,
        ..Default::default()
    })
}
fn read_text(path: &Path, limit: usize) -> std::result::Result<Option<String>, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.is_symlink() => {}
        Ok(_) => return Err("export metadata must be a regular file".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.to_string()),
    }
    let bytes = storage::read_bounded_regular(path, limit).map_err(|error| error.to_string())?;
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|_| "export metadata must be UTF-8".into())
}
fn metadata(view: &ConfigurationInspection) -> std::result::Result<ExportMetadata, String> {
    let root = &view.root;
    let mut name = None;
    let mut version = None;
    for file in ["ici.toml", "dev.toml", "pyproject.toml"] {
        if let Some(text) = read_text(&root.join(file), 4 * 1024 * 1024)? {
            let doc = text
                .parse::<toml_edit::DocumentMut>()
                .map_err(|_| format!("invalid metadata TOML: {file}"))?;

            for (key, slot) in [("name", &mut name), ("version", &mut version)] {
                if let Some(value) = if file == "pyproject.toml" {
                    doc.get("project").and_then(|table| table.get(key))
                } else {
                    doc.get(key)
                } {
                    let value = value
                        .as_str()
                        .ok_or_else(|| format!("project {key} must be a string"))?;
                    let raw = if key == "version" {
                        value.strip_prefix('v').unwrap_or(value)
                    } else {
                        value
                    };
                    if raw.is_empty()
                        || !raw
                            .bytes()
                            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
                    {
                        return Err(format!("invalid project {key}"));
                    }
                    if slot.is_none() {
                        *slot = Some(if key == "version" {
                            format!("v{raw}")
                        } else {
                            raw.into()
                        });
                    }
                }
            }
        }
    }
    let has_qmake = fs::read_dir(root)
        .map_err(|error| error.to_string())?
        .take(4097)
        .try_fold((0usize, false), |(count, found), entry| {
            let entry = entry.map_err(|error| error.to_string())?;
            if count >= 4096 {
                return Err("export root exceeds 4096 directory entries".to_string());
            }
            let is_pro = entry.path().extension().is_some_and(|value| value == "pro")
                && entry
                    .file_type()
                    .map_err(|error| error.to_string())?
                    .is_file();
            Ok((count + 1, found || is_pro))
        })?
        .1;
    let backend = view
        .effective
        .pointer("/project/backend")
        .or_else(|| view.effective.get("backend"))
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "cmake" | "qmake" | "make"))
        .map(str::to_owned)
        .or_else(|| view.builds.iter().find_map(|build| build.system.clone()))
        .or_else(|| {
            root.join("CMakeLists.txt")
                .is_file()
                .then(|| "cmake".into())
        })
        .or_else(|| has_qmake.then(|| "qmake".into()));
    let has_cpp =
        backend.is_some() || root.join("Makefile").is_file() || root.join("makefile").is_file();
    let has_python = root.join("pyproject.toml").is_file() || root.join("setup.py").is_file();
    let project_type = view
        .effective
        .get("type")
        .or_else(|| view.effective.pointer("/project/type"))
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "python" | "cpp" | "hybrid"))
        .unwrap_or(if has_cpp && has_python {
            "hybrid"
        } else if has_cpp {
            "cpp"
        } else {
            "python"
        })
        .to_string();
    Ok(ExportMetadata {
        backend,
        name: name.unwrap_or_else(|| view.workspace_name.clone()),
        version: version.unwrap_or_else(|| "v1.0.0".into()),
        project_type,
        generator: None,
    })
}
fn output_path(
    root: &Path,
    value: &str,
    view: &ConfigurationInspection,
    context: &CompilationContext,
) -> std::result::Result<Option<PathBuf>, String> {
    if value == "-" {
        return Ok(None);
    }
    if value.is_empty()
        || value.contains('\\')
        || value.contains('\0')
        || value.as_bytes().get(1) == Some(&b':')
    {
        return Err("--output uses invalid or foreign path syntax".into());
    }
    let raw = root.join(value);
    let mut path = PathBuf::new();
    for part in raw.components() {
        match part {
            Component::ParentDir => {
                path.pop();
            }
            Component::CurDir => (),
            _ => path.push(part.as_os_str()),
        }
    }
    if path.file_name().is_none() {
        return Err("--output must identify a regular file".into());
    }
    let protected_names = [
        "ici.toml",
        "dev.toml",
        "pyproject.toml",
        "CMakeLists.txt",
        "CMakeCache.txt",
        "Makefile",
        "makefile",
        "compile_commands.json",
        ".ici-workspace.toml",
    ];
    let protected_extension = [
        "c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx", "m", "mm", "pro", "pri", "qrc", "ui",
        "cmake", "toml", "py", "pyi", "rsp",
    ];
    if path
        .file_name()
        .and_then(|value| value.to_str())
        .is_some_and(|value| protected_names.contains(&value))
        || path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| protected_extension.contains(&value))
        || view.configuration_files.contains(&path)
        || context
            .database_path
            .as_ref()
            .is_some_and(|db| root.join(db) == path)
        || context
            .units
            .iter()
            .any(|unit| root.join(&unit.source) == path)
        || context
            .response_files
            .iter()
            .any(|input| root.join(&input.path) == path)
    {
        return Err("--output must not overwrite compilation inputs or project policy".into());
    }
    let mut ancestor = PathBuf::new();
    for part in path.components() {
        ancestor.push(part.as_os_str());
        match fs::symlink_metadata(&ancestor) {
            Ok(metadata) => {
                if metadata.is_symlink()
                    || ancestor != path && !metadata.is_dir()
                    || ancestor == path && (!metadata.is_file() || metadata.nlink() != 1)
                {
                    return Err(
                        "--output refuses symlinks, special files and multiply linked files".into(),
                    );
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(Some(path))
}
pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    let explicit = invocation.value("config_path").map(|value| cwd.join(value));
    let local = invocation
        .value("local_config")
        .or_else(|| invocation.value("local_path"))
        .map(|value| cwd.join(value));
    let view = ici_config::inspect_configuration_with_environment(
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(error)?;
    let request = match selected_request(&view, invocation, environment) {
        Ok(request) => request,
        Err(error) => return Ok(failure(error, 2)),
    };
    let mut context = ici_cpp::load_compilation_context(&view.root, &request);
    if context.origin == "ambiguous" {
        return Ok(failure(
            "ambiguous compilation databases; select one with --database",
            2,
        ));
    }
    let output = invocation.value("output").unwrap_or("-");
    if let Err(error) = output_path(&view.root, output, &view, &context) {
        return Ok(failure(error, 2));
    }
    let mut metadata = metadata(&view).map_err(error)?;
    let mut guard = None;
    if invocation.flag("prepare")
        && context.database_path.is_none()
        && context.diagnostics.is_empty()
    {
        let project = ici_config::resolve_with_environment(
            cwd,
            explicit.as_deref(),
            local.as_deref(),
            environment,
        )
        .map_err(error)?;
        let components = project
            .components
            .iter()
            .filter(|component| component.languages.iter().any(|language| language == "cpp"))
            .collect::<Vec<_>>();
        if components.len() != 1 {
            return Ok(failure(
                "--prepare requires exactly one selected C++ component",
                2,
            ));
        }
        let plan = export_context::plan_preparation(
            &project,
            components[0],
            environment,
            &std::env::current_exe().map_err(error)?,
        )
        .map_err(error)?;
        if !matches!(plan.build.system.as_str(), "cmake" | "qmake") {
            return Ok(failure(
                "--prepare export supports canonical CMake or qmake contexts",
                2,
            ));
        }
        let scheduler = Scheduler::new(Runner::with_environment(environment.clone()), 2);
        let cancellation = Cancellation::default();
        let _signals = crate::events::SignalGuard::install(&cancellation).map_err(error)?;
        let prepared = export_context::execute_preparation(
            &plan,
            &scheduler,
            &cancellation,
            &crate::events::EventLog::default(),
        )
        .map_err(error)?;
        metadata.backend = Some(plan.build.system.clone());
        metadata.generator = if plan.build.system == "qmake" {
            Some("qmake".into())
        } else {
            read_text(
                &project.root.join(&plan.build.shadow).join("CMakeCache.txt"),
                4 * 1024 * 1024,
            )
            .map_err(error)?
            .and_then(|text| {
                text.lines().find_map(|line| {
                    line.strip_prefix("CMAKE_GENERATOR:INTERNAL=")
                        .map(str::to_owned)
                })
            })
        };
        context = prepared.context.clone();
        guard = Some(prepared);
    }
    let payload = match export_context::compilation_export_payload(&view.root, &metadata, &context)
    {
        Ok(payload) => payload,
        Err(error) => {
            return Ok(failure(
                error,
                if context.database_digest.is_none()
                    || context.units.is_empty()
                        && !context.diagnostics.iter().any(|item| item.level == "error")
                {
                    2
                } else {
                    1
                },
            ));
        }
    };
    let bytes = export_context::render_compilation_export(&payload, invocation.flag("pretty"))
        .map_err(error)?;
    let target = match output_path(&view.root, output, &view, &context) {
        Ok(target) => target,
        Err(error) => return Ok(failure(error, 2)),
    };
    let result = if let Some(target) = target {
        storage::atomic_write(&target, &bytes, WriteMode::ReplaceRegular).map_err(error)?;
        CommandOutput::default()
    } else {
        CommandOutput::text(String::from_utf8(bytes).map_err(error)?)
    };
    drop(guard);
    Ok(result)
}
