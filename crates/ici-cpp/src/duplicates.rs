//! Translation-phase-aware bounded C++ Type-2 normalization, shared matching in ici-analysis.
use crate::{CppError, legacy_heuristic::py_space};
use ici_analysis::duplicates::DuplicateFileData;
use regex::Regex;
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BTreeSet, BinaryHeap},
    sync::{
        LazyLock,
        atomic::{AtomicBool, Ordering},
    },
};
static NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<core>0[xX][0-9A-Fa-f](?:'?[0-9A-Fa-f])*(?:\.(?:[0-9A-Fa-f](?:'?[0-9A-Fa-f])*)?)?(?:[pP][+-]?[0-9](?:'?[0-9])*)?|0[bB][01](?:'?[01])*|(?:[0-9](?:'?[0-9])*\.(?:[0-9](?:'?[0-9])*)?|\.[0-9](?:'?[0-9])*)(?:[eE][+-]?[0-9](?:'?[0-9])*)?|[0-9](?:'?[0-9])*[eE][+-]?[0-9](?:'?[0-9])*|[0-9](?:'?[0-9])*)(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)?").unwrap()
});
const KEYWORDS: &str = "alignas alignof and and_eq asm atomic_cancel atomic_commit atomic_noexcept auto bitand bitor bool break case catch char char8_t char16_t char32_t class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit export extern false final float for friend goto if import inline int long module mutable namespace new noexcept not not_eq nullptr operator or or_eq override private protected public reflexpr register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch synchronized template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor xor_eq";
const ATTRIBUTES: &str = "assume carries_dependency deprecated fallthrough likely maybe_unused no_unique_address nodiscard noreturn unlikely";
const QT: &str = "CONSTANT DESIGNABLE FINAL NOTIFY READ REQUIRED RESET REVISION SCRIPTABLE STORED USER WRITE QByteArrayLiteral QLatin1Char QLatin1String QStringLiteral Qt connect disconnect emit foreach forever invokeMethod qOverload qRegisterMetaType qobject_cast qvariant_cast signals slots";
const DECLARATION: &str = "ID auto bool char char8_t char16_t char32_t double float int long short signed unsigned void wchar_t * & && > ]";
const PUNCTUATORS: &[&str] = &[
    "%:%:", "<=>", ">>=", "<<=", "->*", "...", "::", ".*", "->", "++", "--", "<<", ">>", "<=",
    ">=", "==", "!=", "&&", "||", "*=", "/=", "%=", "+=", "-=", "&=", "^=", "|=", "##", "<%", "%>",
    "<:", ":>", "%:",
];
fn contains(table: &str, value: &str) -> bool {
    table.split_whitespace().any(|word| word == value)
}
/// Python `str.isalpha`: general category L (not Rust's derived `Alphabetic`, which also takes
/// letter numbers and combining vowel signs).
static PY_ALPHA: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\p{L}$").unwrap());
fn identifier_start(character: char) -> bool {
    if character.is_ascii() {
        return character == '_' || character.is_ascii_alphabetic();
    }
    let mut buffer = [0u8; 4];
    PY_ALPHA.is_match(character.encode_utf8(&mut buffer))
}
/// Python `character == "_" or character.isalnum()`: general categories L and N.
fn identifier_continue(character: char) -> bool {
    if character.is_ascii() {
        return character == '_' || character.is_ascii_alphanumeric();
    }
    let mut buffer = [0u8; 4];
    ici_analysis::unicode::word(character.encode_utf8(&mut buffer))
}
fn identifier_end(text: &str, start: usize) -> usize {
    let mut end = start;
    for character in text[start..].chars() {
        if !identifier_continue(character) {
            break;
        }
        end += character.len_utf8();
    }
    end
}
fn following(text: &str, start: usize) -> &str {
    let mut i = start;
    while i < text.len() {
        let character = text[i..].chars().next().unwrap();
        if py_space(character) {
            i += character.len_utf8();
            continue;
        }
        if text[i..].starts_with("//") {
            i = text[i..].find('\n').map_or(text.len(), |end| i + end + 1);
            continue;
        }
        if text[i..].starts_with("/*") {
            i = text[i + 2..]
                .find("*/")
                .map_or(text.len(), |end| i + end + 4);
            continue;
        }
        break;
    }
    if text[i..].starts_with("::") {
        "::"
    } else {
        &text[i..i + text[i..].chars().next().map_or(0, char::len_utf8)]
    }
}
fn identifier(value: &str, previous: &str, next: &str) -> String {
    let qt_macro = ["Q_", "QT_", "QML_"].iter().any(|prefix| {
        value.strip_prefix(prefix).is_some_and(|tail| {
            !tail.is_empty()
                && tail
                    .bytes()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
        })
    });
    if contains(KEYWORDS, value) || contains(ATTRIBUTES, value) || contains(QT, value) || qt_macro {
        return value.into();
    }
    let declaration = next == "("
        && (contains(DECLARATION, previous)
            || previous.starts_with("NAME(")
            || previous.starts_with("NAME_SHA256("));
    if [".", ".*", "->", "->*", "::", "case", "goto"].contains(&previous)
        || next == "::"
        || (next == "(" && !declaration)
        || (value.chars().count() > 1
            && value.chars().next().is_some_and(char::is_uppercase)
            && value == value.to_uppercase())
    {
        if value.chars().count() <= 128 {
            format!("NAME({value})")
        } else {
            format!("NAME_SHA256({})", &ici_domain::sha256(value)[7..])
        }
    } else {
        "ID".into()
    }
}
fn suffix(text: &str, start: usize, kind: &str) -> (usize, String) {
    if text[start..].chars().next().is_some_and(identifier_start) {
        let end = identifier_end(text, start);
        (end, format!("{kind}:UDL({})", &text[start..end]))
    } else {
        (start, kind.into())
    }
}
fn literal(text: &str, start: usize) -> Option<(usize, String)> {
    for prefix in ["u8R\"", "uR\"", "UR\"", "LR\"", "R\""] {
        if !text[start..].starts_with(prefix) {
            continue;
        }
        let delimiter_start = start + prefix.len();
        let opening = text[delimiter_start..]
            .char_indices()
            .take(17)
            .find_map(|(index, character)| (character == '(').then_some(delimiter_start + index))?;
        let delimiter = &text[delimiter_start..opening];
        if delimiter.chars().count() > 16
            || delimiter.chars().any(|c| py_space(c) || "()\\".contains(c))
        {
            return None;
        }
        let close = format!("){delimiter}\"");
        return Some(if let Some(end) = text[opening + 1..].find(&close) {
            suffix(text, opening + 1 + end + close.len(), "RAW_STRING_LIT")
        } else {
            (text.len(), "UNTERMINATED_RAW_STRING_LIT".into())
        });
    }
    for prefix in [
        "u8\"", "u8'", "u\"", "u'", "U\"", "U'", "L\"", "L'", "\"", "'",
    ] {
        if !text[start..].starts_with(prefix) {
            continue;
        }
        let quote = prefix.chars().last().unwrap();
        let kind = if quote == '\"' {
            "STRING_LIT"
        } else {
            "CHAR_LIT"
        };
        let mut i = start + prefix.len();
        while i < text.len() {
            let character = text[i..].chars().next().unwrap();
            if character == '\\' {
                i += 1;
                if i >= text.len() {
                    return Some((text.len(), format!("UNTERMINATED_{kind}")));
                }
                i += text[i..].chars().next().unwrap().len_utf8();
                continue;
            }
            if character == quote {
                return Some(suffix(text, i + 1, kind));
            }
            if character == '\n' {
                return Some((i, format!("UNTERMINATED_{kind}")));
            }
            i += character.len_utf8();
        }
        return Some((text.len(), format!("UNTERMINATED_{kind}")));
    }
    None
}
/// Frozen `_splice_cpp_source`: phase-2 line splicing with the physical start of each line.
struct Spliced {
    text: String,
    starts: Vec<usize>,
    last_line: u32,
}
impl Spliced {
    fn new(raw: &str) -> Self {
        let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
        let mut text = String::with_capacity(normalized.len());
        let mut starts = vec![0usize];
        let mut i = 0;
        while i < normalized.len() {
            if normalized[i..].starts_with("\\\n") {
                starts.push(text.len());
                i += 2;
            } else {
                let character = normalized[i..].chars().next().unwrap();
                text.push(character);
                i += character.len_utf8();
                if character == '\n' {
                    starts.push(text.len());
                }
            }
        }
        let last_line = if raw.is_empty() {
            0
        } else {
            starts.len() as u32 - u32::from(normalized.ends_with('\n'))
        };
        Self {
            text,
            starts,
            last_line,
        }
    }
    fn line_at(&self, offset: usize) -> u32 {
        self.starts.partition_point(|start| *start <= offset) as u32
    }
}
fn lex(
    raw: &str,
    maximum: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Vec<(u32, String)>, CppError> {
    if raw.contains('\0') || raw.len() > 16 * 1024 * 1024 || maximum == 0 {
        return Err(CppError::new(
            "duplicate-input",
            "C++ duplicate source or token bound invalid",
        ));
    }
    let spliced = Spliced::new(raw);
    let text = &spliced.text;
    let line_at = |offset: usize| spliced.line_at(offset);
    let mut lines: BTreeMap<u32, Vec<String>> = BTreeMap::new();
    let mut count = 0;
    let mut index = 0;
    let mut prefix = true;
    let mut previous = String::new();
    while index < text.len() {
        if count % 4096 == 0 && cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            return Err(CppError::new(
                "analysis-cancelled",
                "C++ duplicate normalization cancelled",
            ));
        }
        let character = text[index..].chars().next().unwrap();
        if character == '\n' {
            prefix = true;
            index += 1;
            continue;
        }
        if py_space(character) {
            index += character.len_utf8();
            continue;
        }
        if prefix && (character == '#' || text[index..].starts_with("%:")) {
            let end = text[index..]
                .find('\n')
                .map_or(text.len(), |end| index + end + 1);
            index = end;
            prefix = true;
            previous.clear();
            continue;
        }
        if text[index..].starts_with("//") {
            index = text[index..]
                .find('\n')
                .map_or(text.len(), |end| index + end);
            continue;
        }
        if text[index..].starts_with("/*") {
            if let Some(close) = text[index + 2..].find("*/") {
                let end = index + close + 4;
                if text[index..end].contains('\n') {
                    prefix = true;
                }
                index = end;
                continue;
            } else {
                count += 1;
                if count > maximum {
                    return Err(CppError::new(
                        "duplicate-token-limit",
                        "C++ source exceeds token bound",
                    ));
                }
                lines
                    .entry(line_at(index))
                    .or_default()
                    .push("UNTERMINATED_BLOCK_COMMENT".into());
                break;
            }
        }
        let line = line_at(index);
        let (end, token) = if let Some(literal) = literal(text, index) {
            literal
        } else if let Some(number) = NUMBER
            .captures(&text[index..])
            .filter(|capture| capture.get(0).unwrap().start() == 0)
        {
            let core = &number["core"];
            let lower = core.to_ascii_lowercase();
            let is_float = core.contains('.')
                || (!lower.starts_with("0x") && lower.contains('e'))
                || lower.contains('p');
            let kind = if is_float { "FLOAT_LIT" } else { "INT_LIT" };
            let suffix = number.name("suffix").map_or("", |value| value.as_str());
            (
                index + number.get(0).unwrap().end(),
                if suffix.is_empty() {
                    kind.into()
                } else {
                    format!("{kind}:UDL({suffix})")
                },
            )
        } else if identifier_start(character) {
            let end = identifier_end(text, index);
            (
                end,
                identifier(&text[index..end], &previous, following(text, end)),
            )
        } else {
            let punctuator = PUNCTUATORS
                .iter()
                .find(|token| text[index..].starts_with(**token))
                .copied()
                .unwrap_or(&text[index..index + character.len_utf8()]);
            let normalized = match punctuator {
                "<%" => "{",
                "%>" => "}",
                "<:" => "[",
                ":>" => "]",
                "%:" => "#",
                "%:%:" => "##",
                other => other,
            };
            (index + punctuator.len(), normalized.to_owned())
        };
        count += 1;
        if count > maximum {
            return Err(CppError::new(
                "duplicate-token-limit",
                "C++ source exceeds token bound",
            ));
        }
        previous = token.clone();
        lines.entry(line).or_default().push(token);
        index = end;
        prefix = false;
    }
    Ok(lines
        .into_iter()
        .map(|(line, tokens)| (line, tokens.join("\x1f")))
        .collect())
}
pub fn tokenize_cpp_lines(text: &str) -> Result<Vec<(u32, String)>, CppError> {
    lex(text, 500_000, None)
}
/// Frozen `cpp_directive_lines`: the physical lines of actual preprocessing directives. This is
/// its own scan, not the tokenizer's: it steps over identifiers and numbers one character at a
/// time, so a literal prefix or a digit separator inside a word opens a literal here that the
/// tokenizer never saw (`fooR"(\n#define X\n)"`).
pub fn cpp_directive_lines(raw: &str) -> Result<Vec<u32>, CppError> {
    if raw.contains('\0') {
        return Err(CppError::new(
            "duplicate-input",
            "C++ source must not contain a null byte",
        ));
    }
    let spliced = Spliced::new(raw);
    let text = &spliced.text;
    let mut barriers = BTreeSet::new();
    let mut index = 0;
    let mut prefix = true;
    while index < text.len() {
        let character = text[index..].chars().next().unwrap();
        if character == '\n' {
            prefix = true;
            index += 1;
            continue;
        }
        if py_space(character) {
            index += character.len_utf8();
            continue;
        }
        if prefix && (character == '#' || text[index..].starts_with("%:")) {
            let end = text[index..]
                .find('\n')
                .map_or(text.len(), |end| index + end + 1);
            let mut final_line = spliced.line_at(end - 1);
            if end == text.len() && !text.ends_with('\n') {
                final_line = final_line.max(spliced.last_line);
            }
            barriers.extend(spliced.line_at(index)..=final_line);
            index = end;
            prefix = true;
            continue;
        }
        if text[index..].starts_with("//") {
            index = text[index..]
                .find('\n')
                .map_or(text.len(), |end| index + end);
            continue;
        }
        if text[index..].starts_with("/*") {
            let end = text[index + 2..]
                .find("*/")
                .map_or(text.len(), |close| index + close + 4);
            if text[index..end].contains('\n') {
                prefix = true;
            }
            index = end;
            continue;
        }
        if let Some((end, _)) = literal(text, index) {
            index = end;
            prefix = false;
            continue;
        }
        prefix = false;
        index += character.len_utf8();
    }
    Ok(barriers.into_iter().collect())
}
/// Frozen `DuplicateEngine._load_and_index_files` bounds over the owned C++ sources in path
/// order: the per-file `MAX_CPP_DUPLICATE_TOKENS` tokenizer stop, then the cumulative
/// `MAX_DUPLICATE_NORMALIZED_CHARS` and `MAX_DUPLICATE_INDEXED_RECORDS` stops, as the
/// `(file, message)` of the frozen `_SourceTokenizationError`.
pub(crate) fn legacy_normalization_error(
    inputs: &[&ici_domain::SourceInput],
) -> Option<(String, String)> {
    let (path, stop) = normalization_stop(inputs)?;
    let message = match stop {
        NormalizationStop::Tokens(detail) => {
            format!("Source lexical normalization failed: {detail}")
        }
        NormalizationStop::Chars => format!(
            "Normalized duplicate-analysis input exceeds max_normalized_chars={NORMALIZED_CHARS}"
        ),
        NormalizationStop::Records => format!(
            "Normalized duplicate-analysis input exceeds MAX_DUPLICATE_INDEXED_RECORDS={INDEXED_RECORDS}"
        ),
    };
    Some((path, message))
}

/// Frozen next `languages/duplicates._index_sources` over the same bounds, as the `(file, text)`
/// of its failed-task limitation.
pub(crate) fn next_normalization_error(
    inputs: &[&ici_domain::SourceInput],
) -> Option<(String, String)> {
    let (path, stop) = normalization_stop(inputs)?;
    let message = match stop {
        NormalizationStop::Tokens(detail) => {
            format!("{path}: lexical normalization failed: {detail}")
        }
        NormalizationStop::Chars => "normalized duplicate input exceeds the bounded limit".into(),
        NormalizationStop::Records => "indexed duplicate records exceed the bounded limit".into(),
    };
    Some((path, message))
}

const NORMALIZED_CHARS: usize = 128 * 1024 * 1024;
const INDEXED_RECORDS: usize = 500_000;

enum NormalizationStop {
    Tokens(String),
    Chars,
    Records,
}

fn normalization_stop(inputs: &[&ici_domain::SourceInput]) -> Option<(String, NormalizationStop)> {
    let mut ordered = inputs.to_vec();
    ordered.sort_by(|left, right| left.path.cmp(&right.path));
    let (mut chars, mut records) = (0usize, 0usize);
    for input in ordered {
        let text = std::str::from_utf8(&input.bytes).ok()?;
        let indexed = match lex(text, 500_000, None) {
            Ok(indexed) => indexed,
            Err(error) => {
                let detail = if error.code == "duplicate-token-limit" {
                    "max_tokens=500000 exceeded while tokenizing C++ source".to_owned()
                } else {
                    error.message
                };
                return Some((input.path.clone(), NormalizationStop::Tokens(detail)));
            }
        };
        chars += indexed
            .iter()
            .map(|(_, tokens)| tokens.chars().count())
            .sum::<usize>();
        if chars > NORMALIZED_CHARS {
            return Some((input.path.clone(), NormalizationStop::Chars));
        }
        records += indexed.len();
        if records > INDEXED_RECORDS {
            return Some((input.path.clone(), NormalizationStop::Records));
        }
    }
    None
}

/// Frozen `cpp_duplicate_regions`: each code line's narrowest fallback function scope
/// (`_cpp_function_spans` over `text.splitlines()`; a bounded scan stop means no scope) and its
/// directive segment. Both frozen paths (`dup` and the next `*.dup` check) use these regions.
pub(crate) fn file_data(
    path: &str,
    text: &str,
    cancellation: Option<&AtomicBool>,
) -> Result<DuplicateFileData, CppError> {
    let indexed = lex(text, 500_000, cancellation)?;
    let spans =
        crate::legacy_heuristic::function_inventory(&crate::qt_codegen::py_splitlines(text))
            .map_or_else(|_| vec![], |(spans, _)| spans);
    let mut intervals: Vec<(u32, u32, u32)> = spans
        .iter()
        .enumerate()
        .filter(|(_, span)| span.end_line >= span.start_line)
        .map(|(index, span)| {
            (
                span.start_line as u32,
                span.end_line as u32,
                index as u32 + 1,
            )
        })
        .collect();
    intervals.sort_unstable();
    let barriers = cpp_directive_lines(text)?;
    let mut active: BinaryHeap<Reverse<(u32, u32, u32)>> = BinaryHeap::new();
    let mut next = 0;
    let mut regions = Vec::with_capacity(indexed.len());
    for (line, _) in &indexed {
        while next < intervals.len() && intervals[next].0 <= *line {
            let (start, end, scope) = intervals[next];
            active.push(Reverse((end - start, end, scope)));
            next += 1;
        }
        while active.peek().is_some_and(|Reverse(top)| top.1 < *line) {
            active.pop();
        }
        regions.push((
            active.peek().map_or(0, |Reverse(top)| top.2),
            barriers.partition_point(|barrier| barrier <= line) as u32,
        ));
    }
    Ok(DuplicateFileData {
        file_path: path.to_owned(),
        language: "cpp".into(),
        raw_text: text.to_owned(),
        indexed,
        regions,
    })
}
