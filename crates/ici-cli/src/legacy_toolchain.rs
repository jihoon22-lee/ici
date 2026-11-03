//! Frozen `core/toolchain.py` capability semantics for the legacy capability inventory
//! (`ici verify` reports and the legacy `ici doctor`): the registered probe argv, the version
//! banner parse, vendor and compiler family, the metadata detail probes and the frozen
//! available/complete/error rules. The native inventory keeps its own row shape (origin, digest,
//! probe record); these functions only decide the frozen public values inside it.

use regex::Regex;
use serde_json::{Map, Value, json};
use std::sync::OnceLock;

/// Frozen `PROBE_TIMEOUT_SECONDS` and `PROBE_OUTPUT_LIMIT` of every registered probe.
pub(crate) const PROBE_TIMEOUT_SECONDS: f64 = 5.0;
pub(crate) const PROBE_OUTPUT_LIMIT: usize = 65_536;

/// Frozen `_COMPILER_DRIVER_NAMES`: a version without a compiler family is incomplete.
const COMPILER_DRIVERS: [&str; 4] = ["gcc", "g++", "clang", "clang++"];

/// A frozen registered metadata probe run after a successful version probe.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Detail {
    Target,
    QmakeQuery,
    CmakeCapabilities,
}

impl Detail {
    pub(crate) fn args(self) -> &'static [&'static str] {
        match self {
            Detail::Target => &["-dumpmachine"],
            Detail::QmakeQuery => &["-query"],
            Detail::CmakeCapabilities => &["-E", "capabilities"],
        }
    }
}

/// Frozen `DEFAULT_TOOL_PROBES` version argv and metadata probe of a policy tool name.
pub(crate) fn registered(name: &str) -> (Vec<String>, Option<Detail>) {
    let version = match name {
        "qmake" => vec!["-v"],
        "python3" => vec!["-VV"],
        _ => vec!["--version"],
    };
    let detail = match name {
        "gcc" | "g++" | "clang" | "clang++" => Some(Detail::Target),
        "cmake" => Some(Detail::CmakeCapabilities),
        "qmake" => Some(Detail::QmakeQuery),
        _ => None,
    };
    (version.into_iter().map(str::to_owned).collect(), detail)
}

/// Frozen `DEFAULT_TOOL_PROBES` order, which is the inventory's row order.
const REGISTRY: [&str; 32] = [
    "gcc",
    "g++",
    "clang",
    "clang++",
    "clang-format",
    "clang-tidy",
    "clazy",
    "clangd",
    "clang-check",
    "cmake",
    "ctest",
    "qmake",
    "make",
    "ninja",
    "gcov",
    "readelf",
    "addr2line",
    "objdump",
    "nm",
    "ld",
    "ar",
    "strip",
    "pkg-config",
    "qt5",
    "qt6",
    "git",
    "ruff",
    "mypy",
    "pytest",
    "coverage",
    "uv",
    "python3",
];

/// A row's position in frozen's inventory: registered tools in registry order, then others.
pub(crate) fn registry_rank(name: &str) -> usize {
    REGISTRY
        .iter()
        .position(|registered| *registered == name)
        .unwrap_or(REGISTRY.len())
}

/// Frozen `_combined_lines`: stripped, nonempty lines of stdout then stderr (`str.splitlines`).
fn combined_lines(stdout: &str, stderr: &str) -> Vec<String> {
    format!("{stdout}\n{stderr}")
        .split([
            '\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\u{85}', '\u{2028}', '\u{2029}',
        ])
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect()
}

fn tool_patterns(name: &str) -> Vec<&'static Regex> {
    static PATTERNS: OnceLock<Vec<(&'static str, Regex)>> = OnceLock::new();
    static COMPILER: OnceLock<Regex> = OnceLock::new();
    let patterns =
        PATTERNS.get_or_init(|| {
            [
            ("python3", r"(?i)\bPython\s+(?P<version>\d+(?:\.\d+){1,3})"),
            ("cmake", r"(?i)\bcmake\s+version\s+(?P<version>\d+(?:\.\d+)+)"),
            ("ctest", r"(?i)\bctest\s+version\s+(?P<version>\d+(?:\.\d+)+)"),
            ("qmake", r"(?i)\bQMake\s+version\s+(?P<version>\d+(?:\.\d+)+)"),
            ("make", r"(?i)\b(?:GNU\s+)?Make\s+(?P<version>\d+(?:\.\d+)+)"),
            ("git", r"(?i)\bgit\s+version\s+(?P<version>\d+(?:\.\d+)+)"),
            ("pytest", r"(?i)\bpytest\s+(?P<version>\d+(?:\.\d+)+)"),
            (
                "clazy",
                r"(?i)\bclazy(?:-standalone)?\s+(?:version\s*:?\s*)?(?P<version>\d+(?:\.\d+)+)\b",
            ),
        ]
        .into_iter()
        .map(|(name, pattern)| (name, Regex::new(pattern).expect("frozen version pattern")))
        .collect()
        });
    let mut selected: Vec<&Regex> = patterns
        .iter()
        .filter(|(tool, _)| *tool == name)
        .map(|(_, pattern)| pattern)
        .collect();
    if matches!(
        name,
        "gcc"
            | "g++"
            | "clang"
            | "clang++"
            | "clang-format"
            | "clang-tidy"
            | "clangd"
            | "clang-check"
    ) {
        selected.push(COMPILER.get_or_init(|| {
            Regex::new(
                r"(?i)\b(?:Apple\s+|Ubuntu\s+)?(?:gcc|g\+\+|clang(?:\+\+)?)(?:\s+version)?(?:\s+\([^)]*\))?\s+(?P<version>\d+(?:\.\d+)+)",
            )
            .expect("frozen compiler version pattern")
        }));
    }
    selected
}

fn numeric(version: &str) -> Option<Vec<u64>> {
    version.split('.').map(|part| part.parse().ok()).collect()
}

/// Frozen `_GENERIC_VERSION_RE` (`(?<![A-Za-z0-9_])\d+(?:\.\d+)+`), first match of a line.
fn generic_version(line: &str) -> Option<String> {
    static DOTTED: OnceLock<Regex> = OnceLock::new();
    let dotted = DOTTED.get_or_init(|| Regex::new(r"^\d+(?:\.\d+)+").expect("dotted version"));
    let mut previous: Option<char> = None;
    for (index, ch) in line.char_indices() {
        let boundary =
            previous.is_none_or(|before| !(before.is_ascii_alphanumeric() || before == '_'));
        if boundary
            && ch.is_numeric()
            && let Some(found) = dotted.find(&line[index..])
        {
            return Some(found.as_str().to_owned());
        }
        previous = Some(ch);
    }
    None
}

/// Frozen `parse_tool_version`: the first line (in pattern then line order) carrying the tool's
/// version, redacted and bounded to 200 characters, and its numeric tuple; else `("", [])`.
pub(crate) fn parse_tool_version(name: &str, stdout: &str, stderr: &str) -> (String, Vec<u64>) {
    let lines = combined_lines(stdout, stderr);
    for pattern in tool_patterns(name) {
        for line in &lines {
            if let Some(captures) = pattern.captures(line)
                && let Some(tuple) = numeric(&captures["version"])
            {
                return (bounded_line(line), tuple);
            }
        }
    }
    if name != "clazy" {
        for line in &lines {
            if let Some(tuple) = generic_version(line).as_deref().and_then(numeric) {
                return (bounded_line(line), tuple);
            }
        }
    }
    (String::new(), vec![])
}

fn bounded_line(line: &str) -> String {
    ici_execution::redaction::redact_text(&line.chars().take(200).collect::<String>())
}

/// Frozen `compiler_family_from_version`.
pub(crate) fn compiler_family(version: &str) -> &'static str {
    let folded = version.to_lowercase();
    if folded.contains("clang") {
        "clang"
    } else if folded.contains("gcc") || folded.contains("g++") || folded.contains("gnu compiler") {
        "gcc"
    } else {
        ""
    }
}

/// Frozen `_vendor`.
pub(crate) fn vendor(version: &str) -> &'static str {
    let folded = version.to_lowercase();
    if folded.starts_with("apple ") {
        "Apple"
    } else if folded.starts_with("ubuntu ") {
        "Ubuntu"
    } else if folded.starts_with("gnu ") {
        "GNU"
    } else if folded.contains("(ubuntu ") || folded.contains(" for ubuntu") {
        "Ubuntu"
    } else if folded.starts_with("llvm ")
        || (folded.contains(" clang version") && folded.contains("llvm"))
    {
        "LLVM"
    } else if folded.starts_with("microsoft ") {
        "Microsoft"
    } else {
        ""
    }
}

/// The frozen outcome of one finished probe process.
pub(crate) struct Probe<'a> {
    pub exit_code: Option<i32>,
    pub signal: Option<i32>,
    pub timed_out: bool,
    pub truncated: bool,
    pub stdout: &'a [u8],
    pub stderr: &'a [u8],
}

impl Probe<'_> {
    fn succeeded(&self) -> bool {
        !self.timed_out && !self.truncated && self.exit_code == Some(0)
    }
    /// Frozen `_failure_reason` (a signal is Python's negative return code).
    fn failure(&self, label: &str) -> String {
        if self.timed_out {
            format!("{label} timed out")
        } else if self.truncated {
            format!("{label} output truncated")
        } else {
            let code = self
                .exit_code
                .or(self.signal.map(|signal| -signal))
                .map_or_else(|| "None".to_owned(), |code| code.to_string());
            format!("{label} exited {code}")
        }
    }
    fn text(bytes: &[u8]) -> String {
        String::from_utf8_lossy(bytes).into_owned()
    }
}

/// The frozen public values of one capability row.
pub(crate) struct Row {
    pub available: bool,
    pub complete: bool,
    pub version: String,
    pub error: String,
    pub details: Map<String, Value>,
}

/// Frozen `collect_registered_capability` over a resolved executable: the version probe, then
/// (only when it succeeded) the metadata probe of `detail`.
pub(crate) fn row(
    name: &str,
    alias: &str,
    module: Option<&str>,
    version_probe: &Probe<'_>,
    detail: Option<(Detail, &Probe<'_>)>,
) -> Row {
    let mut details = Map::new();
    if !version_probe.succeeded() {
        details.insert("resolved_alias".into(), json!(alias));
        if let Some(module) = module {
            details.insert("provider".into(), json!("python-module"));
            details.insert("module".into(), json!(module));
        }
        return Row {
            available: false,
            complete: false,
            version: String::new(),
            error: version_probe.failure("probe"),
            details,
        };
    }
    let (version, tuple) = parse_tool_version(
        name,
        &Probe::text(version_probe.stdout),
        &Probe::text(version_probe.stderr),
    );
    let vendor = vendor(&version);
    if !vendor.is_empty() {
        details.insert("vendor".into(), json!(vendor));
    }
    let family = compiler_family(&version);
    if !family.is_empty() {
        details.insert("compiler_family".into(), json!(family));
    }
    let driver = COMPILER_DRIVERS.contains(&name);
    let mut complete = !tuple.is_empty() && (!driver || !family.is_empty());
    let mut error = if complete {
        String::new()
    } else if !tuple.is_empty() && driver {
        "probe did not identify a supported compiler family".into()
    } else {
        "probe did not report a parseable version".into()
    };
    details.insert("resolved_alias".into(), json!(alias));
    if let Some(module) = module {
        details.insert("provider".into(), json!("python-module"));
        details.insert("module".into(), json!(module));
    }
    if let Some((kind, probe)) = detail {
        let failure = if probe.succeeded() {
            detail_values(
                kind,
                &Probe::text(probe.stdout),
                &Probe::text(probe.stderr),
                &mut details,
            )
        } else {
            Some(probe.failure("metadata probe"))
        };
        if let Some(failure) = failure {
            complete = false;
            error = failure;
        }
    }
    Row {
        available: true,
        complete,
        version,
        error,
        details,
    }
}

/// Frozen `_validate_registered_detail`: the parsed metadata, or the reason it is unusable.
fn detail_values(
    kind: Detail,
    stdout: &str,
    stderr: &str,
    details: &mut Map<String, Value>,
) -> Option<String> {
    match kind {
        Detail::Target => {
            let target = combined_lines(stdout, stderr)
                .into_iter()
                .next()
                .unwrap_or_default();
            if is_target_triple(&target) {
                details.insert(
                    "target_triple".into(),
                    json!(ici_execution::redaction::redact_text(
                        &target.chars().take(200).collect::<String>()
                    )),
                );
                None
            } else {
                Some("metadata probe returned an invalid target triple".into())
            }
        }
        Detail::QmakeQuery => {
            let parsed = qmake_query(if stdout.is_empty() { stderr } else { stdout });
            let reported = parsed.contains_key("qt_version");
            details.extend(parsed);
            (!reported).then(|| "metadata probe did not report QT_VERSION".into())
        }
        Detail::CmakeCapabilities => {
            let parsed = cmake_capabilities(if stdout.is_empty() { stderr } else { stdout });
            let empty = parsed.is_empty();
            details.extend(parsed);
            empty.then(|| "metadata probe returned malformed CMake capabilities".into())
        }
    }
}

/// Frozen `_is_target_triple`.
fn is_target_triple(value: &str) -> bool {
    static SHAPE: OnceLock<Regex> = OnceLock::new();
    static ARCH: OnceLock<Regex> = OnceLock::new();
    let shape = SHAPE.get_or_init(|| {
        Regex::new(r"^[A-Za-z0-9_+.]+(?:-[A-Za-z0-9_+.]+){1,4}$").expect("triple shape")
    });
    let arch = ARCH.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:aarch64|arm64|arm(?:v[0-9.]+)?|avr|bpf|hexagon|i[3-6]86|loongarch64|mips(?:64)?(?:el)?|msp430|nvptx64|powerpc(?:64le)?|ppc(?:64le)?|riscv(?:32|64)|s390x|sparc(?:64)?|wasm(?:32|64)|x86_64)$",
        )
        .expect("triple arch")
    });
    if !shape.is_match(value) {
        return false;
    }
    let folded = value.to_lowercase();
    let mut parts = folded.split('-');
    let first = parts.next().unwrap_or_default();
    if !arch.is_match(first) {
        return false;
    }
    const PLATFORMS: [&str; 19] = [
        "android",
        "darwin",
        "dragonfly",
        "eabi",
        "elf",
        "freebsd",
        "gnu",
        "haiku",
        "ios",
        "linux",
        "mingw",
        "msvc",
        "musl",
        "netbsd",
        "none",
        "openbsd",
        "solaris",
        "wasi",
        "windows",
    ];
    parts.any(|part| PLATFORMS.iter().any(|prefix| part.starts_with(prefix)))
}

/// Frozen `parse_qmake_query`.
fn qmake_query(text: &str) -> Map<String, Value> {
    let redact = ici_execution::redaction::redact_text;
    let mut values: Map<String, Value> = Map::new();
    let mut raw = std::collections::BTreeMap::new();
    for line in text.lines() {
        if let Some((key, value)) = line.split_once(':')
            && !key.trim().is_empty()
        {
            raw.insert(
                key.trim().to_owned(),
                redact(&value.trim().chars().take(2_000).collect::<String>()),
            );
        }
    }
    let present = |key: &str| raw.get(key).filter(|value| !value.is_empty());
    if let Some(version) = present("QT_VERSION") {
        values.insert("qt_version".into(), json!(version));
        let major: String = version.chars().take_while(char::is_ascii_digit).collect();
        if !major.is_empty() {
            values.insert("qt_major".into(), json!(major));
        }
    }
    if let Some(specification) = present("QMAKE_XSPEC").or_else(|| present("QMAKE_SPEC")) {
        values.insert("generator".into(), json!(specification));
    }
    if let Some(prefix) = present("QT_INSTALL_PREFIX") {
        values.insert("qt_prefix".into(), json!(prefix));
    }
    if let Some(features) = present("QT_CONFIG") {
        values.insert(
            "features".into(),
            json!(features.split_whitespace().collect::<Vec<_>>().join(" ")),
        );
    }
    values
}

/// Frozen `parse_cmake_capabilities`.
fn cmake_capabilities(text: &str) -> Map<String, Value> {
    let mut details = Map::new();
    let Ok(Value::Object(data)) = serde_json::from_str::<Value>(text) else {
        return details;
    };
    let redact = ici_execution::redaction::redact_text;
    let mut names: Vec<String> = data
        .get("generators")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| item.get("name"))
        .filter(|name| python_truthy(name))
        .map(|name| {
            let text = match name {
                Value::String(text) => text.clone(),
                other => python_str(other),
            };
            redact(&text).chars().take(200).collect::<String>()
        })
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    names.truncate(100);
    if !names.is_empty() {
        details.insert(
            "generators".into(),
            json!(names.join(", ").chars().take(4_000).collect::<String>()),
        );
    }
    if let Some(mode) = data.get("serverMode") {
        details.insert(
            "server_mode".into(),
            json!(if python_truthy(mode) { "true" } else { "false" }),
        );
    }
    details
}

fn python_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

fn python_str(value: &Value) -> String {
    match value {
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok<'a>(stdout: &'a [u8]) -> Probe<'a> {
        Probe {
            exit_code: Some(0),
            signal: None,
            timed_out: false,
            truncated: false,
            stdout,
            stderr: b"",
        }
    }

    #[test]
    fn version_banners_keep_the_frozen_first_matching_line() {
        assert_eq!(
            parse_tool_version(
                "coverage",
                "Coverage.py, version 7.15.4 with C extension\nFull documentation is at https://coverage.readthedocs.io/en/7.15.4\n",
                ""
            ),
            (
                "Coverage.py, version 7.15.4 with C extension".into(),
                vec![7, 15, 4]
            )
        );
        assert_eq!(
            parse_tool_version(
                "python3",
                "Python 3.14.4 (main, Aug 20 2026, 10:41:58) [GCC 15.2.0]\n",
                ""
            )
            .1,
            vec![3, 14, 4]
        );
        // The generic pattern needs a non-word character before the digits.
        assert_eq!(
            parse_tool_version("ld", "v1.2 x2.3 tool 4.5\n", "").1,
            vec![4, 5]
        );
        assert_eq!(
            parse_tool_version("clazy", "clazy built 1.2\n", ""),
            (String::new(), vec![])
        );
        assert_eq!(
            parse_tool_version("g++", "g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0\n", "").1,
            vec![15, 2, 0]
        );
    }

    #[test]
    fn compiler_rows_need_a_family_and_a_valid_target() {
        let target = ok(b"x86_64-linux-gnu\n");
        let row = row(
            "g++",
            "g++",
            None,
            &ok(b"g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0\n"),
            Some((Detail::Target, &target)),
        );
        assert!(row.available && row.complete && row.error.is_empty());
        assert_eq!(row.details["vendor"], "Ubuntu");
        assert_eq!(row.details["compiler_family"], "gcc");
        assert_eq!(row.details["target_triple"], "x86_64-linux-gnu");
        let bad = ok(b"not-a-triple\n");
        let row = super::row(
            "gcc",
            "gcc",
            None,
            &ok(b"gcc (GCC) 15.2.0\n"),
            Some((Detail::Target, &bad)),
        );
        assert!(row.available && !row.complete);
        assert_eq!(
            row.error,
            "metadata probe returned an invalid target triple"
        );
        let row = super::row("clang", "clang", None, &ok(b"weird 1.0\n"), None);
        assert_eq!(
            row.error,
            "probe did not identify a supported compiler family"
        );
    }

    #[test]
    fn a_failed_probe_is_an_unavailable_row_with_the_frozen_reason() {
        let failed = Probe {
            exit_code: Some(1),
            signal: None,
            timed_out: false,
            truncated: false,
            stdout: b"",
            stderr: b"No module named pytest",
        };
        let row = row("pytest", "python -m pytest", Some("pytest"), &failed, None);
        assert!(!row.available && !row.complete);
        assert_eq!(row.error, "probe exited 1");
        assert_eq!(row.details["provider"], "python-module");
        let timed = Probe {
            exit_code: None,
            signal: Some(9),
            timed_out: true,
            truncated: false,
            stdout: b"",
            stderr: b"",
        };
        assert_eq!(
            super::row("mypy", "python -m mypy", Some("mypy"), &timed, None).error,
            "probe timed out"
        );
    }

    #[test]
    fn qmake_and_cmake_metadata_follow_the_frozen_parsers() {
        let details = qmake_query(
            "QT_VERSION:6.8.2\nQMAKE_XSPEC:linux-g++\nQT_INSTALL_PREFIX:/usr\nQT_CONFIG: a  b\n",
        );
        assert_eq!(details["qt_major"], "6");
        assert_eq!(details["generator"], "linux-g++");
        assert_eq!(details["features"], "a b");
        let details = cmake_capabilities(
            r#"{"generators":[{"name":"Unix Makefiles"},{"name":"Ninja"},{"name":"Ninja"}],"serverMode":false}"#,
        );
        assert_eq!(details["generators"], "Ninja, Unix Makefiles");
        assert_eq!(details["server_mode"], "false");
        assert!(cmake_capabilities("not json").is_empty());
    }
}
