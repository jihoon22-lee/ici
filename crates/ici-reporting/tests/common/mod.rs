//! Shared report-page helpers for integration tests.
#![allow(dead_code)]
use serde_json::Value;
use std::io::Read;

/// Inflates the embedded saved result exactly as the page does (base64, then raw DEFLATE).
pub fn embedded(html: &str) -> Value {
    let text = html
        .split("data-encoding=deflate-raw+base64 data-bytes=")
        .nth(1)
        .unwrap();
    let (declared, rest) = text.split_once('>').unwrap();
    let encoded = rest.split("</script>").next().unwrap();
    let mut bytes = vec![];
    let mut buffer = 0u32;
    let mut bits = 0;
    for character in encoded.bytes().filter(|byte| *byte != b'=') {
        let value = match character {
            b'A'..=b'Z' => character - b'A',
            b'a'..=b'z' => character - b'a' + 26,
            b'0'..=b'9' => character - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => panic!("invalid base64"),
        };
        buffer = buffer << 6 | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            bytes.push((buffer >> bits) as u8);
        }
    }
    let mut json = String::new();
    flate2::read::DeflateDecoder::new(&bytes[..])
        .read_to_string(&mut json)
        .unwrap();
    assert_eq!(declared.parse::<usize>().unwrap(), json.len());
    serde_json::from_str(&json).unwrap()
}
