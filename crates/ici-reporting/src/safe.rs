use serde_json::Value;
pub(crate) fn redact(value: &Value) -> Value {
    let value = ici_execution::redaction::redact_value(value);
    fn keys(value: &mut Value) {
        match value {
            Value::Object(items) => {
                for (key, item) in items {
                    let key = key.to_ascii_lowercase().replace(['-', '_'], "");
                    if [
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
                    .contains(&key.as_str())
                    {
                        *item = Value::String(ici_execution::redaction::REDACTED.into());
                    } else {
                        keys(item);
                    }
                }
            }
            Value::Array(items) => items.iter_mut().for_each(keys),
            _ => {}
        }
    }
    let mut value = value;
    keys(&mut value);
    value
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
