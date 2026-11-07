//! Frozen next C++ suite contract (`adapters/providers/cpptest.py`, `identity.py`) for the
//! per-suite steps of `application/testing.expand_cpp_tests`: the step identity, the
//! `process_fingerprint` finding identity and the parse-failure wording.
use crate::{ValidationPlan, ValidationRequest};
use std::path::{Component, Path, PathBuf};

/// The suite step of a C++ test plan: frozen's `<task>.<suite id>` when declared, else
/// native's `<task>.suite`.
pub(crate) fn step_id(request: &ValidationRequest) -> String {
    request
        .options
        .suite_step_id
        .clone()
        .unwrap_or_else(|| format!("{}.suite", request.task_id))
}

/// Frozen `process_fingerprint(provider, spec, root, *details)`: sha256 of the compact JSON
/// (`ensure_ascii`) of `[provider, cwd, argv, details]`, where the cwd and each argument naming
/// a path under the workspace are spelled `${workspace}/…` (`invocation_identity`).
pub(crate) fn process_fingerprint(
    plan: &ValidationPlan,
    provider: &str,
    details: &[&str],
) -> String {
    let step = step_id(&plan.context);
    let root = &plan.context.root;
    let (cwd, argv) = plan
        .tasks
        .iter()
        .find(|task| task.id == step)
        .map(|task| (task.cwd.clone(), task.argv.clone()))
        .unwrap_or_default();
    let payload = serde_json::json!([
        provider,
        relative(&cwd, root),
        argv.iter()
            .map(|value| argument(value, root))
            .collect::<Vec<_>>(),
        details,
    ]);
    ici_domain::sha256(ensure_ascii(&payload.to_string()))
}

/// Frozen `_argument`: `-key=value` and joined path options keep the option spelling.
fn argument(value: &str, root: &Path) -> String {
    if value.starts_with('-')
        && let Some((key, argument)) = value.split_once('=')
    {
        let quoted = argument.len() > 1
            && argument.starts_with(['"', '\''])
            && argument.as_bytes().first() == argument.as_bytes().last();
        let argument = if quoted {
            let quote = &argument[..1];
            format!(
                "{quote}{}{quote}",
                relative(&argument[1..argument.len() - 1], root)
            )
        } else {
            relative(argument, root)
        };
        return format!("{key}={argument}");
    }
    for option in [
        "-isystem",
        "-idirafter",
        "-iquote",
        "-include",
        "-imacros",
        "-isysroot",
        "-I",
        "-L",
        "-F",
        "-B",
        "@",
    ] {
        if let Some(rest) = value.strip_prefix(option) {
            return format!("{option}{}", relative(rest, root));
        }
    }
    relative(value, root)
}

/// Frozen `_relative`: an absolute path under the root (after `normpath`) becomes
/// `${workspace}` or `${workspace}/<posix relative>`; anything else is kept as written.
fn relative(value: &str, root: &Path) -> String {
    let path = Path::new(value);
    if !path.is_absolute() {
        return value.into();
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    match normalized.strip_prefix(root) {
        Ok(rest) if rest.as_os_str().is_empty() => "${workspace}".into(),
        Ok(rest) => format!("${{workspace}}/{}", rest.to_string_lossy()),
        Err(_) => value.into(),
    }
}

/// Python `json.dumps(..., ensure_ascii=True)`: every non-ASCII character is a `\uXXXX`
/// escape (UTF-16 units, lowercase hex); serde_json already escapes control characters the
/// same way.
fn ensure_ascii(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for character in text.chars() {
        if character.is_ascii() {
            escaped.push(character);
        } else {
            let mut units = [0u16; 2];
            for unit in character.encode_utf16(&mut units) {
                escaped.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    escaped
}

/// Frozen `cpptest._test_source(name, cwd)`: the suite/case stem (`name` before `::`, after the
/// last `/`) names the first `.cpp` under the suite's cwd, in sorted path order, whose file name
/// starts with the stem, else the first that contains it, spelled relative to the cwd; with no
/// such file the stem itself (or `test-suite`) is the location. The walk does not follow
/// directory symlinks and stops after 200,000 entries.
#[cfg(test)]
pub(crate) fn test_source(cwd: &Path, name: &str) -> String {
    locate(&cpp_files(cwd), name)
}

/// The `.cpp` files under one suite cwd, walked once per observed suite run and shared by every
/// case line of it (frozen re-globbed per case; the files a run reads do not change under it).
pub(crate) struct SuiteSources<'a> {
    cwd: &'a Path,
    files: std::cell::OnceCell<Vec<PathBuf>>,
}

impl<'a> SuiteSources<'a> {
    pub(crate) fn new(cwd: &'a Path) -> Self {
        Self {
            cwd,
            files: std::cell::OnceCell::new(),
        }
    }

    /// [`test_source`] over the walk this value made the first time it was asked.
    pub(crate) fn locate(&self, name: &str) -> String {
        locate(self.files.get_or_init(|| cpp_files(self.cwd)), name)
    }
}

/// Every `.cpp` under `cwd` (no directory symlinks, at most 200,000 entries), relative to
/// `cwd`, in sorted path order.
fn cpp_files(cwd: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = vec![];
    let mut pending = vec![cwd.to_path_buf()];
    let mut budget = 200_000usize;
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            if budget == 0 {
                break;
            }
            budget -= 1;
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let path = entry.path();
            if kind.is_dir() {
                pending.push(path);
            } else if path.extension().is_some_and(|extension| extension == "cpp")
                && let Ok(relative) = path.strip_prefix(cwd)
            {
                files.push(relative.to_path_buf());
            }
        }
    }
    files.sort();
    files
}

/// The frozen stem search over the sorted `.cpp` list.
fn locate(files: &[PathBuf], name: &str) -> String {
    let stem = name
        .split("::")
        .next()
        .unwrap_or(name)
        .rsplit('/')
        .next()
        .unwrap_or_default();
    let named = |prefix: bool| {
        files.iter().find(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| {
                    if prefix {
                        name.starts_with(stem)
                    } else {
                        name.contains(stem)
                    }
                })
        })
    };
    match named(true).or_else(|| named(false)) {
        Some(path) => path.to_string_lossy().replace('\\', "/"),
        None if stem.is_empty() => "test-suite".into(),
        None => stem.into(),
    }
}

/// Frozen `_parse_ctest` without a verdict line.
pub(crate) fn ctest_without_verdicts(text: &str) -> &'static str {
    if text.to_ascii_lowercase().contains("no tests were found") {
        "ctest found no tests"
    } else {
        "ctest output held no test verdicts"
    }
}

/// Frozen `_parse_qtest` without a case verdict.
pub(crate) fn qtest_without_verdicts(text: &str) -> &'static str {
    let totals =
        regex::Regex::new(r"Totals:\s*(\d+)\s+passed,\s*(\d+)\s+failed").expect("constant regex");
    match totals.captures(text) {
        Some(found) if &found[1] == "0" && &found[2] == "0" => "qtest binary ran no cases",
        _ => "qtest output held no case verdicts",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Python 3.10 `json.dumps(("ctest", "${workspace}", ("${workspace}/tools/ctest",
    /// "--test-dir", "${workspace}/out", "--output-on-failure"), ("bad", "***Failed")),
    /// separators=(",", ":"))` and the same with a non-ASCII detail.
    /// Frozen `_test_source`: prefix before substring, sorted order, else the bare stem.
    #[test]
    fn test_source_follows_the_frozen_stem_search() {
        let root = tempfile::tempdir().unwrap();
        for file in ["b/TestUnitExtra.cpp", "a/my_TestUnit.cpp", "z/TestUnit.cpp"] {
            std::fs::create_dir_all(root.path().join(file).parent().unwrap()).unwrap();
            std::fs::write(root.path().join(file), "").unwrap();
        }
        assert_eq!(
            test_source(root.path(), "TestUnit::adds()"),
            "b/TestUnitExtra.cpp"
        );
        assert_eq!(test_source(root.path(), "my_Test"), "a/my_TestUnit.cpp");
        assert_eq!(test_source(root.path(), "TestOther::adds()"), "TestOther");
        // An empty stem matches every `.cpp` like frozen `rglob("*.cpp")`.
        assert_eq!(test_source(root.path(), "::x"), "a/my_TestUnit.cpp");
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(test_source(empty.path(), "::x"), "test-suite");
    }

    /// One walk per suite run: every case name resolves exactly as the per-case search does,
    /// and a file added after the first lookup is not seen by the same run.
    #[test]
    fn suite_sources_walk_once_and_locate_like_the_per_case_search() {
        let root = tempfile::tempdir().unwrap();
        for file in [
            "b/TestUnitExtra.cpp",
            "a/my_TestUnit.cpp",
            "z/TestUnit.cpp",
            "c/tst_other.cpp",
            "c/notes.txt",
        ] {
            std::fs::create_dir_all(root.path().join(file).parent().unwrap()).unwrap();
            std::fs::write(root.path().join(file), "").unwrap();
        }
        let sources = SuiteSources::new(root.path());
        for name in [
            "TestUnit::adds()",
            "my_Test",
            "TestOther::adds()",
            "::x",
            "suite/tst_other::case",
            "other",
            "",
        ] {
            assert_eq!(
                sources.locate(name),
                test_source(root.path(), name),
                "{name}"
            );
        }
        std::fs::write(root.path().join("a/Late.cpp"), "").unwrap();
        assert_eq!(sources.locate("Late"), "Late");
        assert_eq!(test_source(root.path(), "Late"), "a/Late.cpp");
    }

    #[test]
    fn payload_matches_python_compact_ascii_json() {
        let root = Path::new("/w/project");
        let argv: Vec<String> = [
            "/w/project/tools/ctest",
            "--test-dir",
            "/w/project/./out",
            "-I/w/project/inc",
            "--x=/w/project/a",
            "/usr/bin/ctest",
        ]
        .iter()
        .map(|value| argument(value, root))
        .collect();
        let payload = serde_json::json!([
            "ctest",
            relative("/w/project", root),
            argv,
            ["bad", "***Failed \u{e9}\u{1f600}"],
        ]);
        assert_eq!(
            ensure_ascii(&payload.to_string()),
            r#"["ctest","${workspace}",["${workspace}/tools/ctest","--test-dir","${workspace}/out","-I${workspace}/inc","--x=${workspace}/a","/usr/bin/ctest"],["bad","***Failed \u00e9\ud83d\ude00"]]"#
        );
        assert_eq!(
            qtest_without_verdicts("Totals: 0 passed, 0 failed, 0 skipped"),
            "qtest binary ran no cases"
        );
        assert_eq!(
            qtest_without_verdicts("garbage"),
            "qtest output held no case verdicts"
        );
        assert_eq!(
            ctest_without_verdicts("No tests were found!!!"),
            "ctest found no tests"
        );
        assert_eq!(
            ctest_without_verdicts(""),
            "ctest output held no test verdicts"
        );
    }
}
