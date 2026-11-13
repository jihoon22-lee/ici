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
    matches!(project_type(root, effective), "python" | "hybrid") && tests.exists()
}

/// The frozen `project_type()` legacy engines read: the declared `type` (top level, then
/// `[project]`), else `detect_project_type` with its `python` fallback.
fn project_type<'a>(root: &Path, effective: &'a Value) -> &'a str {
    let declared = effective
        .get("type")
        .filter(|value| !value.is_null())
        .or_else(|| effective.pointer("/project/type"))
        .and_then(Value::as_str)
        .filter(|kind| matches!(*kind, "python" | "cpp" | "hybrid"));
    declared.unwrap_or_else(|| detected_type(root))
}

/// Which suites frozen legacy `TestEngine._run_project_tests` (engines/test.py:249-276) attempts.
/// Not the sanitizer's scope: it reads `tests/**/test_*.py` (not any `.py`) and needs a selected
/// source for the declared type, never a bare `tests` path.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LegacyTestScope {
    /// `tests/**/test_*.py`, or a `python`/`hybrid` project type with a selected Python source.
    pub python: bool,
    /// `tests/**/*.cpp`, or a `cpp`/`hybrid` project type with a selected C/C++ source.
    pub cpp: bool,
    /// The frozen project type is `cpp` (declared, or detected).
    pub cpp_type: bool,
    /// The frozen project type is `python` (declared, detected, or the fallback).
    pub python_type: bool,
}

/// Frozen `get_all_cpp_sources` suffixes (core/project.py:290): translation units only, compared
/// case-sensitively like `Path.suffix in (...)`; headers never count.
pub fn frozen_cpp_source(path: &str) -> bool {
    [".cpp", ".cc", ".cxx", ".c"].iter().any(|suffix| {
        let name = path.rsplit('/').next().unwrap_or(path);
        name.len() > suffix.len() && name.ends_with(suffix)
    })
}

/// Frozen `_run_project_tests` scope decision. `selected` is the configured scope's source
/// files (project-relative), as `project_python_sources()`/`project_cpp_sources()` list them.
/// `tests_root.rglob` matches any entry name and skips no ignored directory.
pub fn legacy_test_scope(root: &Path, effective: &Value, selected: &[String]) -> LegacyTestScope {
    let tests = root.join("tests");
    let tests_match =
        |matches: &dyn Fn(&str, bool) -> bool| tests.is_dir() && any_entry(&tests, false, matches);
    let kind = project_type(root, effective);
    let python_sources = selected.iter().any(|file| {
        let name = file.rsplit('/').next().unwrap_or(file);
        name.len() > 3 && name.ends_with(".py")
    });
    let cpp_sources = selected.iter().any(|file| frozen_cpp_source(file));
    LegacyTestScope {
        python: tests_match(&|name: &str, _: bool| {
            name.starts_with("test_") && name.ends_with(".py")
        }) || (matches!(kind, "python" | "hybrid") && python_sources),
        cpp: tests_match(&|name: &str, _: bool| name.ends_with(".cpp"))
            || (matches!(kind, "cpp" | "hybrid") && cpp_sources),
        cpp_type: kind == "cpp",
        python_type: kind == "python",
    }
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
    use super::{
        LegacyTestScope, frozen_cpp_source, legacy_python_sanitize_scope, legacy_test_scope,
    };
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

    fn tree(files: &[&str]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for file in files {
            if let Some(directory) = file.strip_suffix('/') {
                fs::create_dir_all(dir.path().join(directory)).unwrap();
            } else {
                let path = dir.path().join(file);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, "").unwrap();
            }
        }
        dir
    }

    fn scope(files: &[&str], effective: serde_json::Value, selected: &[&str]) -> (bool, bool) {
        let dir = tree(files);
        let selected: Vec<String> = selected.iter().map(|file| (*file).to_owned()).collect();
        let LegacyTestScope { python, cpp, .. } =
            legacy_test_scope(dir.path(), &effective, &selected);
        (python, cpp)
    }

    #[test]
    fn the_test_scope_follows_frozen_run_project_tests() {
        let cpp = json!({"project":{"type":"cpp"}});
        let python = json!({"project":{"type":"python"}});
        let hybrid = json!({"type":"hybrid"});
        // tests/**/test_*.py attempts Python whatever the type, flat or nested.
        assert_eq!(
            scope(&["tests/test_calc.py"], cpp.clone(), &[]),
            (true, false)
        );
        assert_eq!(
            scope(&["tests/a/b/test_x.py"], cpp.clone(), &[]),
            (true, false)
        );
        assert_eq!(
            scope(&["tests/build/test_x.py"], cpp.clone(), &[]),
            (true, false)
        );
        // Not the sanitizer's scope: a helper or a bare tests path attempts nothing.
        assert_eq!(
            scope(&["tests/helper.py"], cpp.clone(), &[]),
            (false, false)
        );
        assert_eq!(scope(&["tests/"], python.clone(), &[]), (false, false));
        assert_eq!(
            scope(&["tests/data.txt"], hybrid.clone(), &[]),
            (false, false)
        );
        // Python outside tests/ only through a selected source of a python/hybrid project.
        assert_eq!(
            scope(&["tools/helper.py"], cpp.clone(), &[]),
            (false, false)
        );
        assert_eq!(
            scope(&["src/calc.py"], cpp.clone(), &["src/calc.py"]),
            (false, false)
        );
        assert_eq!(
            scope(&["src/calc.py"], python.clone(), &["src/calc.py"]),
            (true, false)
        );
        assert_eq!(
            scope(&["src/calc.py"], hybrid.clone(), &["src/calc.py"]),
            (true, false)
        );
        // C++: tests/**/*.cpp whatever the type, or a cpp/hybrid project's translation unit.
        assert_eq!(scope(&["tests/t.cpp"], python.clone(), &[]), (false, true));
        assert_eq!(
            scope(&["src/a.cpp"], cpp.clone(), &["src/a.cpp"]),
            (false, true)
        );
        assert_eq!(
            scope(&["src/a.c"], hybrid.clone(), &["src/a.c"]),
            (false, true)
        );
        assert_eq!(
            scope(&["src/a.cpp"], python.clone(), &["src/a.cpp"]),
            (false, false)
        );
        // Headers are no C++ source; tests/*.cc is no C++ test.
        assert_eq!(
            scope(&["src/a.h"], cpp.clone(), &["src/a.h"]),
            (false, false)
        );
        assert_eq!(scope(&["tests/t.cc"], cpp.clone(), &[]), (false, false));
        // Both, and neither.
        assert_eq!(
            scope(
                &["src/a.cpp", "tests/test_a.py"],
                cpp.clone(),
                &["src/a.cpp"]
            ),
            (true, true)
        );
        assert_eq!(scope(&[], cpp.clone(), &[]), (false, false));
        // Undeclared: detected `cpp` from a build descriptor, `python` by fallback.
        let dir = tree(&["CMakeLists.txt", "src/a.cpp"]);
        let detected = legacy_test_scope(dir.path(), &json!({}), &["src/a.cpp".into()]);
        assert_eq!(
            detected,
            LegacyTestScope {
                python: false,
                cpp: true,
                cpp_type: true,
                python_type: false
            }
        );
        let dir = tree(&["src/calc.py"]);
        let detected = legacy_test_scope(dir.path(), &json!({}), &["src/calc.py".into()]);
        assert_eq!(
            detected,
            LegacyTestScope {
                python: true,
                cpp: false,
                cpp_type: false,
                python_type: true
            }
        );
    }

    #[test]
    fn frozen_cpp_sources_are_translation_units_by_exact_suffix() {
        for file in ["a.cpp", "src/a.cc", "a.cxx", "x/y.c"] {
            assert!(frozen_cpp_source(file), "{file}");
        }
        for file in [
            "a.h", "a.hpp", "a.C", "a.c++", "a.mm", ".cpp", "src/.c", "a.py",
        ] {
            assert!(!frozen_cpp_source(file), "{file}");
        }
    }
}
