//! Root ruling 2026-11-12 (reader facet, (b)): a stored report's integer tokens are judged by value
//! at the raw stage and refused outside -9223372036854775808..18446744073709551615 instead of being
//! rounded through `f64`. The expectations below are written out literally, independent of the
//! reader's own bounds.
mod common;
use ici_reporting::{StoredResult, check_stored_integers};

type Reader = fn(&[u8]) -> ici_reporting::Result<StoredResult>;
const RANGE: &str = "outside the supported range -9223372036854775808..18446744073709551615";

fn refused_at(text: &str) -> Option<String> {
    check_stored_integers(text.as_bytes())
        .err()
        .map(|error| error.to_string())
}

#[test]
fn signed_and_unsigned_bounds_are_kept_and_one_step_past_each_is_refused() {
    for kept in [
        "[-9223372036854775808]",
        "[18446744073709551615]",
        "[9223372036854775807]",
        "[9223372036854775808]",
        "[0, -0, 1, -1]",
        "[000]",
    ] {
        assert_eq!(refused_at(kept), None, "{kept}");
    }
    for (refused, offset) in [
        ("[-9223372036854775809]", 1),
        ("[18446744073709551616]", 1),
        ("[1, 99999999999999999999]", 4),
        ("[-99999999999999999999]", 1),
        ("{\"a\": {\"b\": [2, 3, 100000000000000000000]}}", 19),
    ] {
        let error = refused_at(refused).unwrap_or_else(|| panic!("{refused} kept"));
        assert!(error.contains(RANGE), "{error}");
        assert!(error.contains(&format!("byte offset {offset} ")), "{error}");
        // The refused digits themselves are never quoted (the message names only the range).
        for digits in [
            "9223372036854775809",
            "18446744073709551616",
            "99999999999999999999",
            "100000000000000000000",
        ] {
            assert!(!error.contains(digits), "{error}");
        }
    }
    // A byte-order mark counts in the reported offset.
    let error = check_stored_integers(b"\xef\xbb\xbf[18446744073709551616]")
        .unwrap_err()
        .to_string();
    assert!(error.contains("byte offset 4 "), "{error}");
}

#[test]
fn strings_escapes_keys_and_real_numbers_are_not_integer_tokens() {
    for kept in [
        r#"{"99999999999999999999": "18446744073709551616"}"#,
        r#"["\"99999999999999999999", "\\", 1]"#,
        r#"["99", "a\\\"b 99999999999999999999"]"#,
        "[99999999999999999999.0, 1e20, 1E+20, -9223372036854775809.5, 18446744073709551616e0]",
        "[true, false, null]",
    ] {
        assert_eq!(refused_at(kept), None, "{kept}");
    }
    // After a string ends, an unquoted integer is still checked.
    assert!(refused_at(r#"["\"", 18446744073709551616]"#).is_some());
}

#[test]
fn the_stored_report_reader_refuses_before_rounding_and_keeps_its_other_refusals() {
    // The whole reader path, not only the scan: the 20-digit value never becomes 1e+20.
    let rounded = br#"{"schema_version":"ici.result/v3","suite_status":"PASS","results":[{"engine_name":"x","status":"PASS","extra":{"version_tuple":[99999999999999999999,1]}}],"baseline_comparison":null}"#;
    let readers: [Reader; 2] = [StoredResult::from_bytes, StoredResult::from_report_bytes];
    for read in readers {
        let error = read(rounded).unwrap_err().to_string();
        assert!(error.contains(RANGE), "{error}");
    }
    // The existing refusals still apply first-hand.
    for (bytes, words) in [
        (&br#"{"a":1,"a":2}"#[..], "duplicate JSON key"),
        (&b"{} trailing"[..], "trailing"),
        (&b"[1e400]"[..], "number out of range"),
        (&b"[NaN]"[..], "expected value"),
    ] {
        let error = StoredResult::from_bytes(bytes).unwrap_err().to_string();
        assert!(error.contains(words), "{words}: {error}");
        assert!(!error.contains(RANGE), "{error}");
    }
}

/// The frozen big-integer capture with its out-of-range integers replaced by u64::MAX as raw text
/// (never parsed through serde_json first, which would round them): the reader keeps the value,
/// and the page's complete embedded result, inflated as the page does, carries the exact integer
/// in every tool's `version_tuple`, not only in the visible text.
#[test]
fn the_unsigned_maximum_is_kept_exactly_in_the_embedded_complete_result() {
    let frozen = include_str!("../../../tests/rust-parity/reporting/bigint-verify-report.json");
    let kept = frozen
        .replace("99999999999999999999", "18446744073709551615")
        .replace("18446744073709551616", "18446744073709551615");
    let stored = StoredResult::from_bytes(kept.as_bytes()).unwrap();
    let html = ici_reporting::render_html(&stored, &Default::default()).unwrap();
    let embedded = common::embedded(&html);
    let tools = embedded["capability_inventory"]["tools"]
        .as_array()
        .unwrap();
    for name in ["ar", "gcc", "nm", "clang-tidy", "ld"] {
        let tool = tools
            .iter()
            .find(|tool| tool["name"] == name)
            .unwrap_or_else(|| panic!("{name}"));
        assert_eq!(
            tool["version_tuple"][0].as_u64(),
            Some(u64::MAX),
            "{name}: {tool}"
        );
        assert!(tool["version_tuple"][0].is_u64(), "{name}");
    }
}

/// Both ends of the range survive the common bytes -> StoredResult -> JSON round trip literally.
#[test]
fn both_range_ends_survive_the_stored_round_trip() {
    let bytes = br#"{"schema_version":"ici.result/v3","suite_status":"PASS","results":[{"engine_name":"x","status":"PASS","summary":"ok","extra":{"low":-9223372036854775808,"high":18446744073709551615,"text":"18446744073709551616","real":1.5e3}}],"baseline_comparison":null}"#;
    let value = StoredResult::from_bytes(bytes).unwrap().to_value().unwrap();
    let extra = &value["results"][0]["extra"];
    assert_eq!(extra["low"].as_i64(), Some(i64::MIN));
    assert_eq!(extra["high"].as_u64(), Some(u64::MAX));
    assert_eq!(extra["text"], "18446744073709551616");
    assert_eq!(extra["real"].as_f64(), Some(1500.0));
    let text = serde_json::to_string(&value).unwrap();
    assert!(text.contains("-9223372036854775808") && text.contains("18446744073709551615"));
}
