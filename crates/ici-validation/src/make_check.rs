//! Frozen `core/_cmake_test_results.py` for a `make check` transcript: the per-binary results
//! (`parse_make_check_stdout`), concatenated QtTest xunit documents (`parse_qtest_xunit`), their
//! qmake merge (`_qmake_results`) and the sanitizer transcript attribution
//! (`_attach_sanitizer_output`). Pure functions over bounded process output.
use quick_xml::{
    Reader, XmlVersion,
    events::{BytesStart, Event},
};
use regex::Regex;
use std::{path::Path, sync::LazyLock};

const MAX_TEST_RESULT_CHARS: usize = 512;
const MAX_SANITIZER_TRANSPORT_BYTES: usize = 65_536;
const NOT_EXECUTED: [&str; 5] = ["notrun", "skip", "skipped", "disabled", "blacklisted"];
const PASS_STATES: [&str; 4] = ["", "run", "pass", "passed"];

static INVOCATION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:^|\s)\./(?P<name>[\w.+-]+)(?:\s|$)").expect("regex"));
static NOISE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*(?:make(?:\[\d+\])?:|\()").expect("regex"));
static ERROR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*make(?:\[\d+\])?: \*\*\* .*Error \d+").expect("regex"));
static TESTSUITE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)<testsuite\b.*?</testsuite>").expect("regex"));
static DOCTYPE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)<!DOCTYPE").expect("regex"));
static SANITIZERS: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    [
        (
            "LeakSanitizer",
            r"(?i)\bLeakSanitizer:\s*detected memory leaks\b",
        ),
        (
            "AddressSanitizer",
            r"(?i)\b(?:ERROR|SUMMARY):\s*AddressSanitizer:\s*\S",
        ),
        (
            "UndefinedBehaviorSanitizer",
            r"(?i)\b(?:ERROR|SUMMARY):\s*UndefinedBehaviorSanitizer:\s*\S",
        ),
        (
            "ThreadSanitizer",
            r"(?i)\b(?:WARNING|SUMMARY):\s*ThreadSanitizer:\s*\S",
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
fn lines(text: &str) -> Vec<&str> {
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
    value
        .to_lowercase()
        .chars()
        .filter(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        .collect()
}

/// Frozen `_bounded_test_result`: stripped, at most 512 characters with a `...` cut.
fn bounded(value: &str) -> String {
    let value = value.trim();
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

/// Python `repr()` of a string, as frozen formats unknown states.
fn repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut text = String::from(quote);
    for character in value.chars() {
        match character {
            '\\' => text.push_str("\\\\"),
            '\n' => text.push_str("\\n"),
            '\r' => text.push_str("\\r"),
            '\t' => text.push_str("\\t"),
            character if character == quote => {
                text.push('\\');
                text.push(character);
            }
            character if (character as u32) < 0x20 || character == '\x7f' => {
                text.push_str(&format!("\\x{:02x}", character as u32));
            }
            character => text.push(character),
        }
    }
    text.push(quote);
    text
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

#[derive(Default)]
struct Node {
    name: String,
    attributes: Vec<(String, String)>,
    text: String,
    children: Vec<Node>,
}
impl Node {
    fn get(&self, key: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.as_str())
    }
    fn find_all<'a>(&'a self, name: &'a str) -> impl Iterator<Item = &'a Node> + 'a {
        self.children.iter().filter(move |child| child.name == name)
    }
    /// ElementTree `iter(name)`: this element and its descendants in document order.
    fn iter<'a>(&'a self, name: &str, found: &mut Vec<&'a Node>) {
        if self.name == name {
            found.push(self);
        }
        for child in &self.children {
            child.iter(name, found);
        }
    }
}

fn element(event: &BytesStart<'_>) -> Option<Node> {
    let mut node = Node {
        name: std::str::from_utf8(event.name().as_ref()).ok()?.to_owned(),
        ..Node::default()
    };
    for attribute in event.attributes() {
        let attribute = attribute.ok()?;
        let key = std::str::from_utf8(attribute.key.as_ref()).ok()?.to_owned();
        let value = attribute
            .normalized_value(XmlVersion::Explicit1_0)
            .ok()?
            .into_owned();
        if node.attributes.iter().any(|(name, _)| *name == key) {
            return None;
        }
        node.attributes.push((key, value));
    }
    Some(node)
}

/// Frozen `_parse_xml`: one well-formed document without a DTD, else nothing. Only the text
/// before an element's first child is kept (ElementTree `.text`).
fn parse_xml(text: &str) -> Option<Node> {
    if DOCTYPE.is_match(text) {
        return None;
    }
    let mut reader = Reader::from_str(text);
    reader.config_mut().check_end_names = true;
    let mut stack: Vec<Node> = vec![];
    let mut root = None;
    loop {
        match reader.read_event().ok()? {
            Event::Start(event) => {
                if root.is_some() {
                    return None;
                }
                stack.push(element(&event)?);
            }
            Event::Empty(event) => {
                if root.is_some() {
                    return None;
                }
                let node = element(&event)?;
                match stack.last_mut() {
                    Some(parent) => parent.children.push(node),
                    None => root = Some(node),
                }
            }
            Event::End(_) => {
                let node = stack.pop()?;
                match stack.last_mut() {
                    Some(parent) => parent.children.push(node),
                    None => root = Some(node),
                }
            }
            Event::Text(event) => {
                let value = event.decode().ok()?;
                let value = quick_xml::escape::unescape(&value).ok()?;
                match stack.last_mut() {
                    Some(parent) if parent.children.is_empty() => parent.text.push_str(&value),
                    Some(_) => {}
                    None if value.trim().is_empty() => {}
                    None => return None,
                }
            }
            Event::CData(event) => {
                let value = std::str::from_utf8(event.as_ref()).ok()?.to_owned();
                if let Some(parent) = stack.last_mut()
                    && parent.children.is_empty()
                {
                    parent.text.push_str(&value);
                }
            }
            Event::GeneralRef(event) => {
                let name = std::str::from_utf8(event.as_ref()).ok()?;
                let value = if let Some(code) = name.strip_prefix('#') {
                    let number = if let Some(hex) = code.strip_prefix('x') {
                        u32::from_str_radix(hex, 16).ok()?
                    } else {
                        code.parse().ok()?
                    };
                    char::from_u32(number)?.to_string()
                } else {
                    quick_xml::escape::resolve_predefined_entity(name)?.to_owned()
                };
                match stack.last_mut() {
                    Some(parent) if parent.children.is_empty() => parent.text.push_str(&value),
                    Some(_) => {}
                    None => return None,
                }
            }
            Event::Eof => break,
            Event::Decl(_) | Event::Comment(_) | Event::PI(_) => {}
            Event::DocType(_) => return None,
        }
    }
    if !stack.is_empty() {
        return None;
    }
    root
}

/// Frozen `_junit_case`.
fn junit_case(node: &Node) -> MakeCase {
    let name = bounded(node.get("name").unwrap_or(""));
    let failures: Vec<&Node> = node
        .find_all("failure")
        .chain(node.find_all("error"))
        .collect();
    let streams: Vec<&str> = node
        .find_all("system-out")
        .chain(node.find_all("system-err"))
        .map(|child| child.text.as_str())
        .collect();
    let sanitizer = |failures: &[&Node]| {
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
                _ => failure.text.trim().to_owned(),
            })
            .filter(|part| !part.is_empty())
            .collect();
        return MakeCase::new(name.clone(), false, bounded(&parts.join("; ")));
    }
    let skipped: Vec<String> = node
        .find_all("skipped")
        .map(|item| match item.get("message") {
            Some(message) if !message.is_empty() => message.to_owned(),
            _ => item.text.trim().to_owned(),
        })
        .collect();
    if node.find_all("skipped").next().is_some() {
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
    let status = node.get("status").unwrap_or("").trim();
    let state = normalized(status);
    if NOT_EXECUTED.contains(&state.as_str()) {
        return MakeCase {
            executed: false,
            ..MakeCase::new(
                name.clone(),
                false,
                bounded(&format!("ctest reported status {}", repr(status))),
            )
        };
    }
    if !PASS_STATES.contains(&state.as_str()) {
        return MakeCase::new(
            name.clone(),
            false,
            bounded(&format!(
                "test framework reported unknown status {}",
                repr(status)
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
    let text = String::from_utf8_lossy(&bytes);
    if text.chars().count() > MAX_CTEST_REPORT_BYTES
        || text.len() > MAX_CTEST_REPORT_BYTES
        || text.contains('\0')
    {
        return false;
    }
    let Some(root) = parse_xml(&text) else {
        return false;
    };
    let mut cases = vec![];
    root.iter("testcase", &mut cases);
    !cases.is_empty()
}

/// Frozen `parse_qtest_xunit`: one or more concatenated QtTest xunitxml documents.
pub fn parse_qtest_xunit(text: &str) -> Vec<MakeCase> {
    if DOCTYPE.is_match(text) {
        return vec![];
    }
    let mut results = vec![];
    for block in TESTSUITE.find_iter(text) {
        let Some(suite) = parse_xml(block.as_str()) else {
            continue;
        };
        let suite_name = suite.get("name").unwrap_or("").to_owned();
        let mut nodes = vec![];
        suite.iter("testcase", &mut nodes);
        for node in nodes {
            let case = junit_case(node);
            let name = if suite_name.is_empty() {
                case.name.clone()
            } else {
                format!("{suite_name}::{}", case.name)
            };
            let result = node.get("result").unwrap_or("pass").trim();
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
                message = format!("QtTest reported result {}", repr(result));
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
