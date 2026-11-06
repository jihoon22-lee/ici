//! The XML reader frozen's test-result parsing sees (`xml.etree.ElementTree.fromstring`): expat
//! in namespace mode over a `str`, so the declared encoding is ignored. Only what a document
//! without a DTD can hold is read; frozen's `_parse_xml` refuses any DOCTYPE first. Every
//! refusal of expat is `None`, as frozen's `ElementTree.ParseError` is.
//!
//! The name tables are expat's own, read by
//! `tests/rust-parity/cpp/capture_make_check.py` and pinned against its oracle below.
use std::collections::{HashMap, HashSet};

const NAME_START: [u16; 404] = [
    0x00C0, 0x00D6, 0x00D8, 0x00F6, 0x00F8, 0x0131, 0x0134, 0x013E, 0x0141, 0x0148, 0x014A, 0x017E,
    0x0180, 0x01C3, 0x01CD, 0x01F0, 0x01F4, 0x01F5, 0x01FA, 0x0217, 0x0250, 0x02A8, 0x02BB, 0x02C1,
    0x0386, 0x0386, 0x0388, 0x038A, 0x038C, 0x038C, 0x038E, 0x03A1, 0x03A3, 0x03CE, 0x03D0, 0x03D6,
    0x03DA, 0x03DA, 0x03DC, 0x03DC, 0x03DE, 0x03DE, 0x03E0, 0x03E0, 0x03E2, 0x03F3, 0x0401, 0x040C,
    0x040E, 0x044F, 0x0451, 0x045C, 0x045E, 0x0481, 0x0490, 0x04C4, 0x04C7, 0x04C8, 0x04CB, 0x04CC,
    0x04D0, 0x04EB, 0x04EE, 0x04F5, 0x04F8, 0x04F9, 0x0531, 0x0556, 0x0559, 0x0559, 0x0561, 0x0586,
    0x05D0, 0x05EA, 0x05F0, 0x05F2, 0x0621, 0x063A, 0x0641, 0x064A, 0x0671, 0x06B7, 0x06BA, 0x06BE,
    0x06C0, 0x06CE, 0x06D0, 0x06D3, 0x06D5, 0x06D5, 0x06E5, 0x06E6, 0x0905, 0x0939, 0x093D, 0x093D,
    0x0958, 0x0961, 0x0985, 0x098C, 0x098F, 0x0990, 0x0993, 0x09A8, 0x09AA, 0x09B0, 0x09B2, 0x09B2,
    0x09B6, 0x09B9, 0x09DC, 0x09DD, 0x09DF, 0x09E1, 0x09F0, 0x09F1, 0x0A05, 0x0A0A, 0x0A0F, 0x0A10,
    0x0A13, 0x0A28, 0x0A2A, 0x0A30, 0x0A32, 0x0A33, 0x0A35, 0x0A36, 0x0A38, 0x0A39, 0x0A59, 0x0A5C,
    0x0A5E, 0x0A5E, 0x0A72, 0x0A74, 0x0A85, 0x0A8B, 0x0A8D, 0x0A8D, 0x0A8F, 0x0A91, 0x0A93, 0x0AA8,
    0x0AAA, 0x0AB0, 0x0AB2, 0x0AB3, 0x0AB5, 0x0AB9, 0x0ABD, 0x0ABD, 0x0AE0, 0x0AE0, 0x0B05, 0x0B0C,
    0x0B0F, 0x0B10, 0x0B13, 0x0B28, 0x0B2A, 0x0B30, 0x0B32, 0x0B33, 0x0B36, 0x0B39, 0x0B3D, 0x0B3D,
    0x0B5C, 0x0B5D, 0x0B5F, 0x0B61, 0x0B85, 0x0B8A, 0x0B8E, 0x0B90, 0x0B92, 0x0B95, 0x0B99, 0x0B9A,
    0x0B9C, 0x0B9C, 0x0B9E, 0x0B9F, 0x0BA3, 0x0BA4, 0x0BA8, 0x0BAA, 0x0BAE, 0x0BB5, 0x0BB7, 0x0BB9,
    0x0C05, 0x0C0C, 0x0C0E, 0x0C10, 0x0C12, 0x0C28, 0x0C2A, 0x0C33, 0x0C35, 0x0C39, 0x0C60, 0x0C61,
    0x0C85, 0x0C8C, 0x0C8E, 0x0C90, 0x0C92, 0x0CA8, 0x0CAA, 0x0CB3, 0x0CB5, 0x0CB9, 0x0CDE, 0x0CDE,
    0x0CE0, 0x0CE1, 0x0D05, 0x0D0C, 0x0D0E, 0x0D10, 0x0D12, 0x0D28, 0x0D2A, 0x0D39, 0x0D60, 0x0D61,
    0x0E01, 0x0E2E, 0x0E30, 0x0E30, 0x0E32, 0x0E33, 0x0E40, 0x0E45, 0x0E81, 0x0E82, 0x0E84, 0x0E84,
    0x0E87, 0x0E88, 0x0E8A, 0x0E8A, 0x0E8D, 0x0E8D, 0x0E94, 0x0E97, 0x0E99, 0x0E9F, 0x0EA1, 0x0EA3,
    0x0EA5, 0x0EA5, 0x0EA7, 0x0EA7, 0x0EAA, 0x0EAB, 0x0EAD, 0x0EAE, 0x0EB0, 0x0EB0, 0x0EB2, 0x0EB3,
    0x0EBD, 0x0EBD, 0x0EC0, 0x0EC4, 0x0F40, 0x0F47, 0x0F49, 0x0F69, 0x10A0, 0x10C5, 0x10D0, 0x10F6,
    0x1100, 0x1100, 0x1102, 0x1103, 0x1105, 0x1107, 0x1109, 0x1109, 0x110B, 0x110C, 0x110E, 0x1112,
    0x113C, 0x113C, 0x113E, 0x113E, 0x1140, 0x1140, 0x114C, 0x114C, 0x114E, 0x114E, 0x1150, 0x1150,
    0x1154, 0x1155, 0x1159, 0x1159, 0x115F, 0x1161, 0x1163, 0x1163, 0x1165, 0x1165, 0x1167, 0x1167,
    0x1169, 0x1169, 0x116D, 0x116E, 0x1172, 0x1173, 0x1175, 0x1175, 0x119E, 0x119E, 0x11A8, 0x11A8,
    0x11AB, 0x11AB, 0x11AE, 0x11AF, 0x11B7, 0x11B8, 0x11BA, 0x11BA, 0x11BC, 0x11C2, 0x11EB, 0x11EB,
    0x11F0, 0x11F0, 0x11F9, 0x11F9, 0x1E00, 0x1E9B, 0x1EA0, 0x1EF9, 0x1F00, 0x1F15, 0x1F18, 0x1F1D,
    0x1F20, 0x1F45, 0x1F48, 0x1F4D, 0x1F50, 0x1F57, 0x1F59, 0x1F59, 0x1F5B, 0x1F5B, 0x1F5D, 0x1F5D,
    0x1F5F, 0x1F7D, 0x1F80, 0x1FB4, 0x1FB6, 0x1FBC, 0x1FBE, 0x1FBE, 0x1FC2, 0x1FC4, 0x1FC6, 0x1FCC,
    0x1FD0, 0x1FD3, 0x1FD6, 0x1FDB, 0x1FE0, 0x1FEC, 0x1FF2, 0x1FF4, 0x1FF6, 0x1FFC, 0x2126, 0x2126,
    0x212A, 0x212B, 0x212E, 0x212E, 0x2180, 0x2182, 0x3007, 0x3007, 0x3021, 0x3029, 0x3041, 0x3094,
    0x30A1, 0x30FA, 0x3105, 0x312C, 0x4E00, 0x9FA5, 0xAC00, 0xD7A3,
];

const NAME_CHAR: [u16; 564] = [
    0x00B7, 0x00B7, 0x00C0, 0x00D6, 0x00D8, 0x00F6, 0x00F8, 0x0131, 0x0134, 0x013E, 0x0141, 0x0148,
    0x014A, 0x017E, 0x0180, 0x01C3, 0x01CD, 0x01F0, 0x01F4, 0x01F5, 0x01FA, 0x0217, 0x0250, 0x02A8,
    0x02BB, 0x02C1, 0x02D0, 0x02D1, 0x0300, 0x0345, 0x0360, 0x0361, 0x0386, 0x038A, 0x038C, 0x038C,
    0x038E, 0x03A1, 0x03A3, 0x03CE, 0x03D0, 0x03D6, 0x03DA, 0x03DA, 0x03DC, 0x03DC, 0x03DE, 0x03DE,
    0x03E0, 0x03E0, 0x03E2, 0x03F3, 0x0401, 0x040C, 0x040E, 0x044F, 0x0451, 0x045C, 0x045E, 0x0481,
    0x0483, 0x0486, 0x0490, 0x04C4, 0x04C7, 0x04C8, 0x04CB, 0x04CC, 0x04D0, 0x04EB, 0x04EE, 0x04F5,
    0x04F8, 0x04F9, 0x0531, 0x0556, 0x0559, 0x0559, 0x0561, 0x0586, 0x0591, 0x05A1, 0x05A3, 0x05B9,
    0x05BB, 0x05BD, 0x05BF, 0x05BF, 0x05C1, 0x05C2, 0x05C4, 0x05C4, 0x05D0, 0x05EA, 0x05F0, 0x05F2,
    0x0621, 0x063A, 0x0640, 0x0652, 0x0660, 0x0669, 0x0670, 0x06B7, 0x06BA, 0x06BE, 0x06C0, 0x06CE,
    0x06D0, 0x06D3, 0x06D5, 0x06E8, 0x06EA, 0x06ED, 0x06F0, 0x06F9, 0x0901, 0x0903, 0x0905, 0x0939,
    0x093C, 0x094D, 0x0951, 0x0954, 0x0958, 0x0963, 0x0966, 0x096F, 0x0981, 0x0983, 0x0985, 0x098C,
    0x098F, 0x0990, 0x0993, 0x09A8, 0x09AA, 0x09B0, 0x09B2, 0x09B2, 0x09B6, 0x09B9, 0x09BC, 0x09BC,
    0x09BE, 0x09C4, 0x09C7, 0x09C8, 0x09CB, 0x09CD, 0x09D7, 0x09D7, 0x09DC, 0x09DD, 0x09DF, 0x09E3,
    0x09E6, 0x09F1, 0x0A02, 0x0A02, 0x0A05, 0x0A0A, 0x0A0F, 0x0A10, 0x0A13, 0x0A28, 0x0A2A, 0x0A30,
    0x0A32, 0x0A33, 0x0A35, 0x0A36, 0x0A38, 0x0A39, 0x0A3C, 0x0A3C, 0x0A3E, 0x0A42, 0x0A47, 0x0A48,
    0x0A4B, 0x0A4D, 0x0A59, 0x0A5C, 0x0A5E, 0x0A5E, 0x0A66, 0x0A74, 0x0A81, 0x0A83, 0x0A85, 0x0A8B,
    0x0A8D, 0x0A8D, 0x0A8F, 0x0A91, 0x0A93, 0x0AA8, 0x0AAA, 0x0AB0, 0x0AB2, 0x0AB3, 0x0AB5, 0x0AB9,
    0x0ABC, 0x0AC5, 0x0AC7, 0x0AC9, 0x0ACB, 0x0ACD, 0x0AE0, 0x0AE0, 0x0AE6, 0x0AEF, 0x0B01, 0x0B03,
    0x0B05, 0x0B0C, 0x0B0F, 0x0B10, 0x0B13, 0x0B28, 0x0B2A, 0x0B30, 0x0B32, 0x0B33, 0x0B36, 0x0B39,
    0x0B3C, 0x0B43, 0x0B47, 0x0B48, 0x0B4B, 0x0B4D, 0x0B56, 0x0B57, 0x0B5C, 0x0B5D, 0x0B5F, 0x0B61,
    0x0B66, 0x0B6F, 0x0B82, 0x0B83, 0x0B85, 0x0B8A, 0x0B8E, 0x0B90, 0x0B92, 0x0B95, 0x0B99, 0x0B9A,
    0x0B9C, 0x0B9C, 0x0B9E, 0x0B9F, 0x0BA3, 0x0BA4, 0x0BA8, 0x0BAA, 0x0BAE, 0x0BB5, 0x0BB7, 0x0BB9,
    0x0BBE, 0x0BC2, 0x0BC6, 0x0BC8, 0x0BCA, 0x0BCD, 0x0BD7, 0x0BD7, 0x0BE7, 0x0BEF, 0x0C01, 0x0C03,
    0x0C05, 0x0C0C, 0x0C0E, 0x0C10, 0x0C12, 0x0C28, 0x0C2A, 0x0C33, 0x0C35, 0x0C39, 0x0C3E, 0x0C44,
    0x0C46, 0x0C48, 0x0C4A, 0x0C4D, 0x0C55, 0x0C56, 0x0C60, 0x0C61, 0x0C66, 0x0C6F, 0x0C82, 0x0C83,
    0x0C85, 0x0C8C, 0x0C8E, 0x0C90, 0x0C92, 0x0CA8, 0x0CAA, 0x0CB3, 0x0CB5, 0x0CB9, 0x0CBE, 0x0CC4,
    0x0CC6, 0x0CC8, 0x0CCA, 0x0CCD, 0x0CD5, 0x0CD6, 0x0CDE, 0x0CDE, 0x0CE0, 0x0CE1, 0x0CE6, 0x0CEF,
    0x0D02, 0x0D03, 0x0D05, 0x0D0C, 0x0D0E, 0x0D10, 0x0D12, 0x0D28, 0x0D2A, 0x0D39, 0x0D3E, 0x0D43,
    0x0D46, 0x0D48, 0x0D4A, 0x0D4D, 0x0D57, 0x0D57, 0x0D60, 0x0D61, 0x0D66, 0x0D6F, 0x0E01, 0x0E2E,
    0x0E30, 0x0E3A, 0x0E40, 0x0E4E, 0x0E50, 0x0E59, 0x0E81, 0x0E82, 0x0E84, 0x0E84, 0x0E87, 0x0E88,
    0x0E8A, 0x0E8A, 0x0E8D, 0x0E8D, 0x0E94, 0x0E97, 0x0E99, 0x0E9F, 0x0EA1, 0x0EA3, 0x0EA5, 0x0EA5,
    0x0EA7, 0x0EA7, 0x0EAA, 0x0EAB, 0x0EAD, 0x0EAE, 0x0EB0, 0x0EB9, 0x0EBB, 0x0EBD, 0x0EC0, 0x0EC4,
    0x0EC6, 0x0EC6, 0x0EC8, 0x0ECD, 0x0ED0, 0x0ED9, 0x0F18, 0x0F19, 0x0F20, 0x0F29, 0x0F35, 0x0F35,
    0x0F37, 0x0F37, 0x0F39, 0x0F39, 0x0F3E, 0x0F47, 0x0F49, 0x0F69, 0x0F71, 0x0F84, 0x0F86, 0x0F8B,
    0x0F90, 0x0F95, 0x0F97, 0x0F97, 0x0F99, 0x0FAD, 0x0FB1, 0x0FB7, 0x0FB9, 0x0FB9, 0x10A0, 0x10C5,
    0x10D0, 0x10F6, 0x1100, 0x1100, 0x1102, 0x1103, 0x1105, 0x1107, 0x1109, 0x1109, 0x110B, 0x110C,
    0x110E, 0x1112, 0x113C, 0x113C, 0x113E, 0x113E, 0x1140, 0x1140, 0x114C, 0x114C, 0x114E, 0x114E,
    0x1150, 0x1150, 0x1154, 0x1155, 0x1159, 0x1159, 0x115F, 0x1161, 0x1163, 0x1163, 0x1165, 0x1165,
    0x1167, 0x1167, 0x1169, 0x1169, 0x116D, 0x116E, 0x1172, 0x1173, 0x1175, 0x1175, 0x119E, 0x119E,
    0x11A8, 0x11A8, 0x11AB, 0x11AB, 0x11AE, 0x11AF, 0x11B7, 0x11B8, 0x11BA, 0x11BA, 0x11BC, 0x11C2,
    0x11EB, 0x11EB, 0x11F0, 0x11F0, 0x11F9, 0x11F9, 0x1E00, 0x1E9B, 0x1EA0, 0x1EF9, 0x1F00, 0x1F15,
    0x1F18, 0x1F1D, 0x1F20, 0x1F45, 0x1F48, 0x1F4D, 0x1F50, 0x1F57, 0x1F59, 0x1F59, 0x1F5B, 0x1F5B,
    0x1F5D, 0x1F5D, 0x1F5F, 0x1F7D, 0x1F80, 0x1FB4, 0x1FB6, 0x1FBC, 0x1FBE, 0x1FBE, 0x1FC2, 0x1FC4,
    0x1FC6, 0x1FCC, 0x1FD0, 0x1FD3, 0x1FD6, 0x1FDB, 0x1FE0, 0x1FEC, 0x1FF2, 0x1FF4, 0x1FF6, 0x1FFC,
    0x20D0, 0x20DC, 0x20E1, 0x20E1, 0x2126, 0x2126, 0x212A, 0x212B, 0x212E, 0x212E, 0x2180, 0x2182,
    0x3005, 0x3005, 0x3007, 0x3007, 0x3021, 0x302F, 0x3031, 0x3035, 0x3041, 0x3094, 0x3099, 0x309A,
    0x309D, 0x309E, 0x30A1, 0x30FA, 0x30FC, 0x30FE, 0x3105, 0x312C, 0x4E00, 0x9FA5, 0xAC00, 0xD7A3,
];

/// One ElementTree element: a namespaced name is `{uri}local`, `text` is what precedes the first
/// child, and `children` index [`Document::elements`].
#[derive(Debug, Default)]
pub(crate) struct Element {
    pub(crate) name: String,
    pub(crate) attributes: Vec<(String, String)>,
    pub(crate) text: String,
    pub(crate) children: Vec<usize>,
}
impl Element {
    pub(crate) fn get(&self, key: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.as_str())
    }
}

/// A flat tree, so a deep document needs no recursion: `elements` is in document order with the
/// root first, which is the order of ElementTree `root.iter()`.
#[derive(Debug, Default)]
pub(crate) struct Document {
    pub(crate) elements: Vec<Element>,
}
impl Document {
    pub(crate) fn root(&self) -> &Element {
        &self.elements[0]
    }
    /// ElementTree `root.iter(name)`.
    pub(crate) fn iter<'a>(&'a self, name: &'a str) -> impl Iterator<Item = &'a Element> + 'a {
        self.elements
            .iter()
            .filter(move |element| element.name == name)
    }
    /// ElementTree `element.findall(name)`.
    pub(crate) fn find_all<'a>(
        &'a self,
        element: &'a Element,
        name: &'a str,
    ) -> impl Iterator<Item = &'a Element> + 'a {
        element
            .children
            .iter()
            .map(|child| &self.elements[*child])
            .filter(move |child| child.name == name)
    }
}

fn within(table: &[u16], character: char) -> bool {
    let Ok(code) = u16::try_from(character as u32) else {
        return false;
    };
    // Flat `[first, last, first, last, ...]`: an odd insertion point falls inside a range.
    let point = table.partition_point(|bound| *bound < code);
    point % 2 == 1 || table.get(point) == Some(&code)
}
fn name_start(character: char) -> bool {
    if character.is_ascii() {
        character.is_ascii_alphabetic() || character == '_'
    } else {
        within(&NAME_START, character)
    }
}
fn name_char(character: char) -> bool {
    if character.is_ascii() {
        character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '-')
    } else {
        within(&NAME_CHAR, character)
    }
}
/// A character expat's tokenizer refuses wherever it appears: controls, U+FFFE and U+FFFF.
fn valid_character(character: char) -> bool {
    !(matches!(character, '\0'..='\x08' | '\x0b' | '\x0c' | '\x0e'..='\x1f')
        || matches!(character, '\u{fffe}' | '\u{ffff}'))
}
/// expat `checkCharRefNumber`, with `char::from_u32` refusing surrogates and overflow.
fn valid_reference(number: u32) -> Option<char> {
    char::from_u32(number).filter(|character| valid_character(*character))
}
fn space(character: char) -> bool {
    matches!(character, ' ' | '\t' | '\r' | '\n')
}

const XML_NAMESPACE: &str = "http://www.w3.org/XML/1998/namespace";
const XMLNS_NAMESPACE: &str = "http://www.w3.org/2000/xmlns/";

struct Parser<'a> {
    text: &'a str,
    at: usize,
    document: Document,
    /// Open elements: index, raw name, and how many declarations they made.
    open: Vec<(usize, &'a str, usize)>,
    /// The URIs in scope per prefix (`""` is the default namespace), innermost last.
    bindings: HashMap<&'a str, Vec<String>>,
    declared: Vec<&'a str>,
}

impl<'a> Parser<'a> {
    fn rest(&self) -> &'a str {
        &self.text[self.at..]
    }
    fn peek(&self) -> Option<char> {
        self.rest().chars().next()
    }
    fn accept(&mut self, value: &str) -> bool {
        let found = self.rest().starts_with(value);
        if found {
            self.at += value.len();
        }
        found
    }
    fn skip_space(&mut self) -> bool {
        let start = self.at;
        while self.peek().is_some_and(space) {
            self.at += 1;
        }
        self.at != start
    }
    fn local_name(&mut self) -> Option<&'a str> {
        let start = self.at;
        let first = self.peek().filter(|character| name_start(*character))?;
        self.at += first.len_utf8();
        while let Some(character) = self.peek().filter(|character| name_char(*character)) {
            self.at += character.len_utf8();
        }
        Some(&self.text[start..self.at])
    }
    /// A name in expat's namespace tokenizer: one optional colon between two name parts.
    fn qualified_name(&mut self) -> Option<&'a str> {
        let start = self.at;
        self.local_name()?;
        if self.accept(":") {
            self.local_name()?;
        }
        Some(&self.text[start..self.at])
    }

    /// `&...;`, with the `&` already consumed.
    fn reference(&mut self) -> Option<char> {
        if self.accept("#") {
            let hexadecimal = self.accept("x");
            let radix = if hexadecimal { 16 } else { 10 };
            let start = self.at;
            let mut number = 0u32;
            while let Some(digit) = self.peek().and_then(|character| character.to_digit(radix)) {
                // Anything past U+10FFFF is refused, so saturating keeps it out of range.
                number = number.saturating_mul(radix).saturating_add(digit);
                self.at += 1;
            }
            if self.at == start || !self.accept(";") {
                return None;
            }
            return valid_reference(number);
        }
        let start = self.at;
        self.local_name()?;
        let name = &self.text[start..self.at];
        if !self.accept(";") {
            return None;
        }
        match name {
            "lt" => Some('<'),
            "gt" => Some('>'),
            "amp" => Some('&'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            _ => None,
        }
    }

    /// `<!--` consumed: a `--` must close the comment.
    fn comment(&mut self) -> Option<()> {
        let end = self.rest().find("--")?;
        self.at += end;
        self.accept("-->").then_some(())
    }
    /// `<?` consumed. A target spelled `xml` in any case is refused here: only the document's
    /// declaration may use it, and that is read before this.
    fn processing_instruction(&mut self) -> Option<()> {
        let target = self.local_name()?;
        if target.eq_ignore_ascii_case("xml") {
            return None;
        }
        if self.accept("?>") {
            return Some(());
        }
        if !self.skip_space() {
            return None;
        }
        let end = self.rest().find("?>")?;
        self.at += end + 2;
        Some(())
    }
    /// A comment or processing instruction, the only markup outside the root.
    fn miscellaneous(&mut self) -> Option<bool> {
        if self.accept("<!--") {
            self.comment()?;
            return Some(true);
        }
        if self.accept("<?") {
            self.processing_instruction()?;
            return Some(true);
        }
        Some(false)
    }

    /// Literal text: expat reports a line ending as `\n`; `&#13;` stays a carriage return.
    fn append_literal(&mut self, value: &str) {
        if value.contains('\r') {
            self.append_text(&value.replace("\r\n", "\n").replace('\r', "\n"));
        } else {
            self.append_text(value);
        }
    }
    fn append_text(&mut self, value: &str) {
        let Some((index, _, _)) = self.open.last() else {
            return;
        };
        let element = &mut self.document.elements[*index];
        if element.children.is_empty() {
            element.text.push_str(value);
        }
    }

    fn attribute_value(&mut self) -> Option<String> {
        let quote = self.peek().filter(|value| matches!(value, '"' | '\''))?;
        self.at += 1;
        let mut value = String::new();
        loop {
            let character = self.peek()?;
            self.at += character.len_utf8();
            match character {
                _ if character == quote => return Some(value),
                '<' => return None,
                '&' => value.push(self.reference()?),
                '\r' => {
                    self.accept("\n");
                    value.push(' ');
                }
                '\t' | '\n' => value.push(' '),
                _ => value.push(character),
            }
        }
    }

    /// expat `addBinding`.
    fn declare(&mut self, prefix: &'a str, uri: String) -> Option<()> {
        if !prefix.is_empty() && uri.is_empty() {
            return None;
        }
        if prefix == "xmlns"
            || uri == XMLNS_NAMESPACE
            || (prefix == "xml") != (uri == XML_NAMESPACE)
        {
            return None;
        }
        self.bindings.entry(prefix).or_default().push(uri);
        self.declared.push(prefix);
        Some(())
    }
    fn bound(&self, prefix: &str) -> Option<&str> {
        self.bindings.get(prefix)?.last().map(String::as_str)
    }
    /// expat `storeAtts`: declarations apply to the whole start tag, so they are read first.
    fn resolve(
        &mut self,
        raw_name: &'a str,
        raw: Vec<(&'a str, String)>,
    ) -> Option<(Element, usize)> {
        let mut seen = HashSet::with_capacity(raw.len());
        let before = self.declared.len();
        let mut plain = Vec::with_capacity(raw.len());
        for (name, value) in raw {
            if !seen.insert(name) {
                return None;
            }
            if name == "xmlns" {
                self.declare("", value)?;
            } else if let Some(prefix) = name.strip_prefix("xmlns:") {
                self.declare(prefix, value)?;
            } else {
                plain.push((name, value));
            }
        }
        let mut element = Element::default();
        let mut expanded = HashSet::with_capacity(plain.len());
        for (name, value) in plain {
            let key = match name.split_once(':') {
                Some((prefix, local)) => format!("{{{}}}{local}", self.bound(prefix)?),
                None => name.to_owned(),
            };
            if !expanded.insert(key.clone()) {
                return None;
            }
            element.attributes.push((key, value));
        }
        element.name = match raw_name.split_once(':') {
            Some((prefix, local)) => format!("{{{}}}{local}", self.bound(prefix)?),
            None => match self.bound("") {
                Some(uri) if !uri.is_empty() => format!("{{{uri}}}{raw_name}"),
                _ => raw_name.to_owned(),
            },
        };
        Some((element, self.declared.len() - before))
    }
    fn release(&mut self, count: usize) {
        for _ in 0..count {
            if let Some(prefix) = self.declared.pop()
                && let Some(uris) = self.bindings.get_mut(prefix)
            {
                uris.pop();
            }
        }
    }

    /// `<` consumed.
    fn start_tag(&mut self) -> Option<()> {
        let raw_name = self.qualified_name()?;
        let mut raw = vec![];
        let empty = loop {
            let spaced = self.skip_space();
            if self.accept(">") {
                break false;
            }
            if self.accept("/>") {
                break true;
            }
            if !spaced {
                return None;
            }
            let name = self.qualified_name()?;
            self.skip_space();
            if !self.accept("=") {
                return None;
            }
            self.skip_space();
            raw.push((name, self.attribute_value()?));
        };
        let (element, declared) = self.resolve(raw_name, raw)?;
        let index = self.document.elements.len();
        self.document.elements.push(element);
        if let Some((parent, _, _)) = self.open.last() {
            self.document.elements[*parent].children.push(index);
        }
        if empty {
            self.release(declared);
        } else {
            self.open.push((index, raw_name, declared));
        }
        Some(())
    }
    /// `</` consumed.
    fn end_tag(&mut self) -> Option<()> {
        let name = self.qualified_name()?;
        self.skip_space();
        if !self.accept(">") {
            return None;
        }
        let (_, opened, declared) = self.open.pop()?;
        if opened != name {
            return None;
        }
        self.release(declared);
        Some(())
    }

    /// Everything inside the root, until it closes.
    fn content(&mut self) -> Option<()> {
        while !self.open.is_empty() {
            match self.peek()? {
                '<' => {
                    self.at += 1;
                    if self.accept("!--") {
                        self.comment()?;
                    } else if self.accept("![CDATA[") {
                        let end = self.rest().find("]]>")?;
                        let value = &self.rest()[..end];
                        self.at += end + 3;
                        self.append_literal(value);
                    } else if self.accept("?") {
                        self.processing_instruction()?;
                    } else if self.accept("/") {
                        self.end_tag()?;
                    } else {
                        self.start_tag()?;
                    }
                }
                '&' => {
                    self.at += 1;
                    let character = self.reference()?;
                    self.append_text(character.encode_utf8(&mut [0; 4]));
                }
                _ => {
                    let end = self
                        .rest()
                        .find(['<', '&'])
                        .unwrap_or_else(|| self.rest().len());
                    let value = &self.rest()[..end];
                    if value.contains("]]>") {
                        return None;
                    }
                    self.at += end;
                    self.append_literal(value);
                }
            }
        }
        Some(())
    }

    /// expat `parsePseudoAttribute`, over the declaration's body.
    fn pseudo_attribute(body: &'a str, at: &mut usize) -> Result<Option<(&'a str, &'a str)>, ()> {
        let bytes = body.as_bytes();
        if *at == bytes.len() {
            return Ok(None);
        }
        if !(bytes[*at] as char).is_ascii() || !space(bytes[*at] as char) {
            return Err(());
        }
        while *at < bytes.len() && space(bytes[*at] as char) {
            *at += 1;
        }
        if *at == bytes.len() {
            return Ok(None);
        }
        let name_start = *at;
        loop {
            let byte = *bytes.get(*at).ok_or(())?;
            if !byte.is_ascii() {
                return Err(());
            }
            if byte == b'=' || space(byte as char) {
                break;
            }
            *at += 1;
        }
        let name = &body[name_start..*at];
        while *at < bytes.len() && space(bytes[*at] as char) {
            *at += 1;
        }
        if name.is_empty() || bytes.get(*at) != Some(&b'=') {
            return Err(());
        }
        *at += 1;
        while *at < bytes.len() && space(bytes[*at] as char) {
            *at += 1;
        }
        let quote = *bytes.get(*at).ok_or(())?;
        if quote != b'"' && quote != b'\'' {
            return Err(());
        }
        *at += 1;
        let value_start = *at;
        loop {
            let byte = *bytes.get(*at).ok_or(())?;
            if byte == quote {
                break;
            }
            if !(byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_')) {
                return Err(());
            }
            *at += 1;
        }
        let value = &body[value_start..*at];
        *at += 1;
        Ok(Some((name, value)))
    }
    /// expat `doParseXmlDecl`: `version`, then optional `encoding` and `standalone`, in order.
    fn declaration(body: &'a str) -> Option<()> {
        let mut at = 0;
        let (name, _) = Self::pseudo_attribute(body, &mut at).ok()??;
        if name != "version" {
            return None;
        }
        let Some((mut name, mut value)) = Self::pseudo_attribute(body, &mut at).ok()? else {
            return Some(());
        };
        if name == "encoding" {
            if !value
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphabetic)
            {
                return None;
            }
            let Some(next) = Self::pseudo_attribute(body, &mut at).ok()? else {
                return Some(());
            };
            (name, value) = next;
        }
        if name != "standalone" || !matches!(value, "yes" | "no") {
            return None;
        }
        body[at..].chars().all(space).then_some(())
    }

    fn document(mut self) -> Option<Document> {
        self.accept("\u{feff}");
        let rest = self.rest();
        if let Some(after) = rest.strip_prefix("<?xml")
            && (after.starts_with('?') || after.chars().next().is_some_and(space))
        {
            let end = after.find("?>")?;
            Self::declaration(&after[..end])?;
            self.at += "<?xml".len() + end + 2;
        }
        loop {
            self.skip_space();
            if !self.miscellaneous()? {
                break;
            }
        }
        if !self.accept("<") {
            return None;
        }
        self.start_tag()?;
        self.content()?;
        loop {
            self.skip_space();
            if self.at == self.text.len() {
                break;
            }
            if !self.miscellaneous()? {
                return None;
            }
        }
        Some(self.document)
    }
}

/// `ElementTree.fromstring(text)`; a `None` is frozen's `ParseError`.
pub(crate) fn parse(text: &str) -> Option<Document> {
    if !text.chars().all(valid_character) {
        return None;
    }
    let mut bindings = HashMap::new();
    bindings.insert("xml", vec![XML_NAMESPACE.to_owned()]);
    Parser {
        text,
        at: 0,
        document: Document::default(),
        open: vec![],
        bindings,
        declared: vec![],
    }
    .document()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tables above are expat's own, as `capture_make_check.py` read them from the oracle's
    /// interpreter.
    #[test]
    fn name_tables_equal_expats() {
        let oracle: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/rust-parity/cpp/make-check-oracle.json"
        ))
        .unwrap();
        for (key, table) in [
            ("expat_name_start", &NAME_START[..]),
            ("expat_name_char", &NAME_CHAR[..]),
        ] {
            let captured: Vec<u64> = oracle[key]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_u64().unwrap())
                .collect();
            let held: Vec<u64> = table.iter().map(|value| u64::from(*value)).collect();
            assert_eq!(held, captured, "{key}");
        }
        assert!(name_start('\u{c0}') && !name_start('\u{b7}') && name_char('\u{b7}'));
        assert!(!name_start('\u{10000}') && !name_char('\u{10000}'));
    }
}
