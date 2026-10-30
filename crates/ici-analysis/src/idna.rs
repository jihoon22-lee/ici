//! CPython 3.14.7 `idna` (RFC 3490 ToUnicode over byte labels) and `punycode` source codecs.
//!
//! Byte input never reaches nameprep directly: CPython only decodes ASCII-compatible
//! labels and checks each `xn--` label by punycode-decoding it and re-encoding the result
//! through ToASCII (nameprep over Unicode 3.2 stringprep tables plus Unicode 3.2 NFKC).
//! Every table is generated from the reference interpreter; see
//! `tests/rust-parity/python/generate_idna_tables.py`.
use crate::codecs::Decoded;
use crate::idna_tables::{
    B1, B2_DATA, B2_MAP, CORRECTIONS, L_CAT, PROHIBITED, RAND_AL, UNASSIGNED_32,
};
use crate::source::SourceError;
use crate::stateful::{Builder, cancelled};
use std::sync::atomic::AtomicBool;
use unicode_normalization::char::{canonical_combining_class, compose, decompose_compatible};

const MAX_LABEL: usize = 1024;
const S_BASE: u32 = 0xAC00;
const L_BASE: u32 = 0x1100;
const V_BASE: u32 = 0x1161;
const T_BASE: u32 = 0x11A7;
const L_COUNT: u32 = 19;
const V_COUNT: u32 = 21;
const T_COUNT: u32 = 28;
const S_COUNT: u32 = L_COUNT * V_COUNT * T_COUNT;

fn in_ranges(table: &[(u32, u32)], code: u32) -> bool {
    let index = table.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= table[index - 1].1
}
fn is_surrogate(code: u32) -> bool {
    (0xD800..0xE000).contains(&code)
}
fn ccc(code: u32) -> u8 {
    char::from_u32(code).map_or(0, canonical_combining_class)
}

/// CPython `nfd_nfkd(ucd_3_2_0, ..., k=1)`: current decompositions, except that
/// characters unassigned in Unicode 3.2 do not decompose and five corrected
/// characters are replaced first. Reordering uses the current combining classes.
fn nfkd32(input: &[u32], output: &mut Vec<u32>) {
    let mut stack = Vec::new();
    for &first in input {
        stack.push(first);
        while let Some(code) = stack.pop() {
            if (S_BASE..S_BASE + S_COUNT).contains(&code) {
                let index = code - S_BASE;
                output.push(L_BASE + index / (V_COUNT * T_COUNT));
                output.push(V_BASE + (index % (V_COUNT * T_COUNT)) / T_COUNT);
                if !index.is_multiple_of(T_COUNT) {
                    output.push(T_BASE + index % T_COUNT);
                }
                continue;
            }
            if let Ok(index) = CORRECTIONS.binary_search_by_key(&code, |(from, _)| *from) {
                stack.push(CORRECTIONS[index].1);
                continue;
            }
            match char::from_u32(code) {
                Some(character) if !in_ranges(UNASSIGNED_32, code) => {
                    let mut parts = Vec::new();
                    decompose_compatible(character, |part| parts.push(part as u32));
                    if parts.len() == 1 && parts[0] == code {
                        output.push(code);
                    } else {
                        // Each part is decomposed again under the same 3.2 rules.
                        stack.extend(parts.into_iter().rev());
                    }
                }
                _ => output.push(code),
            }
        }
    }
    let mut start = 0;
    while start < output.len() {
        if ccc(output[start]) == 0 {
            start += 1;
            continue;
        }
        let mut end = start;
        while end < output.len() && ccc(output[end]) != 0 {
            end += 1;
        }
        output[start..end].sort_by_key(|code| ccc(*code));
        start = end;
    }
}

fn composes(first: u32, second: u32) -> Option<u32> {
    // Hangul is composed only by CPython's explicit adjacent L/V/T branch.
    let hangul =
        |code: u32| (0x1100..0x1200).contains(&code) || (S_BASE..S_BASE + S_COUNT).contains(&code);
    if hangul(first) || hangul(second) {
        return None;
    }
    compose(char::from_u32(first)?, char::from_u32(second)?).map(|code| code as u32)
}

/// CPython `nfc_nfkc(ucd_3_2_0, ..., k=1)` over the 3.2 decomposition above.
pub(crate) fn nfkc32(input: &[u32]) -> Vec<u32> {
    let mut data = Vec::with_capacity(input.len());
    nfkd32(input, &mut data);
    let mut skipped = vec![false; data.len()];
    let mut output = Vec::with_capacity(data.len());
    let mut i = 0;
    while i < data.len() {
        if skipped[i] {
            i += 1;
            continue;
        }
        let code = data[i];
        if (L_BASE..L_BASE + L_COUNT).contains(&code)
            && i + 1 < data.len()
            && (V_BASE..V_BASE + V_COUNT).contains(&data[i + 1])
        {
            let mut composed =
                S_BASE + ((code - L_BASE) * V_COUNT + data[i + 1] - V_BASE) * T_COUNT;
            i += 2;
            if i < data.len() && T_BASE < data[i] && data[i] < T_BASE + T_COUNT {
                composed += data[i] - T_BASE;
                i += 1;
            }
            output.push(composed);
            continue;
        }
        let mut current = code;
        let mut comb = 0;
        let mut next = i + 1;
        while next < data.len() {
            if skipped[next] {
                next += 1;
                continue;
            }
            let following = data[next];
            let class = ccc(following);
            if comb != 0 {
                if class == 0 {
                    break;
                }
                if comb >= class {
                    next += 1;
                    continue;
                }
            }
            match composes(current, following) {
                Some(value) => {
                    current = value;
                    skipped[next] = true;
                    next += 1;
                }
                None => {
                    if class == 0 {
                        break;
                    }
                    comb = class;
                    next += 1;
                }
            }
        }
        output.push(current);
        i += 1;
    }
    output
}

/// RFC 3491 nameprep exactly as `encodings.idna.nameprep` (AllowUnassigned).
pub(crate) fn nameprep(label: &[u32]) -> Option<Vec<u32>> {
    let mut mapped = Vec::with_capacity(label.len());
    for &code in label {
        if in_ranges(B1, code) {
            continue;
        }
        match B2_MAP.binary_search_by_key(&code, |(from, _, _)| *from) {
            Ok(index) => {
                let (_, start, length) = B2_MAP[index];
                mapped
                    .extend_from_slice(&B2_DATA[start as usize..start as usize + length as usize]);
            }
            Err(_) => mapped.push(code),
        }
    }
    // Lone surrogates are prohibited (C.5) and never change before that check.
    if mapped.iter().any(|code| is_surrogate(*code)) {
        return None;
    }
    let normalized = nfkc32(&mapped);
    if normalized.iter().any(|code| in_ranges(PROHIBITED, *code)) {
        return None;
    }
    let rand_al: Vec<bool> = normalized
        .iter()
        .map(|code| in_ranges(RAND_AL, *code))
        .collect();
    if rand_al.iter().any(|value| *value)
        && (normalized.iter().any(|code| in_ranges(L_CAT, *code))
            || !rand_al[0]
            || !rand_al[rand_al.len() - 1])
    {
        return None;
    }
    Some(normalized)
}

fn adapt(delta: u64, first: bool, length: u64) -> u64 {
    let mut delta = if first { delta / 700 } else { delta / 2 };
    delta += delta / length;
    let mut divisions = 0;
    while delta > 455 {
        delta /= 35;
        divisions += 36;
    }
    divisions + 36 * delta / (delta + 38)
}

/// `encodings.punycode.punycode_decode(text, "strict")` over bytes. Python integers
/// are unbounded; any value that leaves u64 necessarily exceeds U+10FFFF below.
pub(crate) fn punycode_decode(text: &[u8]) -> Option<Vec<u32>> {
    let (mut base, extended): (Vec<u32>, &[u8]) = match text.iter().rposition(|byte| *byte == b'-')
    {
        None => (Vec::new(), text),
        Some(position) => {
            if !text[..position].is_ascii() {
                return None;
            }
            (
                text[..position]
                    .iter()
                    .map(|byte| u32::from(*byte))
                    .collect(),
                &text[position + 1..],
            )
        }
    };
    let mut character: i128 = 0x80;
    let mut position: i128 = -1;
    let mut bias: u64 = 72;
    let mut extpos = 0;
    while extpos < extended.len() {
        let start = extpos;
        let mut result: u64 = 0;
        let mut weight: u64 = 1;
        let mut overflow = false;
        let mut j: u64 = 0;
        loop {
            let byte = extended.get(extpos)?.to_ascii_uppercase();
            extpos += 1;
            let digit = match byte {
                b'A'..=b'Z' => u64::from(byte - b'A'),
                b'0'..=b'9' => u64::from(byte) - 22,
                _ => return None,
            };
            let threshold = (36 * (j + 1)).saturating_sub(bias).clamp(1, 26);
            match digit
                .checked_mul(weight)
                .and_then(|value| result.checked_add(value))
            {
                Some(value) if !overflow => result = value,
                _ => overflow = true,
            }
            if digit < threshold {
                break;
            }
            weight = weight.saturating_mul(36 - threshold);
            j += 1;
        }
        if overflow {
            return None;
        }
        let length = base.len() as i128 + 1;
        position += i128::from(result) + 1;
        character += position / length;
        if character > 0x10FFFF {
            return None;
        }
        position %= length;
        base.insert(position as usize, character as u32);
        bias = adapt(result, start == 0, base.len() as u64);
    }
    Some(base)
}

/// ToASCII(result) for a decoded ACE label, compared with the lower-case label.
fn round_trips(result: &[u32], label: &[u8]) -> bool {
    let lower = label.to_ascii_lowercase();
    let ascii = |values: &[u32]| values.iter().all(|code| *code < 0x80);
    let check_ascii = |values: &[u32]| {
        (1..64).contains(&values.len())
            && values
                .iter()
                .map(|code| *code as u8)
                .eq(lower.iter().copied())
    };
    if ascii(result) {
        return check_ascii(result);
    }
    let Some(prepared) = nameprep(result) else {
        return false;
    };
    if ascii(&prepared) {
        return check_ascii(&prepared);
    }
    if prepared.len() >= 4
        && prepared[..4].iter().zip(b"xn--").all(|(code, expected)| {
            char::from_u32(*code).is_some_and(|c| c.to_ascii_lowercase() == char::from(*expected))
        })
    {
        return false;
    }
    let Some(characters) = prepared
        .iter()
        .map(|code| char::from_u32(*code))
        .collect::<Option<Vec<_>>>()
    else {
        return false;
    };
    let Some(encoded) = idna::punycode::encode(&characters) else {
        return false;
    };
    let ace = format!("xn--{encoded}");
    ace.len() < 64 && ace.as_bytes() == lower.as_slice()
}

fn contains_ace(bytes: &[u8]) -> bool {
    bytes
        .windows(4)
        .any(|window| window.eq_ignore_ascii_case(b"xn--"))
}

/// CPython `encodings.idna.Codec.decode(bytes, "strict")` with original-byte boundaries.
pub(crate) fn decode(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let mut checked = 0;
    if !contains_ace(bytes) {
        cancelled(cancellation, 0, &mut checked)?;
        return match std::str::from_utf8(bytes) {
            Ok(text) if bytes.is_ascii() => Ok(Decoded {
                text: text.to_owned(),
                map: None,
            }),
            _ => {
                let mut builder = Builder::new(0, raw_base);
                let valid = bytes.iter().position(|byte| !byte.is_ascii()).unwrap_or(0);
                for (index, byte) in bytes[..valid].iter().enumerate() {
                    builder.push(u32::from(*byte), index + 1)?;
                }
                Err(builder.invalid("idna"))
            }
        };
    }
    let mut builder = Builder::new(bytes.len(), raw_base);
    let mut offset = 0;
    let labels: Vec<&[u8]> = bytes.split(|byte| *byte == b'.').collect();
    for (number, label) in labels.iter().enumerate() {
        cancelled(cancellation, offset, &mut checked)?;
        if number > 0 {
            builder.push(u32::from(b'.'), offset)?;
        }
        let end = offset + label.len();
        if label.len() > MAX_LABEL {
            return Err(builder.invalid("idna"));
        }
        if label.len() >= 4 && label[..4].eq_ignore_ascii_case(b"xn--") {
            let decoded = punycode_decode(&label[4..])
                .filter(|result| round_trips(result, label))
                .ok_or_else(|| builder.invalid("idna"))?;
            for (index, code) in decoded.iter().enumerate() {
                builder.push(*code, end)?;
                if index + 1 < decoded.len() {
                    builder.unrepresentable();
                }
            }
            if decoded.is_empty() {
                builder.boundary(end);
            }
        } else {
            for (index, byte) in label.iter().enumerate() {
                if !byte.is_ascii() {
                    return Err(builder.invalid("idna"));
                }
                builder.push(u32::from(*byte), offset + index + 1)?;
            }
        }
        offset = end + 1;
    }
    Ok(builder.finish(bytes.len()))
}

/// CPython `punycode` as a source codec. A non-empty Python source always ends in
/// LF before decoding, and LF is never a punycode digit, so source decoding always
/// fails; the decoder is still the exact codec rather than a blanket refusal.
pub(crate) fn decode_punycode(
    bytes: &[u8],
    raw_base: usize,
    cancellation: Option<&AtomicBool>,
) -> Result<Decoded, SourceError> {
    let mut checked = 0;
    cancelled(cancellation, 0, &mut checked)?;
    let builder = Builder::new(bytes.len(), raw_base);
    let decoded = punycode_decode(bytes).ok_or_else(|| builder.invalid("punycode"))?;
    let mut builder = builder;
    for (index, code) in decoded.iter().enumerate() {
        builder.push(*code, bytes.len())?;
        if index + 1 < decoded.len() {
            builder.unrepresentable();
        }
    }
    Ok(builder.finish(bytes.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use sha2::{Digest, Sha256};

    fn manifest() -> Value {
        serde_json::from_str(include_str!("../data/python-codecs/idna-manifest.json")).unwrap()
    }
    fn render(result: Option<Vec<u32>>) -> String {
        result.map_or("E".into(), |values| {
            values
                .iter()
                .map(|code| format!("{code:x}"))
                .collect::<Vec<_>>()
                .join(",")
        })
    }
    fn check(evidence: &Value, key: &str, lines: impl Iterator<Item = String>) {
        let mut hash = Sha256::new();
        let mut count = 0;
        for line in lines {
            hash.update(line.as_bytes());
            hash.update(b"\n");
            count += 1;
        }
        assert_eq!(evidence["observations"][key]["count"], count, "{key} count");
        assert_eq!(
            evidence["observations"][key]["sha256"],
            format!("{:x}", hash.finalize()),
            "{key} digest"
        );
    }
    fn splitmix(state: &mut u64) -> u64 {
        *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut value = *state;
        value = (value ^ (value >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        value ^ (value >> 31)
    }
    fn random_strings(evidence: &Value) -> Vec<Vec<u32>> {
        let pool: Vec<u32> = evidence["random_pool"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| u32::from_str_radix(value.as_str().unwrap(), 16).unwrap())
            .collect();
        let mut state = 0x1C1DA0;
        (0..evidence["random_strings"].as_u64().unwrap())
            .map(|_| {
                let length = 1 + splitmix(&mut state) % 8;
                (0..length)
                    .map(|_| pool[(splitmix(&mut state) % pool.len() as u64) as usize])
                    .collect()
            })
            .collect()
    }
    fn joined(values: &[u32]) -> String {
        values
            .iter()
            .map(|code| format!("{code:x}"))
            .collect::<Vec<_>>()
            .join(",")
    }

    #[test]
    fn nameprep_and_unicode32_nfkc_match_every_cpython_observation() {
        let evidence = manifest();
        let tables = include_str!("idna_tables.rs");
        assert_eq!(
            evidence["tables_sha256"],
            format!("{:x}", Sha256::digest(tables.as_bytes()))
        );
        check(
            &evidence,
            "nameprep_codepoints",
            (0..0x110000u32).map(|code| format!("{code:x}:{}", render(nameprep(&[code])))),
        );
        check(
            &evidence,
            "nfkc32_scalars",
            (0..0x110000u32)
                .filter(|code| !is_surrogate(*code))
                .map(|code| format!("{code:x}:{}", render(Some(nfkc32(&[code]))))),
        );
        let list = |key: &str| -> Vec<u32> {
            evidence["pairs"][key]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| u32::from_str_radix(value.as_str().unwrap(), 16).unwrap())
                .collect()
        };
        let (firsts, seconds) = (list("firsts"), list("seconds"));
        check(
            &evidence,
            "nfkc32_pairs",
            firsts.iter().flat_map(|a| {
                seconds
                    .iter()
                    .map(move |b| format!("{a:x},{b:x}:{}", render(Some(nfkc32(&[*a, *b])))))
            }),
        );
        let strings = random_strings(&evidence);
        check(
            &evidence,
            "nameprep_random",
            strings
                .iter()
                .map(|value| format!("{}:{}", joined(value), render(nameprep(value)))),
        );
        check(
            &evidence,
            "nfkc32_random",
            strings
                .iter()
                .filter(|value| !value.iter().any(|code| is_surrogate(*code)))
                .map(|value| format!("{}:{}", joined(value), render(Some(nfkc32(value))))),
        );
    }

    #[test]
    fn idna_labels_and_byte_sources_match_cpython() {
        let evidence = manifest();
        let mut differences = Vec::new();
        for label in evidence["labels"].as_array().unwrap() {
            let bytes = hex(label["hex"].as_str().unwrap());
            let actual = decode(&bytes, 0, None)
                .ok()
                .map(|decoded| decoded.text.chars().map(|ch| ch as u32).collect());
            if render(actual.clone()) != label["decoded"].as_str().unwrap() {
                differences.push(format!(
                    "{}: {} != {}",
                    label["hex"],
                    render(actual),
                    label["decoded"]
                ));
            }
        }
        assert!(differences.is_empty(), "{}", differences.join("\n"));
        for sample in evidence["sources"].as_array().unwrap() {
            let bytes = hex(sample["hex"].as_str().unwrap());
            let parsed = crate::source::SourceDocument::decode(&ici_domain::SourceInput {
                path: "main.py".into(),
                bytes,
            })
            .and_then(|source| crate::parser::ParsedFile::parse(source, "3.14"));
            assert_eq!(
                parsed.is_ok(),
                sample["source_accepted"].as_bool().unwrap(),
                "{}: {:?}",
                sample["hex"],
                parsed.err()
            );
        }
    }

    #[test]
    fn punycode_sources_always_end_in_an_invalid_digit() {
        // CPython's tokenizer appends LF to non-empty exec input before decoding
        // (Parser/tokenizer/helpers.c _PyTokenizer_translate_newlines). The final
        // LF is in the extended part after the last '-', or the whole text is the
        // extended part, and LF is never a punycode digit.
        assert_eq!(
            punycode_decode(b"#coding:punycode\nabc-"),
            Some("#coding:punycode\nabc".chars().map(|c| c as u32).collect())
        );
        for text in [
            b"#coding:punycode\nabc-\n".as_slice(),
            b"#coding:punycode\n",
            b"# coding: punycode\nx-1\n",
        ] {
            assert_eq!(punycode_decode(text), None);
        }
    }

    #[test]
    fn decoded_ace_labels_map_only_their_original_byte_ends() {
        let source = b"x = 'a.xn--bcher-kva.b'\n";
        let decoded = decode(source, 0, None).unwrap();
        assert_eq!(decoded.text, "x = 'a.b\u{fc}cher.b'\n");
        let map = decoded.map.unwrap();
        let start = "x = 'a.".len();
        assert_eq!(map.raw_offset(&decoded.text, start), Some(7));
        let end = start + "b\u{fc}cher".len();
        assert_eq!(map.raw_offset(&decoded.text, end), Some(20));
        assert_eq!(map.raw_offset(&decoded.text, start + 1), None);
        assert!(decode(b"x = 'caf\xc3\xa9'\n", 0, None).is_err());
        let flag = AtomicBool::new(true);
        assert!(matches!(
            decode(source, 0, Some(&flag)),
            Err(error) if error.code == "cancelled"
        ));
    }

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap())
            .collect()
    }
}
