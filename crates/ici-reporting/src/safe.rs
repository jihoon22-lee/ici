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
                    let item = if ici_execution::redaction::sensitive_key(key) {
                        Value::String(ici_execution::redaction::REDACTED.into())
                    } else if key.ends_with("argv") && is_argv(item) {
                        argv_value(item)
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
const SECRET_FLAGS: [&str; 6] = [
    "--api-key",
    "--api_key",
    "--password",
    "--passwd",
    "--secret",
    "--token",
];
fn is_argv(value: &Value) -> bool {
    value
        .as_array()
        .is_some_and(|items| items.iter().all(Value::is_string))
}
fn argv_value(value: &Value) -> Value {
    let argv: Vec<String> = value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(String::from)
                .collect()
        })
        .unwrap_or_default();
    Value::from(redact_argv(&argv))
}
/// Frozen `_redact_argv`: mask each item and hide the value after a secret flag.
pub(crate) fn redact_argv(argv: &[String]) -> Vec<String> {
    let mut hide_next = false;
    argv.iter()
        .map(|value| {
            let safe = if hide_next {
                ici_execution::redaction::REDACTED.to_string()
            } else {
                ici_execution::redaction::redact_text(value)
            };
            hide_next = !hide_next && SECRET_FLAGS.contains(&value.to_lowercase().as_str());
            safe
        })
        .collect()
}
/// Frozen `redact_data`: recursive string masking that keeps every key's value shape.
pub(crate) fn redact_plain(value: &Value) -> Value {
    match value {
        Value::String(text) => Value::String(ici_execution::redaction::redact_text(text)),
        Value::Array(items) => Value::Array(items.iter().map(redact_plain).collect()),
        Value::Object(items) => {
            let mut safe = serde_json::Map::new();
            for (key, item) in items {
                let base = ici_execution::redaction::redact_text(key);
                let mut key = base.clone();
                let mut suffix = 2;
                while safe.contains_key(&key) {
                    key = format!("{base}#{suffix}");
                    suffix += 1;
                }
                safe.insert(key, redact_plain(item));
            }
            Value::Object(safe)
        }
        _ => value.clone(),
    }
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

#[cfg(test)]
mod frozen_redaction_tests {
    use serde_json::Value;

    /// tests/rust-parity/reporting/capture_finding_redaction.py: frozen `_redact_finding`
    /// (core/redaction.py:360) and `_redact_delta` (core/redaction.py:447) over credentials in
    /// every string field, metric names that collide once masked, fixes and locations.
    fn frozen() -> Value {
        let golden: Value = serde_json::from_str(include_str!(
            "../../../tests/rust-parity/reporting/finding-redaction.json"
        ))
        .unwrap();
        assert_eq!(golden["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
        golden
    }

    #[test]
    fn finding_fields_metric_names_and_fixes_redact_like_the_frozen_finding_redactor() {
        let golden = frozen();
        assert_eq!(
            super::redact(&golden["finding"]["input"]),
            golden["finding"]["redacted"]
        );
    }

    #[test]
    fn baseline_delta_rule_message_engine_and_locations_redact_like_the_frozen_delta_redactor() {
        let golden = frozen();
        assert_eq!(
            super::redact(&golden["delta"]["input"]),
            golden["delta"]["redacted"]
        );
    }

    #[test]
    fn secret_free_values_pass_through_redaction_unchanged() {
        let golden = frozen();
        for record in ["finding", "delta"] {
            let redacted = super::redact(&golden[record]["redacted"]);
            assert_eq!(redacted, golden[record]["redacted"], "{record}");
        }
    }
}
