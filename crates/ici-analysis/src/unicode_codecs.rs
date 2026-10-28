//! Native strict Unicode codecs over the tested CPython/Unicode character set.
use crate::codecs::{Codec, Decoded};
use crate::source::SourceError;
use crate::stateful::{Builder, cancelled};
use std::sync::atomic::AtomicBool;
include!("unicode_codec_tables.rs");

pub(crate) fn lookup(name: &str) -> Option<Codec> {
    if name == "charmap" {
        return crate::codecs::lookup("latin1");
    }
    UNICODE_ALIASES
        .binary_search_by_key(&name, |(alias, _)| *alias)
        .ok()
        .map(|index| UNICODE_ALIASES[index].1)
}
pub(crate) fn named_character(name: &str) -> Option<char> {
    if !name.is_ascii() || name.len() > 256 {
        return None;
    }
    let upper = name.to_ascii_uppercase();
    if let Ok(index) = NAME_ALIASES.binary_search_by_key(&upper.as_str(), |(alias, _)| *alias) {
        return char::from_u32(NAME_ALIASES[index].1);
    }
    let value = unicode_names2::character(name)?;
    // The dependency accepts zero-padded CJK spellings that CPython rejects.
    // Only an exact (ASCII-case-insensitive) Unicode 16 canonical name or
    // official alias may authorize a named source escape.
    unicode_names2::name(value)
        .filter(|canonical| canonical.to_string().eq_ignore_ascii_case(name))
        .map(|_| value)
}
pub(crate) fn invalid_named_escape(text: &str) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'\\' {
            index += 1;
            continue;
        }
        if bytes.get(index + 1) != Some(&b'N') {
            index += 2;
            continue;
        }
        if bytes.get(index + 2) != Some(&b'{') {
            return Some(index);
        }
        let begin = index + 3;
        let Some(relative_end) = bytes[begin..].iter().position(|byte| *byte == b'}') else {
            return Some(index);
        };
        let end = begin + relative_end;
        if named_character(&text[begin..end]).is_none() {
            return Some(index);
        }
        index = end + 1;
    }
    None
}
fn non_scalar(builder: &Builder, name: &str) -> SourceError {
    let mut error = builder.invalid(name);
    error.code = "invalid-text";
    error.message = format!("Source {name} codec decoded an unpaired Unicode surrogate");
    error
}
fn hex(byte: u8) -> Option<u32> {
    match byte {
        b'0'..=b'9' => Some(u32::from(byte - b'0')),
        b'a'..=b'f' => Some(u32::from(byte - b'a') + 10),
        b'A'..=b'F' => Some(u32::from(byte - b'A') + 10),
        _ => None,
    }
}
fn escapes(
    raw: bool,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let name = if raw {
        "raw_unicode_escape"
    } else {
        "unicode_escape"
    };
    if bytes.is_ascii() && !bytes.contains(&b'\\') {
        return crate::codecs::decode(Codec::Utf8, bytes, raw_base, cancellation);
    }
    let mut builder = Builder::new(bytes.len(), raw_base);
    let (mut offset, mut checked) = (0, 0);
    while offset < bytes.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let byte = bytes[offset];
        if byte != b'\\' {
            offset += 1;
            builder.push(u32::from(byte), offset)?;
            continue;
        }
        let start = offset;
        offset += 1;
        let Some(&escape) = bytes.get(offset) else {
            if raw {
                builder.push(u32::from(b'\\'), offset)?;
                break;
            }
            return Err(builder.invalid(name));
        };
        offset += 1;
        let value = match escape {
            b'u' | b'U' | b'x' if escape != b'x' || !raw => {
                let count = match escape {
                    b'x' => 2,
                    b'u' => 4,
                    _ => 8,
                };
                let encoded = bytes
                    .get(offset..offset + count)
                    .ok_or_else(|| builder.invalid(name))?;
                let mut value = 0;
                for &digit in encoded {
                    value = (value << 4) | hex(digit).ok_or_else(|| builder.invalid(name))?;
                }
                offset += count;
                if value > 0x10ffff {
                    return Err(builder.invalid(name));
                }
                if (0xd800..=0xdfff).contains(&value) {
                    return Err(non_scalar(&builder, name));
                }
                value
            }
            b'\n' if !raw => continue,
            b'\\' | b'\'' | b'"' if !raw => u32::from(escape),
            b'a' if !raw => 7,
            b'b' if !raw => 8,
            b'f' if !raw => 12,
            b'n' if !raw => 10,
            b'r' if !raw => 13,
            b't' if !raw => 9,
            b'v' if !raw => 11,
            b'0'..=b'7' if !raw => {
                let mut value = u32::from(escape - b'0');
                for _ in 0..2 {
                    match bytes.get(offset) {
                        Some(next @ b'0'..=b'7') => {
                            value = (value << 3) + u32::from(next - b'0');
                            offset += 1;
                        }
                        _ => break,
                    }
                }
                value
            }
            b'N' if !raw => {
                if bytes.get(offset) != Some(&b'{') {
                    return Err(builder.invalid(name));
                }
                offset += 1;
                let begin = offset;
                while bytes.get(offset).is_some_and(|byte| *byte != b'}') {
                    cancelled(cancellation, offset, &mut checked)?;
                    offset += 1;
                }
                if bytes.get(offset) != Some(&b'}') {
                    return Err(builder.invalid(name));
                }
                let label = std::str::from_utf8(&bytes[begin..offset])
                    .map_err(|_| builder.invalid(name))?;
                let value = named_character(label).ok_or_else(|| builder.invalid(name))?;
                offset += 1;
                u32::from(value)
            }
            _ => {
                builder.push(u32::from(b'\\'), start + 1)?;
                builder.unrepresentable();
                u32::from(escape)
            }
        };
        builder.push(value, offset)?;
    }
    Ok(builder.finish(bytes.len()))
}
fn base64(byte: u8) -> Option<u32> {
    match byte {
        b'A'..=b'Z' => Some(u32::from(byte - b'A')),
        b'a'..=b'z' => Some(u32::from(byte - b'a') + 26),
        b'0'..=b'9' => Some(u32::from(byte - b'0') + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}
fn utf7(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    if bytes.is_ascii() && !bytes.contains(&b'+') {
        return crate::codecs::decode(Codec::Utf8, bytes, raw_base, cancellation);
    }
    let mut builder = Builder::new(bytes.len(), raw_base);
    let (mut offset, mut checked) = (0, 0);
    while offset < bytes.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let byte = bytes[offset];
        if byte != b'+' {
            if byte >= 128 {
                return Err(builder.invalid("utf_7"));
            }
            offset += 1;
            builder.push(u32::from(byte), offset)?;
            continue;
        }
        offset += 1;
        if bytes.get(offset) == Some(&b'-') {
            offset += 1;
            builder.push(u32::from(b'+'), offset)?;
            continue;
        }
        if bytes
            .get(offset)
            .is_some_and(|byte| base64(*byte).is_none())
        {
            return Err(builder.invalid("utf_7"));
        }
        let (mut bits, mut buffer, mut high) = (0u32, 0u32, None::<u32>);
        let mut emitted = false;
        while let Some(value) = bytes.get(offset).and_then(|byte| base64(*byte)) {
            cancelled(cancellation, offset, &mut checked)?;
            offset += 1;
            buffer = (buffer << 6) | value;
            bits += 6;
            if bits < 16 {
                continue;
            }
            let word = buffer >> (bits - 16);
            bits -= 16;
            buffer &= (1 << bits) - 1;
            let scalar = if let Some(leading) = high.take() {
                if !(0xdc00..=0xdfff).contains(&word) {
                    return Err(non_scalar(&builder, "utf_7"));
                }
                0x10000 + ((leading - 0xd800) << 10) + word - 0xdc00
            } else if (0xd800..=0xdbff).contains(&word) {
                high = Some(word);
                continue;
            } else if (0xdc00..=0xdfff).contains(&word) {
                return Err(non_scalar(&builder, "utf_7"));
            } else {
                word
            };
            builder.push(scalar, offset)?;
            emitted = true;
            // A scalar boundary splitting the bits of one base64 byte has no
            // unique original byte position. Aligned boundaries are exact.
            if bits != 0 {
                builder.unrepresentable();
            }
        }
        if bits >= 6 || bits > 0 && buffer != 0 {
            return Err(builder.invalid("utf_7"));
        }
        if high.is_some() {
            if offset == bytes.len() || bytes[offset] >= 128 {
                return Err(builder.invalid("utf_7"));
            }
            return Err(non_scalar(&builder, "utf_7"));
        }
        if bytes.get(offset) == Some(&b'-') {
            offset += 1;
        }
        if emitted {
            builder.boundary(offset);
        }
    }
    Ok(builder.finish(bytes.len()))
}
fn utf16(
    little: Option<bool>,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let (little, bom) = match (little, bytes.get(..2)) {
        (None, Some([0xff, 0xfe])) => (true, 2),
        (None, Some([0xfe, 0xff])) => (false, 2),
        (order, _) => (order.unwrap_or(cfg!(target_endian = "little")), 0),
    };
    let data = &bytes[bom..];
    let mut builder = Builder::new(data.len(), raw_base + bom);
    let unit = |position| -> Option<u32> {
        let pair: [u8; 2] = data.get(position..position + 2)?.try_into().ok()?;
        Some(u32::from(if little {
            u16::from_le_bytes(pair)
        } else {
            u16::from_be_bytes(pair)
        }))
    };
    let (mut offset, mut checked) = (0, 0);
    while offset < data.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let first = unit(offset).ok_or_else(|| builder.invalid("utf_16"))?;
        offset += 2;
        let value = if (0xd800..=0xdbff).contains(&first) {
            let second = unit(offset).ok_or_else(|| builder.invalid("utf_16"))?;
            if !(0xdc00..=0xdfff).contains(&second) {
                return Err(builder.invalid("utf_16"));
            }
            offset += 2;
            0x10000 + ((first - 0xd800) << 10) + second - 0xdc00
        } else if (0xdc00..=0xdfff).contains(&first) {
            return Err(builder.invalid("utf_16"));
        } else {
            first
        };
        builder.push(value, offset)?;
    }
    Ok(builder.finish(data.len()))
}
fn utf32(
    little: Option<bool>,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let (little, bom) = match (little, bytes.get(..4)) {
        (None, Some([0xff, 0xfe, 0, 0])) => (true, 4),
        (None, Some([0, 0, 0xfe, 0xff])) => (false, 4),
        (order, _) => (order.unwrap_or(cfg!(target_endian = "little")), 0),
    };
    let data = &bytes[bom..];
    let mut builder = Builder::new(data.len(), raw_base + bom);
    let (mut offset, mut checked) = (0, 0);
    while offset < data.len() {
        cancelled(cancellation, offset, &mut checked)?;
        let word: [u8; 4] = data
            .get(offset..offset + 4)
            .ok_or_else(|| builder.invalid("utf_32"))?
            .try_into()
            .expect("fixed code unit");
        let value = if little {
            u32::from_le_bytes(word)
        } else {
            u32::from_be_bytes(word)
        };
        if char::from_u32(value).is_none() {
            return Err(builder.invalid("utf_32"));
        }
        offset += 4;
        builder.push(value, offset)?;
    }
    Ok(builder.finish(data.len()))
}
pub(crate) fn decode(
    codec: Codec,
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    match codec {
        Codec::UnicodeEscape(raw) => escapes(raw, bytes, raw_base, cancellation),
        Codec::Utf7 => utf7(bytes, raw_base, cancellation),
        Codec::Utf16(little) => utf16(little, bytes, raw_base, cancellation),
        Codec::Utf32(little) => utf32(little, bytes, raw_base, cancellation),
        _ => Err(SourceError::new(
            "invalid-codec",
            "Invalid native Unicode codec dispatch",
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    struct Observed {
        hash: Sha256,
        bytes: Vec<u8>,
        scalar: u64,
        invalid: u64,
        non_scalar: u64,
    }
    impl Observed {
        fn new() -> Self {
            Self {
                hash: Sha256::new(),
                bytes: Vec::new(),
                scalar: 0,
                invalid: 0,
                non_scalar: 0,
            }
        }
        fn update(&mut self, data: &[u8]) {
            self.bytes.extend_from_slice(data);
            if self.bytes.len() >= 65536 {
                self.hash.update(&self.bytes);
                self.bytes.clear();
            }
        }
        fn result(&mut self, codec: Codec, raw: &[u8]) {
            self.update(raw);
            match crate::codecs::decode(codec, raw, 0, None) {
                Ok(decoded) => {
                    self.scalar += 1;
                    self.update(&[1]);
                    self.update(&(decoded.text.len() as u32).to_le_bytes());
                    self.update(decoded.text.as_bytes());
                }
                Err(error) => {
                    if error.code == "invalid-text" {
                        self.non_scalar += 1;
                    } else {
                        self.invalid += 1;
                    }
                    self.update(&[0]);
                }
            }
        }
        fn digest(mut self) -> String {
            self.hash.update(&self.bytes);
            format!("{:x}", self.hash.finalize())
        }
        fn compare(self, expected: &Value, name: &str) {
            assert_eq!(
                self.scalar,
                expected["counts"]["scalar"].as_u64().unwrap(),
                "{name} scalar"
            );
            assert_eq!(
                self.invalid,
                expected["counts"]["invalid"].as_u64().unwrap(),
                "{name} invalid"
            );
            assert_eq!(
                self.non_scalar,
                expected["counts"]["non_scalar"].as_u64().unwrap(),
                "{name} non-scalar"
            );
            assert_eq!(self.digest(), expected["sha256"], "{name}");
        }
    }
    fn manifest() -> Value {
        serde_json::from_str(include_str!("../data/python-codecs/unicode-manifest.json")).unwrap()
    }
    fn scalar_bytes(value: u32, codec: Codec) -> Vec<u8> {
        match codec {
            Codec::UnicodeEscape(_) => format!("\\U{value:08X}").into_bytes(),
            Codec::Utf7 => {
                let units = utf16_units(value);
                let bytes: Vec<_> = units.iter().flat_map(|word| word.to_be_bytes()).collect();
                let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
                format!("+{}-", encoded.trim_end_matches('=')).into_bytes()
            }
            Codec::Utf16(little) => utf16_units(value)
                .iter()
                .flat_map(|word| {
                    if little.unwrap_or(cfg!(target_endian = "little")) {
                        word.to_le_bytes()
                    } else {
                        word.to_be_bytes()
                    }
                })
                .collect(),
            Codec::Utf32(little) => if little.unwrap_or(cfg!(target_endian = "little")) {
                value.to_le_bytes()
            } else {
                value.to_be_bytes()
            }
            .to_vec(),
            _ => panic!("expected Unicode codec"),
        }
    }
    fn utf16_units(value: u32) -> Vec<u16> {
        if value < 0x10000 {
            vec![value as u16]
        } else {
            let scalar = value - 0x10000;
            vec![
                (0xd800 + (scalar >> 10)) as u16,
                (0xdc00 + (scalar & 1023)) as u16,
            ]
        }
    }
    #[test]
    fn strict_short_unicode_codec_inputs_match_every_cpython_byte_pair() {
        let evidence = manifest();
        assert_eq!(
            evidence["platform_byteorder"],
            if cfg!(target_endian = "little") {
                "little"
            } else {
                "big"
            }
        );
        for (name, record) in evidence["codecs"].as_object().unwrap() {
            let codec = crate::codecs::lookup(name).unwrap();
            let mut observed = Observed::new();
            for width in [1u8, 2] {
                observed.update(&[width]);
                for value in 0..256u32.pow(u32::from(width)) {
                    let pair = (value as u16).to_be_bytes();
                    observed.result(codec, &pair[2 - usize::from(width)..]);
                }
            }
            observed.compare(&record["short"], name);
        }
    }
    #[test]
    fn every_unicode_codepoint_matches_strict_cpython_codec_observations() {
        let evidence = manifest();
        for (name, record) in evidence["codecs"].as_object().unwrap() {
            let Some(expected) = record.get("all_codepoints") else {
                continue;
            };
            let codec = crate::codecs::lookup(name).unwrap();
            let mut observed = Observed::new();
            for value in 0..0x110000u32 {
                observed.update(&value.to_le_bytes());
                observed.result(codec, &scalar_bytes(value, codec));
            }
            observed.compare(expected, name);
        }
    }
    fn title_ascii(name: &str) -> String {
        let mut start = true;
        name.chars()
            .map(|character| {
                if character.is_ascii_alphabetic() {
                    let result = if start {
                        character.to_ascii_uppercase()
                    } else {
                        character.to_ascii_lowercase()
                    };
                    start = false;
                    result
                } else {
                    start = true;
                    character
                }
            })
            .collect()
    }
    #[test]
    fn unicode16_names_and_lookup_variants_match_every_cpython_named_scalar() {
        let evidence = manifest();
        let mut names = Observed::new();
        let mut lookups = Observed::new();
        let mut count = 0u64;
        for value in 0..0x110000u32 {
            names.update(&value.to_le_bytes());
            let Some(name) = char::from_u32(value).and_then(unicode_names2::name) else {
                names.update(&[0]);
                continue;
            };
            count += 1;
            let name = name.to_string();
            names.update(&[1]);
            names.update(&(name.len() as u16).to_le_bytes());
            names.update(name.as_bytes());
            for label in [
                name.clone(),
                name.to_ascii_lowercase(),
                title_ascii(&name),
                name.replace(' ', "  "),
                name.replace('-', ""),
                format!("{name} "),
            ] {
                let raw = format!("\\N{{{label}}}");
                lookups.update(&(raw.len() as u16).to_le_bytes());
                lookups.update(raw.as_bytes());
                if let Some(character) = named_character(&label) {
                    let mut buffer = [0u8; 4];
                    let encoded = character.encode_utf8(&mut buffer);
                    lookups.update(&[1]);
                    lookups.update(&(encoded.len() as u32).to_le_bytes());
                    lookups.update(encoded.as_bytes());
                } else {
                    lookups.update(&[0]);
                }
            }
        }
        assert_eq!(
            count,
            evidence["unicode_names"]["named_scalars"].as_u64().unwrap()
        );
        assert_eq!(names.digest(), evidence["unicode_names"]["names_sha256"]);
        assert_eq!(lookups.digest(), evidence["unicode_names"]["lookup_sha256"]);
        assert_eq!(
            NAME_ALIASES.len() as u64,
            evidence["name_aliases_count"].as_u64().unwrap()
        );
        for (name, value) in NAME_ALIASES {
            assert_eq!(named_character(name), char::from_u32(*value));
            assert_eq!(
                named_character(&name.to_ascii_lowercase()),
                char::from_u32(*value)
            );
        }
        assert_eq!(named_character("CJK UNIFIED IDEOGRAPH-04E00"), None);
        assert_eq!(named_character("CJK UNIFIED IDEOGRAPH-4e00"), Some('一'));
    }
    #[test]
    fn available_standard_source_codec_census_keeps_unclosed_families_explicit() {
        let evidence = manifest();
        let mut differences = Vec::new();
        let mut gaps = Vec::new();
        for (name, record) in evidence["source_codec_census"].as_object().unwrap() {
            if record["disposition"] == "pending_source_audit" {
                if record["samples"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|sample| sample["source_accepted"] == true)
                {
                    gaps.push(name.as_str());
                }
                continue;
            }
            for (index, sample) in record["samples"].as_array().unwrap().iter().enumerate() {
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(sample["bytes_base64"].as_str().unwrap())
                    .unwrap();
                let source = crate::source::SourceDocument::decode(&ici_domain::SourceInput {
                    path: "main.py".into(),
                    bytes,
                });
                let actual =
                    source.and_then(|source| crate::parser::ParsedFile::parse(source, "3.14"));
                if actual.is_ok() != sample["source_accepted"].as_bool().unwrap() {
                    differences.push(format!(
                        "{name}[{index}]: {}",
                        actual
                            .err()
                            .map_or("accepted".into(), |error| format!("{error:?}"))
                    ));
                }
            }
        }
        assert!(differences.is_empty(), "{}", differences.join("\n"));
        assert_eq!(
            gaps,
            vec!["idna"],
            "Remaining accepted standard codec families stay explicit"
        );
    }
    #[test]
    fn shifted_utf7_and_greedy_escape_maps_prove_only_original_byte_boundaries() {
        let decoded = crate::codecs::decode(Codec::Utf7, b"+AGEAYgBjAGQ-", 0, None).unwrap();
        assert_eq!(decoded.text, "abcd");
        let map = decoded.map.unwrap();
        for (position, expected) in [
            (0, Some(0)),
            (1, None),
            (2, None),
            (3, Some(9)),
            (4, Some(13)),
        ] {
            assert_eq!(map.raw_offset(&decoded.text, position), expected);
        }
        let decoded = crate::codecs::decode(Codec::Utf7, b"+ZeU!", 0, None).unwrap();
        assert_eq!(decoded.text, "日!");
        let map = decoded.map.unwrap();
        assert_eq!(map.raw_offset(&decoded.text, 3), Some(4));
        assert_eq!(map.raw_offset(&decoded.text, 4), Some(5));
        let decoded =
            crate::codecs::decode(Codec::UnicodeEscape(false), b"\\777x", 0, None).unwrap();
        assert_eq!(decoded.text, "ǿx");
        let map = decoded.map.unwrap();
        assert_eq!(map.raw_offset(&decoded.text, 2), Some(4));
        assert_eq!(map.raw_offset(&decoded.text, 3), Some(5));
    }
    #[test]
    fn utf16_and_utf32_decoding_observe_active_cancellation() {
        use std::sync::atomic::Ordering;
        use std::time::{Duration, Instant};
        for (codec, atom) in [
            (Codec::Utf16(Some(true)), b"A\0".as_slice()),
            (Codec::Utf32(Some(false)), b"\0\0\0A".as_slice()),
        ] {
            let bytes = atom.repeat(16 * 1024 * 1024 / atom.len());
            let flag = AtomicBool::new(false);
            let started = Instant::now();
            let result = std::thread::scope(|scope| {
                let worker = scope.spawn(|| {
                    std::thread::sleep(Duration::from_millis(2));
                    flag.store(true, Ordering::Relaxed);
                });
                let result = crate::codecs::decode(codec, &bytes, 0, Some(&flag));
                worker.join().unwrap();
                result
            });
            assert!(matches!(result,Err(error) if error.code == "cancelled"));
            assert!(started.elapsed() < Duration::from_secs(2));
        }
    }
}
