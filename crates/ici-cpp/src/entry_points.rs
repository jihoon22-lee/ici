//! Frozen legacy entry-point guard; this is a source-spelling heuristic, not compiler evidence.
use crate::CppError;
use ici_domain::SourceInput;
use regex::Regex;
use std::sync::LazyLock;

static RAW: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"^(?:u8|u|U|L)?R"(?P<delimiter>[^\s()\\]{0,16})\("#).unwrap());
static MAIN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bint\s+main\s*\([^{};]*\)\s*(?:noexcept\s*)?\{").unwrap());
static OPERATOR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\boperator\s*$").unwrap());

/// Match the frozen direct-build `int main` guard after blanking comments and literals.
pub fn legacy_main_definition_count(input: &SourceInput) -> Result<usize, CppError> {
    if input.bytes.len() > 16 * 1024 * 1024 {
        return Err(CppError::new(
            "source-too-large",
            "entry-point source exceeds 16 MiB",
        ));
    }
    let text = std::str::from_utf8(&input.bytes)
        .map_err(|_| CppError::new("source-encoding", "entry-point source is not UTF-8"))?;
    if text.contains('\0') {
        return Err(CppError::new(
            "source-null",
            "entry-point source contains NUL",
        ));
    }
    let mut masked = input.bytes.clone();
    let mut index = 0usize;
    while index < text.len() {
        let start = index;
        let mut end = None;
        if let Some(raw) = RAW.captures(&text[index..]) {
            let closing = format!("){}\"", &raw["delimiter"]);
            let opening = index + raw.get(0).expect("raw match").end();
            end = Some(
                text[opening..]
                    .find(&closing)
                    .map_or(text.len(), |offset| opening + offset + closing.len()),
            );
        } else if text[index..].starts_with("//") {
            let mut cursor = index + 2;
            while cursor < text.len() {
                if text.as_bytes()[cursor] == b'\n' && text.as_bytes()[cursor - 1] != b'\\' {
                    break;
                }
                cursor += text[cursor..].chars().next().expect("character").len_utf8();
            }
            end = Some(cursor);
        } else if text[index..].starts_with("/*") {
            end = Some(
                text[index + 2..]
                    .find("*/")
                    .map_or(text.len(), |offset| index + offset + 4),
            );
        } else if matches!(text.as_bytes()[index], b'\"' | b'\'') {
            let prefix = text[..index]
                .char_indices()
                .rev()
                .nth(63)
                .map_or(0, |(offset, _)| offset);
            if text[index..].starts_with("\"\"") && OPERATOR.is_match(&text[prefix..index]) {
                index += 2;
                continue;
            }
            let quote = text.as_bytes()[index];
            let mut cursor = index + 1;
            while cursor < text.len() {
                let character = text[cursor..].chars().next().expect("quoted character");
                cursor += character.len_utf8();
                if character == '\\' {
                    if cursor < text.len() {
                        cursor += text[cursor..]
                            .chars()
                            .next()
                            .expect("escaped character")
                            .len_utf8();
                    }
                } else if character as u32 == quote as u32 || matches!(character, '\r' | '\n') {
                    break;
                }
            }
            end = Some(cursor);
        }
        if let Some(end) = end {
            for byte in &mut masked[start..end] {
                if !matches!(*byte, b'\r' | b'\n') {
                    *byte = b' ';
                }
            }
            index = end;
        } else {
            index += text[index..]
                .chars()
                .next()
                .expect("source character")
                .len_utf8();
        }
    }
    let masked =
        String::from_utf8(masked).expect("masking keeps unmodified Unicode and ASCII spaces");
    Ok(MAIN.find_iter(&masked).count())
}
