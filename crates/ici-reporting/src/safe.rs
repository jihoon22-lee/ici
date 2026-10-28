use serde_json::Value;
pub(crate) fn redact(value: &Value) -> Value {
    use std::collections::{BTreeMap, BTreeSet};
    fn text(value: &str, cache: &mut BTreeMap<String, String>) -> String {
        if let Some(cached) = cache.get(value) {
            return cached.clone();
        }
        let safe = ici_execution::redaction::redact_text(value);
        if cache.len() < 4096 && value.len() <= 512 {
            cache.insert(value.into(), safe.clone());
        }
        safe
    }
    fn walk(value: &Value, cache: &mut BTreeMap<String, String>) -> Value {
        match value {
            Value::Object(items) => {
                let mut safe = serde_json::Map::new();
                let mut keys = BTreeSet::new();
                for (key, item) in items {
                    let normalized = key.to_ascii_lowercase().replace(['-', '_'], "");
                    let item = if [
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
                    {
                        Value::String(ici_execution::redaction::REDACTED.into())
                    } else {
                        walk(item, cache)
                    };
                    let base = text(key, cache);
                    let mut key = base.clone();
                    let mut suffix = 2;
                    while !keys.insert(key.clone()) {
                        key = format!("{base}#{suffix}");
                        suffix += 1;
                    }
                    safe.insert(key, item);
                }
                Value::Object(safe)
            }
            Value::Array(items) => {
                Value::Array(items.iter().map(|item| walk(item, cache)).collect())
            }
            Value::String(value) => Value::String(text(value, cache)),
            _ => value.clone(),
        }
    }
    walk(value, &mut BTreeMap::new())
}
pub(crate) fn html(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}
pub(crate) fn plain(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "—".into(),
        _ => value.to_string(),
    }
}
pub(crate) fn terminal(text: &str) -> String {
    text.chars()
        .filter(|ch| *ch == '\n' || *ch == '\t' || !ch.is_control())
        .collect()
}
