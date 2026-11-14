//! Frozen `core/_cmake_test_results.py` for a `make check` transcript: the per-binary results
//! (`parse_make_check_stdout`), concatenated QtTest xunit documents (`parse_qtest_xunit`), their
//! qmake merge (`_qmake_results`) and the sanitizer transcript attribution
//! (`_attach_sanitizer_output`). Pure functions over bounded process output.
use crate::frozen_xml::{self, Document, Element};
use ici_analysis::unicode::{fold, python_repr, python_strip};
use regex::Regex;
use std::{path::Path, sync::LazyLock};

const MAX_TEST_RESULT_CHARS: usize = 512;
const MAX_SANITIZER_TRANSPORT_BYTES: usize = 65_536;
const NOT_EXECUTED: [&str; 5] = ["notrun", "skip", "skipped", "disabled", "blacklisted"];
const PASS_STATES: [&str; 4] = ["", "run", "pass", "passed"];

// Frozen's patterns over Python `str`: `\s` also holds U+001C..U+001F, and `\w` and `\b` follow
// letters, numbers and `_` (no combining marks or connector punctuation).
static INVOCATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^|[\s\x1c-\x1f])\./(?P<name>[\p{L}\p{N}_.+-]+)(?:[\s\x1c-\x1f]|$)")
        .expect("regex")
});
static NOISE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[\s\x1c-\x1f]*(?:make(?:\[\d+\])?:|\()").expect("regex"));
static ERROR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[\s\x1c-\x1f]*make(?:\[\d+\])?: \*\*\* .*Error \d+").expect("regex")
});
/// Frozen `_CTEST_LINE_RE` (core/_cmake_test_results.py:23-25), matched with `re.match` on each
/// `str.splitlines()` line; the `#` index is captured here only to order a parallel run.
static CTEST_LINE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^[\s\x1c-\x1f]*\d+/\d+[\s\x1c-\x1f]+Test[\s\x1c-\x1f]+#(?P<index>\d+):[\s\x1c-\x1f]+(?P<name>[^\s\x1c-\x1f]+)[\s\x1c-\x1f]+[. ]*(?P<verdict>.+?)[\s\x1c-\x1f]+[\d.]+[\s\x1c-\x1f]+sec[\s\x1c-\x1f]*$",
    )
    .expect("regex")
});
static DOCTYPE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)<!DOCTYPE").expect("regex"));
static SANITIZERS: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    [
        (
            "LeakSanitizer",
            r"(?i)(?:^|[^\p{L}\p{N}_])LeakSanitizer:[\s\x1c-\x1f]*detected memory leaks(?:[^\p{L}\p{N}_]|\z)",
        ),
        (
            "AddressSanitizer",
            r"(?i)(?:^|[^\p{L}\p{N}_])(?:ERROR|SUMMARY):[\s\x1c-\x1f]*AddressSanitizer:[\s\x1c-\x1f]*[^\s\x1c-\x1f]",
        ),
        (
            "UndefinedBehaviorSanitizer",
            r"(?i)(?:^|[^\p{L}\p{N}_])(?:ERROR|SUMMARY):[\s\x1c-\x1f]*UndefinedBehaviorSanitizer:[\s\x1c-\x1f]*[^\s\x1c-\x1f]",
        ),
        (
            "ThreadSanitizer",
            r"(?i)(?:^|[^\p{L}\p{N}_])(?:WARNING|SUMMARY):[\s\x1c-\x1f]*ThreadSanitizer:[\s\x1c-\x1f]*[^\s\x1c-\x1f]",
        ),
    ]
    .into_iter()
    .map(|(name, pattern)| (name, Regex::new(pattern).expect("regex")))
    .collect()
});

/// Frozen `TestCaseResult`: one test as the build system reported it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MakeCase {
    pub name: String,
    pub passed: bool,
    pub message: String,
    pub executed: bool,
    /// The bounded sanitizer transcript attributed to this case (sanitizer engines only).
    pub diagnostic_output: String,
    pub diagnostic_output_truncated: bool,
}
impl MakeCase {
    fn new(name: impl Into<String>, passed: bool, message: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            passed,
            message: message.into(),
            executed: true,
            ..Self::default()
        }
    }
}

/// Python `str.splitlines()` boundaries.
pub(crate) fn lines(text: &str) -> Vec<&str> {
    let mut result = vec![];
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, character)) = chars.next() {
        if matches!(
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
        ) {
            result.push(&text[start..index]);
            let mut end = index + character.len_utf8();
            if character == '\r' && chars.peek().is_some_and(|(_, next)| *next == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < text.len() {
        result.push(&text[start..]);
    }
    result
}

/// Frozen `_normalized_test_state`: casefolded, only ASCII letters and digits kept.
fn normalized(value: &str) -> String {
    fold(value)
        .chars()
        .filter(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        .collect()
}

/// Frozen `_bounded_test_result`: stripped, at most 512 characters with a `...` cut.
fn bounded(value: &str) -> String {
    let value = python_strip(value);
    if value.chars().count() <= MAX_TEST_RESULT_CHARS {
        return value.to_owned();
    }
    format!(
        "{}...",
        value
            .chars()
            .take(MAX_TEST_RESULT_CHARS - 3)
            .collect::<String>()
    )
}

fn sanitizer_in(values: &[&str]) -> Option<&'static str> {
    SANITIZERS.iter().find_map(|(name, marker)| {
        values
            .iter()
            .any(|value| marker.is_match(value))
            .then_some(*name)
    })
}

/// Frozen `_bounded_sanitizer_transport`: at most 64 KiB of UTF-8.
fn transport(value: &str) -> (String, bool) {
    if value.len() <= MAX_SANITIZER_TRANSPORT_BYTES {
        return (value.to_owned(), false);
    }
    let mut end = MAX_SANITIZER_TRANSPORT_BYTES;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    (value[..end].to_owned(), true)
}

/// Frozen `parse_ctest_stdout` (core/_cmake_test_results.py:282-302), with each case's CTest
/// `#` index: `verdict = match.group("verdict").strip().lstrip("*")`, not executed when the
/// normalized verdict starts with `notrun`, `disabled` or `skip`, passed only for an executed
/// `"Passed"`, message `"" if verdict == "Passed" else verdict`.
pub fn parse_ctest_stdout(text: &str) -> Vec<(u64, MakeCase)> {
    let mut results = vec![];
    for line in lines(text) {
        let Some(capture) = CTEST_LINE.captures(line) else {
            continue;
        };
        let verdict = python_strip(&capture["verdict"]).trim_start_matches('*');
        let state = normalized(verdict);
        let executed = !(state.starts_with("notrun")
            || state.starts_with("disabled")
            || state.starts_with("skip"));
        let mut case = MakeCase::new(
            &capture["name"],
            executed && verdict == "Passed",
            if verdict == "Passed" { "" } else { verdict },
        );
        case.executed = executed;
        results.push((capture["index"].parse().unwrap_or(u64::MAX), case));
    }
    results
}

/// Frozen `parse_make_check_stdout`: make echoes each test command, an Error line fails the test
/// that last started, and a non-zero exit with no attributed failure blames the last test.
pub fn parse_make_check_stdout(text: &str, returncode: i32) -> Vec<MakeCase> {
    let mut names: Vec<String> = vec![];
    let mut failed = std::collections::BTreeSet::new();
    let mut current: Option<String> = None;
    for line in lines(text) {
        if NOISE.is_match(line) {
            if let Some(current) = &current
                && ERROR.is_match(line)
            {
                failed.insert(current.clone());
            }
            continue;
        }
        if let Some(capture) = INVOCATION.captures(line) {
            let name = capture["name"].to_owned();
            if !names.contains(&name) {
                names.push(name.clone());
            }
            current = Some(name);
        }
    }
    let mut results: Vec<_> = names
        .iter()
        .map(|name| {
            let failed = failed.contains(name);
            MakeCase::new(
                name.clone(),
                !failed,
                if failed { "make check failed" } else { "" },
            )
        })
        .collect();
    if returncode != 0
        && failed.is_empty()
        && let Some(last) = results.last_mut()
    {
        *last = MakeCase::new(
            last.name.clone(),
            false,
            "make check exited non-zero after this test started",
        );
    }
    results
}

/// Frozen `_parse_xml`: one well-formed document without a DTD, else nothing.
fn parse_xml(text: &str) -> Option<Document> {
    if DOCTYPE.is_match(text) {
        return None;
    }
    frozen_xml::parse(text)
}

/// Frozen `_junit_case`.
fn junit_case(document: &Document, node: &Element) -> MakeCase {
    let name = bounded(node.get("name").unwrap_or(""));
    let failures: Vec<&Element> = document
        .find_all(node, "failure")
        .chain(document.find_all(node, "error"))
        .collect();
    let streams: Vec<&str> = document
        .find_all(node, "system-out")
        .chain(document.find_all(node, "system-err"))
        .map(|child| child.text.as_str())
        .collect();
    let sanitizer = |failures: &[&Element]| {
        let mut evidence: Vec<&str> = vec![];
        for failure in failures {
            evidence.push(failure.get("message").unwrap_or(""));
            evidence.push(&failure.text);
        }
        evidence.extend(&streams);
        sanitizer_in(&evidence).map(|sanitizer| (sanitizer, transport(&evidence.join("\n"))))
    };
    let diagnostic = |sanitizer: &str, (output, truncated): (String, bool)| MakeCase {
        diagnostic_output: output,
        diagnostic_output_truncated: truncated,
        ..MakeCase::new(name.clone(), false, format!("{sanitizer} diagnostic"))
    };
    if !failures.is_empty() {
        if let Some((found, output)) = sanitizer(&failures) {
            return diagnostic(found, output);
        }
        let parts: Vec<String> = failures
            .iter()
            .map(|failure| match failure.get("message") {
                Some(message) if !message.is_empty() => message.to_owned(),
                _ => python_strip(&failure.text).to_owned(),
            })
            .filter(|part| !part.is_empty())
            .collect();
        return MakeCase::new(name.clone(), false, bounded(&parts.join("; ")));
    }
    let skipped: Vec<String> = document
        .find_all(node, "skipped")
        .map(|item| match item.get("message") {
            Some(message) if !message.is_empty() => message.to_owned(),
            _ => python_strip(&item.text).to_owned(),
        })
        .collect();
    if document.find_all(node, "skipped").next().is_some() {
        let message = bounded(
            &skipped
                .into_iter()
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join("; "),
        );
        return MakeCase {
            executed: false,
            ..MakeCase::new(
                name.clone(),
                false,
                if message.is_empty() {
                    "test was skipped".into()
                } else {
                    message
                },
            )
        };
    }
    if let Some((found, output)) = sanitizer(&[]) {
        return diagnostic(found, output);
    }
    let status = python_strip(node.get("status").unwrap_or(""));
    let state = normalized(status);
    if NOT_EXECUTED.contains(&state.as_str()) {
        return MakeCase {
            executed: false,
            ..MakeCase::new(
                name.clone(),
                false,
                bounded(&format!("ctest reported status {}", python_repr(status))),
            )
        };
    }
    if !PASS_STATES.contains(&state.as_str()) {
        return MakeCase::new(
            name.clone(),
            false,
            bounded(&format!(
                "test framework reported unknown status {}",
                python_repr(status)
            )),
        );
    }
    MakeCase::new(name, true, "")
}

/// Frozen `_MAX_CTEST_REPORT_BYTES = 1_000_000` (core/_cmake_test_results.py:18).
const MAX_CTEST_REPORT_BYTES: usize = 1_000_000;

/// Whether frozen `run_tests` (core/cmake.py:751-754) takes the CTest JUnit reading:
/// `report = _read_ctest_junit(junit, session.shadow)`,
/// `parsed = parse_ctest_junit(report) if report is not None else []`, `if parsed:` — every
/// other case falls back to `parse_ctest_stdout(result.stdout)`. `_read_ctest_junit` returns
/// None for a missing, non-regular, linked or over-1,000,000-byte file (`_read_bounded_regular`)
/// and decodes `payload.decode("utf-8", errors="replace")`; `parse_ctest_junit` returns `[]`
/// when `len(xml_text) > _MAX_CTEST_REPORT_BYTES`, `encoded_size > _MAX_CTEST_REPORT_BYTES or
/// "\x00" in xml_text`, `_parse_xml` refuses it (a DOCTYPE or an XML parse error), or
/// `root.iter("testcase")` finds no case.
pub fn ctest_junit_accepted(path: &Path) -> bool {
    use std::{io::Read, os::unix::fs::OpenOptionsExt};
    let Ok(mut file) = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags((nix::fcntl::OFlag::O_NOFOLLOW | nix::fcntl::OFlag::O_NONBLOCK).bits())
        .open(path)
    else {
        return false;
    };
    match file.metadata() {
        Ok(metadata) if metadata.is_file() && metadata.len() <= MAX_CTEST_REPORT_BYTES as u64 => {}
        _ => return false,
    }
    let mut bytes = vec![];
    if (&mut file)
        .take(MAX_CTEST_REPORT_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .is_err()
        || bytes.len() > MAX_CTEST_REPORT_BYTES
    {
        return false;
    }
    !parse_ctest_junit(&String::from_utf8_lossy(&bytes)).is_empty()
}

/// Frozen `parse_ctest_junit`.
pub fn parse_ctest_junit(text: &str) -> Vec<MakeCase> {
    if text.chars().count() > MAX_CTEST_REPORT_BYTES
        || text.len() > MAX_CTEST_REPORT_BYTES
        || text.contains('\0')
    {
        return vec![];
    }
    let Some(document) = parse_xml(text) else {
        return vec![];
    };
    document
        .iter("testcase")
        .map(|node| junit_case(&document, node))
        .collect()
}

/// Frozen `_TESTSUITE_RE` (`<testsuite\\b.*?</testsuite>`, `re.DOTALL`) over Python `\\b`.
fn testsuite_blocks(text: &str) -> Vec<&str> {
    const OPEN: &str = "<testsuite";
    const CLOSE: &str = "</testsuite>";
    let mut blocks = vec![];
    let mut at = 0;
    while let Some(found) = text[at..].find(OPEN) {
        let start = at + found;
        let after = start + OPEN.len();
        if text[after..]
            .chars()
            .next()
            .is_some_and(ici_analysis::unicode::python_word)
        {
            at = after;
            continue;
        }
        let Some(close) = text[after..].find(CLOSE) else {
            break;
        };
        at = after + close + CLOSE.len();
        blocks.push(&text[start..at]);
    }
    blocks
}

/// Frozen `parse_qtest_xunit`: one or more concatenated QtTest xunitxml documents.
pub fn parse_qtest_xunit(text: &str) -> Vec<MakeCase> {
    if DOCTYPE.is_match(text) {
        return vec![];
    }
    let mut results = vec![];
    for block in testsuite_blocks(text) {
        let Some(suite) = parse_xml(block) else {
            continue;
        };
        let suite_name = suite.root().get("name").unwrap_or("");
        for node in suite.iter("testcase") {
            let case = junit_case(&suite, node);
            let name = if suite_name.is_empty() {
                case.name.clone()
            } else {
                format!("{suite_name}::{}", case.name)
            };
            let result = python_strip(node.get("result").unwrap_or("pass"));
            let state = normalized(result);
            let (executed, passed) = if NOT_EXECUTED.contains(&state.as_str()) {
                (false, false)
            } else if matches!(state.as_str(), "pass" | "passed" | "xfail") {
                (case.executed, case.passed && case.executed)
            } else {
                (true, false)
            };
            let mut message = case.message.clone();
            if !passed && message.is_empty() {
                message = format!("QtTest reported result {}", python_repr(result));
            }
            results.push(MakeCase {
                name,
                passed,
                message,
                executed,
                ..case
            });
        }
    }
    results
}

/// Frozen `_qmake_results`: the transcript names the binaries; the failed binary's message
/// carries every failed QtTest case of the xunit output.
pub fn qmake_results(text: &str, returncode: i32) -> Vec<MakeCase> {
    let results = parse_make_check_stdout(text, returncode);
    if results.is_empty() {
        return parse_qtest_xunit(text);
    }
    let failures: Vec<_> = parse_qtest_xunit(text)
        .into_iter()
        .filter(|case| case.executed && !case.passed)
        .collect();
    if failures.is_empty() {
        return results;
    }
    let detail = failures
        .iter()
        .map(|case| {
            format!("{}: {}", case.name, case.message)
                .trim_matches(|character| character == ':' || character == ' ')
                .to_owned()
        })
        .collect::<Vec<_>>()
        .join("; ");
    results
        .into_iter()
        .map(|case| {
            if case.passed {
                case
            } else {
                MakeCase {
                    message: format!("{} — {detail}", case.message),
                    ..case
                }
            }
        })
        .collect()
}

/// Frozen `_attach_sanitizer_output`: a sanitizer report in the whole transcript fails the first
/// failed (else first executed) case, or a synthetic `sanitizer-process` case.
pub fn attach_sanitizer_output(mut results: Vec<MakeCase>, output: &str) -> Vec<MakeCase> {
    if results
        .iter()
        .any(|case| !case.diagnostic_output.is_empty())
    {
        return results;
    }
    let Some(sanitizer) = sanitizer_in(&[output]) else {
        return results;
    };
    let (diagnostic_output, truncated) = transport(output);
    let index = results
        .iter()
        .position(|case| case.executed && !case.passed)
        .or_else(|| results.iter().position(|case| case.executed));
    let Some(index) = index else {
        results.push(MakeCase {
            diagnostic_output,
            diagnostic_output_truncated: truncated,
            ..MakeCase::new(
                "sanitizer-process",
                false,
                format!("{sanitizer} diagnostic"),
            )
        });
        return results;
    };
    let selected = &mut results[index];
    if selected.passed || selected.message.is_empty() {
        selected.message = format!("{sanitizer} diagnostic");
    }
    selected.passed = false;
    selected.diagnostic_output = diagnostic_output;
    selected.diagnostic_output_truncated = truncated;
    results
}
