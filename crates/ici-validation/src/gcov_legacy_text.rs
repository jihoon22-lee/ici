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

use crate::python_digits::{DECIMAL, DIGIT_ONLY};

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

/// CPython 3.10.21 `str.isdecimal` value of one character (unicodedata 13.0.0, the generated
/// `python_digits::DECIMAL`), the digit `int()` reads it as.
fn python_decimal(character: char) -> Option<u8> {
    let code = u32::from(character);
    let index = DECIMAL.partition_point(|(_, last, _)| *last < code);
    DECIMAL
        .get(index)
        .filter(|(first, ..)| *first <= code)
        .map(|(first, _, value)| value + (code - first) as u8)
}

/// CPython 3.10.21 `str.isdigit`: every character decimal or one of the generated
/// `python_digits::DIGIT_ONLY` digits (superscripts, circled digits), and at least one.
fn python_isdigit(text: &str) -> bool {
    !text.is_empty()
        && text.chars().all(|character| {
            let code = u32::from(character);
            python_decimal(character).is_some()
                || DIGIT_ONLY
                    .get(DIGIT_ONLY.partition_point(|(_, last)| *last < code))
                    .is_some_and(|(first, _)| *first <= code)
        })
}

/// CPython 3.10 `sys.get_int_max_str_digits()` default.
const INT_MAX_STR_DIGITS: usize = 4300;

/// Why Python `int(text)` raises `ValueError`.
#[derive(Debug, PartialEq)]
enum IntError {
    Invalid,
    /// More decimal digits than `INT_MAX_STR_DIGITS` before the first non-digit.
    Limit(usize),
}

/// Python 3.10 `int(text)` with base 10 (`PyLong_FromUnicodeObject`): each non-ASCII decimal
/// (Nd) character is its digit, non-ASCII whitespace a space, any other non-ASCII character ends
/// the text as an invalid one; then optional whitespace, an optional sign, ASCII digits with
/// single underscores between them (at most 4300 digits, underscores not counted) and optional
/// whitespace. Values beyond `i128` saturate, which keeps the sign every caller here compares
/// against zero.
fn python_int(text: &str) -> Result<i128, IntError> {
    let mut bytes = vec![];
    for character in text.chars() {
        if character.is_ascii() {
            bytes.push(character as u8);
        } else if python_space(character) {
            bytes.push(b' ');
        } else if let Some(digit) = python_decimal(character) {
            bytes.push(b'0' + digit);
        } else {
            bytes.push(b'?');
            break;
        }
    }
    let ascii_space = |byte: &u8| matches!(byte, b' ' | b'\t' | b'\n' | b'\x0b' | b'\x0c' | b'\r');
    let mut rest: &[u8] = &bytes;
    while rest.first().is_some_and(ascii_space) {
        rest = &rest[1..];
    }
    let negative = rest.first() == Some(&b'-');
    if matches!(rest.first(), Some(b'+' | b'-')) {
        rest = &rest[1..];
    }
    if rest.first() == Some(&b'_') {
        return Err(IntError::Invalid);
    }
    let run = rest
        .iter()
        .take_while(|byte| byte.is_ascii_digit() || **byte == b'_')
        .count();
    let (number, tail) = rest.split_at(run);
    if number.windows(2).any(|pair| pair == b"__") || number.last() == Some(&b'_') {
        return Err(IntError::Invalid);
    }
    let digits = number.iter().filter(|byte| byte.is_ascii_digit()).count();
    if digits > INT_MAX_STR_DIGITS {
        return Err(IntError::Limit(digits));
    }
    if digits == 0 || !tail.iter().all(ascii_space) {
        return Err(IntError::Invalid);
    }
    let value = number
        .iter()
        .filter(|byte| byte.is_ascii_digit())
        .fold(0i128, |value, digit| {
            value
                .saturating_mul(10)
                .saturating_add(i128::from(digit - b'0'))
        });
    Ok(if negative { -value } else { value })
}

/// Frozen's `ValueError` text (`str(exception)`) for `int(text)` where `str.isdigit(text)`:
/// every character is a printable digit, so `repr(text)` is the text in single quotes, which
/// `%.200R` cuts to 200 characters.
fn value_error(text: &str, error: &IntError) -> String {
    match error {
        IntError::Invalid => format!(
            "invalid literal for int() with base 10: {}",
            format!("'{text}'").chars().take(200).collect::<String>()
        ),
        IntError::Limit(digits) => format!(
            "Exceeds the limit ({INT_MAX_STR_DIGITS}) for integer string conversion: value has \
             {digits} digits; use sys.set_int_max_str_digits() to increase the limit"
        ),
    }
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

/// Why a legacy gcov text report is not measured.
#[derive(Debug, PartialEq)]
pub(crate) enum Fault {
    /// Frozen's `int()` raises `ValueError` out of `parse_gcov_dir` (`_count_gcov_source` does
    /// not catch it), which crashes frozen's test engine: frozen's exception text.
    Crash(String),
    /// A counted line number past u64. Frozen's `int` counts it; native refuses the reading.
    Range,
}

/// A counted record's line number past u64 (frozen's Python `int` has no bound).
pub(crate) const LINE_PAST_U64: &str = "legacy gcov text line number exceeds the supported integer \
     range 0..=18446744073709551615; C++ coverage was not measured";

/// Frozen `_count_gcov_text` over one report. A `ValueError` frozen raises is `Fault::Crash`
/// wherever it occurs in the report; a counted line number past u64 is `Fault::Range` when
/// nothing crashes.
pub(crate) fn count(bytes: &[u8]) -> Result<Tally, Fault> {
    let mut tally = Tally::default();
    let mut past_u64 = false;
    for line in python_lines(bytes) {
        if line.starts_with("branch") {
            // `_count_gcov_branch`: its `int()` failure is caught (not taken).
            if line.contains(EXCEPTION_UNWIND_ARM) {
                continue;
            }
            let parts = python_split(&line);
            tally.branches += 1;
            if let Some(index) = parts.iter().position(|part| *part == "taken") {
                let value = parts.get(index + 1).copied().unwrap_or("0");
                if python_int(value.trim_end_matches('%')).is_ok_and(|value| value > 0) {
                    tally.covered_branches += 1;
                }
            }
        } else if line.contains(':') {
            // `_count_gcov_source`: `str.isdigit()` then an uncaught `int()`.
            let parts: Vec<&str> = line.splitn(3, ':').collect();
            if parts.len() < 3 {
                continue;
            }
            let count = python_strip(parts[0]);
            let number = python_strip(parts[1]);
            if count.starts_with('-') || !python_isdigit(number) {
                continue;
            }
            let value = python_int(number).map_err(|error| {
                Fault::Crash(format!("ValueError: {}", value_error(number, &error)))
            })?;
            let line_number = u64::try_from(value).ok();
            if count.starts_with('#') {
                tally.miss += 1;
                match line_number {
                    Some(number) => tally.missing.push(number),
                    None => past_u64 = true,
                }
                continue;
            }
            let digits = count.trim_end_matches('*');
            if python_isdigit(digits)
                && python_int(digits).map_err(|error| {
                    Fault::Crash(format!("ValueError: {}", value_error(digits, &error)))
                })? > 0
            {
                tally.covered += 1;
                match line_number {
                    Some(number) => tally.covered_lines.push(number),
                    None => past_u64 = true,
                }
            }
        }
    }
    if past_u64 {
        return Err(Fault::Range);
    }
    Ok(tally)
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
                .and_then(|value| python_int(value).ok())
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
    /// A report frozen's reader crashes on (first in report-name order; frozen reads its
    /// directory order) or, without one, a counted line number past u64. Frozen raises before
    /// it checks for rows or missing sources, so this outranks both.
    pub fault: Option<Fault>,
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
    let mut fault = None;
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
        // Frozen counts only a report that maps to a declared source.
        let tally = match count(&artifact.bytes) {
            Ok(tally) => tally,
            Err(crash @ Fault::Crash(_)) => {
                fault = Some(crash);
                break;
            }
            Err(Fault::Range) => {
                fault = Some(Fault::Range);
                continue;
            }
        };
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
        // Every counted line number, as frozen keeps it (root ruling 2026-11-14: none past u32
        // is dropped).
        file.executed.extend(tally.covered_lines.iter().copied());
        file.missing.extend(tally.missing.iter().copied());
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
        fault,
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
        let tally = count(text).unwrap();
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

    /// The generated CPython 3.10.21 tables (unicodedata 13.0.0): 650 decimal characters and
    /// 128 more `isdigit()` ones. Nag Mundari and Tangsa digits (Unicode 15.0, later than the
    /// frozen interpreter's tables) are neither, so the tables are not Rust's own.
    #[test]
    fn python_310_digit_tables() {
        let decimal: u32 = DECIMAL
            .iter()
            .map(|(first, last, _)| last - first + 1)
            .sum();
        let digit_only: u32 = DIGIT_ONLY
            .iter()
            .map(|(first, last)| last - first + 1)
            .sum();
        assert_eq!((decimal, digit_only), (650, 128));
        assert_eq!(python_decimal('7'), Some(7));
        assert_eq!(python_decimal('\u{0663}'), Some(3));
        assert_eq!(python_decimal('\u{FF19}'), Some(9));
        assert_eq!(python_decimal('\u{1D7CF}'), Some(1));
        assert_eq!(python_decimal('\u{00B2}'), None);
        for later in ['\u{1E4F1}', '\u{16AC1}'] {
            assert!(later.is_numeric(), "{later:?} is a digit to Rust");
            assert_eq!(python_decimal(later), None);
            assert!(!python_isdigit(&later.to_string()));
        }
        assert!(python_isdigit("\u{00B2}\u{00B3}\u{00B9}"));
        assert!(python_isdigit("\u{2460}"));
        assert!(python_isdigit("1\u{0661}\u{00B2}"));
        for text in ["", "1a", "\u{2155}", "1 ", "-1", "1_0"] {
            assert!(!python_isdigit(text), "{text:?}");
        }
    }

    /// Python 3.10.21 `int(text)` (values and refusals measured on the frozen interpreter).
    #[test]
    fn python_310_int() {
        for (text, value) in [
            ("\u{0665}\u{0660}", 50),
            ("+\u{0667}", 7),
            ("-\u{0661}", -1),
            ("\u{0661}_\u{0660}", 10),
            (" 1 ", 1),
            ("\u{2003}1", 1),
            ("\u{A0}1\u{3000}", 1),
            ("007", 7),
        ] {
            assert_eq!(python_int(text), Ok(value), "{text:?}");
        }
        for text in [
            "1__0",
            "_1",
            "1_",
            "",
            "1\u{1C}",
            "+_1",
            "\u{0663}\u{00B2}",
            "1 2",
            "\u{00B2}",
        ] {
            assert_eq!(python_int(text), Err(IntError::Invalid), "{text:?}");
        }
        assert_eq!(python_int(&"1".repeat(4300)), Ok(i128::MAX));
        assert_eq!(python_int(&"1".repeat(4301)), Err(IntError::Limit(4301)));
        assert_eq!(python_int(&"0".repeat(4301)), Err(IntError::Limit(4301)));
        assert_eq!(
            python_int(&"\u{0661}".repeat(4302)),
            Err(IntError::Limit(4302))
        );
        assert_eq!(
            python_int(&format!("{}1", "1_".repeat(4300))),
            Err(IntError::Limit(4301))
        );
        assert_eq!(
            python_int(&format!("{}1", "1_".repeat(4299))),
            Ok(i128::MAX)
        );
        assert_eq!(
            python_int(&format!("{}\u{00B2}", "1".repeat(4301))),
            Err(IntError::Limit(4301))
        );
        assert_eq!(
            python_int(&format!("\u{00B2}{}", "1".repeat(4301))),
            Err(IntError::Invalid)
        );
        assert_eq!(
            value_error("\u{00B9}\u{00B2}", &IntError::Invalid),
            "invalid literal for int() with base 10: '\u{00B9}\u{00B2}'"
        );
        assert_eq!(
            value_error(&format!("{}\u{00B2}", "1".repeat(250)), &IntError::Invalid),
            format!(
                "invalid literal for int() with base 10: '{}",
                "1".repeat(199)
            )
        );
        assert_eq!(
            value_error(&format!("{}\u{00B2}", "1".repeat(198)), &IntError::Invalid),
            format!(
                "invalid literal for int() with base 10: '{}\u{00B2}",
                "1".repeat(198)
            )
        );
        assert_eq!(
            value_error(&"1".repeat(4301), &IntError::Limit(4301)),
            "Exceeds the limit (4300) for integer string conversion: value has 4301 digits; \
             use sys.set_int_max_str_digits() to increase the limit"
        );
    }

    /// Root ruling 2026-11-14: frozen `_count_gcov_source`/`_count_gcov_branch` and
    /// `parse_gcov_functions` read Nd numbers; an `isdigit()` line number or count that `int()`
    /// refuses raises out of the reader (the 'taken' and 'called' values catch it), and a
    /// counted line number past u64 is native's refusal.
    #[test]
    fn frozen_text_numbers_outside_ascii() {
        let text = "    #####:  \u{0661}\u{0660}\u{0661}:  a();\n\
            \x20   \u{1D7CF}*:  \u{0967}\u{0966}\u{0969}:  b();\n\
            \x20   \u{0660}:  104:  c();\n\
            \x20       -:  \u{0661}\u{0660}\u{0665}:  d\n\
            \x20   =====:  18446744073709551616:  e();\n\
            branch  0 taken \u{0665}\u{0660}%\n\
            branch  1 taken +\u{0667}\n\
            branch  2 taken \u{00B2}%\n\
            branch  3 taken \u{0660}%\n";
        let mut long = text.to_owned();
        long.push_str(&format!("branch  4 taken {}%\n", "1".repeat(4301)));
        assert_eq!(
            count(long.as_bytes()),
            Ok(Tally {
                covered: 1,
                miss: 1,
                branches: 5,
                covered_branches: 2,
                missing: vec![101],
                covered_lines: vec![103],
            })
        );
        let calls = format!(
            "function a called \u{0662} returned 1%\nfunction b called \u{00B2} returned 0%\n\
             function c called {} returned 0%\n",
            "1".repeat(4301)
        );
        assert_eq!(
            functions(calls.as_bytes()),
            vec![
                ("a".to_owned(), true),
                ("b".to_owned(), false),
                ("c".to_owned(), false)
            ]
        );
        let crash = |text: &str| match count(text.as_bytes()) {
            Err(Fault::Crash(exception)) => exception,
            other => panic!("{text:?}: {other:?}"),
        };
        assert_eq!(
            crash("    #####:  \u{00B2}:  x();\n"),
            "ValueError: invalid literal for int() with base 10: '\u{00B2}'"
        );
        // Any record whose count is not '-' converts its line number, even an unexecutable one.
        assert_eq!(
            crash("    =====:  \u{2460}:  x();\n"),
            "ValueError: invalid literal for int() with base 10: '\u{2460}'"
        );
        assert_eq!(
            crash("    \u{00B9}\u{00B2}:  105:  x();\n"),
            "ValueError: invalid literal for int() with base 10: '\u{00B9}\u{00B2}'"
        );
        assert_eq!(
            crash(&format!("    #####:  {}:  x();\n", "1".repeat(4301))),
            "ValueError: Exceeds the limit (4300) for integer string conversion: value has 4301 \
             digits; use sys.set_int_max_str_digits() to increase the limit"
        );
        // A '-' count is never converted.
        assert!(count("        -:  \u{00B2}:  x\n".as_bytes()).is_ok());
        assert_eq!(
            count("    #####:  18446744073709551616:  x();\n".as_bytes()),
            Err(Fault::Range)
        );
        assert_eq!(
            count("        1:  18446744073709551616:  x();\n".as_bytes()),
            Err(Fault::Range)
        );
        assert_eq!(
            count("    #####:  18446744073709551615:  x();\n".as_bytes())
                .unwrap()
                .missing,
            vec![u64::MAX]
        );
        // A crash anywhere in the report outranks a line past u64 before it.
        assert_eq!(
            crash("    #####:  18446744073709551616:  x();\n    #####:  \u{00B2}:  y();\n"),
            "ValueError: invalid literal for int() with base 10: '\u{00B2}'"
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
