//! Frozen v0.13.0 legacy gcov text coverage (`analysis/coverage_support.py` `parse_gcov_dir`,
//! `parse_gcov_functions` and `_gcov_source_path`, `engines/test_coverage.py`
//! `_consume_cpp_coverage`) for the legacy C++ coverage path, used when `gcov --help` does not
//! advertise `--json-format`.
//!
//! Native `gcov_text` keeps its strict parser for the next path. Frozen's text reader is
//! lenient: it reads every `*.gcov` report that maps to a declared source, ignores lines it does
//! not understand, keeps one row per report and leaves a declared source without executable
//! records out of the rows (`legacy gcov text evidence is missing N source(s)`).
use crate::{
    ValidationPlan,
    artifacts::CapturedArtifacts,
    coverage::{Counts, CoverageReport, FileCoverage},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
};

/// Frozen `_EXCEPTION_UNWIND_ARM`.
const EXCEPTION_UNWIND_ARM: &str = "(throw)";

/// Python `str.isspace` (`str.split()`/`str.strip()` without arguments).
fn python_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

fn python_strip(text: &str) -> &str {
    text.trim_matches(python_space)
}

fn python_split(text: &str) -> Vec<&str> {
    text.split(python_space)
        .filter(|part| !part.is_empty())
        .collect()
}

/// Python `str.isdigit` for the ASCII digits gcov writes.
fn ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

/// Python `int(text)` (`None` where it raises `ValueError`): an optional sign, then ASCII
/// digits with single underscores between them. Values beyond `i128` saturate, which keeps the
/// sign that every caller here compares against zero.
fn python_int(text: &str) -> Option<i128> {
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
        || !digits
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'_')
    {
        return None;
    }
    let value = digits
        .bytes()
        .filter(u8::is_ascii_digit)
        .fold(0i128, |value, digit| {
            value
                .saturating_mul(10)
                .saturating_add(i128::from(digit - b'0'))
        });
    Some(if negative { -value } else { value })
}

/// `Path.read_text(encoding="utf-8", errors="replace")` then `str.splitlines()`: universal
/// newlines first, then every Python line boundary.
fn python_lines(bytes: &[u8]) -> Vec<String> {
    let text = String::from_utf8_lossy(bytes)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let mut lines: Vec<String> = text
        .split([
            '\n', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
        ])
        .map(str::to_owned)
        .collect();
    if text.is_empty()
        || text.ends_with([
            '\n', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
        ])
    {
        lines.pop();
    }
    lines
}

/// Frozen `_GcovCounts` and `_count_gcov_text`.
#[derive(Debug, Default, PartialEq)]
pub(crate) struct Tally {
    pub covered: u64,
    pub miss: u64,
    pub branches: u64,
    pub covered_branches: u64,
    pub missing: Vec<u64>,
    pub covered_lines: Vec<u64>,
}

pub(crate) fn count(bytes: &[u8]) -> Tally {
    let mut tally = Tally::default();
    for line in python_lines(bytes) {
        if line.starts_with("branch") {
            // `_count_gcov_branch`.
            if line.contains(EXCEPTION_UNWIND_ARM) {
                continue;
            }
            let parts = python_split(&line);
            tally.branches += 1;
            if let Some(index) = parts.iter().position(|part| *part == "taken") {
                let value = parts.get(index + 1).copied().unwrap_or("0");
                if python_int(value.trim_end_matches('%')).is_some_and(|value| value > 0) {
                    tally.covered_branches += 1;
                }
            }
        } else if line.contains(':') {
            // `_count_gcov_source`.
            let parts: Vec<&str> = line.splitn(3, ':').collect();
            if parts.len() < 3 {
                continue;
            }
            let count = python_strip(parts[0]);
            let number = python_strip(parts[1]);
            if count.starts_with('-') || !ascii_digits(number) {
                continue;
            }
            // gcov writes 32-bit line numbers; a larger one is no source line.
            let Ok(number) = number.parse::<u64>() else {
                continue;
            };
            if count.starts_with('#') {
                tally.miss += 1;
                tally.missing.push(number);
                continue;
            }
            let digits = count.trim_end_matches('*');
            if ascii_digits(digits) && python_int(digits).is_some_and(|value| value > 0) {
                tally.covered += 1;
                tally.covered_lines.push(number);
            }
        }
    }
    tally
}

/// Frozen `parse_gcov_functions` over one report: `(name, called > 0)` per `function` record.
pub(crate) fn functions(bytes: &[u8]) -> Vec<(String, bool)> {
    python_lines(bytes)
        .iter()
        .filter(|line| line.starts_with("function "))
        .filter_map(|line| {
            let parts = python_split(line);
            if parts.len() < 3 {
                return None;
            }
            let called = parts
                .iter()
                .position(|part| *part == "called")
                .and_then(|index| parts.get(index + 1))
                .and_then(|value| python_int(value))
                .unwrap_or(0);
            Some((parts[1].to_owned(), called > 0))
        })
        .collect()
}

/// Python 3.10 `Path.resolve()` (non-strict): symlinks of the existing prefix are followed,
/// the rest is normalized lexically; `None` for a symlink loop (pathlib's `RuntimeError`, which
/// frozen's gcov JSON mapping skips), here more than 40 links like the kernel's `ELOOP`.
pub(crate) fn resolve(path: &Path) -> Option<PathBuf> {
    let mut resolved = PathBuf::from("/");
    let mut parts: Vec<std::ffi::OsString> = path
        .components()
        .rev()
        .map(|part| part.as_os_str().to_owned())
        .collect();
    let mut links = 0;
    while let Some(part) = parts.pop() {
        match Path::new(&part).components().next() {
            Some(Component::RootDir | Component::Prefix(_)) => resolved = PathBuf::from("/"),
            Some(Component::ParentDir) => {
                resolved.pop();
            }
            Some(Component::Normal(name)) => {
                let next = resolved.join(name);
                match std::fs::read_link(&next) {
                    Ok(target) => {
                        links += 1;
                        if links > 40 {
                            return None;
                        }
                        parts.extend(
                            target
                                .components()
                                .rev()
                                .map(|part| part.as_os_str().to_owned()),
                        );
                    }
                    Err(_) => resolved = next,
                }
            }
            Some(Component::CurDir) | None => {}
        }
    }
    Some(resolved)
}

/// Frozen `_in_scope_relative`.
fn in_scope(candidate: &str, sources: &BTreeSet<&str>, root: &Path) -> Option<String> {
    let path = Path::new(candidate);
    let absolute = resolve(&if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    })?;
    let relative = absolute.strip_prefix(root).ok()?.to_str()?.to_owned();
    let relative = if relative.is_empty() {
        ".".to_owned()
    } else {
        relative
    };
    sources.contains(relative.as_str()).then_some(relative)
}

/// Frozen `_gcov_declared_source`: the `Source:` value of the first four lines.
fn declared_source(bytes: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(bytes);
    let mut rest: &str = &text;
    for _ in 0..4 {
        if rest.is_empty() {
            return None;
        }
        let end = rest
            .find(['\n', '\r'])
            .map(|index| {
                if rest[index..].starts_with("\r\n") {
                    index + 2
                } else {
                    index + 1
                }
            })
            .unwrap_or(rest.len());
        let line = &rest[..end];
        rest = &rest[end..];
        if let Some(marker) = line.find("Source:") {
            return Some(python_strip(&line[marker + "Source:".len()..]).to_owned());
        }
    }
    None
}

/// Frozen `_match_source_suffix` over `PurePosixPath(candidate).parts`.
fn suffix_source(candidate: &str, sources: &BTreeSet<&str>) -> Option<String> {
    let parts: Vec<&str> = candidate
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    (0..parts.len())
        .map(|start| parts[start..].join("/"))
        .find(|suffix| sources.contains(suffix.as_str()))
}

/// Frozen `_gcov_source_path` for one report file name and its bytes.
pub(crate) fn source(
    name: &str,
    bytes: &[u8],
    sources: &BTreeSet<&str>,
    root: &Path,
) -> Option<String> {
    let candidate = name.strip_suffix(".gcov").unwrap_or(name).replace('#', "/");
    if sources.contains(candidate.as_str()) {
        return Some(candidate);
    }
    if let Some(relative) = in_scope(&candidate, sources, root) {
        return Some(relative);
    }
    let declared = declared_source(bytes)?;
    if Path::new(&declared).is_absolute() {
        in_scope(&declared, sources, root)
    } else {
        suffix_source(&declared, sources)
    }
}

/// Frozen `test_coverage.py:241-246`.
pub(crate) fn missing_message(missing: &[&str]) -> String {
    let preview = missing
        .iter()
        .take(8)
        .copied()
        .collect::<Vec<_>>()
        .join(", ");
    let suffix = if missing.len() > 8 {
        format!(" (+{} more)", missing.len() - 8)
    } else {
        String::new()
    };
    format!(
        "legacy gcov text evidence is missing {} source(s): {preview}{suffix}",
        missing.len()
    )
}

/// The legacy gcov text reading: the coverage report (one row per report in frozen; two reports
/// of one source are summed here), frozen's `coverage_provenance.cpp` and the sources the rows
/// miss.
pub(crate) struct Reading {
    pub report: CoverageReport,
    pub provenance: Value,
    pub missing: Option<String>,
}

pub(crate) fn read(plan: &ValidationPlan, artifacts: &CapturedArtifacts, prefix: &str) -> Reading {
    let sources: BTreeSet<&str> = plan
        .context
        .source_files
        .iter()
        .map(String::as_str)
        .collect();
    let root = resolve(&plan.context.root).unwrap_or_else(|| plan.context.root.clone());
    let mut files: BTreeMap<String, FileCoverage> = BTreeMap::new();
    let mut function_rows = vec![];
    // Frozen globs the directory; a report it cannot read is skipped (`except OSError:
    // continue`), as is a capture error here.
    for artifact in artifacts
        .files
        .values()
        .filter(|artifact| artifact.id.starts_with(prefix))
    {
        let name = &artifact.id[prefix.len()..];
        let Some(relative) = source(name, &artifact.bytes, &sources, &root) else {
            continue;
        };
        for (function, covered) in functions(&artifact.bytes) {
            function_rows.push(json!({"file": relative, "name": function, "start_line": 1,
                "end_line": 1, "covered": covered, "missing_lines": []}));
        }
        let tally = count(&artifact.bytes);
        if tally.covered + tally.miss == 0 {
            continue;
        }
        let file = files
            .entry(relative.clone())
            .or_insert_with(|| FileCoverage {
                path: relative,
                executed: BTreeSet::new(),
                missing: BTreeSet::new(),
                counts: Counts {
                    covered_functions: None,
                    total_functions: None,
                    ..Counts::default()
                },
                functions: None,
            });
        file.counts.covered_lines += tally.covered;
        file.counts.total_lines += tally.covered + tally.miss;
        file.counts.covered_branches += tally.covered_branches;
        file.counts.total_branches += tally.branches;
        file.executed.extend(
            tally
                .covered_lines
                .iter()
                .filter_map(|line| u32::try_from(*line).ok()),
        );
        file.missing.extend(
            tally
                .missing
                .iter()
                .filter_map(|line| u32::try_from(*line).ok()),
        );
    }
    let mut counts = Counts::default();
    for file in files.values() {
        counts.covered_lines += file.counts.covered_lines;
        counts.total_lines += file.counts.total_lines;
        counts.covered_branches += file.counts.covered_branches;
        counts.total_branches += file.counts.total_branches;
    }
    let covered_functions = function_rows
        .iter()
        .filter(|row| row["covered"] == true)
        .count() as u64;
    (counts.covered_functions, counts.total_functions) = if function_rows.is_empty() {
        (None, None)
    } else {
        (Some(covered_functions), Some(function_rows.len() as u64))
    };
    let missing: Vec<&str> = sources
        .iter()
        .copied()
        .filter(|path| !files.contains_key(*path))
        .collect();
    let provenance = json!({
        "format": "gcov-text",
        "expected_sources": sources.len(),
        "covered_sources": files.len(),
        "function_geometry": "line-1-fallback",
        "source_mapping": "legacy-header-suffix",
        "throw_branches_excluded": true,
        "limitations": [
            "function columns and end lines are unavailable",
            "source relocation uses an exact known-suffix fallback",
        ],
    });
    Reading {
        report: CoverageReport {
            files,
            counts,
            function_rows,
            excluded_files: vec![],
            limitations: vec![],
        },
        provenance,
        missing: (!missing.is_empty()).then(|| missing_message(&missing)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Frozen `_count_gcov_text` (coverage_support.py:357-363): `#####` is missed, `=====` and
    /// `$$$$$` are neither, `-` is not executable, `N*` is covered, a `(throw)` arm is not a
    /// branch and `never executed` is an untaken branch.
    #[test]
    fn frozen_text_counts() {
        let text = b"        -:    0:Source:src/a.cpp\n\
            function _Z1fv called 2 returned 100% blocks executed 100%\n\
            \x20       2:    1:int f() {\n\
            \x20   #####:    2:  x();\n\
            \x20   =====:    3:  y();\n\
            \x20   $$$$$:    4:  z();\n\
            \x20      1*:    5:  w();\n\
            \x20       0:    6:  v();\n\
            branch  0 taken 50%\n\
            branch  1 taken 0%\n\
            branch  2 never executed\n\
            branch  3 taken 1 (throw)\n\
            branch  4 taken\n\
            call    0 returned 100%\n";
        let tally = count(text);
        assert_eq!(
            tally,
            Tally {
                covered: 2,
                miss: 1,
                branches: 4,
                covered_branches: 1,
                missing: vec![2],
                covered_lines: vec![1, 5],
            }
        );
        assert_eq!(functions(text), vec![("_Z1fv".to_owned(), true)]);
        assert_eq!(
            functions(b"function _Z1gv called x\nfunction _Z1hv called\nfunction a\n"),
            vec![("_Z1gv".to_owned(), false), ("_Z1hv".to_owned(), false)]
        );
    }

    /// Frozen `_gcov_source_path` (coverage_support.py:284-300): the mangled name, then the
    /// `Source:` header (absolute in the project, or the first known suffix).
    #[test]
    fn frozen_text_source_mapping() {
        let sources: BTreeSet<&str> = ["src/a.cpp", "lib/src/b.cpp"].into_iter().collect();
        let root = Path::new("/nonexistent/project");
        assert_eq!(
            source("src#a.cpp.gcov", b"", &sources, root).as_deref(),
            Some("src/a.cpp")
        );
        assert_eq!(
            source("#nonexistent#project#src#a.cpp.gcov", b"", &sources, root).as_deref(),
            Some("src/a.cpp")
        );
        assert_eq!(
            source(
                "^#^#lib#src#b.cpp.gcov",
                b"        -:    0:Source:../../lib/src/b.cpp\n",
                &sources,
                root
            )
            .as_deref(),
            Some("lib/src/b.cpp")
        );
        assert_eq!(
            source(
                "x.gcov",
                b"        -:    0:Source:/elsewhere/src/a.cpp\n",
                &sources,
                root
            ),
            None
        );
        assert_eq!(
            source(
                "x.gcov",
                b"a\nb\nc\nd\n        -:    0:Source:src/a.cpp\n",
                &sources,
                root
            ),
            None
        );
        assert_eq!(
            missing_message(&["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]),
            "legacy gcov text evidence is missing 10 source(s): a, b, c, d, e, f, g, h (+2 more)"
        );
    }
}
