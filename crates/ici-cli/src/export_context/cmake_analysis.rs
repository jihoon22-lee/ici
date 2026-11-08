//! Frozen `core/cmake_context.py` over the canonical release analysis database a legacy CMake
//! preparation configured: the cache metadata (`_read_cmake_metadata`), whether a build must
//! first generate sources (`_needs_generation_build`) and whether the context is a unity build
//! (`_has_unity_units`). `verify` and `export-compilation-context --prepare` read the same result.
use ici_cpp::{CompilationContext, ContextDiagnostic};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Read,
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::{Component, Path},
};

/// Linux `O_NONBLOCK`: opening a FIFO planted as the cache does not wait for a writer.
const O_NONBLOCK: i32 = 0o4000;

/// Frozen `MAX_CMAKE_CACHE_BYTES`.
const MAX_CACHE_BYTES: u64 = 4 * 1024 * 1024;
const TRUE_VALUES: [&str; 5] = ["1", "on", "true", "yes", "y"];
const FALSE_VALUES: [&str; 8] = ["0", "off", "false", "no", "n", "ignore", "notfound", ""];

/// What frozen adds to the loaded context of a configured canonical CMake database.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CmakeAnalysis {
    /// The cache's `CMAKE_GENERATOR` (empty: missing or unreadable cache).
    pub generator: String,
    /// Frozen `context.unity_build`: the cache's `CMAKE_UNITY_BUILD`, `true` for a unity build.
    pub unity_build: Option<bool>,
    /// In frozen order: cache metadata, `cmake-generation-failed`, `cmake-unity-build`.
    pub diagnostics: Vec<ContextDiagnostic>,
    /// After a failed generation build: frozen keeps the configured context, so each unit's
    /// `stale-source` stands even if the failed build left the file behind (source, diagnostic).
    pub stale_units: Vec<(String, ContextDiagnostic)>,
}

/// Frozen `_cache_values`: `None` is an unreadable cache (`cmake-cache-invalid`).
fn cache_values(bytes: &[u8]) -> Option<BTreeMap<&'static str, String>> {
    let text = std::str::from_utf8(bytes).ok()?;
    if text.contains('\0') {
        return None;
    }
    let mut values = BTreeMap::new();
    // Python `str.splitlines` boundaries.
    for line in text.split_inclusive(|character| {
        matches!(
            character,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        )
    }) {
        let line = line.trim_end_matches(|character| {
            matches!(
                character,
                '\n' | '\r'
                    | '\x0b'
                    | '\x0c'
                    | '\x1c'
                    | '\x1d'
                    | '\x1e'
                    | '\u{85}'
                    | '\u{2028}'
                    | '\u{2029}'
            )
        });
        if line.is_empty()
            || line.starts_with('#')
            || line.starts_with("//")
            || !line.contains('=')
            || !line.contains(':')
        {
            continue;
        }
        let (declaration, value) = line.split_once('=')?;
        // A `:` only after the `=` leaves no name/type split (frozen raises: invalid cache).
        let (name, _) = declaration.split_once(':')?;
        let Some(name) = [
            "CMAKE_GENERATOR",
            "CMAKE_UNITY_BUILD",
            "CMAKE_EXPORT_COMPILE_COMMANDS",
        ]
        .into_iter()
        .find(|wanted| *wanted == name) else {
            continue;
        };
        if values.contains_key(name)
            || value.chars().count() > 512
            || value.chars().any(|character| (character as u32) < 32)
        {
            return None;
        }
        values.insert(name, value.to_owned());
    }
    Some(values)
}

/// Python `str.casefold` for the values frozen compares (ASCII sets).
fn casefold(value: &str) -> String {
    value.to_lowercase().replace('\u{17f}', "s")
}

/// Frozen `_cmake_bool`.
fn cmake_bool(value: &str) -> Option<bool> {
    let value = casefold(value);
    if TRUE_VALUES.contains(&value.as_str()) {
        Some(true)
    } else if FALSE_VALUES.contains(&value.as_str()) || value.ends_with("-notfound") {
        Some(false)
    } else {
        None
    }
}

enum Cache {
    Missing,
    Invalid,
    Values(BTreeMap<&'static str, String>),
}

/// Frozen `_read_bounded_regular`: one regular file of at most 4 MiB, never through a symlink
/// (the opened file must be the one `lstat` saw).
fn read_cache(path: &Path) -> Cache {
    let seen = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Cache::Missing,
        Err(_) => return Cache::Invalid,
    };
    if !seen.is_file() || seen.len() > MAX_CACHE_BYTES {
        return Cache::Invalid;
    }
    let Ok(file) = OpenOptions::new()
        .read(true)
        .custom_flags(O_NONBLOCK)
        .open(path)
    else {
        return Cache::Invalid;
    };
    let same = file.metadata().is_ok_and(|opened| {
        opened.is_file() && opened.dev() == seen.dev() && opened.ino() == seen.ino()
    });
    let mut bytes = vec![];
    if !same
        || file
            .take(MAX_CACHE_BYTES + 1)
            .read_to_end(&mut bytes)
            .is_err()
        || bytes.len() as u64 > MAX_CACHE_BYTES
    {
        return Cache::Invalid;
    }
    cache_values(&bytes).map_or(Cache::Invalid, Cache::Values)
}

/// Frozen `_read_cmake_metadata` over `<shadow>/CMakeCache.txt`.
fn metadata(cache: &Path) -> (String, Option<bool>, Vec<ContextDiagnostic>) {
    let values = match read_cache(cache) {
        Cache::Missing => {
            return (
                String::new(),
                None,
                vec![ContextDiagnostic::error(
                    "cmake-cache-missing",
                    "The canonical CMake cache was not generated.",
                )],
            );
        }
        Cache::Invalid => {
            return (
                String::new(),
                None,
                vec![ContextDiagnostic::error(
                    "cmake-cache-invalid",
                    "The canonical CMake cache is not safe to read.",
                )],
            );
        }
        Cache::Values(values) => values,
    };
    let generator = values.get("CMAKE_GENERATOR").cloned().unwrap_or_default();
    let unity_value = values.get("CMAKE_UNITY_BUILD");
    let unity = unity_value.and_then(|value| cmake_bool(value));
    let mut diagnostics = vec![];
    if generator.is_empty() {
        diagnostics.push(ContextDiagnostic::error(
            "cmake-generator-missing",
            "The canonical CMake generator is unknown.",
        ));
    } else if !(generator == "Ninja" || generator.ends_with("Makefiles")) {
        // A multi-config generator ignores CMAKE_BUILD_TYPE: no single Release configuration.
        diagnostics.push(ContextDiagnostic::error(
            "cmake-generator-unsupported",
            "The selected CMake generator cannot provide exact compile commands.",
        ));
    }
    let export = values
        .get("CMAKE_EXPORT_COMPILE_COMMANDS")
        .map(|value| casefold(value))
        .unwrap_or_default();
    if !TRUE_VALUES.contains(&export.as_str()) {
        diagnostics.push(ContextDiagnostic::error(
            "cmake-export-disabled",
            "The canonical CMake configure did not enable compile-command export.",
        ));
    }
    if unity_value.is_some() && unity.is_none() {
        diagnostics.push(ContextDiagnostic::error(
            "cmake-unity-unknown",
            "The canonical CMake unity-build setting could not be interpreted safely.",
        ));
    }
    (generator, unity, diagnostics)
}

/// Frozen `_has_unity_units`: a path part named `unity` or a file name starting `unity_`
/// (casefolded). Native `ici-cpp` keeps its own, wider name rule for its refusal.
pub fn has_unity_units(context: &CompilationContext) -> bool {
    context.units.iter().any(|unit| {
        let path = Path::new(&unit.source);
        path.components().any(|part| {
            matches!(part, Component::Normal(name) if casefold(&name.to_string_lossy()) == "unity")
        }) || path
            .file_name()
            .is_some_and(|name| casefold(&name.to_string_lossy()).starts_with("unity_"))
    })
}

/// Frozen `_needs_generation_build`: a stale (not yet generated) source under the shadow this
/// preparation owns. Frozen names its own shadow `build/ici-cmake-build/`; native's is
/// `plan.build.shadow`.
pub fn needs_generation_build(context: &CompilationContext, shadow: &str) -> bool {
    let prefix = format!("{}/", shadow.trim_end_matches('/'));
    context.units.iter().any(|unit| {
        unit.source.starts_with(&prefix)
            && unit
                .diagnostics
                .iter()
                .any(|diagnostic| diagnostic.code == "stale-source")
    })
}

/// Frozen `_read_cmake_metadata` right after the configure: what the cache said before any
/// build (frozen never re-reads it after its generation build).
#[derive(Clone, Debug)]
pub struct CacheMetadata {
    generator: String,
    unity_build: Option<bool>,
    diagnostics: Vec<ContextDiagnostic>,
}

/// The metadata of `<root>/<shadow>/CMakeCache.txt` as the configure left it.
pub fn configured_metadata(root: &Path, shadow: &str) -> CacheMetadata {
    let (generator, unity_build, diagnostics) = metadata(&root.join(shadow).join("CMakeCache.txt"));
    CacheMetadata {
        generator,
        unity_build,
        diagnostics,
    }
}

/// Frozen `prepare_cmake_compilation_context` after a completed configure: the configured
/// cache metadata, then `cmake-generation-failed` when the generation build failed, then
/// `cmake-unity-build` for a unity context (the context frozen kept: reloaded after a
/// successful generation build, else the configured one).
pub fn analyse(
    metadata: CacheMetadata,
    context: &CompilationContext,
    generation_failed: bool,
) -> CmakeAnalysis {
    let CacheMetadata {
        generator,
        mut unity_build,
        mut diagnostics,
    } = metadata;
    if generation_failed {
        diagnostics.push(ContextDiagnostic::error(
            "cmake-generation-failed",
            "CMake could not generate sources required by the compilation database.",
        ));
    }
    if unity_build == Some(true) || has_unity_units(context) {
        unity_build = Some(true);
        diagnostics.push(ContextDiagnostic::error(
            "cmake-unity-build",
            "Unity compilation prevents exact source-level compile-command coverage.",
        ));
    }
    let stale_units = if generation_failed {
        context
            .units
            .iter()
            .flat_map(|unit| {
                unit.diagnostics
                    .iter()
                    .filter(|diagnostic| diagnostic.code == "stale-source")
                    .map(|diagnostic| (unit.source.clone(), diagnostic.clone()))
            })
            .collect()
    } else {
        vec![]
    };
    CmakeAnalysis {
        generator,
        unity_build,
        diagnostics,
        stale_units,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_cache_reads_like_frozen() {
        let values = cache_values(
            b"// comment\n#x\nCMAKE_GENERATOR:INTERNAL=Ninja\r\nOTHER:STRING=a=b\nCMAKE_UNITY_BUILD:BOOL=ON\n",
        )
        .unwrap();
        assert_eq!(values["CMAKE_GENERATOR"], "Ninja");
        assert_eq!(values["CMAKE_UNITY_BUILD"], "ON");
        // A repeated wanted key, a long value, a control character, NUL or non-UTF-8 refuse.
        assert!(cache_values(b"CMAKE_GENERATOR:X=a\nCMAKE_GENERATOR:Y=b\n").is_none());
        assert!(
            cache_values(format!("CMAKE_GENERATOR:X={}\n", "a".repeat(513)).as_bytes()).is_none()
        );
        assert!(
            cache_values(format!("CMAKE_GENERATOR:X={}\n", "a".repeat(512)).as_bytes()).is_some()
        );
        assert!(cache_values(b"CMAKE_GENERATOR:X=a\tb\n").is_none());
        assert!(cache_values(b"A:B=c\0\n").is_none());
        assert!(cache_values(b"A:B=\xff\n").is_none());
        // Lines without both separators, or another key, are skipped.
        assert!(
            cache_values(b"CMAKE_GENERATOR=Ninja\nnothing\n")
                .unwrap()
                .is_empty()
        );
        // A colon only in the value leaves the declaration without a type: frozen raises.
        assert!(cache_values(b"CMAKE_GENERATOR=a:b\n").is_none());
    }

    #[test]
    fn cmake_booleans_and_unity_names_are_frozen() {
        assert_eq!(cmake_bool("On"), Some(true));
        assert_eq!(cmake_bool("x-NOTFOUND"), Some(false));
        assert_eq!(cmake_bool(""), Some(false));
        assert_eq!(cmake_bool("maybe"), None);
        let context = |source: &str| CompilationContext {
            units: vec![
                serde_json::from_value(serde_json::json!({
                    "source": source, "directory": "", "original_argv": [], "argv": [],
                    "compiler": "", "language": "", "standard": "", "output": "", "target": "",
                    "variant": "", "defines": [], "include_paths": [], "sysroot": "",
                    "sysroot_scope": "", "configuration": "", "response_files": [],
                    "diagnostics": [],
                }))
                .unwrap(),
            ],
            ..Default::default()
        };
        assert!(has_unity_units(&context("src/Unity/all.cpp")));
        assert!(has_unity_units(&context("src/Unity_all.cpp")));
        assert!(!has_unity_units(&context("src/community_util.cpp")));
        assert!(!has_unity_units(&context("src/unity.cpp")));
    }
}
