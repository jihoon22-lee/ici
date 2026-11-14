//! Credential masking at log/report boundaries, never on provider parse bytes.
use regex::{Captures, Regex};
use serde_json::Value;
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    marker::PhantomData,
    sync::{Arc, OnceLock},
};

pub const REDACTED: &str = "***REDACTED***";
/// A remembered value this long or longer is masked wherever it appears. A shorter one is masked
/// only where it stands as its own token (the whole text, `private=<value>`, a quoted word): an
/// ordinary word that merely contains its letters is left as it is.
pub const LITERAL_VALUE_CHARS: usize = 6;

/// The sensitive values one command remembers (`sensitive_key` names of its environment).
#[derive(Clone, Debug, Default)]
pub struct RedactionContext {
    values: Arc<Vec<String>>,
}
impl RedactionContext {
    /// Values of the caller's sensitive variables, longest first so a value inside another
    /// stays covered. Empty and all-space values hide nothing and are skipped.
    pub fn from_environment(environment: &BTreeMap<String, String>) -> Self {
        let mut values: Vec<String> = environment
            .iter()
            .filter(|(key, value)| sensitive_key(key) && !value.trim().is_empty())
            .map(|(_, value)| value.clone())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        values.sort_by_key(|value| std::cmp::Reverse(value.len()));
        Self {
            values: Arc::new(values),
        }
    }
    /// Make this context active on the current thread until the guard drops. A worker thread
    /// enters the context of the command that started it.
    pub fn enter(&self) -> RedactionScope {
        ACTIVE.with(|active| {
            let mut active = active.borrow_mut();
            active.push(self.clone());
            RedactionScope {
                depth: active.len() - 1,
                _thread: PhantomData,
            }
        })
    }
}
thread_local! {
    static ACTIVE: RefCell<Vec<RedactionContext>> = const { RefCell::new(Vec::new()) };
}
/// While alive, text redacted on this thread also masks its context's values. Dropping it (also
/// on an early error or unwind) forgets them, with any context entered after it.
#[must_use = "the context is active only while the guard lives"]
pub struct RedactionScope {
    depth: usize,
    _thread: PhantomData<*const ()>,
}
impl Drop for RedactionScope {
    fn drop(&mut self) {
        let _ = ACTIVE.try_with(|active| active.borrow_mut().truncate(self.depth));
    }
}
/// One command's context: the values of its environment's sensitive variables, so every
/// boundary that masks text also masks them where a tool printed one without its name (a tool
/// gets the whole environment and may echo it). Never applied to a tool's input or parser bytes.
/// Contexts are per thread: a command running at the same time on another thread is unaffected,
/// and a command nested in another masks the outer command's values too.
pub fn command_scope(environment: &BTreeMap<String, String>) -> RedactionScope {
    RedactionContext::from_environment(environment).enter()
}
/// The contexts active on this thread, merged, for a worker thread to enter.
pub fn current() -> RedactionContext {
    ACTIVE.with(|active| {
        let active = active.borrow();
        match active.as_slice() {
            [] => RedactionContext::default(),
            [one] => one.clone(),
            many => {
                let mut values: Vec<String> = many
                    .iter()
                    .flat_map(|context| context.values.iter().cloned())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect();
                values.sort_by_key(|value| std::cmp::Reverse(value.len()));
                RedactionContext {
                    values: Arc::new(values),
                }
            }
        }
    })
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
/// The active context's values. Text already masked stays as it is, so redacting twice equals
/// redacting once, even when a value contains the marker or part of it.
fn redact_sensitive_values(text: &str) -> String {
    let context = current();
    let mut result = text.to_owned();
    for value in context.values.iter() {
        if result.contains(value.as_str()) {
            result = outside_markers(&result, |part| mask_value(part, value));
        }
    }
    result
}
fn outside_markers(text: &str, mask: impl Fn(&str) -> String) -> String {
    let mut output = String::with_capacity(text.len());
    for (index, part) in text.split(REDACTED).enumerate() {
        if index > 0 {
            output.push_str(REDACTED);
        }
        output.push_str(&mask(part));
    }
    output
}
fn word(character: Option<char>) -> bool {
    character.is_some_and(|c| c.is_alphanumeric() || c == '_')
}
/// A long value everywhere; a short one only where no word character joins it on a side where
/// its own edge is a word character.
fn mask_value(text: &str, value: &str) -> String {
    if value.chars().count() >= LITERAL_VALUE_CHARS {
        return text.replace(value, REDACTED);
    }
    let first = word(value.chars().next());
    let last = word(value.chars().next_back());
    let mut output = String::with_capacity(text.len());
    let mut rest = 0;
    let mut from = 0;
    while let Some(found) = text[from..].find(value) {
        let start = from + found;
        let end = start + value.len();
        let before = text[..start].chars().next_back();
        let after = text[end..].chars().next();
        if (!first || !word(before)) && (!last || !word(after)) {
            output.push_str(&text[rest..start]);
            output.push_str(REDACTED);
            rest = end;
            from = end;
        } else {
            from = start + value.chars().next().map_or(1, char::len_utf8);
        }
    }
    output.push_str(&text[rest..]);
    output
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
