//! Credential masking at log/report boundaries, never on provider parse bytes.
use regex::{Captures, Regex};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{OnceLock, RwLock},
};

pub const REDACTED: &str = "***REDACTED***";
/// A sensitive variable's value shorter than this is not hidden: masking it everywhere it
/// appears would rewrite ordinary words and numbers in reports.
pub const MIN_SENSITIVE_VALUE_CHARS: usize = 6;
fn sensitive_values() -> &'static RwLock<BTreeSet<String>> {
    static VALUES: OnceLock<RwLock<BTreeSet<String>>> = OnceLock::new();
    VALUES.get_or_init(RwLock::default)
}
/// Remember the values of the caller's sensitive variables (`sensitive_key` names) so every
/// boundary that masks text also masks them where a tool printed one without its name: a tool
/// gets the whole environment, and its output may echo it. Never applied to a tool's input.
pub fn remember_sensitive_environment(environment: &BTreeMap<String, String>) {
    let values: Vec<_> = environment
        .iter()
        .filter(|(key, value)| {
            sensitive_key(key) && value.chars().count() >= MIN_SENSITIVE_VALUE_CHARS
        })
        .map(|(_, value)| value.clone())
        .collect();
    if !values.is_empty()
        && let Ok(mut known) = sensitive_values().write()
    {
        known.extend(values);
    }
}
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
            // A URL's userinfo: everything up to the authority's last `@`.
            Regex::new(r#"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s?#'"]+)@"#).expect("constant regex"),
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
    result = redact_sensitive_values(&result);
    patterns[6]
        .replace_all(&result, |caps: &Captures<'_>| {
            // `user:secret` is a credential under any scheme. A lone value is a token over
            // http(s) and a login name elsewhere (`ssh://git@host`), which stays readable.
            let scheme = caps[1].to_ascii_lowercase();
            let web = ["http://", "https://"].iter().any(|s| scheme.ends_with(s));
            if web || caps[2].contains(':') {
                format!("{}{REDACTED}@", &caps[1])
            } else {
                caps[0].to_string()
            }
        })
        .into_owned()
}
/// The remembered sensitive values, longest first so a value inside another stays covered.
fn redact_sensitive_values(text: &str) -> String {
    let Ok(known) = sensitive_values().read() else {
        return text.to_owned();
    };
    if known.is_empty() {
        return text.to_owned();
    }
    let mut values: Vec<&String> = known.iter().collect();
    values.sort_by_key(|value| std::cmp::Reverse(value.len()));
    let mut result = text.to_owned();
    for value in values {
        if result.contains(value.as_str()) {
            result = result.replace(value.as_str(), REDACTED);
        }
    }
    result
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
