//! Bounded native sanitizer normalization over immutable captured source coordinates.
use crate::{ValidationError, ValidationKind, ValidationPlan, planning, python::finding};
use ici_analysis::unicode::{python_split, python_strip};
use ici_domain::{AnalysisOutput, Finding, InspectionTarget, Measurement, SourceSpan};
use ici_execution::TaskOutcome;
use regex::Regex;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

#[derive(Clone, Debug, serde::Serialize)]
pub struct SanitizerDiagnostic {
    pub kind: String,
    pub tool_name: String,
    pub defect: String,
    pub rule_id: String,
    pub message: String,
    pub primary_location: Option<SourceSpan>,
    pub related_locations: Vec<SourceSpan>,
    pub frames_observed: usize,
    pub project_frames: usize,
    pub external_locations: Vec<ExternalLocation>,
    /// The CTest case whose `--output-on-failure` block printed the report (frozen
    /// `_SanitizerFinding.test_name`); absent outside a CTest sanitizer suite.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub test_name: Option<String>,
    #[serde(skip)]
    pub finding: Finding,
}
#[derive(Clone, Debug, serde::Serialize)]
pub struct ExternalLocation {
    pub path: String,
    pub start_line: u32,
    pub native_column: Option<u32>,
    pub column_unit: String,
    pub label: String,
}

const UNCONFIRMED_LINE: &str =
    "diagnostic source line could not be confirmed against immutable UTF-8 input";

/// Frozen's patterns (`_ERROR_RE` and the rest) over Python `str`, where `\\s` also holds
/// U+001C..U+001F. A runtime error's location is its own group: a later `path:line` in the message
/// is not it, and a relative path holds no whitespace.
struct Patterns {
    error: Regex,
    warning: Regex,
    runtime: Regex,
    summary: Regex,
    frame: Regex,
    location: Regex,
    address: Regex,
    number: Regex,
}
static PATTERNS: LazyLock<Patterns> = LazyLock::new(|| {
    const PATH: &str = r"(?:[A-Za-z]:[\\/][^:\r\n]*|/[^:\r\n]*|(?:\.{1,2}[\\/])?[^:\s\x1c-\x1f]+)";
    let constant = |pattern: &str| Regex::new(pattern).expect("constant regex");
    Patterns {
        error: constant(
            r"^[\s\x1c-\x1f]*(?:==\d+==)?[\s\x1c-\x1f]*ERROR:[\s\x1c-\x1f]*(AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer):[\s\x1c-\x1f]*([^\s\x1c-\x1f].*)$",
        ),
        warning: constant(
            r"^[\s\x1c-\x1f]*(?:==\d+==)?[\s\x1c-\x1f]*WARNING:[\s\x1c-\x1f]*(ThreadSanitizer):[\s\x1c-\x1f]*([^\s\x1c-\x1f].*)$",
        ),
        runtime: constant(&format!(
            r"^({PATH}:[1-9]\d*(?::[1-9]\d*)?):[\s\x1c-\x1f]*runtime error:[\s\x1c-\x1f]*([^\s\x1c-\x1f].*)$"
        )),
        summary: constant(
            r"^[\s\x1c-\x1f]*(?:==\d+==)?[\s\x1c-\x1f]*SUMMARY:[\s\x1c-\x1f]*(AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|ThreadSanitizer):[\s\x1c-\x1f]*([^\s\x1c-\x1f].*)$",
        ),
        frame: constant(r"^[\s\x1c-\x1f]*#(\d+)[\s\x1c-\x1f]+"),
        location: constant(&format!(r"({PATH}):([1-9]\d*)(?::([1-9]\d*))?")),
        address: constant(r"(?i)0x[0-9a-f]+"),
        number: constant(r"\d+"),
    }
});

/// How frozen `_scan_locations` classifies a report line, in its order.
#[derive(Clone, Copy, PartialEq)]
enum LineKind {
    Runtime,
    Frame,
    Marker,
}
impl LineKind {
    fn of(line: &str) -> Option<Self> {
        if PATTERNS.runtime.is_match(line) {
            Some(Self::Runtime)
        } else if PATTERNS.frame.is_match(line) {
            Some(Self::Frame)
        } else if PATTERNS.error.is_match(line)
            || PATTERNS.warning.is_match(line)
            || PATTERNS.summary.is_match(line)
        {
            Some(Self::Marker)
        } else {
            None
        }
    }
}
/// Frozen next `_MARKERS` (`_ERROR_RE`, `_TSAN_WARNING_RE`, `_SUMMARY_RE`, `_RUNTIME_RE`).
pub(crate) fn is_report_marker(line: &str) -> bool {
    matches!(
        LineKind::of(line),
        Some(LineKind::Runtime | LineKind::Marker)
    )
}
/// The location frozen reads from a report line: the last one on it, and for a runtime error the
/// last one of its location group.
fn last_location<'a>(line: &'a str, kind: LineKind) -> Option<regex::Captures<'a>> {
    let searched = if kind == LineKind::Runtime {
        PATTERNS.runtime.captures(line)?.get(1)?.as_str()
    } else {
        line
    };
    PATTERNS.location.captures_iter(searched).last()
}
/// The project-relative paths a report's located lines name, as `parse_diagnostics` reads them
/// under frozen's reading.
pub(crate) fn named_project_paths(transcript: &str, root: &std::path::Path) -> Vec<String> {
    let mut paths = vec![];
    for line in crate::make_check::lines(transcript) {
        if let Some(kind) = LineKind::of(line)
            && let Some(capture) = last_location(line, kind)
            && let FrozenPath::Project(path) = frozen_project_path(&capture[1], root)
        {
            paths.push(path);
        }
    }
    paths
}
/// Python `int()` of a `\\d+` group, for a line or column: past 2**31-1 names no location.
fn python_int(digits: &str) -> Option<u32> {
    let mut value = 0u32;
    for digit in digits.chars() {
        value = value
            .checked_mul(10)?
            .checked_add(u32::from(ici_analysis::unicode::decimal_digit(digit)?))?;
    }
    (value <= i32::MAX as u32).then_some(value)
}

/// What frozen `canonical_project_path` makes of a report's path.
#[derive(Debug, PartialEq)]
enum FrozenPath {
    Project(String),
    /// `ValueError`: outside the project, escaping it, or an ambiguous drive-relative path.
    External,
    /// A POSIX `//` root: `project_root / relative` leaves the project, so no location is named.
    Unreadable,
}
/// Frozen `canonical_project_path`: lexical, with a relative path taken from the project root and
/// never from a process directory.
fn frozen_project_path(raw: &str, root: &std::path::Path) -> FrozenPath {
    let drive = |value: &str| {
        let bytes = value.as_bytes();
        bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':'
    };
    let slashed = raw.replace('\\', "/");
    let trimmed = slashed.trim_end_matches('/');
    let mut value = if trimmed.is_empty() { "." } else { trimmed };
    if drive(value) && value.as_bytes().get(2) != Some(&b'/') {
        return FrozenPath::External;
    }
    let root = root.to_string_lossy().replace('\\', "/");
    let stripped = root.trim_end_matches('/');
    let root = if stripped.is_empty() { "/" } else { stripped };
    if value.starts_with('/') || drive(value) {
        // A POSIX project root never equals a drive path, however that is case-folded.
        if value == root {
            value = ".";
        } else if root == "/" {
            value = value.trim_start_matches('/');
        } else if let Some(below) = value
            .strip_prefix(root)
            .and_then(|below| below.strip_prefix('/'))
        {
            value = below;
        } else {
            return FrozenPath::External;
        }
    }
    if value.starts_with("//") && !value.starts_with("///") {
        return FrozenPath::Unreadable;
    }
    let mut parts: Vec<&str> = vec![];
    for part in value.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return FrozenPath::External;
                }
            }
            part if part.len() == 2 && drive(part) => {}
            part => parts.push(part),
        }
    }
    FrozenPath::Project(if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join("/")
    })
}

pub fn parse(text: &str, plan: &ValidationPlan) -> Result<Vec<Finding>, ValidationError> {
    parse_diagnostics(text, plan).map(|items| items.into_iter().map(|item| item.finding).collect())
}
pub fn parse_diagnostics(
    text: &str,
    plan: &ValidationPlan,
) -> Result<Vec<SanitizerDiagnostic>, ValidationError> {
    if text.len() > 1024 * 1024 || text.contains('\0') {
        return Err(ValidationError(
            "sanitizer transcript exceeds bounded UTF-8/NUL contract".into(),
        ));
    }
    // The legacy engines and the frozen next path read frozen `parse_sanitizer_diagnostics`
    // itself: its project path and line model, and the producer's raw byte column.
    let frozen_reading = plan.context.options.legacy_unlocated_sanitizer_scope
        || plan.context.options.frozen_next_sanitizer;
    let lines = crate::make_check::lines(text);
    let mut starters = vec![];
    for (index, line) in lines.iter().enumerate() {
        if let Some(capture) = PATTERNS
            .error
            .captures(line)
            .or_else(|| PATTERNS.warning.captures(line))
        {
            starters.push((index, capture[1].to_string(), capture[2].to_string()));
        } else if let Some(capture) = PATTERNS.runtime.captures(line) {
            starters.push((
                index,
                "UndefinedBehaviorSanitizer".into(),
                capture[2].to_string(),
            ));
        }
    }
    if starters.is_empty() {
        for (index, line) in lines.iter().enumerate() {
            if let Some(capture) = PATTERNS.summary.captures(line) {
                starters.push((index, capture[1].to_string(), capture[2].to_string()));
            }
        }
    }
    if starters.len() > 64 {
        return Err(ValidationError(
            "sanitizer diagnostic count exceeds 64".into(),
        ));
    }
    // `ctest --output-on-failure` prints a case's output right after its result line, before any
    // later `Start`/result line.
    let ctest_case = (plan.context.options.sanitizer_suite_kind == Some(ValidationKind::Ctest))
        .then(|| {
            Regex::new(r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(.+?)\s*\.{3,}").expect("constant regex")
        });
    let start_line = Regex::new(r"^\s*Start\s+\d+:").expect("constant regex");
    let mut findings = vec![];
    for (index, (start, tool, description)) in starters.iter().enumerate() {
        let test_name = ctest_case.as_ref().and_then(|case| {
            lines[..*start]
                .iter()
                .rev()
                .take_while(|line| !start_line.is_match(line))
                .find_map(|line| case.captures(line).map(|capture| capture[1].to_string()))
        });
        let end = starters.get(index + 1).map_or(lines.len(), |item| item.0);
        let mut runtime_locations = vec![];
        let mut frames = vec![];
        let mut markers = vec![];
        let mut frames_seen = 0;
        let mut limitations = vec![];
        let mut external_locations = vec![];
        for line in &lines[*start..end] {
            let Some(kind) = LineKind::of(line) else {
                continue;
            };
            if kind == LineKind::Frame {
                frames_seen += 1;
                if frames_seen > 32 {
                    return Err(ValidationError(
                        "sanitizer stack exceeds 32 frame limit".into(),
                    ));
                }
            }
            let Some(capture) = last_location(line, kind) else {
                continue;
            };
            // Frozen `_normalized_location`: a line or column past 2**31-1 names no location
            // (the frame still counts); it does not refuse the report.
            let Some(row) = python_int(&capture[2]) else {
                continue;
            };
            let column = match capture.get(3) {
                None => None,
                Some(column) => match python_int(column.as_str()) {
                    Some(column) => Some(column),
                    None => continue,
                },
            };
            let label = match kind {
                LineKind::Runtime => "runtime error".to_string(),
                LineKind::Frame => frame_label(line, &capture),
                LineKind::Marker => "sanitizer diagnostic".into(),
            };
            let path = if frozen_reading {
                match frozen_project_path(&capture[1], &plan.context.root) {
                    FrozenPath::Project(path) => Some(path),
                    FrozenPath::External => None,
                    FrozenPath::Unreadable => {
                        limitations.push(UNCONFIRMED_LINE.into());
                        continue;
                    }
                }
            } else {
                planning::relative(&capture[1], &plan.context.root, &plan.context.cwd)
                    .ok()
                    .filter(|path| {
                        // A build-directory-relative frame that names no confirmed project line
                        // and escapes the project from its root
                        // (`../../../../src/libsanitizer/...`, the runtime's own sources) is an
                        // external frame.
                        plan.source_coordinates.contains_line(path, row) == Some(true)
                            || planning::relative(
                                &capture[1],
                                &plan.context.root,
                                &plan.context.root,
                            )
                            .is_ok()
                    })
            };
            let mut span = if let Some(path) = path {
                if frozen_reading {
                    // Frozen `_safe_source_location`: a row past the file's `splitlines()` or a
                    // column past that line's bytes names no location; the column stays raw.
                    let Some(bytes) = plan.source_coordinates.python_line_bytes(&path, row) else {
                        limitations.push(UNCONFIRMED_LINE.into());
                        continue;
                    };
                    if column.is_some_and(|column| column > bytes.saturating_add(1)) {
                        continue;
                    }
                    let mut span = SourceSpan::line(path, row);
                    span.start_column = column;
                    span
                } else {
                    if plan.source_coordinates.contains_line(&path, row) != Some(true) {
                        limitations.push(UNCONFIRMED_LINE.into());
                        continue;
                    }
                    let mut span = SourceSpan::line(path, row);
                    if let Some(column) = column {
                        // A column past the line's end names no location; native converts the
                        // producer's byte column to a character column.
                        match plan.source_coordinates.byte_column(&span.path, row, column) {
                            Ok(converted) => span.start_column = converted,
                            Err(_) => continue,
                        }
                    }
                    span
                }
            } else {
                external_locations.push(ExternalLocation {
                    path: "[external]".into(),
                    start_line: row,
                    native_column: column,
                    column_unit: "producer-native".into(),
                    label: label.clone(),
                });
                SourceSpan::line("[external]", row)
            };
            span.label = label;
            // An external span keeps no column, yet frozen tells two frames apart by it.
            let key_column = if span.path == "[external]" {
                column
            } else {
                span.start_column
            };
            let located = (span, key_column);
            match kind {
                LineKind::Runtime => runtime_locations.push(located),
                LineKind::Frame => frames.push(located),
                LineKind::Marker => markers.push(located),
            }
        }
        let owned = |located: &&(SourceSpan, Option<u32>)| located.0.path != "[external]";
        let primary = runtime_locations
            .iter()
            .find(owned)
            .or_else(|| frames.iter().find(owned))
            .or_else(|| markers.iter().find(owned))
            .map(|located| located.0.clone());
        let (kind, defect) = taxonomy(tool, description);
        let message = format!("{tool} detected {}", defect.replace('-', " "));
        let anchor = primary
            .clone()
            .unwrap_or_else(|| SourceSpan::line(crate::cpp::source(plan, "sanitizer"), 1));
        let mut item = finding(
            plan,
            &format!("ici.sanitize.{kind}.{defect}"),
            &message,
            &anchor.path,
            anchor.start_line,
        );
        item.primary_location = anchor;
        item.native_rule_id = defect.clone();
        item.category = "runtime".into();
        if primary.is_none() {
            limitations.push("the diagnostic named no confirmed project source location".into());
        }
        let include_markers = !runtime_locations
            .iter()
            .chain(&frames)
            .any(|located| owned(&located));
        let project_frames = frames.iter().filter(owned).count();
        let primary_key = primary
            .as_ref()
            .map(|span| (span.path.clone(), span.start_line, span.start_column));
        let mut unique = BTreeSet::new();
        let mut related_locations = vec![];
        for (span, key_column) in runtime_locations
            .into_iter()
            .chain(frames)
            .chain(if include_markers { markers } else { vec![] })
        {
            let key = (span.path.clone(), span.start_line, key_column);
            if Some(&key) != primary_key.as_ref() && unique.insert(key) {
                if span.path != "[external]" {
                    item.related_locations.push(span.clone());
                }
                related_locations.push(span);
            }
        }
        limitations.sort();
        limitations.dedup();
        item.limitations = limitations;
        findings.push(SanitizerDiagnostic {
            kind,
            tool_name: tool.clone(),
            defect,
            rule_id: item.rule_id.clone(),
            message,
            primary_location: primary,
            related_locations,
            frames_observed: frames_seen,
            project_frames,
            external_locations,
            test_name,
            finding: item,
        });
    }
    Ok(findings)
}
/// Frozen `_function_label`.
fn frame_label(line: &str, location: &regex::Captures<'_>) -> String {
    // `int(frame_index)`: `#007` and `#٣` are frames 7 and 3.
    let index = PATTERNS
        .frame
        .captures(line)
        .and_then(|capture| capture.get(1))
        .map_or_else(String::new, |index| {
            let digits: String = index
                .as_str()
                .chars()
                .filter_map(ici_analysis::unicode::decimal_digit)
                .map(|digit| char::from(b'0' + digit))
                .collect();
            let significant = digits.trim_start_matches('0');
            if significant.is_empty() {
                "0".to_owned()
            } else {
                significant.to_owned()
            }
        });
    let prefix = python_strip(&line[..location.get(1).expect("matched source path").start()]);
    // Only an ASan/UBSan/LSan "... in <function>" frame names its function; a TSan frame
    // ("#0 free <path>:<line> (lib+0x..)") is just "frame #<n>".
    let function = prefix
        .rsplit_once(" in ")
        .map_or("", |(_, function)| python_strip(function));
    let function =
        ici_analysis::unicode::python_bounded_replace(function, &PATTERNS.address, "address");
    let function = python_split(&function)
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(480)
        .collect::<String>();
    // `f"frame #{frame_index}: {function}".rstrip(": ")`, then `label[:512]`.
    format!("frame #{index}: {function}")
        .trim_end_matches([':', ' '])
        .chars()
        .take(512)
        .collect()
}
fn taxonomy(tool: &str, description: &str) -> (String, String) {
    // Frozen `_defect`: `" ".join(summary.casefold().split())`.
    let folded = ici_analysis::unicode::fold(description);
    let lowered = python_split(&folded).collect::<Vec<_>>().join(" ");
    let kind = if tool == "ThreadSanitizer" {
        "tsan"
    } else if tool == "LeakSanitizer" || lowered.contains("leak") {
        "lsan"
    } else if tool == "UndefinedBehaviorSanitizer" {
        "ubsan"
    } else {
        "asan"
    };
    let definitions = if kind == "tsan" {
        vec![
            ("data race", "data-race"),
            ("lock-order-inversion", "lock-order-inversion"),
            ("thread leak", "thread-leak"),
            ("mutex destroyed while busy", "mutex-destroyed-while-busy"),
            ("unlock of an unlocked mutex", "invalid-mutex-unlock"),
            ("double lock of a mutex", "double-mutex-lock"),
        ]
    } else {
        vec![
            ("heap-use-after-free", "heap-use-after-free"),
            ("stack-use-after-return", "stack-use-after-return"),
            ("stack-use-after-scope", "stack-use-after-scope"),
            ("heap-buffer-overflow", "heap-buffer-overflow"),
            ("stack-buffer-overflow", "stack-buffer-overflow"),
            ("global-buffer-overflow", "global-buffer-overflow"),
            ("container-overflow", "container-overflow"),
            ("initialization-order-fiasco", "initialization-order-fiasco"),
            ("alloc-dealloc-mismatch", "alloc-dealloc-mismatch"),
            ("new-delete-type-mismatch", "new-delete-type-mismatch"),
            ("attempting double-free", "double-free"),
            ("attempting free on address", "invalid-free"),
            ("negative-size-param", "negative-size-param"),
            ("detected memory leaks", "memory-leak"),
            ("direct leak", "memory-leak"),
            ("indirect leak", "memory-leak"),
            ("signed integer overflow", "signed-integer-overflow"),
            ("unsigned integer overflow", "unsigned-integer-overflow"),
            ("division by zero", "division-by-zero"),
            ("shift exponent", "invalid-shift"),
            ("shift base", "invalid-shift"),
            ("load of misaligned address", "misaligned-load"),
            ("store to misaligned address", "misaligned-store"),
            (
                "reference binding to misaligned address",
                "misaligned-reference",
            ),
            ("member access within", "invalid-member-access"),
            ("index ", "out-of-bounds-index"),
            ("null pointer", "null-pointer"),
            ("applying non-zero offset", "invalid-pointer-offset"),
            ("implicit conversion", "implicit-conversion"),
            ("execution reached an unreachable", "unreachable"),
        ]
    };
    let defect = definitions
        .iter()
        .find(|(prefix, _)| lowered.starts_with(prefix) || lowered.contains(&format!(": {prefix}")))
        .map(|(_, defect)| (*defect).to_string())
        .unwrap_or_else(|| {
            if kind == "tsan" {
                "thread-safety-defect".into()
            } else {
                let slug = Regex::new(r"[^a-z0-9]+").expect("constant regex");
                let stable = ici_analysis::unicode::python_bounded_replace(
                    &lowered,
                    &PATTERNS.address,
                    "address",
                );
                let stable = ici_analysis::unicode::python_bounded_replace(
                    &stable,
                    &PATTERNS.number,
                    "number",
                );
                let stable = stable
                    .split(" on address")
                    .next()
                    .unwrap_or(&stable)
                    .split(" at address")
                    .next()
                    .unwrap_or(&stable)
                    .split(':')
                    .next()
                    .unwrap_or(&stable);
                let slug = slug
                    .replace_all(stable, "-")
                    .trim_matches('-')
                    .chars()
                    .take(80)
                    .collect::<String>();
                if slug.is_empty() {
                    "runtime-defect".into()
                } else {
                    slug.trim_end_matches('-').into()
                }
            }
        });
    (kind.into(), defect)
}
/// A legacy direct g++ sanitizer test (one instrumented executable per test source).
fn legacy_direct(plan: &ValidationPlan) -> bool {
    plan.context.options.legacy_unlocated_sanitizer_scope
        && plan.context.options.sanitizer_suite_kind == Some(ValidationKind::CppExecutable)
}

/// A direct test whose report ended the run before its verdict was read: the test evidence
/// names only the diagnosed test (the legacy projection finds its process record by it).
fn unread_direct_suite(plan: &ValidationPlan, output: &mut AnalysisOutput) {
    output.extra["sanitizer_test_evidence"] = serde_json::json!({
        "kind": ValidationKind::CppExecutable, "complete": false,
        "targets": [{"file_path": crate::cpp::source(plan, "sanitizer")}],
    });
}

/// Frozen `SanitizeEngine._snippet(result.stderr or result.stdout)`: the decoded stream split on
/// Python whitespace (`str.split()`, which also splits on U+001C..U+001F) and joined by one
/// space, at most 300 code points.
fn frozen_snippet(stderr: &[u8], stdout: &[u8]) -> String {
    let text = if stderr.is_empty() {
        String::from_utf8_lossy(stdout)
    } else {
        String::from_utf8_lossy(stderr)
    };
    text.split(|character: char| {
        character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
    })
    .filter(|word| !word.is_empty())
    .collect::<Vec<_>>()
    .join(" ")
    .chars()
    .take(300)
    .collect()
}

/// Frozen `_sanitizer_diagnostics.SanitizerDiagnosticError` text for a refused transcript.
pub(crate) fn frozen_parse_error(transcript: &str, native: &str) -> String {
    if native.contains("frame limit") {
        "sanitizer stack exceeds the bounded frame limit".into()
    } else if native.contains("diagnostic count") {
        "sanitizer output exceeds the diagnostic limit".into()
    } else if transcript.len() > 1024 * 1024 {
        "sanitizer output exceeds the bounded UTF-8 byte limit".into()
    } else if transcript.contains('\0') {
        "sanitizer output contains NUL bytes".into()
    } else {
        native.into()
    }
}

pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if plan.context.options.frozen_next_sanitizer {
        return crate::next_sanitizer::observe(plan, outcomes, output);
    }
    if plan.context.options.sanitizer_suite_kind.is_some() {
        output.extra["sanitizer_scope"] = crate::sanitizer_suite::proof(plan, outcomes)?;
    } else {
        let probe_id = format!("{}.instrumentation", plan.context.task_id);
        let probe = outcomes
            .get(&probe_id)
            .ok_or_else(|| ValidationError("instrumentation proof is absent".into()))?;
        crate::cpp::instrumentation(plan, probe)?;
    }
    let id = format!("{}.suite", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("sanitizer suite was not executed".into()))?;
    if legacy_direct(plan) && (outcome.timed_out || outcome.truncated) {
        // Frozen SanitizeEngine checks `_process_incomplete` before reading the transcript
        // (engines/sanitize.py:335-340): a test still running at its bound (run_process's
        // 300 s DEFAULT_TIMEOUT, `timeout_seconds` of the legacy direct plan) or a stream past
        // its 524,287-byte bound is the test's `SanitizerExecution` ERROR and a tool error,
        // "<label> timed out: <snippet>" before "<label> output was truncated: <snippet>"
        // (`_incomplete_message`, :895-899; root ruling 2026-11-14 for the timeout).
        let message = if outcome.timed_out {
            // Frozen `_finish_process` prefixes a timed-out run's stderr with "Command timed out
            // after {timeout}s: " (core/runner.py:424-425, Python's float spelling), so the
            // snippet always reads that stream.
            let stderr = format!(
                "Command timed out after {:?}s: {}",
                plan.context.options.timeout_seconds,
                String::from_utf8_lossy(&outcome.stderr)
            );
            format!(
                "Sanitizer execution timed out: {}",
                frozen_snippet(stderr.as_bytes(), &outcome.stdout)
            )
        } else {
            format!(
                "Sanitizer execution output was truncated: {}",
                frozen_snippet(&outcome.stderr, &outcome.stdout)
            )
        };
        let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
        target.target_name = "SanitizerExecution".into();
        target.status = "ERROR".into();
        target.message = message.clone();
        output.targets.push(target);
        unread_direct_suite(plan, output);
        return Err(ValidationError(message));
    }
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("sanitizer stderr is not UTF-8".into()))?;
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("sanitizer stdout is not UTF-8".into()))?;
    let transcript = format!("{stderr}\n{stdout}");
    // Frozen reads any project file a report names, also one outside the run's sources.
    let parse_plan = if plan.context.options.legacy_unlocated_sanitizer_scope {
        crate::next_sanitizer::with_named_sources(plan, &transcript)
    } else {
        std::borrow::Cow::Borrowed(plan)
    };
    let diagnostics = match parse_diagnostics(&transcript, &parse_plan) {
        Ok(diagnostics) => diagnostics,
        Err(error) if legacy_direct(plan) => {
            // Frozen SanitizeEngine: a report `parse_sanitizer_diagnostics` refuses is the test's
            // `SanitizerDiagnostic` ERROR and a tool error with frozen's reason.
            let message = format!(
                "Sanitizer diagnostic could not be normalized: {}",
                frozen_parse_error(&transcript, &error.0)
            );
            let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
            target.target_name = "SanitizerDiagnostic".into();
            target.status = "ERROR".into();
            target.message = message.clone();
            output.targets.push(target);
            unread_direct_suite(plan, output);
            return Err(ValidationError(message));
        }
        Err(error) => return Err(error),
    };
    output.extra["sanitizer_diagnostics"] = serde_json::json!(diagnostics);
    let mut counts = BTreeMap::new();
    for diagnostic in &diagnostics {
        if let Some(location) = &diagnostic.primary_location {
            *counts
                .entry((
                    location.path.clone(),
                    crate::finding_details::sanitizer_label(&diagnostic.kind),
                ))
                .or_insert(0usize) += 1;
        }
    }
    let mut unlocated = false;
    let legacy_unlocated = plan.context.options.legacy_unlocated_sanitizer_scope;
    let mut unlocated_scopes = false;
    // Frozen `_append_sanitizer_targets` tool errors, once per message, in report order.
    let mut unlocated_errors: Vec<String> = vec![];
    for diagnostic in diagnostics {
        let Some(location) = &diagnostic.primary_location else {
            if legacy_unlocated {
                // Frozen: the first external frame (else `[external]`:1) carries the ERROR.
                let external = diagnostic
                    .related_locations
                    .iter()
                    .find(|span| span.path == "[external]");
                let mut target =
                    InspectionTarget::at("[external]", external.map_or(1, |span| span.start_line));
                target.target_name = "SanitizerDiagnosticLocation".into();
                target.status = "ERROR".into();
                target.message = format!(
                    "{} diagnostic has no validated project-owned source location",
                    diagnostic.tool_name
                );
                if !unlocated_errors.contains(&target.message) {
                    unlocated_errors.push(target.message.clone());
                }
                target.metrics.insert(
                    "frames_observed".into(),
                    serde_json::json!(diagnostic.frames_observed),
                );
                target.metrics.insert(
                    "project_frames".into(),
                    serde_json::json!(diagnostic.project_frames),
                );
                output.targets.push(target);
                unlocated_scopes = true;
                output
                    .observation
                    .limitations
                    .push("a sanitizer report named no confirmed project source location".into());
            } else {
                unlocated = true;
            }
            continue;
        };
        let label = crate::finding_details::sanitizer_label(&diagnostic.kind);
        let legacy_label = if counts[&(location.path.clone(), label)] == 1 {
            label
        } else {
            ""
        };
        let mut target = InspectionTarget::at(&location.path, location.start_line);
        target.start_column = location.start_column;
        target.end_line = location.end_line;
        target.end_column = location.end_column;
        target.target_name = label.into();
        target.status = "FAIL".into();
        target.message = diagnostic.message.clone();
        target.metrics.insert(
            "frames_observed".into(),
            serde_json::json!(diagnostic.frames_observed),
        );
        target.metrics.insert(
            "project_frames".into(),
            serde_json::json!(diagnostic.project_frames),
        );
        crate::finding_details::sanitizer(plan, &diagnostic, legacy_label, output);
        output.targets.push(target);
        output.observation.findings.push(diagnostic.finding);
    }
    if !unlocated_errors.is_empty() && legacy_direct(plan) {
        // Frozen SanitizeEngine records the unlocated diagnostic of a direct g++ test as a tool
        // error: the run is incomplete (ERROR, NOT_RUN) and its reason is that error text. (An
        // adapter suite keeps its per-case verdicts, which already carry the frozen status.)
        unread_direct_suite(plan, output);
        return Err(ValidationError(unlocated_errors.join("; ")));
    }
    if unlocated {
        let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
        target.target_name = "SanitizerDiagnosticLocation".into();
        target.status = "ERROR".into();
        target.message =
            "sanitizer diagnostic has no validated project-owned source location".into();
        output.targets.push(target);
        return Err(ValidationError("sanitizer diagnostic has no validated project-owned source location; only confirmed source findings were retained".into()));
    }
    if !planning::completed(outcome) {
        let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
        target.target_name = "Sanitizer execution".into();
        target.status = "ERROR".into();
        target.message = "instrumented process did not return a complete verdict".into();
        output.targets.push(target);
        return Err(ValidationError(
            "sanitizer process did not complete; retained diagnostics cover only observed scope"
                .into(),
        ));
    }
    if output.observation.findings.is_empty()
        && !unlocated_scopes
        && [
            "AddressSanitizer:DEADLYSIGNAL",
            "FATAL: ThreadSanitizer",
            "Sanitizer CHECK failed",
            "ERROR: AddressSanitizer",
            "WARNING: ThreadSanitizer",
        ]
        .iter()
        .any(|marker| transcript.contains(marker))
    {
        let legacy = plan.context.options.legacy_unlocated_sanitizer_scope;
        // Frozen `SanitizeEngine`: the marker without a parsed report is its own tool error.
        const LEGACY_NO_DIAGNOSTIC: &str =
            "Sanitizer output had a report marker but no complete diagnostic";
        let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
        target.target_name = if legacy {
            "SanitizerDiagnostic".into()
        } else {
            "Sanitizer runtime".into()
        };
        target.status = "ERROR".into();
        target.message = if legacy {
            LEGACY_NO_DIAGNOSTIC.into()
        } else {
            "sanitizer runtime failed without a complete normalized code diagnostic".into()
        };
        output.targets.push(target);
        if legacy_direct(plan) {
            unread_direct_suite(plan, output);
        }
        return Err(ValidationError(if legacy {
            LEGACY_NO_DIAGNOSTIC.into()
        } else {
            "sanitizer runtime failed or produced an incomplete diagnostic".into()
        }));
    }
    if plan.context.options.sanitizer_suite_kind.is_some() {
        crate::sanitizer_suite::observe_framework(plan, outcome, output)?;
    }
    let suite_failed = output.observation.findings.is_empty() && outcome.exit_code != Some(0);
    if suite_failed {
        // Frozen `_suite_failure_finding` text; the exit code is shown, not its Rust Option.
        let mut failure = finding(
            plan,
            "ici.sanitize.suite-failure",
            &format!(
                "suite exited {} with no sanitizer diagnostic in its output \u{2014} the run failed without the sanitizer reporting a defect",
                outcome.exit_code.map_or_else(
                    || "without an exit code".to_owned(),
                    |code| code.to_string()
                )
            ),
            &crate::cpp::source(plan, "sanitizer"),
            1,
        );
        failure.native_rule_id.clear();
        output.observation.findings.push(failure);
    }
    // Frozen `SanitizeProvider.parse` returns the suite-failure finding without a
    // `<variant>.diagnostics` count (root ruling 2026-11-14 (b5)): no sanitizer diagnostic was
    // counted.
    if !suite_failed {
        output.observation.measurements.push(Measurement::measured(
            format!("{}.diagnostics", plan.kind.provider()),
            output.observation.findings.len() as f64,
        ));
    }
    let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
    target.target_name = "Instrumented suite".into();
    target.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    output.targets.push(target);
    output.summary = format!(
        "{} sanitizer diagnostics",
        output.observation.findings.len()
    );
    if plan.kind == ValidationKind::ThreadSanitizer
        && output
            .observation
            .findings
            .iter()
            .any(|finding| finding.native_rule_id == "thread-safety-defect")
    {
        output
            .observation
            .limitations
            .push("ThreadSanitizer reported a defect outside the explicit taxonomy".into());
    }
    Ok(())
}
