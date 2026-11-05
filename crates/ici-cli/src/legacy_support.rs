//! Frozen `core/support.py` project discovery for the historical report views: the languages and
//! frameworks `evaluate_support_matrix` derived from the project before evaluating declarations.
use crate::planning::PreparedPlan;
use ici_reporting::LegacySupportInputs;
use serde_json::Value;
use std::path::Path;

/// Frozen `_project_languages`: discovered sources/headers, then the configured `type`, then the
/// build-identity fallback (`pyproject.toml`, `CMakeLists.txt`, `*.pro`) when nothing matched.
fn languages(prepared: &PreparedPlan) -> Vec<String> {
    let files = prepared
        .project
        .components
        .iter()
        .flat_map(|component| &component.files);
    let (mut python, mut cpp) = (false, false);
    for file in files {
        match file.rsplit_once('.').map(|(_, extension)| extension) {
            Some("py") => python = true,
            Some("cpp" | "cc" | "cxx" | "c" | "h" | "hh" | "hpp" | "hxx") => cpp = true,
            _ => {}
        }
    }
    let effective = &prepared.project.effective;
    let configured = effective
        .get("type")
        .filter(|value| !value.is_null())
        .or_else(|| effective.pointer("/project/type"))
        .and_then(Value::as_str);
    python |= matches!(configured, Some("python" | "hybrid"));
    cpp |= matches!(configured, Some("cpp" | "hybrid"));
    let root = &prepared.project.root;
    if !python && !cpp {
        python = root.join("pyproject.toml").is_file();
        cpp = root.join("CMakeLists.txt").is_file() || !qmake_projects(root).is_empty();
    }
    [("python", python), ("cpp", cpp)]
        .into_iter()
        .filter(|(_, present)| *present)
        .map(|(language, _)| language.to_owned())
        .collect()
}

/// The legacy configured project `type` (top-level `type`, else `project.type`).
pub(crate) fn declared_type(effective: &Value) -> Option<&str> {
    effective
        .get("type")
        .filter(|value| value.is_string())
        .or_else(|| effective.pointer("/project/type"))
        .and_then(Value::as_str)
}

/// Frozen `any(project_root.rglob("*<suffix>"))`: any path under the root (file, directory or
/// symlink) whose name ends with `suffix`, descending into real directories only (CPython 3.10
/// pathlib does not recurse through a symlinked directory). Unreadable directories are skipped
/// like pathlib's `_ignore_error` scandir failures.
pub(crate) fn rglob_suffix(root: &Path, suffix: &str) -> bool {
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            if entry.file_name().to_string_lossy().ends_with(suffix) {
                return true;
            }
            if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                pending.push(entry.path());
            }
        }
    }
    false
}

/// Root `*.pro` files in frozen `sorted(project_root.glob("*.pro"))` order.
fn qmake_projects(root: &Path) -> Vec<std::path::PathBuf> {
    let mut found: Vec<_> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        // pathlib's `*` also matches dot names and directories.
        .filter(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().ends_with(".pro"))
        })
        .collect();
    found.sort();
    found
}

/// Frozen `_project_frameworks`: a Qt pkg-config package, or a Qt marker in the root build files.
fn frameworks(prepared: &PreparedPlan) -> Vec<String> {
    let packages = prepared
        .project
        .effective
        .pointer("/project/cpp_pkg_config")
        .and_then(Value::as_array);
    if packages.is_some_and(|packages| {
        packages.iter().filter_map(Value::as_str).any(|package| {
            let folded = package.to_lowercase();
            folded.starts_with("qt5") || folded.starts_with("qt6")
        })
    }) {
        return vec!["qt".into()];
    }
    let root = &prepared.project.root;
    let pattern = regex::Regex::new(r"(?:find_package\s*\(\s*Qt[56]\b|\bqt_add_|\bQT\s*\+=)")
        .expect("static Qt marker pattern");
    let candidates = std::iter::once(root.join("CMakeLists.txt")).chain(qmake_projects(root));
    for path in candidates {
        if path.is_file()
            && let Ok(bytes) = std::fs::read(&path)
            && pattern.is_match(&String::from_utf8_lossy(&bytes))
        {
            return vec!["qt".into()];
        }
    }
    vec![]
}

/// Support inputs for one historical view. `profile` is the analysis profile frozen applied:
/// the configured `ici.profile` for a single-engine command, the selected `--profile` for verify.
pub fn inputs(prepared: &PreparedPlan, profile: &str) -> LegacySupportInputs {
    LegacySupportInputs {
        languages: languages(prepared),
        frameworks: frameworks(prepared),
        profile: profile.to_owned(),
        engines: prepared
            .project
            .effective
            .get("engines")
            .cloned()
            .unwrap_or_default(),
    }
}

/// Frozen verify: the suite carries the matrix over every result; each result's own is null.
pub fn attach_suite(document: &mut Value, inputs: &LegacySupportInputs) {
    let results = document["results"].as_array().cloned().unwrap_or_default();
    document["support_matrix"] = ici_reporting::legacy_support_matrix(inputs, &results, None);
    if let Some(results) = document["results"].as_array_mut() {
        for result in results {
            result["support_matrix"] = Value::Null;
        }
    }
}

/// The serialized profile name (`fast`, `standard`, `deep`).
pub fn profile_name(profile: ici_domain::Profile) -> String {
    serde_json::to_value(profile)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default()
}
