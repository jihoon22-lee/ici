//! Frozen legacy compiler-backed C++ function boundaries (`ici verify` only).
//!
//! Line-for-line port of the parts of frozen `analysis/_cpp_function_boundary_parser.py`,
//! `analysis/_cpp_function_boundaries.py` (source snapshots) and the clang-tidy text path of
//! `analysis/_cpp_diagnostics.py` (`parse_clang_tidy_diagnostics`) that turn one dedicated
//! `clang-tidy --checks=-*,readability-function-size` run into function boundaries, plus
//! `_merge_boundaries` over every replayed configuration. Boundaries are located with the brace
//! heuristic's masking helpers over the immutable source snapshot (never a syntax tree), so the
//! geometry, warnings and error messages are frozen's. The next path keeps its own
//! `tidy_boundaries` facts.
//!
//! Every index and column is a Unicode code point, as in Python.
use crate::legacy_heuristic::{
    boundary, cpp_definition_name, cpp_function_like_macro_names, cpp_has_conditional_directive,
    cpp_is_operator_name, cpp_requires_expression_before_brace, mask_cpp_lambda_bodies,
    mask_cpp_literals, py_space, py_word, rstrip, split_space, string,
};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
    sync::LazyLock,
    time::{Duration, Instant},
};

/// Frozen `_PARSER_TIMEOUT_SECONDS`: the parse budget of a call without a unit deadline.
pub const PARSER_BUDGET: Duration = Duration::from_secs(10);
/// Frozen parser ValueError once the deadline passes (_cpp_function_boundary_parser.py:566-567).
pub const PARSER_BUDGET_EXPIRED: &str = "function boundary parser budget expired";

const CHECK: &str = "readability-function-size";
/// Frozen `_MAX_BOUNDARIES`.
pub const MAX_BOUNDARIES: usize = 100_000;
/// Frozen `_MAX_SOURCE_BYTES` of one boundary source (`read_cpp_source_text`).
pub const MAX_SOURCE_BYTES: usize = 8 * 1024 * 1024;
/// Frozen `_MAX_SOURCE_CACHE_BYTES`.
pub const MAX_SOURCE_CACHE_BYTES: usize = 16 * 1024 * 1024;
/// Frozen `_MAX_RUN_SOURCE_BYTES`.
pub const MAX_RUN_SOURCE_BYTES: usize = 64 * 1024 * 1024;
const MAX_FUNCTION_NAME_CHARS: usize = 2_048;
const MAX_DIAGNOSTICS: usize = 10_000;
const MAX_DIAGNOSTIC_LINE: u64 = 2_147_483_647;
const MAX_MESSAGE_CHARS: usize = 8_192;
const MAX_DIAGNOSTIC_OUTPUT_CHARS: usize = 1_000_000;

// Python `\s` (`str.isspace`) is Rust's White_Space plus U+001C..U+001F.
static TEXT_DIAGNOSTIC: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:[\s\x1c-\x1f]*(?P<kind>fatal error|error|warning|note|remark):[\s\x1c-\x1f]*(?P<message>[^\s\x1c-\x1f].*?)(?:[\s\x1c-\x1f]+\[(?P<rule>[-A-Za-z0-9_.]+)\])?$",
    )
    .expect("frozen _TEXT_DIAGNOSTIC_RE")
});
static TEXT_CONTEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[\s\x1c-\x1f]*(?:\d+[\s\x1c-\x1f]*\|.*|\|.*|[\^~].*)$")
        .expect("frozen _TEXT_CONTEXT_RE")
});
static TEXT_CONTEXT_HEADER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^.+:[\s\x1c-\x1f]+(?:At global scope|In (?:function|member function|constructor|destructor|lambda function|instantiation of)(?: .*)?):$",
    )
    .expect("frozen _TEXT_CONTEXT_HEADER_RE")
});
static TEXT_REQUIRED_FROM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^.+:[1-9]\d*(?::[1-9]\d*)?:[\s\x1c-\x1f]+required (?:from|by)[\s\x1c-\x1f]+[^\s\x1c-\x1f].*$",
    )
    .expect("frozen _TEXT_REQUIRED_FROM_RE")
});
static TEXT_TRAILER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?:[1-9]\d* (?:warning|warnings|error|errors) generated\.|compilation terminated\.)$",
    )
    .expect("frozen _TEXT_TRAILER_RE")
});
static TEXT_FIXIT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"^fix-it:"(?P<file>(?:[^"\\]|\\.)*)":\{(?P<start_line>[1-9]\d*):(?P<start_column>[1-9]\d*)-(?P<end_line>[1-9]\d*):(?P<end_column>[1-9]\d*)\}:"(?P<replacement>(?:[^"\\]|\\.)*)"$"#,
    )
    .expect("frozen _TEXT_FIXIT_RE")
});
static TIDY_GENERATED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<count>[1-9]\d*) warnings? generated\.$").expect("frozen generated summary")
});
static TIDY_SUPPRESSED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^Suppressed (?P<count>[1-9]\d*) warnings? \([^\r\n]{1,512}\)\.$")
        .expect("frozen suppression summary")
});
static TIDY_HEADER_HINT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^Use -header-filter=.{1,512}(?: or leave it as default)? to display errors from all non-system headers\.(?: Use -system-headers to display errors from system headers as well\.)?$",
    )
    .expect("frozen header-filter hint")
});
static TIDY_EMPTY_NOTE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?P<file>.+?):(?P<line>[1-9]\d{0,9})(?::(?P<column>[1-9]\d{0,9}))?:[\s\x1c-\x1f]*note:[\s\x1c-\x1f]*$",
    )
    .expect("frozen empty note")
});
static TIDY_CONVERSION_NOTE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^'.{1,256}' and '.{1,256}' may be implicitly converted: [^\s\x1c-\x1f].{1,4096}$")
        .expect("frozen conversion note")
});
static TIDY_PARAMETER_RANGE_NOTE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^the (?:first|last) parameter in the range is '.{1,256}'$")
        .expect("frozen parameter range note")
});
static PARENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^warning: function '(?P<name>.+)' exceeds recommended size/complexity thresholds$")
        .expect("frozen _PARENT_RE")
});
static NOTES: LazyLock<[(&'static str, Regex); 3]> = LazyLock::new(|| {
    [
        (
            "lines",
            Regex::new(
                r"^note: (?P<value>[0-9]+) lines including whitespace and comments \(threshold 0\)$",
            )
            .expect("frozen _LINES_RE"),
        ),
        (
            "statements",
            Regex::new(r"^note: (?P<value>[0-9]+) statements \(threshold 0\)$")
                .expect("frozen _STATEMENTS_RE"),
        ),
        (
            "parameters",
            Regex::new(r"^note: (?P<value>[0-9]+) parameters \(threshold 0\)$")
                .expect("frozen _PARAMETERS_RE"),
        ),
    ]
});
static SUPPRESSED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^Suppressed [1-9][0-9]* warnings? \(").expect("frozen"));
static EXTERNAL_ONLY_SUPPRESSED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^Suppressed (?P<total>[1-9][0-9]*) warnings? \((?P<external>[1-9][0-9]*) in non-user code\)\.$",
    )
    .expect("frozen external-only suppression")
});

/// Frozen `CppFunctionConfigurationMetric`.
#[derive(Clone, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct ConfigurationMetric {
    pub configuration: String,
    pub lines: u64,
    pub statements: u64,
    pub parameters: u64,
}

/// Frozen `CppFunctionBoundary`: one AST-confirmed definition mapped back to project source.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LegacyBoundary {
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub start_column: Option<usize>,
    pub end_column: Option<usize>,
    pub body_start_line: usize,
    pub body_start_column: usize,
    pub name: String,
    pub lines: u64,
    pub statements: u64,
    pub parameters: u64,
    pub configurations: Vec<String>,
    pub configuration_metrics: Vec<ConfigurationMetric>,
    pub function_kind: String,
    pub is_template: bool,
    pub origin: String,
    pub metric_variant: bool,
    pub preprocessor_conditional: bool,
}

/// Frozen `CppFunctionBoundaryOutcome` without its tool evidence (the caller records the
/// processes it ran). `mode` is `unavailable`, `error`, `exact` or `partial`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct LegacyBoundaryOutcome {
    pub boundaries: Vec<LegacyBoundary>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub mode: String,
    pub configurations_checked: u64,
    pub sources_checked: u64,
    pub lambdas_excluded: u64,
    pub macro_functions_excluded: u64,
}

impl Default for LegacyBoundaryOutcome {
    fn default() -> Self {
        Self {
            boundaries: vec![],
            errors: vec![],
            warnings: vec![],
            mode: "unavailable".into(),
            configurations_checked: 0,
            sources_checked: 0,
            lambdas_excluded: 0,
            macro_functions_excluded: 0,
        }
    }
}

/// Frozen `_MappedSource`: the source text, its literal-masked structural copy (brace digraphs
/// normalized), masked line offsets, function-like macro names and lambda body count.
#[derive(Clone, Debug)]
pub struct MappedSource {
    text: Vec<char>,
    masked: Vec<char>,
    offsets: Vec<usize>,
    macro_names: BTreeSet<String>,
    pub lambda_count: usize,
}

/// Frozen `_mapped_source`; `Err` is the `ValueError` of the bounded lambda scan.
pub fn mapped_source(text: &str) -> Result<MappedSource, String> {
    let chars: Vec<char> = text.chars().collect();
    let masked: Vec<char> = string(&mask_cpp_literals(&chars))
        .replace("<%", "{ ")
        .replace("%>", "} ")
        .chars()
        .collect();
    let (_, lambdas) = mask_cpp_lambda_bodies(&masked)?;
    let macro_names = cpp_function_like_macro_names(&string(&masked));
    let mut offsets = vec![0];
    offsets.extend(
        masked
            .iter()
            .enumerate()
            .filter(|(_, character)| **character == '\n')
            .map(|(index, _)| index + 1),
    );
    Ok(MappedSource {
        text: chars,
        masked,
        offsets,
        macro_names,
        lambda_count: lambdas.len(),
    })
}

/// Frozen `_line_number`: the 1-based line of a masked offset.
fn line_number(offsets: &[usize], position: usize) -> usize {
    offsets.partition_point(|offset| *offset <= position).max(1)
}

/// Python `rfind` of one character in a slice (`-1` as `None`).
fn rfind(text: &[char], character: char) -> Option<usize> {
    text.iter().rposition(|item| *item == character)
}

/// One past the last statement separator of `text` (`max(rfind(";"), rfind("}"), rfind("{")) + 1`).
fn after_separator(text: &[char], separators: &[char]) -> usize {
    separators
        .iter()
        .filter_map(|separator| rfind(text, *separator))
        .max()
        .map_or(0, |index| index + 1)
}

/// Frozen `_closing_parenthesis` over the whole masked source.
fn closing_parenthesis(text: &[char], opening: usize) -> Option<usize> {
    let mut depth = 0i64;
    for (index, character) in text.iter().enumerate().skip(opening) {
        if *character == '(' {
            depth += 1;
        } else if *character == ')' {
            depth -= 1;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

/// Frozen `_macro_expansion_location`: the diagnostic lies inside a call to a source-defined
/// function-like macro (Clang reports a macro-generated function at the expansion argument).
fn macro_expansion_location(
    source: &MappedSource,
    start_line: usize,
    column: Option<usize>,
) -> bool {
    if start_line < 1 || start_line > source.offsets.len() || source.macro_names.is_empty() {
        return false;
    }
    let diagnostic = source.offsets[start_line - 1] + column.unwrap_or(1).saturating_sub(1);
    let search_start = diagnostic.saturating_sub(65_536);
    let search_end = source.masked.len().min(diagnostic + 4_096);
    if search_end <= search_start {
        return false;
    }
    let window = &source.masked[search_start..search_end];
    // `\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*\(` with `finditer`.
    let mut index = 0;
    while index < window.len() {
        let first = window[index];
        if !(first.is_ascii_alphabetic() || first == '_') || !boundary(window, index) {
            index += 1;
            continue;
        }
        let mut end = index + 1;
        while end < window.len() && (window[end].is_ascii_alphanumeric() || window[end] == '_') {
            end += 1;
        }
        let name = string(&window[index..end]);
        let mut opening = end;
        while opening < window.len() && matches!(window[opening], ' ' | '\t' | '\r' | '\n') {
            opening += 1;
        }
        if window.get(opening) != Some(&'(') {
            index += 1;
            continue;
        }
        let match_start = index;
        index = opening + 1;
        if !source.macro_names.contains(&name) {
            continue;
        }
        if diagnostic < search_start + match_start {
            break;
        }
        if let Some(closing) = closing_parenthesis(&source.masked, search_start + opening)
            && diagnostic <= closing
        {
            return true;
        }
    }
    false
}

/// Python `" ".join(name.split())`.
fn normalized(name: &str) -> String {
    let chars: Vec<char> = name.chars().collect();
    split_space(&chars)
        .into_iter()
        .map(string)
        .collect::<Vec<_>>()
        .join(" ")
}

/// Frozen `_same_function_name`.
fn same_function_name(observed: Option<&str>, expected: &str) -> bool {
    let Some(observed) = observed else {
        return false;
    };
    let observed = normalized(observed);
    let expected = normalized(expected);
    if cpp_is_operator_name(&observed) || cpp_is_operator_name(&expected) {
        let tail = |name: &str| -> String {
            let tail = match name.rfind("::operator") {
                Some(qualified) => &name[qualified + 2..],
                None => name,
            };
            tail.chars()
                .filter(|character| !py_space(*character))
                .collect()
        };
        return tail(&observed) == tail(&expected);
    }
    let last = |name: &str| name.rsplit("::").next().unwrap_or(name).to_owned();
    last(&observed) == last(&expected)
}

/// Frozen `_function_template_prefix`: `\btemplate\s*<` after the last statement separator in
/// the 4096 code points before the diagnostic.
fn function_template_prefix(
    source: &MappedSource,
    start_line: usize,
    column: Option<usize>,
) -> bool {
    let start = (source.offsets[start_line - 1] + column.unwrap_or(1).saturating_sub(1))
        .min(source.masked.len());
    let prefix = &source.masked[start.saturating_sub(4_096)..start];
    let tail = &prefix[after_separator(prefix, &[';', '{', '}'])..];
    let word: Vec<char> = "template".chars().collect();
    (0..tail.len()).any(|index| {
        tail[index..].starts_with(&word) && boundary(tail, index) && {
            let mut next = index + word.len();
            while next < tail.len() && py_space(tail[next]) {
                next += 1;
            }
            tail.get(next) == Some(&'<')
        }
    })
}

/// Frozen `_top_level_brace_pairs` from `start`: (open, close, parentheses, brackets) of each
/// outermost brace pair in scan order, stopping when `visit` returns a value.
fn top_level_brace_pairs<T>(
    masked: &[char],
    start: usize,
    mut visit: impl FnMut(usize, usize, usize, usize) -> Option<T>,
) -> Option<T> {
    let mut stack: Vec<(usize, usize, usize, usize)> = vec![];
    let (mut parentheses, mut brackets) = (0usize, 0usize);
    for (position, character) in masked.iter().enumerate().skip(start) {
        match character {
            '(' => parentheses += 1,
            ')' => parentheses = parentheses.saturating_sub(1),
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            '{' => stack.push((position, parentheses, brackets, stack.len())),
            '}' => {
                if let Some((opened, open_parentheses, open_brackets, depth)) = stack.pop()
                    && depth == 0
                    && let Some(found) = visit(opened, position, open_parentheses, open_brackets)
                {
                    return Some(found);
                }
            }
            _ => {}
        }
    }
    None
}

/// Frozen `_has_top_level_initializer_colon`.
fn has_top_level_initializer_colon(prefix: &[char]) -> bool {
    let (mut parentheses, mut brackets) = (0usize, 0usize);
    let mut found = false;
    for (index, character) in prefix.iter().enumerate() {
        match character {
            '(' => parentheses += 1,
            ')' => parentheses = parentheses.saturating_sub(1),
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            ':' if parentheses == 0
                && brackets == 0
                && (index == 0 || prefix[index - 1] != ':')
                && prefix.get(index + 1) != Some(&':') =>
            {
                found = true;
            }
            _ => {}
        }
    }
    found
}

/// Frozen `_constructor_initializer_brace`: a braced member/delegating initializer
/// (`(?:[A-Za-z_][A-Za-z0-9_]*|[>\]])$` after a top-level initializer colon).
fn constructor_initializer_brace(prefix: &[char]) -> bool {
    if !has_top_level_initializer_colon(prefix) {
        return false;
    }
    let stripped = rstrip(prefix);
    let Some(last) = stripped.last() else {
        return false;
    };
    if matches!(last, '>' | ']') {
        return true;
    }
    stripped
        .iter()
        .rev()
        .take_while(|character| character.is_ascii_alphanumeric() || **character == '_')
        .any(|character| character.is_ascii_alphabetic() || *character == '_')
}

/// Frozen `_catch_extended_close`: a function-try-block body ends after its last handler.
fn catch_extended_close(source: &MappedSource, close: usize) -> usize {
    let masked = &source.masked;
    let mut final_close = close;
    let mut cursor = close + 1;
    loop {
        // `re.match(r"\s*catch\b", masked[cursor:])`
        let mut index = cursor;
        while index < masked.len() && py_space(masked[index]) {
            index += 1;
        }
        let keyword: Vec<char> = "catch".chars().collect();
        if !masked[index.min(masked.len())..].starts_with(&keyword)
            || masked.get(index + 5).is_some_and(|next| py_word(*next))
        {
            return final_close;
        }
        let pair = top_level_brace_pairs(masked, index + 5, |opened, closed, p, b| {
            Some((opened, closed, p, b))
        });
        let Some((_opened, closed, parentheses, brackets)) = pair else {
            return final_close;
        };
        if parentheses > 0 || brackets > 0 {
            return final_close;
        }
        final_close = closed;
        cursor = final_close + 1;
    }
}

/// Frozen `_locate_body`: (body line, body column, end line, end column) from Clang's location
/// and its line-count note.
fn locate_body(
    source: &MappedSource,
    start_line: usize,
    column: Option<usize>,
    line_count: Option<u64>,
    name: &str,
) -> Result<(usize, usize, usize, usize), String> {
    let offsets = &source.offsets;
    if start_line > offsets.len() {
        return Err("function boundary starts beyond the source".into());
    }
    let line_start = offsets[start_line - 1];
    let diagnostic = (line_start + column.unwrap_or(1).saturating_sub(1)).min(source.masked.len());
    let line_prefix = &source.masked[line_start.min(diagnostic)..diagnostic];
    let start = line_start + after_separator(line_prefix, &[';', '}', '{']);
    let mut failure = None;
    let found = top_level_brace_pairs(&source.masked, start, |opened, closed, p, b| {
        if p > 0 || b > 0 {
            return None;
        }
        let open_line = line_number(offsets, opened);
        let prefix = &source.masked[start..opened];
        if cpp_requires_expression_before_brace(prefix) || constructor_initializer_brace(prefix) {
            return None;
        }
        if !same_function_name(cpp_definition_name(prefix).as_deref(), name) {
            return None;
        }
        let final_close = catch_extended_close(source, closed);
        let close_line = line_number(offsets, final_close);
        let expected = line_count.unwrap_or(0);
        if (close_line as i64 - open_line as i64) != expected as i64 {
            failure = Some(if line_count.is_none() {
                "function-size lines note is required for a multi-line body"
            } else {
                "function-size lines note does not match the mapped body"
            });
            return Some(None);
        }
        Some(Some((
            open_line,
            opened - offsets[open_line - 1] + 1,
            close_line,
            final_close - offsets[close_line - 1] + 1,
        )))
    });
    match found {
        Some(Some(geometry)) => Ok(geometry),
        Some(None) => Err(failure.expect("recorded mismatch").into()),
        None => Err("function body could not be mapped from clang-tidy evidence".into()),
    }
}

/// One frozen `CppDiagnostic` of a clang-tidy transcript: project-relative path (or
/// `[external]`), line, optional column, `<kind>: <message>` and check id; notes are grouped
/// under their primary.
#[derive(Clone, Debug)]
struct Diagnostic {
    file_path: String,
    line: u64,
    column: Option<u64>,
    message: String,
    rule: String,
    related: Vec<Diagnostic>,
}

/// Python `repr()` of a `str`.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ if character == quote => {
                out.push('\\');
                out.push(character);
            }
            _ if character.is_control() || (character.is_whitespace() && character != ' ') => {
                let code = character as u32;
                if code < 0x100 {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code < 0x10000 {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
            _ => out.push(character),
        }
    }
    out.push(quote);
    out
}

/// Python `str.strip()`.
fn py_strip(text: &str) -> &str {
    text.trim_matches(|character: char| py_space(character))
}

/// Python `str.splitlines()`.
fn py_lines(text: &str) -> Vec<String> {
    crate::py_splitlines(text)
}

/// Frozen `_bounded_text`.
fn bounded_text(value: &str, label: &str, limit: usize) -> Result<String, String> {
    if py_strip(value).is_empty() || value.contains('\0') {
        return Err(format!("{label} must be a non-empty string"));
    }
    if value.chars().count() > limit {
        return Err(format!("{label} exceeds the bounded size"));
    }
    Ok(py_strip(value).to_owned())
}

/// Frozen `_text_number` (`_number` range check).
fn text_number(value: &str, label: &str) -> Result<u64, String> {
    if value.chars().count() > 10 {
        return Err(format!("{label} is invalid"));
    }
    let parsed: u64 = value.parse().map_err(|_| format!("{label} is invalid"))?;
    if parsed == 0 || parsed > MAX_DIAGNOSTIC_LINE {
        return Err(format!("{label} is outside the supported range"));
    }
    Ok(parsed)
}

/// Python `Path.resolve(strict=False)`: symlinks of the existing prefix resolved, the rest
/// normalized lexically.
fn resolve_lenient(path: &Path) -> PathBuf {
    if let Ok(resolved) = path.canonicalize() {
        return resolved;
    }
    let mut resolved = PathBuf::new();
    let mut existing = true;
    for component in path.components() {
        match component {
            Component::RootDir => resolved.push("/"),
            Component::CurDir | Component::Prefix(_) => {}
            Component::ParentDir => {
                resolved.pop();
            }
            Component::Normal(name) => {
                resolved.push(name);
                if existing {
                    match resolved.canonicalize() {
                        Ok(canonical) => resolved = canonical,
                        Err(_) => existing = false,
                    }
                }
            }
        }
    }
    resolved
}

/// Frozen `_diagnostic_path`: the project-relative POSIX path, `[external]` outside the project.
fn diagnostic_path(root: &Path, cwd: &Path, value: &str) -> Result<String, String> {
    let raw = bounded_text(value, "diagnostic file", 4_096)?;
    let lexical = Path::new(&raw);
    let path = resolve_lenient(&if lexical.is_absolute() {
        lexical.to_path_buf()
    } else {
        cwd.join(lexical)
    });
    Ok(path.strip_prefix(root).ok().map_or_else(
        || "[external]".into(),
        |relative| {
            if relative.as_os_str().is_empty() {
                ".".into()
            } else {
                relative.display().to_string()
            }
        },
    ))
}

/// Frozen `_ClangTidyTextState` captures of one diagnostic line.
#[derive(Clone)]
struct Line {
    kind: String,
    file: String,
    line: String,
    column: Option<String>,
    message: String,
    rule: Option<String>,
}

fn diagnostic_line(line: &str) -> Option<Line> {
    TEXT_DIAGNOSTIC.captures(line).map(|captures| Line {
        kind: captures["kind"].to_owned(),
        file: captures["file"].to_owned(),
        line: captures["line"].to_owned(),
        column: captures
            .name("column")
            .map(|value| value.as_str().to_owned()),
        message: captures["message"].to_owned(),
        rule: captures.name("rule").map(|value| value.as_str().to_owned()),
    })
}

#[derive(Default)]
struct TidyText {
    retained: Vec<String>,
    generated: Option<u64>,
    suppressed: u64,
    header_hint: bool,
    parent: Option<Line>,
    last_parameter_line: Option<String>,
    pending_empty_note: Option<(String, String, Option<String>)>,
}

const EMPTY_NOTE_ERROR: &str = "clang-tidy empty note is not a valid bugprone structural separator";

/// Frozen `_split_clang_tidy_text`: the retained diagnostic lines and the summary accounting.
fn split_tidy_text(stdout: &str, stderr: &str) -> Result<TidyText, String> {
    let mut state = TidyText::default();
    for raw in py_lines(&format!("{stdout}\n{stderr}")) {
        let line = py_strip(&raw);
        if line.is_empty() {
            continue;
        }
        if let Some(captures) = TIDY_EMPTY_NOTE.captures(line) {
            let file = captures["file"].to_owned();
            let number = captures["line"].to_owned();
            let column = captures
                .name("column")
                .map(|value| value.as_str().to_owned());
            let bounded = file.chars().count() <= 4_096
                && number
                    .parse::<u64>()
                    .is_ok_and(|value| value <= MAX_DIAGNOSTIC_LINE)
                && column.as_deref().is_none_or(|value| {
                    value
                        .parse::<u64>()
                        .is_ok_and(|value| value <= MAX_DIAGNOSTIC_LINE)
                });
            let expected = state.parent.as_ref().is_some_and(|parent| {
                parent.kind == "warning"
                    && parent.rule.as_deref() == Some("bugprone-easily-swappable-parameters")
                    && parent.file == file
                    && parent.line == number
                    && parent.column == column
            });
            if state.pending_empty_note.is_some() || !bounded || !expected {
                return Err(EMPTY_NOTE_ERROR.into());
            }
            state.pending_empty_note = Some((file, number, column));
            continue;
        }
        if let Some(diagnostic) = diagnostic_line(line) {
            if let Some((file, _, _)) = state.pending_empty_note.clone() {
                let expected = match (&state.parent, &state.last_parameter_line) {
                    (Some(parent), Some(last))
                        if diagnostic.kind == "note"
                            && diagnostic.rule.is_none()
                            && diagnostic.file == file
                            && diagnostic.file == parent.file
                            && TIDY_CONVERSION_NOTE.is_match(&diagnostic.message) =>
                    {
                        [&parent.line, &diagnostic.line, last]
                            .iter()
                            .all(|value| value.chars().count() <= 10)
                            && {
                                let first: u64 = parent.line.parse().unwrap_or(u64::MAX);
                                let at: u64 = diagnostic.line.parse().unwrap_or(u64::MAX);
                                let end: u64 = last.parse().unwrap_or(u64::MAX);
                                first <= at && at <= end && end <= MAX_DIAGNOSTIC_LINE
                            }
                    }
                    _ => false,
                };
                if !expected {
                    return Err(EMPTY_NOTE_ERROR.into());
                }
                state.pending_empty_note = None;
            } else if diagnostic.rule.is_some() {
                state.parent = Some(diagnostic.clone());
                state.last_parameter_line = None;
            } else if let Some(parent) = &state.parent {
                let range = diagnostic.kind == "note"
                    && diagnostic.file == parent.file
                    && TIDY_PARAMETER_RANGE_NOTE.is_match(&diagnostic.message);
                if !range {
                    state.parent = None;
                    state.last_parameter_line = None;
                } else if diagnostic.message.starts_with("the last parameter") {
                    state.last_parameter_line = Some(diagnostic.line.clone());
                }
            }
            state.retained.push(raw.clone());
            continue;
        }
        if TEXT_CONTEXT.is_match(line) {
            state.retained.push(raw.clone());
            continue;
        }
        if state.pending_empty_note.is_some() {
            return Err(EMPTY_NOTE_ERROR.into());
        }
        if let Some(captures) = TIDY_GENERATED.captures(line) {
            if state.generated.is_some() {
                return Err("clang-tidy emitted duplicate generated-warning summaries".into());
            }
            state.generated = Some(captures["count"].parse().unwrap_or(u64::MAX));
            state.parent = None;
            state.last_parameter_line = None;
            continue;
        }
        if let Some(captures) = TIDY_SUPPRESSED.captures(line) {
            if state.suppressed > 0 {
                return Err("clang-tidy emitted duplicate suppression summaries".into());
            }
            state.suppressed = captures["count"].parse().unwrap_or(u64::MAX);
            state.parent = None;
            state.last_parameter_line = None;
            continue;
        }
        if TIDY_HEADER_HINT.is_match(line) {
            state.header_hint = true;
            state.parent = None;
            state.last_parameter_line = None;
            continue;
        }
        state.parent = None;
        state.last_parameter_line = None;
        state.retained.push(raw.clone());
    }
    if state.pending_empty_note.is_some() {
        return Err(EMPTY_NOTE_ERROR.into());
    }
    Ok(state)
}

/// Frozen `_parse_text` over the retained clang-tidy lines (fix-its are validated, not kept).
fn parse_text(root: &Path, cwd: &Path, text: &str) -> Result<Vec<Diagnostic>, String> {
    let mut output: Vec<Diagnostic> = vec![];
    let mut ancillary = false;
    for raw in py_lines(text) {
        let line = py_strip(&raw);
        if line.is_empty() {
            continue;
        }
        if let Some(diagnostic) = diagnostic_line(line) {
            if output.len() >= MAX_DIAGNOSTICS {
                return Err("diagnostic count exceeds the bounded limit".into());
            }
            let file_path = diagnostic_path(root, cwd, &diagnostic.file)?;
            let number = text_number(&diagnostic.line, "diagnostic line")?;
            let column = diagnostic
                .column
                .as_deref()
                .map(|value| text_number(value, "diagnostic column"))
                .transpose()?;
            let message =
                bounded_text(&diagnostic.message, "diagnostic message", MAX_MESSAGE_CHARS)?;
            output.push(Diagnostic {
                file_path,
                line: number,
                column,
                message: format!("{}: {message}", diagnostic.kind.to_lowercase()),
                rule: diagnostic.rule.unwrap_or_default(),
                related: vec![],
            });
            continue;
        }
        if let Some(captures) = TEXT_FIXIT.captures(line) {
            if output.is_empty() {
                return Err("fix-it appeared before a diagnostic".into());
            }
            let decode = |value: &str, label: &str| -> Result<String, String> {
                serde_json::from_str::<String>(&format!("\"{value}\""))
                    .map_err(|_| format!("{label} is not a supported quoted string"))
            };
            let file = decode(&captures["file"], "fix-it file")?;
            bounded_text(&file, "fix-it file", 4_096)?;
            let replacement = decode(&captures["replacement"], "fix-it replacement")?;
            if replacement.contains('\0') {
                return Err("fix-it replacement must be a string".into());
            }
            if replacement.chars().count() > MAX_MESSAGE_CHARS {
                return Err("fix-it replacement exceeds the bounded size".into());
            }
            let start = (
                text_number(&captures["start_line"], "fix-it start line")?,
                text_number(&captures["start_column"], "fix-it start column")?,
            );
            let end = (
                text_number(&captures["end_line"], "fix-it end line")?,
                text_number(&captures["end_column"], "fix-it end column")?,
            );
            if end < start {
                return Err("fix-it range is reversed".into());
            }
            continue;
        }
        if line.starts_with("In file included from") || line.starts_with("from ") {
            ancillary = true;
            continue;
        }
        if !output.is_empty() && TEXT_CONTEXT.is_match(line) {
            continue;
        }
        if TEXT_CONTEXT_HEADER.is_match(line)
            || TEXT_REQUIRED_FROM.is_match(line)
            || TEXT_TRAILER.is_match(line)
        {
            ancillary = true;
            continue;
        }
        return Err(format!(
            "unrecognized compiler output line: {}",
            py_repr(line)
        ));
    }
    if ancillary && output.is_empty() {
        return Err("compiler context output has no located diagnostic".into());
    }
    Ok(output)
}

/// Frozen `parse_clang_tidy_diagnostics` (text): primaries with their grouped notes.
fn parse_tidy_diagnostics(
    root: &Path,
    cwd: &Path,
    stdout: &str,
    stderr: &str,
) -> Result<Vec<Diagnostic>, String> {
    if stdout.chars().count() + stderr.chars().count() > MAX_DIAGNOSTIC_OUTPUT_CHARS {
        return Err("clang-tidy diagnostic output exceeds the bounded size".into());
    }
    if stdout.contains('\0') || stderr.contains('\0') {
        return Err("clang-tidy diagnostic output contains a null byte".into());
    }
    let text = split_tidy_text(stdout, stderr)?;
    let parsed = parse_text(root, cwd, &text.retained.join("\n"))?;
    let mut primaries: Vec<Diagnostic> = vec![];
    for diagnostic in parsed {
        if diagnostic.message.starts_with("note:") {
            let Some(parent) = primaries.last_mut() else {
                return Err("clang-tidy note has no preceding primary diagnostic".into());
            };
            if !diagnostic.rule.is_empty() && diagnostic.rule != parent.rule {
                return Err("clang-tidy note check does not match its primary diagnostic".into());
            }
            let rule = parent.rule.clone();
            parent.related.push(Diagnostic { rule, ..diagnostic });
            continue;
        }
        if diagnostic.rule.is_empty() {
            return Err("clang-tidy diagnostic has no check identifier".into());
        }
        primaries.push(diagnostic);
    }
    if text.generated.is_some() && primaries.is_empty() && text.suppressed == 0 {
        return Err("clang-tidy generated-warning summary has no diagnostic accounting".into());
    }
    if text.header_hint && text.generated.is_none() && text.suppressed == 0 && primaries.is_empty()
    {
        return Err("clang-tidy header-filter hint has no diagnostic summary".into());
    }
    Ok(primaries)
}

struct Pending {
    diagnostic: Diagnostic,
    name: String,
    values: BTreeMap<&'static str, u64>,
}

/// A macro-generated function frozen excluded: (file, line, column, name).
pub type ExcludedScope = (String, usize, Option<usize>, String);

fn pending_name(name: &str) -> Result<String, String> {
    if name.is_empty() || name.chars().count() > MAX_FUNCTION_NAME_CHARS || name.contains('\0') {
        return Err("function boundary name is outside the bounded shape".into());
    }
    Ok(name.to_owned())
}

/// Frozen `_note_value`.
fn note_value(pending: &mut Pending, diagnostic: &Diagnostic) -> Result<(), String> {
    let parent = &pending.diagnostic;
    if (&diagnostic.file_path, diagnostic.line, diagnostic.column)
        != (&parent.file_path, parent.line, parent.column)
    {
        return Err("function-size note does not match its parent location".into());
    }
    for (field, pattern) in NOTES.iter() {
        let Some(captures) = pattern.captures(&diagnostic.message) else {
            continue;
        };
        if pending.values.contains_key(field) {
            return Err(format!("function-size emitted duplicate {field} notes"));
        }
        let value: u64 = captures["value"].parse().unwrap_or(u64::MAX);
        if value == 0 {
            return Err("function-size note must contain a positive metric".into());
        }
        pending.values.insert(field, value);
        return Ok(());
    }
    Err(format!(
        "unexpected function-size note: {}",
        py_repr(&diagnostic.message)
    ))
}

/// Frozen `_finish_pending` over the unit's own source snapshot.
fn finish_pending(
    configuration: &str,
    pending: Pending,
    source: &MappedSource,
    excluded: &mut Vec<ExcludedScope>,
) -> Result<Option<LegacyBoundary>, String> {
    let target = &pending.diagnostic;
    let start_line = target.line as usize;
    let column = target.column.map(|column| column as usize);
    if macro_expansion_location(source, start_line, column) {
        excluded.push((target.file_path.clone(), start_line, column, pending.name));
        return Ok(None);
    }
    let (body_line, body_column, end_line, end_column) = locate_body(
        source,
        start_line,
        column,
        pending.values.get("lines").copied(),
        &pending.name,
    )?;
    let start = source.offsets[body_line - 1] + body_column - 1;
    let end = (source.offsets[end_line - 1] + end_column).min(source.text.len());
    let region = &source.text[start.min(end)..end];
    let (metric_region, _) = mask_cpp_lambda_bodies(&mask_cpp_literals(region))?;
    let lines = pending.values.get("lines").copied().unwrap_or(0);
    let statements = pending.values.get("statements").copied().unwrap_or(0);
    let parameters = pending.values.get("parameters").copied().unwrap_or(0);
    Ok(Some(LegacyBoundary {
        file_path: target.file_path.clone(),
        start_line,
        end_line,
        start_column: column,
        end_column: Some(end_column),
        body_start_line: body_line,
        body_start_column: body_column,
        lines,
        statements,
        parameters,
        configurations: vec![configuration.to_owned()],
        configuration_metrics: vec![ConfigurationMetric {
            configuration: configuration.to_owned(),
            lines,
            statements,
            parameters,
        }],
        function_kind: if cpp_is_operator_name(&pending.name) {
            "operator"
        } else {
            "function"
        }
        .into(),
        is_template: function_template_prefix(source, start_line, column),
        origin: "source-spelled".into(),
        metric_variant: false,
        preprocessor_conditional: cpp_has_conditional_directive(&metric_region),
        name: pending.name,
    }))
}

/// Frozen `parse_function_boundaries` of one unit's dedicated clang-tidy run: its boundaries
/// in diagnostic order and the macro-generated definitions it excluded. `Err` is the frozen
/// `ValueError` message ("function boundary output rejected for <source>: <message>"), under
/// frozen's default 10 s parse budget ([`PARSER_BUDGET`]).
pub fn parse_function_boundaries(
    root: &Path,
    cwd: &Path,
    stdout: &str,
    stderr: &str,
    configuration: &str,
    source_file: &str,
    source: &MappedSource,
) -> Result<(Vec<LegacyBoundary>, Vec<ExcludedScope>), String> {
    let deadline = Instant::now() + PARSER_BUDGET;
    parse_function_boundaries_until(
        root,
        cwd,
        stdout,
        stderr,
        configuration,
        source_file,
        source,
        deadline,
    )
}

/// [`parse_function_boundaries`] under the unit's own deadline (frozen `_run_unit` passes it):
/// past it each diagnostic is [`PARSER_BUDGET_EXPIRED`].
#[allow(clippy::too_many_arguments)]
pub fn parse_function_boundaries_until(
    root: &Path,
    cwd: &Path,
    stdout: &str,
    stderr: &str,
    configuration: &str,
    source_file: &str,
    source: &MappedSource,
    deadline: Instant,
) -> Result<(Vec<LegacyBoundary>, Vec<ExcludedScope>), String> {
    let mut external = false;
    for line in py_lines(&format!("{stdout}\n{stderr}")) {
        let summary = py_strip(&line);
        if !SUPPRESSED.is_match(summary) {
            continue;
        }
        let only = EXTERNAL_ONLY_SUPPRESSED
            .captures(summary)
            .filter(|captures| captures["total"] == captures["external"]);
        if only.is_none() {
            return Err("function boundary diagnostics were suppressed".into());
        }
        external = true;
    }
    let root = resolve_lenient(root);
    let parsed = parse_tidy_diagnostics(&root, cwd, stdout, stderr)
        .map_err(|error| format!("function boundary output is not parseable: {error}"))?;
    let diagnostics: Vec<Diagnostic> = parsed
        .into_iter()
        .filter(|primary| primary.file_path == source_file)
        .flat_map(|mut primary| {
            let related = std::mem::take(&mut primary.related);
            std::iter::once(primary).chain(related)
        })
        .collect();
    if external && diagnostics.is_empty() {
        return Err("function boundary suppression had no visible project diagnostics".into());
    }
    if diagnostics.len() > MAX_BOUNDARIES * 4 {
        return Err("function boundary diagnostic count exceeds the bounded limit".into());
    }
    let mut boundaries = vec![];
    let mut excluded = vec![];
    let mut pending: Option<Pending> = None;
    for diagnostic in diagnostics {
        if Instant::now() > deadline {
            return Err(PARSER_BUDGET_EXPIRED.into());
        }
        if diagnostic.rule != CHECK {
            return Err("function boundary output contains an unexpected check".into());
        }
        if let Some(captures) = PARENT.captures(&diagnostic.message) {
            let name = pending_name(&captures["name"])?;
            if let Some(previous) = pending.take()
                && let Some(finished) =
                    finish_pending(configuration, previous, source, &mut excluded)?
            {
                boundaries.push(finished);
            }
            pending = Some(Pending {
                diagnostic,
                name,
                values: BTreeMap::new(),
            });
            continue;
        }
        let Some(current) = pending
            .as_mut()
            .filter(|_| diagnostic.message.starts_with("note:"))
        else {
            return Err("function-size note appeared without a function warning".into());
        };
        note_value(current, &diagnostic)?;
    }
    if let Some(previous) = pending
        && let Some(finished) = finish_pending(configuration, previous, source, &mut excluded)?
    {
        boundaries.push(finished);
    }
    if boundaries.len() > MAX_BOUNDARIES {
        return Err("function boundary count exceeds the bounded limit".into());
    }
    Ok((boundaries, excluded))
}

/// Frozen `_merge_boundaries`: one boundary per (file, line, column) over every successful
/// configuration, configuration-dependent geometry or coverage withheld and the frozen
/// warnings (each kind once per file and line), ordered by file, line, column and name.
pub fn merge_boundaries(
    boundaries: Vec<LegacyBoundary>,
    successful: &BTreeMap<String, BTreeSet<String>>,
) -> (Vec<LegacyBoundary>, Vec<String>) {
    type Key = (String, usize, Option<usize>);
    let mut grouped: Vec<(Key, LegacyBoundary)> = vec![];
    let mut rejected: BTreeSet<Key> = BTreeSet::new();
    let mut warnings = vec![];
    let mut warned: BTreeSet<(&'static str, String, usize)> = BTreeSet::new();
    let mut warn = |kind: &'static str, boundary: &LegacyBoundary, message: String| {
        if warned.insert((kind, boundary.file_path.clone(), boundary.start_line)) {
            warnings.push(message);
        }
    };
    for boundary in boundaries {
        let key = (
            boundary.file_path.clone(),
            boundary.start_line,
            boundary.start_column,
        );
        if rejected.contains(&key) {
            continue;
        }
        let Some(position) = grouped.iter().position(|(existing, _)| *existing == key) else {
            grouped.push((key, boundary));
            continue;
        };
        let existing = &grouped[position].1;
        let shape = |item: &LegacyBoundary| {
            (
                item.end_line,
                item.end_column,
                item.body_start_line,
                item.body_start_column,
                item.name.clone(),
                item.function_kind.clone(),
                item.is_template,
                item.origin.clone(),
            )
        };
        if shape(existing) != shape(&boundary) {
            warn(
                "scope",
                &boundary,
                format!(
                    "configuration-dependent function boundary was not promoted: {}:{}",
                    boundary.file_path, boundary.start_line
                ),
            );
            grouped.remove(position);
            rejected.insert(key);
            continue;
        }
        let mut metrics: Vec<ConfigurationMetric> = existing
            .configuration_metrics
            .iter()
            .chain(&boundary.configuration_metrics)
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        metrics.sort();
        let values: BTreeSet<(u64, u64, u64)> = metrics
            .iter()
            .map(|item| (item.lines, item.statements, item.parameters))
            .collect();
        let variant = existing.metric_variant || boundary.metric_variant || values.len() > 1;
        if variant {
            warn(
                "metric",
                &boundary,
                format!(
                    "configuration-dependent function metrics remain estimated: {}:{}",
                    boundary.file_path, boundary.start_line
                ),
            );
        }
        let configurations: BTreeSet<String> = existing
            .configurations
            .iter()
            .chain(&boundary.configurations)
            .cloned()
            .collect();
        let merged = LegacyBoundary {
            lines: existing.lines.max(boundary.lines),
            statements: existing.statements.max(boundary.statements),
            parameters: existing.parameters.max(boundary.parameters),
            configurations: configurations.into_iter().collect(),
            configuration_metrics: metrics,
            metric_variant: variant,
            preprocessor_conditional: existing.preprocessor_conditional
                || boundary.preprocessor_conditional,
            ..existing.clone()
        };
        grouped[position].1 = merged;
    }
    let mut kept = vec![];
    for (_, boundary) in grouped {
        let complete = successful.get(&boundary.file_path).is_none_or(|expected| {
            boundary
                .configurations
                .iter()
                .cloned()
                .collect::<BTreeSet<_>>()
                == *expected
        });
        if complete {
            kept.push(boundary);
        } else {
            warn(
                "coverage",
                &boundary,
                format!(
                    "configuration-dependent function boundary was not promoted: {}:{}",
                    boundary.file_path, boundary.start_line
                ),
            );
        }
    }
    for boundary in &kept {
        if boundary.preprocessor_conditional {
            warn(
                "preprocessor",
                boundary,
                format!(
                    "preprocessor-dependent function metrics remain estimated: {}:{}",
                    boundary.file_path, boundary.start_line
                ),
            );
        }
    }
    kept.sort_by(|left, right| {
        (
            &left.file_path,
            left.start_line,
            left.start_column.unwrap_or(0),
            &left.name,
        )
            .cmp(&(
                &right.file_path,
                right.start_line,
                right.start_column.unwrap_or(0),
                &right.name,
            ))
    });
    (kept, warnings)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mapped(text: &str) -> MappedSource {
        mapped_source(text).unwrap()
    }

    #[test]
    fn a_located_definition_maps_its_body_and_geometry() {
        let source = mapped("int f(int n) {\n  return n;\n}\n");
        assert_eq!(
            locate_body(&source, 1, Some(5), Some(2), "f"),
            Ok((1, 14, 3, 1))
        );
        assert_eq!(
            locate_body(&source, 1, Some(5), Some(1), "f"),
            Err("function-size lines note does not match the mapped body".into())
        );
        assert_eq!(
            locate_body(&source, 1, Some(5), None, "f"),
            Err("function-size lines note is required for a multi-line body".into())
        );
        assert_eq!(
            locate_body(&source, 1, Some(5), Some(2), "g"),
            Err("function body could not be mapped from clang-tidy evidence".into())
        );
        assert_eq!(
            locate_body(&source, 9, Some(1), None, "f"),
            Err("function boundary starts beyond the source".into())
        );
    }

    #[test]
    fn a_macro_expansion_and_a_template_head_are_recognized() {
        let source = mapped("#define DEFINE(name) int name() { return 1; }\nDEFINE(made)\n");
        assert!(macro_expansion_location(&source, 2, Some(8)));
        assert!(!macro_expansion_location(&source, 1, Some(1)));
        let template = mapped("template <typename T>\nT pick(T a) { return a; }\n");
        assert!(function_template_prefix(&template, 2, Some(3)));
        assert!(!function_template_prefix(
            &mapped("int f() { return 0; }\n"),
            1,
            Some(5)
        ));
    }

    #[test]
    fn notes_must_follow_their_warning_at_the_same_location() {
        let root = Path::new("/nonexistent-root");
        let source = mapped("int f(int n) {\n  return n;\n}\n");
        let warning = "/nonexistent-root/a.cpp:1:5: warning: function 'f' exceeds recommended size/complexity thresholds [readability-function-size]";
        let note = |text: &str| format!("/nonexistent-root/a.cpp:1:5: note: {text}");
        let ok = format!(
            "{warning}\n{}\n{}\n{}\n",
            note("2 lines including whitespace and comments (threshold 0)"),
            note("1 statements (threshold 0)"),
            note("1 parameters (threshold 0)")
        );
        let (found, excluded) =
            parse_function_boundaries(root, root, &ok, "", "sha256:x", "a.cpp", &source).unwrap();
        assert!(excluded.is_empty());
        assert_eq!(
            (
                found[0].start_line,
                found[0].end_line,
                found[0].end_column,
                found[0].statements
            ),
            (1, 3, Some(1), 1)
        );
        // Frozen `_PARSER_TIMEOUT_SECONDS = 10.0` and, past the deadline,
        // `raise ValueError("function boundary parser budget expired")`
        // (_cpp_function_boundary_parser.py:36, 566-567).
        assert_eq!(PARSER_BUDGET, std::time::Duration::from_secs(10));
        let expired = std::time::Instant::now() - std::time::Duration::from_millis(1);
        assert_eq!(
            parse_function_boundaries_until(root, root, &ok, "", "c", "a.cpp", &source, expired),
            Err("function boundary parser budget expired".into())
        );
        let duplicate = format!("{ok}{}\n", note("1 statements (threshold 0)"));
        assert_eq!(
            parse_function_boundaries(root, root, &duplicate, "", "c", "a.cpp", &source),
            Err("function-size emitted duplicate statements notes".into())
        );
        let unexpected = format!("{warning}\n{}\n", note("odd"));
        assert_eq!(
            parse_function_boundaries(root, root, &unexpected, "", "c", "a.cpp", &source),
            Err("unexpected function-size note: 'note: odd'".into())
        );
        assert_eq!(
            parse_function_boundaries(root, root, "garbage\n", "", "c", "a.cpp", &source),
            Err("function boundary output is not parseable: unrecognized compiler output line: 'garbage'".into())
        );
        assert_eq!(
            parse_function_boundaries(
                root,
                root,
                "Suppressed 2 warnings (1 in non-user code).\n",
                "",
                "c",
                "a.cpp",
                &source
            ),
            Err("function boundary diagnostics were suppressed".into())
        );
    }

    #[test]
    fn merging_withholds_configuration_dependent_boundaries() {
        let boundary = |configuration: &str, statements: u64, end_line: usize| LegacyBoundary {
            file_path: "a.cpp".into(),
            start_line: 1,
            end_line,
            start_column: Some(5),
            end_column: Some(1),
            name: "f".into(),
            statements,
            configurations: vec![configuration.into()],
            configuration_metrics: vec![ConfigurationMetric {
                configuration: configuration.into(),
                statements,
                ..Default::default()
            }],
            function_kind: "function".into(),
            origin: "source-spelled".into(),
            ..Default::default()
        };
        let both = BTreeMap::from([(
            "a.cpp".to_owned(),
            BTreeSet::from(["a".to_owned(), "b".to_owned()]),
        )]);
        let (merged, warnings) =
            merge_boundaries(vec![boundary("a", 1, 3), boundary("b", 2, 3)], &both);
        assert_eq!(merged.len(), 1);
        assert!(merged[0].metric_variant);
        assert_eq!(merged[0].statements, 2);
        assert_eq!(
            warnings,
            ["configuration-dependent function metrics remain estimated: a.cpp:1"]
        );
        let (merged, warnings) =
            merge_boundaries(vec![boundary("a", 1, 3), boundary("b", 1, 4)], &both);
        assert!(merged.is_empty());
        assert_eq!(
            warnings,
            ["configuration-dependent function boundary was not promoted: a.cpp:1"]
        );
        let (merged, warnings) = merge_boundaries(vec![boundary("a", 1, 3)], &both);
        assert!(merged.is_empty());
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn python_repr_quotes_like_python() {
        assert_eq!(py_repr("a'b"), "\"a'b\"");
        assert_eq!(py_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(py_repr("x\u{1}"), "'x\\x01'");
    }
}
