//! Frozen legacy `python_compat` wheel discovery (`analysis/_python_packaging._wheel_paths` over
//! Python 3.10 `pathlib.Path.glob`): the configured `wheel_globs` are expanded against the
//! canonical project root before the run, the matched wheels join the immutable input snapshot,
//! and a candidate frozen refused is carried to the analyzer as `wheel_discovery_error` so the
//! package analysis stops with frozen's message instead of silently missing the wheel.
//!
//! Per pattern, candidates are judged as the walk yields them (scandir order, like frozen's lazy
//! generator): a match whose resolution fails or leaves the project is `wheel glob could not be
//! evaluated: '<pattern>'`; a symlink or a non-file is `wheel candidate is not a regular
//! contained file: <path>`; other regular files are skipped unless their suffix is `.whl`; more
//! than `max_wheels` wheels stops the walk at once (`wheel match count exceeds the configured
//! bound`). A pattern the analyzer's policy refuses ends the discovery so that refusal is the one
//! reported.
//!
//! Bounds frozen did not have (intended difference, runtime closure security review): consecutive
//! `**` parts collapse into one (same pathlib matches, no multiplied walk), each candidate is
//! judged once, and the whole discovery visits at most [`VISIT_LIMIT`] directory entries; beyond
//! that it is refused (`wheel glob visited more than 100000 directory entries: '<pattern>'`).
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashSet},
    ops::ControlFlow,
    path::{Path, PathBuf},
};

/// Directory entries one discovery may visit across every pattern before it is refused.
pub const VISIT_LIMIT: usize = 100_000;

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
    let mut parts: Vec<&str> = vec![];
    for part in pattern
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
    {
        // `**/**` selects exactly what `**` selects; one recursive walk is enough.
        if part == "**" && parts.last() == Some(&"**") {
            continue;
        }
        parts.push(part);
    }
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

/// One walk's state: the entry budget, the candidates already judged and the judge itself.
struct Walk<'a> {
    visited: usize,
    seen: HashSet<PathBuf>,
    judge: &'a mut dyn FnMut(&Path) -> ControlFlow<Discovery>,
    over_limit: Discovery,
}

impl Walk<'_> {
    /// Directory entries of `path` in scandir order, charged to the budget.
    fn entries(&mut self, path: &Path) -> ControlFlow<Discovery, Vec<std::fs::DirEntry>> {
        let mut entries = vec![];
        let Ok(reader) = std::fs::read_dir(path) else {
            return ControlFlow::Continue(entries);
        };
        for entry in reader.flatten() {
            self.visited += 1;
            if self.visited > VISIT_LIMIT {
                return ControlFlow::Break(self.over_limit.clone());
            }
            entries.push(entry);
        }
        ControlFlow::Continue(entries)
    }

    fn yielded(&mut self, path: &Path) -> ControlFlow<Discovery> {
        if self.seen.insert(path.to_owned()) {
            (self.judge)(path)?;
        }
        ControlFlow::Continue(())
    }

    /// Python 3.10 `_RecursiveWildcardSelector`: the directory itself, then every real
    /// (non-symlink) subdirectory depth first in scandir order, each matched as it is reached.
    fn recursive(&mut self, directory: &Path, rest: &[Selector]) -> ControlFlow<Discovery> {
        self.select(directory, rest)?;
        for entry in self.entries(directory)? {
            if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                self.recursive(&entry.path(), rest)?;
            }
        }
        ControlFlow::Continue(())
    }

    /// Matches of `selectors` under `parent`, judged in pathlib's yield order.
    fn select(&mut self, parent: &Path, selectors: &[Selector]) -> ControlFlow<Discovery> {
        let Some((first, rest)) = selectors.split_first() else {
            return self.yielded(parent);
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
                    self.select(&path, rest)?;
                }
            }
            Selector::Wildcard(pattern) => {
                for entry in self.entries(parent)? {
                    if dironly && !entry.path().is_dir() {
                        continue;
                    }
                    let name = entry.file_name();
                    if pattern.is_match(&name.to_string_lossy()) {
                        self.select(&parent.join(name), rest)?;
                    }
                }
            }
            Selector::Recursive => self.recursive(parent, rest)?,
        }
        ControlFlow::Continue(())
    }
}

fn contained(pattern: &str) -> bool {
    !pattern.is_empty()
        && !pattern.starts_with('/')
        && !pattern.contains(['\\', '\0'])
        && !pattern.split('/').any(|part| part == "..")
}

/// Frozen `_wheel_paths(canonical_root, policy)`, bounded.
pub fn discover(root: &Path, globs: &[String], max_wheels: usize) -> Discovery {
    judged(root, globs, max_wheels).0
}

/// The discovery and how many candidates it judged before it ended.
fn judged(root: &Path, globs: &[String], max_wheels: usize) -> (Discovery, usize) {
    let mut judged = 0;
    let Ok(root) = root.canonicalize() else {
        return (Discovery::Wheels(vec![]), judged);
    };
    let mut found = BTreeSet::new();
    let mut visited = 0;
    for pattern in globs {
        if !contained(pattern) {
            break;
        }
        let refused = Discovery::Refused(json!({"pattern": pattern}));
        let Some(selectors) = selectors(pattern) else {
            return (refused, judged);
        };
        if !root.is_dir() {
            continue;
        }
        let mut judge = |path: &Path| -> ControlFlow<Discovery> {
            judged += 1;
            let Ok(resolved) = path.canonicalize() else {
                return ControlFlow::Break(refused.clone());
            };
            let Ok(relative) = resolved.strip_prefix(&root) else {
                return ControlFlow::Break(refused.clone());
            };
            let symlink = std::fs::symlink_metadata(path)
                .map(|metadata| metadata.file_type().is_symlink())
                .unwrap_or(false);
            if symlink || !resolved.is_file() {
                return ControlFlow::Break(Discovery::Refused(
                    json!({"candidate": path.display().to_string()}),
                ));
            }
            if resolved.extension().is_some_and(|suffix| suffix == "whl") {
                found.insert(relative.to_string_lossy().into_owned());
            }
            if found.len() > max_wheels {
                return ControlFlow::Break(Discovery::Refused(json!({"count": true})));
            }
            ControlFlow::Continue(())
        };
        let mut walk = Walk {
            visited,
            seen: HashSet::new(),
            judge: &mut judge,
            over_limit: Discovery::Refused(json!({"visit_limit": VISIT_LIMIT, "pattern": pattern})),
        };
        let flow = walk.select(&root, &selectors);
        visited = walk.visited;
        if let ControlFlow::Break(refusal) = flow {
            return (refusal, judged);
        }
    }
    (Discovery::Wheels(found.into_iter().collect()), judged)
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

    #[test]
    fn consecutive_recursive_parts_collapse_and_the_walk_stops_at_the_wheel_bound() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        std::fs::create_dir_all(root.join("a/b/c")).unwrap();
        std::fs::write(root.join("a/b/c/x-1-py3-none-any.whl"), b"x").unwrap();
        let globs = |pattern: &str, max: usize| discover(&root, &[pattern.to_owned()], max);
        // `**/**/**` is one recursive selector: the same single match, judged once.
        assert_eq!(selectors("**/**/./**/*.whl").unwrap().len(), 2);
        assert_eq!(
            globs("**/**/**/*.whl", 32),
            Discovery::Wheels(vec!["a/b/c/x-1-py3-none-any.whl".into()])
        );
        // The walk stops at the first wheel beyond the bound: 3 of 50 candidates are judged.
        for index in 0..50 {
            std::fs::write(root.join(format!("a/w{index}-1-py3-none-any.whl")), b"w").unwrap();
        }
        assert_eq!(
            judged(&root, &["a/*.whl".into()], 2),
            (Discovery::Refused(json!({"count": true})), 3)
        );
        assert_eq!(
            globs("a/*.whl", 2),
            Discovery::Refused(json!({"count": true}))
        );
    }

    #[test]
    fn a_discovery_visiting_too_many_directory_entries_is_refused_with_a_bound() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let wide = root.join("wide");
        std::fs::create_dir(&wide).unwrap();
        for index in 0..=VISIT_LIMIT {
            std::fs::File::create(wide.join(format!("{index}.txt"))).unwrap();
        }
        assert_eq!(
            discover(&root, &["**/*.whl".into()], 32),
            Discovery::Refused(json!({"visit_limit": VISIT_LIMIT, "pattern": "**/*.whl"}))
        );
        // A precise pattern lists no directory and is not charged.
        assert_eq!(
            discover(&root, &["wide/0.txt".into()], 32),
            Discovery::Wheels(vec![])
        );
    }
}
