//! Unicode 16 default full folding for portable wheel member identity.
//! Tables retain their Unicode LICENSE V3 and source digest under data/unicode-16.
use crate::casefold_table::FULL_FOLD;
use std::{fmt::Write, sync::LazyLock};
use unicode_normalization::UnicodeNormalization;
static WORD: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r"^[\p{L}\p{N}_]+\z").expect("Python Unicode word category")
});
pub fn word(value: &str) -> bool {
    WORD.is_match(value)
}
static NON_PRINTABLE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"[\p{C}\p{Z}]").expect("Unicode general categories"));

pub fn fold(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        if let Ok(index) = FULL_FOLD.binary_search_by_key(&character, |(character, _)| *character) {
            output.push_str(FULL_FOLD[index].1);
        } else {
            output.push(character);
        }
    }
    output
}
pub fn lower(value: &str) -> String {
    let characters: Vec<_> = value.chars().collect();
    let mut following = vec![false; characters.len()];
    let mut cased = false;
    for (index, character) in characters.iter().copied().enumerate().rev() {
        following[index] = cased;
        if !contains(crate::xid_table::CASE_IGNORABLE, character) {
            cased = contains(crate::xid_table::CASED, character);
        }
    }
    let mut preceding = false;
    let mut result = String::with_capacity(value.len());
    for (index, character) in characters.iter().copied().enumerate() {
        if character == 'Σ' {
            result.push(if preceding && !following[index] {
                'ς'
            } else {
                'σ'
            });
        } else if let Ok(index) = crate::casefold_table::LOWERCASE
            .binary_search_by_key(&character, |(character, _)| *character)
        {
            result.push_str(crate::casefold_table::LOWERCASE[index].1);
        } else {
            result.push(character);
        }
        if !contains(crate::xid_table::CASE_IGNORABLE, character) {
            preceding = contains(crate::xid_table::CASED, character);
        }
    }
    result
}
pub fn portable(value: &str) -> String {
    fold(
        &value
            .strip_suffix('/')
            .unwrap_or(value)
            .nfc()
            .collect::<String>(),
    )
}
fn contains(ranges: &[(u32, u32)], character: char) -> bool {
    let code = character as u32;
    let index = ranges.partition_point(|(_, last)| *last < code);
    ranges.get(index).is_some_and(|(first, _)| *first <= code)
}
pub fn identifier_start(character: char) -> bool {
    character == '_' || contains(crate::xid_table::XID_START, character)
}
pub fn identifier_continue(character: char) -> bool {
    contains(crate::xid_table::XID_CONTINUE, character)
}
pub fn identifier(value: &str) -> bool {
    let mut characters = value.chars();
    characters.next().is_some_and(identifier_start) && characters.all(identifier_continue)
}
pub fn decimal_digit(character: char) -> Option<u8> {
    let code = character as u32;
    let before = crate::xid_table::DECIMAL_ZEROS.partition_point(|zero| *zero <= code);
    let zero = *crate::xid_table::DECIMAL_ZEROS.get(before.checked_sub(1)?)?;
    let value = code - zero;
    if value < 10 { Some(value as u8) } else { None }
}
/// Python repr spelling for metadata messages, using Unicode 16 properties.
pub fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut output = String::with_capacity(value.len() + 2);
    output.push(quote);
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value == quote => {
                output.push('\\');
                output.push(value);
            }
            ' ' => output.push(' '),
            value => {
                let mut encoded = [0; 4];
                if NON_PRINTABLE.is_match(value.encode_utf8(&mut encoded)) {
                    let code = value as u32;
                    if code <= 0xff {
                        write!(output, "\\x{code:02x}").unwrap();
                    } else if code <= 0xffff {
                        write!(output, "\\u{code:04x}").unwrap();
                    } else {
                        write!(output, "\\U{code:08x}").unwrap();
                    }
                } else {
                    output.push(value);
                }
            }
        }
    }
    output.push(quote);
    output
}
