//! Strict ISO-2022/HZ state machines over pinned character observations.
use crate::codecs::{Codec, Decoded, OffsetMap};
use crate::source::SourceError;
use std::sync::atomic::{AtomicBool, Ordering};

struct IsoTable {
    name: &'static str,
    shift: bool,
    designations: &'static [(u8, usize, &'static [u8])],
    g2: &'static [(u8, &'static [u8])],
}
include!("stateful_tables.rs");
const UNDEFINED: u32 = u32::MAX;

pub(crate) fn lookup(normalized: &str) -> Option<Codec> {
    if normalized == "hz"
        || normalized == "hzgb"
        || normalized == "hz_gb"
        || normalized == "hz_gb_2312"
    {
        return Some(Codec::Hz);
    }
    ISO_ALIASES
        .binary_search_by_key(&normalized, |(alias, _)| *alias)
        .ok()
        .map(|index| Codec::Iso(ISO_ALIASES[index].1))
}
struct Builder {
    text: String,
    boundaries: Vec<(u32, u32)>,
    raw_base: usize,
}
impl Builder {
    fn new(capacity: usize, raw_base: usize) -> Self {
        Self {
            text: String::with_capacity(capacity),
            boundaries: vec![(0, raw_base as u32)],
            raw_base,
        }
    }
    fn push(&mut self, value: u32, raw_end: usize) -> Result<(), SourceError> {
        if value == UNDEFINED {
            return Err(self.invalid("stateful"));
        }
        let (first, second) = if value & 0x80000000 == 0 {
            (value, None)
        } else {
            let pair = ISO_COMBINED[(value & 0x7fffffff) as usize];
            (pair.0, Some(pair.1))
        };
        self.text
            .push(char::from_u32(first).ok_or_else(|| self.invalid("stateful"))?);
        self.boundaries.push((
            self.text.len() as u32,
            if second.is_some() {
                u32::MAX
            } else {
                (raw_end + self.raw_base) as u32
            },
        ));
        if let Some(second) = second {
            self.text
                .push(char::from_u32(second).ok_or_else(|| self.invalid("stateful"))?);
            self.boundaries
                .push((self.text.len() as u32, (raw_end + self.raw_base) as u32));
        }
        Ok(())
    }
    fn invalid(&self, name: &str) -> SourceError {
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
            message: format!("Source contains an invalid {name} byte sequence"),
            line,
            column,
        }
    }
    fn finish(mut self, raw_end: usize) -> Decoded {
        // A trailing designation consumes bytes without producing a scalar.
        // The final decoded boundary maps to the complete original input.
        self.boundaries.last_mut().expect("initial boundary").1 = (raw_end + self.raw_base) as u32;
        Decoded {
            text: self.text,
            map: Some(OffsetMap::Boundaries(self.boundaries)),
        }
    }
}
fn scalar(table: &[u8], index: usize) -> u32 {
    u32::from_le_bytes(
        table[index * 4..index * 4 + 4]
            .try_into()
            .expect("fixed observation table"),
    )
}
fn cancelled(
    cancellation: Option<&AtomicBool>,
    position: usize,
    checked: &mut usize,
) -> Result<(), SourceError> {
    if position >= *checked {
        *checked = position + 4096;
        if cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            return Err(SourceError::new(
                "cancelled",
                "Source decoding was cancelled",
            ));
        }
    }
    Ok(())
}
fn esc_end(byte: u8) -> bool {
    byte.is_ascii_uppercase() || byte == b'@'
}
fn designation(table: &IsoTable, bytes: &[u8]) -> Option<(usize, usize, u8)> {
    let mut index = 1;
    let mut length = 0;
    while index < 16 {
        let byte = *bytes.get(index)?;
        if esc_end(byte) {
            length = index + 1;
            break;
        }
        if !table.shift && byte == b'&' && bytes.get(index + 1) == Some(&b'@') {
            index += 2;
        }
        index += 1;
    }
    let (plane, mark) = match length {
        3 if bytes[1] == b'$' => (0, bytes[2] | 128),
        3 => (
            match bytes[1] {
                b'(' => 0,
                b')' => 1,
                b'.' if !table.g2.is_empty() => 2,
                _ => return None,
            },
            bytes[2],
        ),
        4 if bytes[1] == b'$' => (
            match bytes[2] {
                b'(' => 0,
                b')' => 1,
                _ => return None,
            },
            bytes[3] | 128,
        ),
        6 if !table.shift && bytes[3..6] == [27, b'$', b'B'] => (0, b'B' | 128),
        _ => return None,
    };
    (mark == b'B' || table.designations.iter().any(|row| row.0 == mark))
        .then_some((length, plane, mark))
}
pub(crate) fn iso(
    index: usize,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let table = &ISO_TABLES[index];
    if bytes.is_ascii()
        && !bytes.contains(&27)
        && (!table.shift || !bytes.iter().any(|byte| matches!(byte, 14 | 15)))
    {
        return crate::codecs::decode(Codec::Utf8, bytes, raw_base, cancellation);
    }
    let mut builder = Builder::new(bytes.len(), raw_base);
    let mut planes = [b'B'; 3];
    let (mut shifted, mut through) = (false, false);
    let (mut offset, mut checked) = (0, 0);
    while offset < bytes.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let byte = bytes[offset];
        if through {
            builder.push(u32::from(byte), offset + 1)?;
            through = !esc_end(byte);
            offset += 1;
            continue;
        }
        let width = match byte {
            27 => {
                let next = *bytes
                    .get(offset + 1)
                    .ok_or_else(|| builder.invalid(table.name))?;
                if matches!(next, b'(' | b')' | b'$' | b'.' | b'&') {
                    let (length, plane, mark) = designation(table, &bytes[offset..])
                        .ok_or_else(|| builder.invalid(table.name))?;
                    planes[plane] = mark;
                    length
                } else if next == b'N' && !table.g2.is_empty() {
                    let value = *bytes
                        .get(offset + 2)
                        .ok_or_else(|| builder.invalid(table.name))?;
                    let data = table
                        .g2
                        .iter()
                        .find(|row| row.0 == planes[2])
                        .ok_or_else(|| builder.invalid(table.name))?
                        .1;
                    let scalar = scalar(data, usize::from(value));
                    if scalar == UNDEFINED {
                        return Err(builder.invalid(table.name));
                    }
                    builder.push(scalar, offset + 3)?;
                    3
                } else {
                    builder.push(27, offset + 1)?;
                    // The incremental codec cannot release ESC until the
                    // following non-designation byte proves it is literal.
                    builder.boundaries.last_mut().expect("ESC boundary").1 = UNDEFINED;
                    through = true;
                    1
                }
            }
            14 | 15 if table.shift => {
                shifted = byte == 14;
                1
            }
            10 => {
                shifted = false;
                builder.push(10, offset + 1)?;
                1
            }
            0..=31 => {
                builder.push(u32::from(byte), offset + 1)?;
                1
            }
            128..=255 => return Err(builder.invalid(table.name)),
            _ => {
                let mark = planes[usize::from(shifted)];
                if mark == b'B' {
                    builder.push(u32::from(byte), offset + 1)?;
                    1
                } else {
                    let (_, width, data) = table
                        .designations
                        .iter()
                        .find(|row| row.0 == mark)
                        .ok_or_else(|| builder.invalid(table.name))?;
                    if offset + width > bytes.len() {
                        return Err(builder.invalid(table.name));
                    }
                    let key = if *width == 1 {
                        usize::from(byte)
                    } else {
                        usize::from(byte) * 256 + usize::from(bytes[offset + 1])
                    };
                    let scalar = scalar(data, key);
                    if scalar == UNDEFINED {
                        return Err(builder.invalid(table.name));
                    }
                    builder.push(scalar, offset + width)?;
                    *width
                }
            }
        };
        offset += width;
    }
    Ok(builder.finish(bytes.len()))
}
pub(crate) fn hz(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    hz_decode(bytes, raw_base, cancellation, false)
}
pub(crate) fn hz_source(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    hz_decode(bytes, raw_base, cancellation, true)
}
fn hz_decode(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
    python_source: bool,
) -> Result<Decoded, SourceError> {
    if bytes.is_ascii() && !bytes.contains(&b'~') {
        return crate::codecs::decode(Codec::Utf8, bytes, raw_base, cancellation);
    }
    let mut builder = Builder::new(bytes.len(), raw_base);
    let (mut offset, mut checked, mut shifted) = (0, 0, false);
    let codec = crate::codecs::lookup("gb2312").expect("pinned GB2312 table");
    while offset < bytes.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let byte = bytes[offset];
        let width = if byte == b'~' {
            match bytes.get(offset + 1) {
                Some(b'~') if !shifted => builder.push(u32::from(b'~'), offset + 2)?,
                Some(b'{') if !shifted => shifted = true,
                Some(b'\n') if !shifted => {}
                Some(b'}') if shifted => shifted = false,
                _ => return Err(builder.invalid("hz")),
            }
            2
        } else if byte >= 128 {
            return Err(builder.invalid("hz"));
        } else if !shifted {
            builder.push(u32::from(byte), offset + 1)?;
            1
        } else {
            let second = *bytes.get(offset + 1).ok_or_else(|| builder.invalid("hz"))?;
            // GB2312's pinned table accepts seven-bit positions with the high
            // bits set, while HZ carries the positions directly.
            if !(33..=126).contains(&byte) || !(33..=126).contains(&second) {
                return Err(builder.invalid("hz"));
            }
            let decoded = crate::codecs::decode(codec, &[byte | 128, second | 128], 0, None)
                .map_err(|_| builder.invalid("hz"))?;
            let mut chars = decoded.text.chars();
            let value = chars.next().ok_or_else(|| builder.invalid("hz"))?;
            if chars.next().is_some() {
                return Err(builder.invalid("hz"));
            }
            builder.push(u32::from(value), offset + 2)?;
            2
        };
        offset += width;
    }
    // CPython's byte-source tokenizer supplies a final newline before source
    // recoding. A terminal GB shift is valid standalone HZ text, but that
    // implicit newline is invalid in GB mode and cannot be parsed as Python.
    if python_source && shifted {
        return Err(builder.invalid("hz"));
    }
    Ok(builder.finish(bytes.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;
    use sha2::{Digest, Sha256};
    fn observations(codec: Codec, prefix: &[u8], width: usize) -> String {
        let mut observation = Vec::new();
        let mut raw = prefix.to_vec();
        for value in 0..256u32.pow(width as u32) {
            let pair = (value as u16).to_be_bytes();
            let payload = &pair[2 - width..];
            raw.truncate(prefix.len());
            raw.extend_from_slice(payload);
            observation.extend_from_slice(payload);
            match crate::codecs::decode(codec, &raw, 0, None) {
                Ok(decoded) => {
                    observation.push(1);
                    observation.extend_from_slice(&(decoded.text.len() as u32).to_le_bytes());
                    observation.extend_from_slice(decoded.text.as_bytes());
                }
                Err(_) => observation.push(0),
            }
        }
        format!("{:x}", Sha256::digest(observation))
    }
    #[test]
    fn every_iso_designation_and_default_byte_pair_matches_cpython() {
        let evidence: serde_json::Value =
            serde_json::from_str(include_str!("../data/python-codecs/stateful-manifest.json"))
                .unwrap();
        for (index, table) in ISO_TABLES.iter().enumerate() {
            let codec = Codec::Iso(index);
            let record = &evidence["codecs"][table.name];
            assert_eq!(
                observations(codec, &[], 1),
                record["ascii_single_sha256"],
                "{} single",
                table.name
            );
            assert_eq!(
                observations(codec, &[], 2),
                record["ascii_pair_sha256"],
                "{} pair",
                table.name
            );
            for row in record["designations"].as_array().unwrap() {
                let prefix = base64::engine::general_purpose::STANDARD
                    .decode(row["prefix_base64"].as_str().unwrap())
                    .unwrap();
                assert_eq!(
                    observations(codec, &prefix, row["width"].as_u64().unwrap() as usize),
                    row["all_input_sha256"],
                    "{} mark{}",
                    table.name,
                    row["mark"]
                );
            }
        }
        let record = &evidence["codecs"]["hz"];
        for (prefix, width, key) in [
            (b"".as_slice(), 1, "ascii_single_sha256"),
            (b"".as_slice(), 2, "ascii_pair_sha256"),
            (b"~{".as_slice(), 2, "shifted_pair_sha256"),
        ] {
            assert_eq!(
                observations(Codec::Hz, prefix, width),
                record[key],
                "hz {key}"
            );
        }
    }
    #[test]
    fn mixed_state_transitions_and_escape_failures_match_cpython() {
        let evidence: serde_json::Value =
            serde_json::from_str(include_str!("../data/python-codecs/stateful-manifest.json"))
                .unwrap();
        let fragments: Vec<_> = evidence["transition_fragments_base64"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| {
                base64::engine::general_purpose::STANDARD
                    .decode(value.as_str().unwrap())
                    .unwrap()
            })
            .collect();
        for name in ISO_TABLES.iter().map(|table| table.name).chain(["hz"]) {
            let codec = lookup(name).unwrap();
            let mut seed = evidence["transition_seed"].as_u64().unwrap() as u32;
            let mut next = || {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                seed
            };
            let mut observation = Vec::new();
            let mut accepted = 0u64;
            for _ in 0..evidence["transition_count"].as_u64().unwrap() {
                let mut raw = Vec::new();
                for _ in 0..next() % 12 {
                    let value = next();
                    if value % 3 == 0 {
                        raw.push((next() % 256) as u8);
                    } else {
                        raw.extend_from_slice(&fragments[value as usize % fragments.len()]);
                    }
                }
                observation.extend_from_slice(&(raw.len() as u32).to_le_bytes());
                observation.extend_from_slice(&raw);
                match crate::codecs::decode(codec, &raw, 0, None) {
                    Ok(decoded) => {
                        accepted += 1;
                        observation.push(1);
                        observation.extend_from_slice(&(decoded.text.len() as u32).to_le_bytes());
                        observation.extend_from_slice(decoded.text.as_bytes());
                    }
                    Err(_) => observation.push(0),
                }
            }
            let expected = &evidence["codecs"][name]["transition_observations"];
            assert_eq!(
                format!("{:x}", Sha256::digest(observation)),
                expected["sha256"],
                "{name}"
            );
            assert_eq!(accepted, expected["accepted"].as_u64().unwrap(), "{name}");
        }
    }
}
