//! Frozen legacy `python_compat` wheel discovery (`analysis/_python_packaging._wheel_paths` over
//! Python 3.10 `pathlib.Path.glob`): the configured `wheel_globs` are expanded against the
//! canonical project root before the run, the matched wheels join the immutable input snapshot,
//! and a candidate frozen refused is carried to the analyzer as `wheel_discovery_error` so the
//! package analysis stops with frozen's message instead of silently missing the wheel.
//!
//! Per pattern, in scandir order: a match whose resolution fails or leaves the project is
//! `wheel glob could not be evaluated: '<pattern>'`; a symlink or a non-file is `wheel candidate
//! is not a regular contained file: <path>`; other regular files are skipped unless their suffix
//! is `.whl`; more than `max_wheels` wheels stops the discovery (`wheel match count exceeds the
//! configured bound`). A pattern the analyzer's policy refuses ends the discovery so that refusal
//! is the one reported.
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
};

/// What frozen `_wheel_paths` returned or raised.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Discovery {
    Wheels(Vec<String>),
    /// The JSON the analyzer turns into the frozen `PythonPackagingError` text.
    Refused(Value),
}

enum Selector {
    Precise(String),
    Wildcard(Regex),
    Recursive,
}

fn wildcard(part: &str) -> bool {
    part.contains(['*', '?', '['])
}

fn selectors(pattern: &str) -> Option<Vec<Selector>> {
    let parts: Vec<_> = pattern
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    if parts.is_empty() {
        return None;
    }
    parts
        .into_iter()
        .map(|part| {
            Some(if part == "**" {
                Selector::Recursive
            } else if wildcard(part) {
                Selector::Wildcard(ici_application::suppressions::fnmatch_regex(part).ok()?)
            } else {
                Selector::Precise(part.to_owned())
            })
        })
        .collect()
}

/// Python 3.10 `_RecursiveWildcardSelector._iterate_directories`: the directory itself, then
/// every real (non-symlink) subdirectory, depth first in scandir order.
fn directories(path: &Path, out: &mut Vec<PathBuf>) {
    out.push(path.to_owned());
    let Ok(entries) = std::fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            directories(&entry.path(), out);
        }
    }
}

/// Matches of `selectors` under `parent` in pathlib's yield order (deduplicated like pathlib).
fn select(parent: &Path, selectors: &[Selector], out: &mut Vec<PathBuf>) {
    let Some((first, rest)) = selectors.split_first() else {
        if !out.iter().any(|seen| seen == parent) {
            out.push(parent.to_owned());
        }
        return;
    };
    let dironly = !rest.is_empty();
    match first {
        Selector::Precise(name) => {
            let path = parent.join(name);
            if if dironly {
                path.is_dir()
            } else {
                path.exists()
            } {
                select(&path, rest, out);
            }
        }
        Selector::Wildcard(pattern) => {
            let Ok(entries) = std::fs::read_dir(parent) else {
                return;
            };
            for entry in entries.flatten() {
                if dironly && !entry.path().is_dir() {
                    continue;
                }
                let name = entry.file_name();
                if pattern.is_match(&name.to_string_lossy()) {
                    select(&parent.join(name), rest, out);
                }
            }
        }
        Selector::Recursive => {
            let mut found = vec![];
            directories(parent, &mut found);
            for directory in found {
                select(&directory, rest, out);
            }
        }
    }
}

fn contained(pattern: &str) -> bool {
    !pattern.is_empty()
        && !pattern.starts_with('/')
        && !pattern.contains(['\\', '\0'])
        && !pattern.split('/').any(|part| part == "..")
}

/// Frozen `_wheel_paths(canonical_root, policy)`.
pub fn discover(root: &Path, globs: &[String], max_wheels: usize) -> Discovery {
    let Ok(root) = root.canonicalize() else {
        return Discovery::Wheels(vec![]);
    };
    let mut found = BTreeSet::new();
    for pattern in globs {
        if !contained(pattern) {
            break;
        }
        let refused = || Discovery::Refused(json!({"pattern": pattern}));
        let Some(selectors) = selectors(pattern) else {
            return refused();
        };
        let mut matches = vec![];
        if root.is_dir() {
            select(&root, &selectors, &mut matches);
        }
        for path in matches {
            let Ok(resolved) = path.canonicalize() else {
                return refused();
            };
            let Ok(relative) = resolved.strip_prefix(&root) else {
                return refused();
            };
            let symlink = std::fs::symlink_metadata(&path)
                .map(|metadata| metadata.file_type().is_symlink())
                .unwrap_or(false);
            if symlink || !resolved.is_file() {
                return Discovery::Refused(json!({"candidate": path.display().to_string()}));
            }
            if resolved.extension().is_some_and(|suffix| suffix == "whl") {
                found.insert(relative.to_string_lossy().into_owned());
            }
            if found.len() > max_wheels {
                return Discovery::Refused(json!({"count": true}));
            }
        }
    }
    Discovery::Wheels(found.into_iter().collect())
}

/// The wheel discovery a check's analysis options ask for, or None without wheel globs.
pub fn for_options(root: &Path, options: &Value) -> Option<Discovery> {
    let globs: Vec<String> = options
        .get("wheel_globs")?
        .as_array()?
        .iter()
        .map(|value| value.as_str().map(str::to_owned))
        .collect::<Option<_>>()?;
    if globs.is_empty() || globs.len() > 32 {
        return None;
    }
    let max_wheels = match options.get("max_wheels") {
        None => 32,
        Some(value) => match value.as_u64() {
            Some(value @ 1..=32) => value as usize,
            _ => return None,
        },
    };
    Some(discover(root, &globs, max_wheels))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pathlib_selectors_match_names_and_skip_symlinked_directories_when_recursing() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        std::fs::create_dir_all(root.join("build/nested")).unwrap();
        std::fs::create_dir(root.join("dist")).unwrap();
        std::fs::write(root.join("dist/a-1-py3-none-any.whl"), b"a").unwrap();
        std::fs::write(root.join("dist/.hidden.whl"), b"h").unwrap();
        std::fs::write(root.join("build/nested/b-1-py3-none-any.whl"), b"b").unwrap();
        std::fs::write(root.join("dist/notes.txt"), b"n").unwrap();
        std::fs::write(root.join("dist/UPPER.WHL"), b"u").unwrap();
        let globs = |patterns: &[&str]| {
            discover(
                &root,
                &patterns.iter().map(|p| (*p).to_owned()).collect::<Vec<_>>(),
                32,
            )
        };
        assert_eq!(
            globs(&["dist/*.whl"]),
            Discovery::Wheels(vec![
                "dist/.hidden.whl".into(),
                "dist/a-1-py3-none-any.whl".into()
            ])
        );
        // `dist/*` also matches the text file and the upper-case name: regular files without
        // the `.whl` suffix are skipped, as frozen did.
        assert_eq!(
            globs(&["./dist/*"]),
            Discovery::Wheels(vec![
                "dist/.hidden.whl".into(),
                "dist/a-1-py3-none-any.whl".into()
            ])
        );
        assert_eq!(
            globs(&["**/b-*.whl"]),
            Discovery::Wheels(vec!["build/nested/b-1-py3-none-any.whl".into()])
        );
        assert_eq!(globs(&["dist/missing.whl"]), Discovery::Wheels(vec![]));
        // A pattern the analyzer refuses ends discovery; the analyzer names it.
        assert_eq!(globs(&["../x.whl", "dist/*"]), Discovery::Wheels(vec![]));
        assert_eq!(
            discover(&root, &["dist/*.whl".into()], 1),
            Discovery::Refused(json!({"count": true}))
        );
        // A directory is not a regular file, whatever its name.
        std::fs::create_dir(root.join("dist/dir.whl")).unwrap();
        assert_eq!(
            globs(&["dist/dir.whl"]),
            Discovery::Refused(
                json!({"candidate": root.join("dist/dir.whl").display().to_string()})
            )
        );
    }
}
