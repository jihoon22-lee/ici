//! Translation-phase-aware bounded C++ Type-2 normalization, shared matching in ici-analysis.
use crate::{
    CppError,
    source::{self, ParsedSource},
};
use ici_analysis::duplicates::DuplicateFileData;
use regex::Regex;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};
static NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?P<core>0[xX][0-9A-Fa-f](?:'?[0-9A-Fa-f])*(?:\.(?:[0-9A-Fa-f](?:'?[0-9A-Fa-f])*)?)?(?:[pP][+-]?[0-9](?:'?[0-9])*)?|0[bB][01](?:'?[01])*|(?:[0-9](?:'?[0-9])*\.(?:[0-9](?:'?[0-9])*)?|\.[0-9](?:'?[0-9])*)(?:[eE][+-]?[0-9](?:'?[0-9])*)?|[0-9](?:'?[0-9])*[eE][+-]?[0-9](?:'?[0-9])*|[0-9](?:'?[0-9])*)(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)?").unwrap()
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
fn identifier_start(character: char) -> bool {
    character == '_' || character.is_alphabetic()
}
fn identifier_end(text: &str, start: usize) -> usize {
    let mut end = start;
    for character in text[start..].chars() {
        if !(character == '_' || character.is_alphanumeric()) {
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
        if character.is_whitespace() {
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
            .find('(')
            .map(|i| delimiter_start + i)?;
        let delimiter = &text[delimiter_start..opening];
        if delimiter.chars().count() > 16
            || delimiter
                .chars()
                .any(|c| c.is_whitespace() || "()\\".contains(c))
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
struct Lexed {
    indexed: Vec<(u32, String)>,
    barriers: BTreeSet<u32>,
}
fn lex(raw: &str, maximum: usize) -> Result<Lexed, CppError> {
    if raw.contains('\0') || raw.len() > 16 * 1024 * 1024 || maximum == 0 {
        return Err(CppError::new(
            "duplicate-input",
            "C++ duplicate source or token bound invalid",
        ));
    }
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
    let line_at = |offset: usize| starts.partition_point(|start| *start <= offset) as u32;
    let mut lines: BTreeMap<u32, Vec<String>> = BTreeMap::new();
    let mut barriers = BTreeSet::new();
    let mut count = 0;
    let mut index = 0;
    let mut prefix = true;
    let mut previous = String::new();
    while index < text.len() {
        let character = text[index..].chars().next().unwrap();
        if character == '\n' {
            prefix = true;
            index += 1;
            continue;
        }
        if character.is_whitespace() {
            index += character.len_utf8();
            continue;
        }
        if prefix && (character == '#' || text[index..].starts_with("%:")) {
            let start = line_at(index);
            let end = text[index..]
                .find('\n')
                .map_or(text.len(), |end| index + end + 1);
            let mut final_line = line_at(end.saturating_sub(1));
            if end == text.len() && !text.ends_with('\n') {
                final_line = final_line.max(last_line);
            }
            barriers.extend(start..=final_line);
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
        let (end, token) = if let Some(literal) = literal(&text, index) {
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
            let end = identifier_end(&text, index);
            (
                end,
                identifier(&text[index..end], &previous, following(&text, end)),
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
    Ok(Lexed {
        indexed: lines
            .into_iter()
            .map(|(line, tokens)| (line, tokens.join("\x1f")))
            .collect(),
        barriers,
    })
}
pub fn tokenize_cpp_lines(text: &str) -> Result<Vec<(u32, String)>, CppError> {
    Ok(lex(text, 500_000)?.indexed)
}
pub fn cpp_directive_lines(text: &str) -> Result<Vec<u32>, CppError> {
    Ok(lex(text, 500_000)?.barriers.into_iter().collect())
}
pub(crate) fn file_data(file: &ParsedSource) -> Result<DuplicateFileData, CppError> {
    let lexed = lex(&file.text, 500_000)?;
    let mut intervals = vec![];
    for node in source::nodes(file.tree.root_node())?
        .into_iter()
        .filter(|node| node.kind() == "function_definition")
    {
        let target = file.target(node, "");
        intervals.push((
            target.start_line,
            target.end_line.unwrap_or(target.start_line),
            intervals.len() as u32 + 1,
        ));
    }
    let barriers: Vec<_> = lexed.barriers.into_iter().collect();
    let regions = lexed
        .indexed
        .iter()
        .map(|(line, _)| {
            let scope = intervals
                .iter()
                .filter(|(start, end, _)| start <= line && line <= end)
                .min_by_key(|(start, end, scope)| (end - start, *end, *scope))
                .map_or(0, |(_, _, scope)| *scope);
            (
                scope,
                barriers.partition_point(|barrier| barrier <= line) as u32,
            )
        })
        .collect();
    Ok(DuplicateFileData {
        file_path: file.path.clone(),
        language: "cpp".into(),
        raw_text: file.text.clone(),
        indexed: lexed.indexed,
        regions,
    })
}
