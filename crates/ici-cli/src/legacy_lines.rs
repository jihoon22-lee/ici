//! Historical line reports inspect source directories and separately retain repository extras.
use crate::{
    commands::{CliError, Result},
    registry::Invocation,
};
use ici_application::plan::Plan;
use ici_config::{Component, ResolvedProject};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[derive(Clone, Debug, Default, Serialize)]
pub struct LineScope {
    pub inputs: Vec<String>,
    pub source_paths: Vec<String>,
    pub gated_paths: Vec<String>,
}
const SOURCE: [&str; 4] = ["src", "include", "lib", "app"];
const JUNK: [&str; 17] = [
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "target",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "htmlcov",
    ".ici",
    ".ici-native",
];

pub fn collect(
    project: &ResolvedProject,
    plan: &Plan,
    invocation: &Invocation,
    generated: &BTreeSet<String>,
) -> Result<BTreeMap<String, LineScope>> {
    if !crate::legacy::is_legacy(invocation) {
        return Ok(BTreeMap::new());
    }
    let checks: Vec<_> = plan
        .checks
        .iter()
        .filter(|check| matches!(check.check.id.as_str(), "python.line" | "cpp.line"))
        .collect();
    if checks.is_empty() {
        return Ok(BTreeMap::new());
    }
    let components: Vec<_> = project
        .components
        .iter()
        .filter(|component| {
            checks
                .iter()
                .any(|check| check.component_id == component.id)
        })
        .collect();
    let mut result: BTreeMap<_, _> = checks
        .iter()
        .map(|check| (check.task_id.clone(), LineScope::default()))
        .collect();
    for component in &components {
        let files = scan(&project.root, component)?;
        let excluded: BTreeSet<_> =
            ici_config::matching_source_paths(component, &files, &component.exclude)
                .map_err(|error| CliError(error.to_string()))?
                .into_iter()
                .collect();
        let config = component
            .settings
            .pointer("/engines/line")
            .unwrap_or(&Value::Null);
        let excludes = strings(config, "exclude_dirs");
        let mut source_dirs: BTreeSet<String> = SOURCE.iter().map(|s| (*s).into()).collect();
        source_dirs.extend(component.source_dirs.iter().cloned());
        source_dirs.extend(strings(config, "include_dirs"));
        let mut gate_dirs = strings(config, "gate_dirs");
        if config.get("gate_dirs").is_none() || gate_dirs.iter().map(String::as_str).eq(SOURCE) {
            gate_dirs = SOURCE
                .iter()
                .map(|s| (*s).into())
                .chain(component.source_dirs.iter().cloned())
                .collect();
        }
        source_dirs.retain(|name| !excludes.contains(name));
        gate_dirs.retain(|name| !excludes.contains(name));
        for path in files {
            if excluded.contains(&path)
                || generated.contains(&path)
                || crate::planning::is_command_output(invocation, &project.root, &path)
            {
                continue;
            }
            let owner = components
                .iter()
                .filter(|candidate| {
                    candidate.root == "." || Path::new(&path).starts_with(&candidate.root)
                })
                .max_by_key(|candidate| {
                    if candidate.root == "." {
                        0
                    } else {
                        Path::new(&candidate.root).components().count()
                    }
                });
            if owner.is_none_or(|owner| owner.id != component.id) {
                continue;
            }
            let relative = if component.root == "." {
                path.as_str()
            } else {
                path.strip_prefix(&format!("{}/", component.root))
                    .expect("component-owned path")
            };
            if excludes
                .iter()
                .any(|directory| Path::new(relative).starts_with(directory))
            {
                continue;
            }
            let preferred = if cpp(&path) {
                "cpp.line"
            } else {
                "python.line"
            };
            let check = checks
                .iter()
                .find(|check| check.component_id == component.id && check.check.id == preferred)
                .or_else(|| {
                    checks
                        .iter()
                        .find(|check| check.component_id == component.id)
                })
                .expect("component has line check");
            let scope = result.get_mut(&check.task_id).expect("line scope");
            let in_source = source_dirs
                .iter()
                .any(|directory| directory == "." || Path::new(relative).starts_with(directory));
            let top = relative.split_once('/').map_or("", |(top, _)| top);
            if in_source {
                scope.source_paths.push(path.clone());
                if gate_dirs.iter().any(|directory| directory == top) {
                    scope.gated_paths.push(path.clone());
                }
            }
            scope.inputs.push(path);
        }
    }
    for scope in result.values_mut() {
        scope.inputs.sort();
        scope.inputs.dedup();
        scope.source_paths.sort();
        scope.source_paths.dedup();
        scope.gated_paths.sort();
        scope.gated_paths.dedup();
    }
    Ok(result)
}
fn strings(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|s| s.trim_matches('/').to_owned())
        .collect()
}
fn cpp(path: &str) -> bool {
    ["cpp", "c", "h", "hpp", "cc", "cxx"].contains(&path.rsplit('.').next().unwrap_or(""))
}
fn accepted(path: &Path) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .is_some_and(|suffix| {
            [
                "cpp", "c", "h", "hpp", "cc", "cxx", "py", "sh", "csh", "toml", "md", "yml",
                "yaml", "json",
            ]
            .contains(&suffix)
        })
}
fn scan(root: &Path, component: &Component) -> Result<Vec<String>> {
    let base = root.join(&component.root);
    let mut pending = vec![(base, 0usize)];
    let mut entries = 0usize;
    let mut paths = vec![];
    while let Some((directory, depth)) = pending.pop() {
        if depth > 64 {
            return Err(CliError(
                "legacy line discovery exceeds 64 directory levels".into(),
            ));
        }
        for entry in fs::read_dir(&directory)
            .map_err(|error| CliError(format!("cannot scan line inputs: {error}")))?
        {
            let entry = entry.map_err(|error| CliError(error.to_string()))?;
            entries += 1;
            if entries > 100_000 {
                return Err(CliError(
                    "legacy line discovery exceeds 100000 entries".into(),
                ));
            }
            let name = entry.file_name();
            let name = name
                .to_str()
                .ok_or_else(|| CliError("line input paths must be UTF-8".into()))?;
            if JUNK.contains(&name) || name.starts_with("v0.") || name.starts_with("v1.") {
                continue;
            }
            let kind = entry
                .file_type()
                .map_err(|error| CliError(error.to_string()))?;
            let path = entry.path();
            if kind.is_dir() {
                if path.join(".git").exists()
                    || path.join(".hg").exists()
                    || path.join(".svn").exists()
                {
                    continue;
                }
                if path.join("ici.toml").is_file() {
                    let config = ici_execution::storage::read_bounded_regular(
                        &path.join("ici.toml"),
                        4 * 1024 * 1024,
                    )
                    .map_err(|error| CliError(error.to_string()))?;
                    let text = std::str::from_utf8(&config)
                        .map_err(|error| CliError(error.to_string()))?;
                    let document = ici_config::parse_document(&path.join("ici.toml"), text, false)
                        .map_err(|error| CliError(error.to_string()))?;
                    if document.get("workspace").is_some()
                        || document.get("schema_version").and_then(Value::as_u64) == Some(2)
                    {
                        continue;
                    }
                }
                pending.push((path, depth + 1));
            } else if kind.is_file() && accepted(&path) {
                let relative = path
                    .strip_prefix(root)
                    .expect("component contained")
                    .to_str()
                    .ok_or_else(|| CliError("line input paths must be UTF-8".into()))?
                    .to_owned();
                paths.push(relative);
            }
        }
    }
    paths.sort();
    Ok(paths)
}
