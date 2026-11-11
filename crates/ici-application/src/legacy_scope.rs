//! Frozen legacy scope decisions that do not follow from the selected source files alone.
use serde_json::Value;
use std::path::Path;

/// Frozen `DEFAULT_SOURCE_DIRS` (core/project.py:17), read by `detect_project_type`.
const DEFAULT_SOURCE_DIRS: [&str; 5] = ["src", "lib", "app", "packages", "python"];
/// Bound on the entries one walk inspects; a larger tree counts as holding the suffix.
const MAX_ENTRIES: usize = 1 << 20;

/// Frozen legacy `SanitizeEngine._analysis_scopes` (engines/sanitize.py:100-125), its Python half
/// for a project whose configured source directories select no Python source: the scope stays in
/// play when `tests/**/*.py` exists (`_has_python_tests`, _sanitize_python_scope.py:68-70), or
/// when the project type is `python` or `hybrid` and a `tests` path exists (the declared type, or
/// `detect_project_type` with its `python` fallback). The engine then measures it, or reports the
/// missing scope; only without it is the engine's no-scope SKIP reached (sanitize.py:197-206).
pub fn legacy_python_sanitize_scope(root: &Path, effective: &Value) -> bool {
    let tests = root.join("tests");
    if tests.is_dir() && any_entry(&tests, false, &|name: &str, _: bool| name.ends_with(".py")) {
        return true;
    }
    let declared = effective
        .get("type")
        .filter(|value| !value.is_null())
        .or_else(|| effective.pointer("/project/type"))
        .and_then(Value::as_str)
        .filter(|kind| matches!(*kind, "python" | "cpp" | "hybrid"));
    let kind = declared.unwrap_or_else(|| detected_type(root));
    matches!(kind, "python" | "hybrid") && tests.exists()
}

/// Frozen `detect_project_type` (core/project.py:133-169) after the configuration files: build
/// descriptors and the default source directories' `.cpp`/`.py` files; neither is `python`.
fn detected_type(root: &Path) -> &'static str {
    let in_sources = |suffix: &str| {
        DEFAULT_SOURCE_DIRS.iter().any(|name| {
            let directory = root.join(name);
            directory.is_dir()
                && any_entry(&directory, true, &|file: &str, regular: bool| {
                    regular && file.len() > suffix.len() && file.ends_with(suffix)
                })
        })
    };
    let cpp = ["CMakeLists.txt", "Makefile"]
        .iter()
        .any(|name| root.join(name).is_file())
        || in_sources(".cpp");
    let python = ["pyproject.toml", "setup.py"]
        .iter()
        .any(|name| root.join(name).is_file())
        || in_sources(".py");
    match (cpp, python) {
        (true, true) => "hybrid",
        (true, false) => "cpp",
        _ => "python",
    }
}

/// Whether an entry under `directory` matches, descending real directories only (CPython 3.10
/// pathlib and `os.walk(followlinks=False)` do not recurse through a symlinked directory),
/// skipping frozen `_should_ignore_path` parts for `_iter_project_files` (`skip_ignored`; pathlib
/// `rglob` skips none). `matches` gets the entry name and whether it is a regular file.
fn any_entry(directory: &Path, skip_ignored: bool, matches: &dyn Fn(&str, bool) -> bool) -> bool {
    let mut pending = vec![directory.to_path_buf()];
    let mut inspected = 0usize;
    while let Some(current) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            inspected += 1;
            if inspected > MAX_ENTRIES {
                return true;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if matches(&name, kind.is_file()) {
                return true;
            }
            if kind.is_dir() && !(skip_ignored && ignored(&name)) {
                pending.push(entry.path());
            }
        }
    }
    false
}

/// Frozen `_should_ignore_path` directory parts (see `ici_config` legacy scan).
fn ignored(part: &str) -> bool {
    matches!(
        part,
        ".venv" | "venv" | "build" | "__pycache__" | ".git" | ".pytest_cache" | ".ruff_cache"
    ) || part.starts_with("v1.")
        || part.starts_with("v0.")
}

#[cfg(test)]
mod tests {
    use super::legacy_python_sanitize_scope;
    use serde_json::json;
    use std::fs;

    #[test]
    fn the_python_scope_follows_frozen_tests_and_project_type() {
        let case = |files: &[&str], effective: serde_json::Value| {
            let dir = tempfile::tempdir().unwrap();
            for file in files {
                let path = dir.path().join(file);
                if let Some(directory) = file.strip_suffix('/') {
                    fs::create_dir_all(dir.path().join(directory)).unwrap();
                } else {
                    fs::create_dir_all(path.parent().unwrap()).unwrap();
                    fs::write(path, "").unwrap();
                }
            }
            legacy_python_sanitize_scope(dir.path(), &effective)
        };
        // tests/**/*.py, whatever the type (frozen `_has_python_tests` reads any `.py`).
        assert!(case(&["tests/helper.py"], json!({})));
        assert!(case(
            &["tests/a/b/test_x.py"],
            json!({"project":{"type":"cpp"}})
        ));
        // A declared python/hybrid project keeps the scope for a tests path without Python.
        assert!(case(
            &["tests/data.txt"],
            json!({"project":{"type":"python"}})
        ));
        assert!(case(&["tests/"], json!({"type":"hybrid"})));
        assert!(!case(
            &["tests/data.txt"],
            json!({"project":{"type":"cpp"}})
        ));
        // Undeclared: detected `python` (the fallback, or pyproject.toml), not `cpp`.
        assert!(case(&["tests/"], json!({})));
        assert!(case(
            &["tests/", "pyproject.toml", "CMakeLists.txt"],
            json!({})
        ));
        assert!(!case(&["tests/", "CMakeLists.txt"], json!({})));
        assert!(!case(&["tests/", "src/a/b.cpp"], json!({})));
        assert!(case(&["tests/", "src/b.cpp", "lib/c.py"], json!({})));
        // Ignored directories hold no source signal; `rglob` under tests ignores nothing.
        assert!(case(&["tests/", "src/build/b.cpp"], json!({})));
        assert!(case(
            &["tests/build/x.py"],
            json!({"project":{"type":"cpp"}})
        ));
        // No tests path: no scope (the engine's no-scope SKIP).
        assert!(!case(
            &["docs/guide.md"],
            json!({"project":{"type":"python"}})
        ));
        assert!(!case(&[], json!({})));
    }
}
