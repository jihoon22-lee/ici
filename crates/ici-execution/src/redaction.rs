//! Credential masking at log/report boundaries, never on provider parse bytes.
use regex::{Captures, Regex};
use serde_json::Value;
use std::{collections::BTreeSet, sync::OnceLock};

pub const REDACTED: &str = "***REDACTED***";
pub fn sensitive_key(key: &str) -> bool {
    let normalized = key.to_ascii_lowercase().replace(['-', '_'], "");
    [
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "apikey",
        "accesskey",
        "authtoken",
        "token",
        "authorization",
        "cookie",
    ]
    .contains(&normalized.as_str())
        || [
            "apikey",
            "clientsecret",
            "secretaccesskey",
            "accesskeyid",
            "accesskey",
        ]
        .iter()
        .any(|suffix| normalized.ends_with(suffix))
        || key.rsplit(['_', '-']).next().is_some_and(|part| {
            ["password", "passwd", "secret", "token"]
                .iter()
                .any(|name| part.eq_ignore_ascii_case(name))
        })
}
const KEY: &str =
    r"password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token";
fn patterns() -> &'static Vec<Regex> {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        vec![
            Regex::new(&format!(r#"(?i)({KEY})(\s*[=:]\s*)"([^"\r\n]*)""#))
                .expect("constant regex"),
            Regex::new(&format!(r"(?i)({KEY})(\s*[=:]\s*)'([^'\r\n]*)'")).expect("constant regex"),
            Regex::new(&format!(r#"(?i)({KEY})(\s*[=:]\s*)([^\s,;"']+)"#)).expect("constant regex"),
            Regex::new(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+").expect("constant regex"),
            Regex::new(r"(?i)(--?(?:password|passwd|secret|token|api[_-]?key)(?:=|\s+))([^\s]+)")
                .expect("constant regex"),
            Regex::new(
                r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b",
            )
            .expect("constant regex"),
            Regex::new(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@").expect("constant regex"),
        ]
    })
}
pub fn redact_text(text: &str) -> String {
    let patterns = patterns();
    let mut result = patterns[0]
        .replace_all(text, |caps: &Captures<'_>| {
            format!("{}{}\"{REDACTED}\"", &caps[1], &caps[2])
        })
        .into_owned();
    result = patterns[1]
        .replace_all(&result, |caps: &Captures<'_>| {
            format!("{}{}'{REDACTED}'", &caps[1], &caps[2])
        })
        .into_owned();
    result = patterns[2]
        .replace_all(&result, |caps: &Captures<'_>| {
            format!("{}{}{REDACTED}", &caps[1], &caps[2])
        })
        .into_owned();
    result = redact_private_keys(&result);
    for pattern in &patterns[3..5] {
        result = pattern
            .replace_all(&result, |caps: &Captures<'_>| {
                format!("{}{REDACTED}", &caps[1])
            })
            .into_owned();
    }
    result = patterns[5].replace_all(&result, REDACTED).into_owned();
    patterns[6]
        .replace_all(&result, |caps: &Captures<'_>| {
            format!("{}{REDACTED}@", &caps[1])
        })
        .into_owned()
}
fn redact_private_keys(text: &str) -> String {
    static BEGIN: OnceLock<Regex> = OnceLock::new();
    let begin = BEGIN.get_or_init(|| {
        Regex::new(r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----").expect("constant regex")
    });
    let mut remaining = text;
    let mut output = String::new();
    while let Some(captures) = begin.captures(remaining) {
        let matched = captures.get(0).expect("match exists");
        output.push_str(&remaining[..matched.start()]);
        output.push_str("-----BEGIN [REDACTED] PRIVATE KEY-----");
        let end = format!("-----END {}-----", &captures[1]);
        remaining = match remaining[matched.end()..].find(&end) {
            Some(index) => &remaining[matched.end() + index + end.len()..],
            None => "",
        };
    }
    output.push_str(remaining);
    output
}
pub fn redact_value(value: &Value) -> Value {
    match value {
        Value::String(text) => Value::String(redact_text(text)),
        Value::Array(items) => Value::Array(items.iter().map(redact_value).collect()),
        Value::Object(items) => {
            let mut keys = BTreeSet::new();
            let mut safe = serde_json::Map::new();
            for (key, item) in items {
                let base = redact_text(key);
                let mut key = base.clone();
                let mut suffix = 2;
                while !keys.insert(key.clone()) {
                    key = format!("{base}#{suffix}");
                    suffix += 1;
                }
                safe.insert(
                    key,
                    if sensitive_key(&base) {
                        Value::String(REDACTED.into())
                    } else {
                        redact_value(item)
                    },
                );
            }
            Value::Object(safe)
        }
        _ => value.clone(),
    }
}
