//! Frozen `SecurityEngine` with `engines.security.scan_tests = true` adds every Python file below
//! the project's `tests/` directory to the selected Python sources (engines/security.py
//! `_test_python_files`). Historical commands give the `python.security` task that scope.
use crate::{
    commands::{CliError, Result},
    legacy_lines::LineScope,
    registry::Invocation,
};
use ici_application::plan::{Plan, file_language};
use ici_config::ResolvedProject;
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

/// Frozen `core/project.py::_should_ignore_path` directory parts.
const IGNORED: [&str; 7] = [
    ".venv",
    "venv",
    "build",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
];

pub fn collect(
    project: &ResolvedProject,
    plan: &Plan,
    invocation: &Invocation,
) -> Result<BTreeMap<String, LineScope>> {
    let mut result = BTreeMap::new();
    if !crate::legacy::is_legacy(invocation) {
        return Ok(result);
    }
    for check in plan
        .checks
        .iter()
        .filter(|check| check.check.id == "python.security")
    {
        let Some(component) = project
            .components
            .iter()
            .find(|component| component.id == check.component_id)
        else {
            continue;
        };
        if component.settings.pointer("/engines/security/scan_tests") != Some(&Value::Bool(true)) {
            continue;
        }
        let mut inputs: Vec<String> = component
            .files
            .iter()
            .filter(|path| file_language(path) == Some("python"))
            .cloned()
            .collect();
        let tests = if component.root == "." {
            "tests".to_owned()
        } else {
            format!("{}/tests", component.root)
        };
        if let Some(tests) = contained(&project.root, &tests) {
            walk(&project.root, &tests, 0, &mut inputs)?;
        }
        inputs.sort();
        inputs.dedup();
        result.insert(
            check.task_id.clone(),
            LineScope {
                source_paths: inputs.clone(),
                gated_paths: inputs.clone(),
                inputs,
            },
        );
    }
    Ok(result)
}

/// Frozen `_safe_project_dir(project_root, tests_dir)`: the directory itself may be a symbolic
/// link resolving inside the project, which is then walked (and named) at its resolved path.
fn contained(root: &Path, relative: &str) -> Option<String> {
    let metadata = fs::symlink_metadata(root.join(relative)).ok()?;
    if !metadata.file_type().is_symlink() {
        return metadata.is_dir().then(|| relative.to_owned());
    }
    let root = root.canonicalize().ok()?;
    let resolved = root.join(relative).canonicalize().ok()?;
    let inside = resolved.strip_prefix(&root).ok()?;
    if !resolved.is_dir() || inside.as_os_str().is_empty() {
        return None;
    }
    inside.to_str().map(str::to_owned)
}

fn ignored(name: &str) -> bool {
    IGNORED.contains(&name) || name.starts_with("v1.") || name.starts_with("v0.")
}

/// Frozen `_iter_project_files(tests_dir, project_root, (".py",))`: regular `.py` files, no
/// symlinked file or directory, ignored directory parts skipped; a missing directory is empty.
fn walk(root: &Path, relative: &str, depth: usize, paths: &mut Vec<String>) -> Result<()> {
    if depth > 64 {
        return Err(CliError(
            "legacy security test discovery exceeds 64 directory levels".into(),
        ));
    }
    let directory = root.join(relative);
    let Ok(metadata) = fs::symlink_metadata(&directory) else {
        return Ok(());
    };
    if !metadata.is_dir() {
        return Ok(());
    }
    let mut entries: Vec<_> = fs::read_dir(&directory)
        .map_err(|error| CliError(format!("cannot scan security test inputs: {error}")))?
        .collect::<std::io::Result<_>>()
        .map_err(|error| CliError(format!("cannot scan security test inputs: {error}")))?;
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let kind = entry
            .file_type()
            .map_err(|error| CliError(format!("cannot scan security test inputs: {error}")))?;
        let path = format!("{relative}/{name}");
        if kind.is_dir() {
            if !ignored(&name) {
                walk(root, &path, depth + 1, paths)?;
            }
        } else if kind.is_file()
            && !ignored(&name)
            && Path::new(&name).extension().is_some_and(|ext| ext == "py")
        {
            paths.push(path);
        }
    }
    Ok(())
}
