//! Strict character decoders from pinned observations; no runtime or filesystem backend.
use crate::source::SourceError;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Clone, Copy, Debug)]
pub(crate) enum Codec {
    Utf8,
    Table(usize),
    Iso(usize),
    Hz,
    UnicodeEscape(bool),
    Utf7,
    Utf16(Option<bool>),
    Utf32(Option<bool>),
    Idna,
    Punycode,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Kind {
    Single,
    Double,
    EucKr,
    Gb18030,
}
pub(crate) struct Table {
    pub name: &'static str,
    pub kind: Kind,
    pub single: &'static [u8],
    pub double: Option<&'static [u8]>,
    pub triple: Option<&'static [u8]>,
}
include!("codec_tables.rs");
const UNDEFINED: u32 = u32::MAX;
const fn ascii_identities() -> [bool; TABLES.len()] {
    let mut identities = [true; TABLES.len()];
    let mut codec = 0;
    while codec < TABLES.len() {
        let table = TABLES[codec].single;
        let mut byte = 0;
        while byte < 128 {
            if u32::from_le_bytes([
                table[byte * 4],
                table[byte * 4 + 1],
                table[byte * 4 + 2],
                table[byte * 4 + 3],
            ]) != byte as u32
            {
                identities[codec] = false;
                break;
            }
            byte += 1;
        }
        codec += 1;
    }
    identities
}
const ASCII_IDENTITIES: [bool; TABLES.len()] = ascii_identities();

#[derive(Clone, Debug)]
pub(crate) enum OffsetMap {
    SingleByte(Vec<(usize, usize)>),
    Boundaries(Vec<(u32, u32)>),
    Transformed {
        inner: Option<Box<OffsetMap>>,
        input_text: Option<String>,
        decoded_offsets: Vec<(usize, usize)>,
        raw_offsets: Vec<(usize, usize)>,
    },
    Variable {
        /// UTF-8 position, raw position and scalar-width index at atom boundaries.
        checkpoints: Vec<(usize, usize, usize)>,
        /// Raw bytes consumed after each scalar; zero is an unrepresentable
        /// boundary inside a character that expands to multiple Unicode scalars.
        widths: Vec<u8>,
    },
}
impl OffsetMap {
    pub(crate) fn heap_bytes(&self) -> usize {
        match self {
            Self::SingleByte(map) => map
                .capacity()
                .saturating_mul(std::mem::size_of::<(usize, usize)>()),
            Self::Boundaries(boundaries) => boundaries
                .capacity()
                .saturating_mul(std::mem::size_of::<(u32, u32)>()),
            Self::Variable {
                checkpoints,
                widths,
            } => checkpoints
                .capacity()
                .saturating_mul(std::mem::size_of::<(usize, usize, usize)>())
                .saturating_add(widths.capacity()),
            Self::Transformed {
                inner,
                input_text,
                decoded_offsets,
                raw_offsets,
            } => inner
                .as_ref()
                .map_or(0, |map| map.heap_bytes())
                .saturating_add(input_text.as_ref().map_or(0, String::capacity))
                .saturating_add(
                    decoded_offsets
                        .capacity()
                        .saturating_add(raw_offsets.capacity())
                        .saturating_mul(std::mem::size_of::<(usize, usize)>()),
                ),
        }
    }
    pub fn raw_offset(&self, text: &str, decoded: usize) -> Option<usize> {
        match self {
            Self::Transformed {
                inner,
                input_text,
                decoded_offsets,
                raw_offsets,
            } => {
                fn translated(points: &[(usize, usize)], offset: usize) -> usize {
                    let Some(index) = points
                        .partition_point(|point| point.0 <= offset)
                        .checked_sub(1)
                    else {
                        return offset;
                    };
                    points[index].1 + offset - points[index].0
                }
                let original = translated(decoded_offsets, decoded);
                let raw = match inner {
                    None => original,
                    Some(map) => map.raw_offset(input_text.as_deref().unwrap_or(text), original)?,
                };
                Some(translated(raw_offsets, raw))
            }
            Self::Boundaries(boundaries) => boundaries
                .binary_search_by_key(&(decoded as u32), |(offset, _)| *offset)
                .ok()
                .and_then(|index| {
                    let raw = boundaries[index].1;
                    (raw != u32::MAX).then_some(raw as usize)
                }),
            Self::SingleByte(map) => {
                let index = map
                    .partition_point(|item| item.0 <= decoded)
                    .saturating_sub(1);
                Some(map[index].1 + text[map[index].0..decoded].chars().count())
            }
            Self::Variable {
                checkpoints,
                widths,
            } => {
                let index = checkpoints
                    .partition_point(|item| item.0 <= decoded)
                    .saturating_sub(1);
                let (utf8, mut raw, scalar) = checkpoints[index];
                if utf8 == decoded {
                    return Some(raw);
                }
                let mut last = 1;
                for (position, _) in text[utf8..decoded].chars().enumerate() {
                    last = widths[scalar + position];
                    raw += usize::from(last);
                }
                (last != 0).then_some(raw)
            }
        }
    }
}
pub(crate) struct Decoded {
    pub text: String,
    pub map: Option<OffsetMap>,
}
struct Builder {
    text: String,
    widths: Vec<u8>,
    checkpoints: Vec<(usize, usize, usize)>,
    singles: Vec<(usize, usize)>,
    single_byte: bool,
    raw_base: usize,
}
impl Builder {
    fn new(capacity: usize, raw_base: usize, single_byte: bool) -> Self {
        Self {
            text: String::with_capacity(capacity),
            widths: Vec::new(),
            checkpoints: Vec::new(),
            singles: Vec::new(),
            single_byte,
            raw_base,
        }
    }
    fn append(&mut self, value: u32, raw: usize, width: u8) -> Result<(), SourceError> {
        let pair = if value & 0x80000000 == 0 {
            (value, None)
        } else {
            let (first, second) = COMBINED[(value & 0x7fffffff) as usize];
            (first, Some(second))
        };
        if self.single_byte {
            if self
                .singles
                .last()
                .is_none_or(|(offset, _)| self.text.len() - offset >= 256)
            {
                self.singles.push((self.text.len(), raw + self.raw_base));
            }
        } else {
            if self
                .checkpoints
                .last()
                .is_none_or(|(offset, _, _)| self.text.len() - offset >= 256)
            {
                self.checkpoints
                    .push((self.text.len(), raw + self.raw_base, self.widths.len()));
            }
            if pair.1.is_some() {
                self.widths.push(0);
            }
            self.widths.push(width);
        }
        self.text
            .push(char::from_u32(pair.0).ok_or_else(invalid_table)?);
        if let Some(second) = pair.1 {
            self.text
                .push(char::from_u32(second).ok_or_else(invalid_table)?);
        }
        Ok(())
    }
    fn finish(mut self, raw_end: usize) -> Decoded {
        let map = if self.single_byte {
            self.singles
                .push((self.text.len(), raw_end + self.raw_base));
            OffsetMap::SingleByte(self.singles)
        } else {
            self.checkpoints
                .push((self.text.len(), raw_end + self.raw_base, self.widths.len()));
            OffsetMap::Variable {
                checkpoints: self.checkpoints,
                widths: self.widths,
            }
        };
        Decoded {
            text: self.text,
            map: Some(map),
        }
    }
    fn invalid(&self, name: &str) -> SourceError {
        let message = match name {
            "ascii" => "Source is not valid ASCII".to_owned(),
            "cp1252" => "Source contains an undefined CP1252 byte".to_owned(),
            _ => format!("Source contains an invalid {name} byte sequence"),
        };
        let mut line = 1;
        let mut column = 1;
        let mut chars = self.text.chars().peekable();
        while let Some(character) = chars.next() {
            if character == '\n' || character == '\r' && chars.peek() != Some(&'\n') {
                line += 1;
                column = 1;
            } else {
                column += 1;
            }
        }
        SourceError {
            code: "invalid-encoding",
            message,
            line,
            column,
        }
    }
}
fn invalid_table() -> SourceError {
    SourceError::new(
        "invalid-codec-table",
        "Pinned source codec table contains an invalid scalar",
    )
}
fn scalar(table: &[u8], index: usize) -> u32 {
    u32::from_le_bytes(
        table[index * 4..index * 4 + 4]
            .try_into()
            .expect("fixed table width"),
    )
}
pub(crate) fn lookup(name: &str) -> Option<Codec> {
    let normalized = name.to_ascii_lowercase().replace('-', "_");
    ALIASES
        .binary_search_by_key(&normalized.as_str(), |(label, _)| *label)
        .ok()
        .map(|index| ALIASES[index].1)
        .or_else(|| crate::stateful::lookup(&normalized))
        .or_else(|| crate::unicode_codecs::lookup(&normalized))
        .or(match normalized.as_str() {
            "idna" => Some(Codec::Idna),
            "punycode" => Some(Codec::Punycode),
            _ => None,
        })
}
fn hangul(bytes: &[u8]) -> Option<u32> {
    if bytes.len() < 8 || bytes[..3] != [0xA4, 0xD4, 0xA4] || bytes[4] != 0xA4 || bytes[6] != 0xA4 {
        return None;
    }
    let leading = EUCKR_LEADING.iter().position(|byte| *byte == bytes[3])?;
    let vowel = EUCKR_VOWEL.iter().position(|byte| *byte == bytes[5])?;
    let trailing = EUCKR_TRAILING.iter().position(|byte| *byte == bytes[7])?;
    Some(0xAC00 + ((leading * 21 + vowel) * 28 + trailing) as u32)
}
fn gb18030(bytes: &[u8]) -> Option<u32> {
    if bytes.len() < 4
        || !(0x81..=0xfe).contains(&bytes[0])
        || !(0x30..=0x39).contains(&bytes[1])
        || !(0x81..=0xfe).contains(&bytes[2])
        || !(0x30..=0x39).contains(&bytes[3])
    {
        return None;
    }
    let pointer = (((u32::from(bytes[0] - 0x81) * 10 + u32::from(bytes[1] - 0x30)) * 126)
        + u32::from(bytes[2] - 0x81))
        * 10
        + u32::from(bytes[3] - 0x30);
    let index = GB18030_RANGES
        .partition_point(|(start, _, _)| *start <= pointer)
        .checked_sub(1)?;
    let (start, end, scalar) = GB18030_RANGES[index];
    (pointer <= end).then_some(scalar + pointer - start)
}
pub(crate) fn decode(
    codec: Codec,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    match codec {
        Codec::Iso(index) => return crate::stateful::iso(index, bytes, raw_base, cancellation),
        Codec::Hz => return crate::stateful::hz(bytes, raw_base, cancellation),
        Codec::UnicodeEscape(_) | Codec::Utf7 | Codec::Utf16(_) | Codec::Utf32(_) => {
            return crate::unicode_codecs::decode(codec, bytes, raw_base, cancellation);
        }
        Codec::Idna => return crate::idna::decode(bytes, raw_base, cancellation),
        Codec::Punycode => return crate::idna::decode_punycode(bytes, raw_base, cancellation),
        Codec::Utf8 | Codec::Table(_) => {}
    }
    if let Codec::Utf8 = codec {
        return String::from_utf8(bytes.to_vec())
            .map(|text| Decoded { text, map: None })
            .map_err(|_| SourceError::new("invalid-encoding", "Source is not valid UTF-8"));
    }
    let Codec::Table(index) = codec else {
        unreachable!()
    };
    let table = &TABLES[index];
    // This optimization is checked against each codec's actual byte mapping;
    // CP864 and EBCDIC are deliberately not treated as ASCII identities.
    if bytes.is_ascii() && ASCII_IDENTITIES[index] {
        return Ok(Decoded {
            text: String::from_utf8(bytes.to_vec()).expect("ASCII"),
            map: None,
        });
    }
    let mut result = Builder::new(bytes.len(), raw_base, table.kind == Kind::Single);
    let mut offset = 0;
    while offset < bytes.len() {
        if offset % 4096 < 8 && cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            return Err(SourceError::new("cancelled", "Source decoding cancelled"));
        }
        let first = bytes[offset];
        let mut value = scalar(table.single, usize::from(first));
        let mut width = 1;
        if value == UNDEFINED
            && let Some(second) = bytes.get(offset + 1)
        {
            let key = usize::from(first) * 256 + usize::from(*second);
            if let Some(double) = table.double {
                value = scalar(double, key);
                width = 2;
            }
            if value == UNDEFINED {
                if table.kind == Kind::EucKr && first == 0xA4 && *second == 0xD4 {
                    if let Some(decoded) = hangul(&bytes[offset..]) {
                        value = decoded;
                        width = 8;
                    }
                } else if table.kind == Kind::Gb18030 {
                    if let Some(decoded) = gb18030(&bytes[offset..]) {
                        value = decoded;
                        width = 4;
                    }
                } else if first == 0x8F
                    && let Some(triple) = table.triple
                    && let Some(third) = bytes.get(offset + 2)
                {
                    value = scalar(triple, usize::from(*second) * 256 + usize::from(*third));
                    width = 3;
                }
            }
        }
        if value == UNDEFINED {
            return Err(result.invalid(table.name));
        }
        result.append(value, offset, width)?;
        offset += usize::from(width);
    }
    Ok(result.finish(bytes.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use sha2::{Digest, Sha256};

    struct BufferedHash {
        hash: Sha256,
        bytes: Vec<u8>,
    }
    impl BufferedHash {
        fn new() -> Self {
            Self {
                hash: Sha256::new(),
                bytes: Vec::with_capacity(65536),
            }
        }
        fn update(&mut self, bytes: &[u8]) {
            self.bytes.extend_from_slice(bytes);
            if self.bytes.len() >= 65536 {
                self.hash.update(&self.bytes);
                self.bytes.clear();
            }
        }
        fn finish(mut self) -> String {
            self.hash.update(&self.bytes);
            format!("{:x}", self.hash.finalize())
        }
    }
    fn manifest() -> Value {
        serde_json::from_str(include_str!("../data/python-codecs/manifest.json")).unwrap()
    }
    #[test]
    fn every_single_and_double_byte_matches_strict_cpython_observations() {
        let evidence = manifest();
        for (index, table) in TABLES.iter().enumerate() {
            let mut hash = BufferedHash::new();
            let mut accepted = [0u64; 3];
            for width in [1u8, 2] {
                hash.update(&[width]);
                for value in 0..256u32.pow(u32::from(width)) {
                    let storage = (value as u16).to_be_bytes();
                    let bytes = &storage[2 - usize::from(width)..];
                    hash.update(bytes);
                    match decode(Codec::Table(index), bytes, 0, None) {
                        Ok(decoded) => {
                            accepted[usize::from(width)] += 1;
                            hash.update(&[1]);
                            hash.update(&(decoded.text.len() as u32).to_le_bytes());
                            hash.update(decoded.text.as_bytes());
                        }
                        Err(_) => hash.update(&[0]),
                    }
                }
            }
            assert_eq!(
                hash.finish(),
                evidence["codecs"][table.name]["all_single_pair_sha256"],
                "{}",
                table.name
            );
            assert_eq!(
                accepted[1],
                evidence["codecs"][table.name]["accepted"]["1"]
                    .as_u64()
                    .unwrap(),
                "{}",
                table.name
            );
            assert_eq!(
                accepted[2],
                evidence["codecs"][table.name]["accepted"]["2"]
                    .as_u64()
                    .unwrap(),
                "{}",
                table.name
            );
        }
    }
    #[test]
    fn all_gb18030_four_byte_sequences_and_euckr_hangul_match_cpython() {
        let gb = lookup("gb18030").unwrap();
        let mut hash = BufferedHash::new();
        for first in 0x81..=0xfe {
            for second in 0x30..=0x39 {
                for third in 0x81..=0xfe {
                    for fourth in 0x30..=0x39 {
                        let bytes = [first, second, third, fourth];
                        hash.update(&bytes);
                        match decode(gb, &bytes, 0, None) {
                            Ok(decoded) => {
                                hash.update(&[1]);
                                hash.update(decoded.text.as_bytes());
                            }
                            Err(_) => hash.update(&[0]),
                        }
                    }
                }
            }
        }
        let evidence = manifest();
        assert_eq!(hash.finish(), evidence["gb18030_all_four_byte_sha256"]);
        let kr = lookup("euc_kr").unwrap();
        let mut hash = BufferedHash::new();
        for (leading, l_byte) in EUCKR_LEADING.iter().enumerate() {
            for (vowel, v_byte) in EUCKR_VOWEL.iter().enumerate() {
                for (trailing, t_byte) in EUCKR_TRAILING.iter().enumerate() {
                    let raw = [0xA4, 0xD4, 0xA4, *l_byte, 0xA4, *v_byte, 0xA4, *t_byte];
                    let decoded = decode(kr, &raw, 0, None).unwrap();
                    assert_eq!(
                        decoded.text,
                        char::from_u32(0xAC00 + ((leading * 21 + vowel) * 28 + trailing) as u32)
                            .unwrap()
                            .to_string()
                    );
                    hash.update(&raw);
                    hash.update(decoded.text.as_bytes());
                }
            }
        }
        assert_eq!(hash.finish(), evidence["euc_kr_all_hangul_sha256"]);
    }
}
