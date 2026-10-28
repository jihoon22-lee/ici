//! Unicode 16 default full folding for portable wheel member identity.
//! Tables retain their Unicode LICENSE V3 and source digest under data/unicode-16.
use crate::casefold_table::FULL_FOLD;
use unicode_normalization::UnicodeNormalization;

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
