//! One bounded JSON intake for stored results and imported supporting evidence.
use crate::{ReportError, Result};
use serde::{
    Deserialize, Deserializer,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::Value;
use std::{collections::BTreeSet, fmt};
pub const MAX_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_RECORDS: usize = 100_000;
const MAX_NODES: usize = 1_000_000;
#[derive(Debug)]
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct Strict;
        impl<'de> Visitor<'de> for Strict {
            type Value = Unique;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("JSON without duplicate object keys")
            }
            fn visit_bool<E: de::Error>(self, value: bool) -> std::result::Result<Unique, E> {
                Ok(Unique(value.into()))
            }
            fn visit_i64<E: de::Error>(self, value: i64) -> std::result::Result<Unique, E> {
                Ok(Unique(value.into()))
            }
            fn visit_u64<E: de::Error>(self, value: u64) -> std::result::Result<Unique, E> {
                Ok(Unique(value.into()))
            }
            fn visit_f64<E: de::Error>(self, value: f64) -> std::result::Result<Unique, E> {
                serde_json::Number::from_f64(value)
                    .map(|value| Unique(Value::Number(value)))
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }
            fn visit_str<E: de::Error>(self, value: &str) -> std::result::Result<Unique, E> {
                Ok(Unique(value.into()))
            }
            fn visit_string<E: de::Error>(self, value: String) -> std::result::Result<Unique, E> {
                Ok(Unique(value.into()))
            }
            fn visit_none<E: de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut sequence: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut values = vec![];
                while let Some(Unique(value)) = sequence.next_element()? {
                    if values.len() >= MAX_NODES {
                        return Err(de::Error::custom("JSON array exceeds bounded item count"));
                    }
                    values.push(value);
                }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut entries: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut keys = BTreeSet::new();
                let mut values = serde_json::Map::new();
                while let Some(key) = entries.next_key::<String>()? {
                    if values.len() >= MAX_RECORDS {
                        return Err(de::Error::custom("JSON object exceeds bounded key count"));
                    }
                    if !keys.insert(key.clone()) {
                        return Err(de::Error::custom(format!("duplicate JSON key {key:?}")));
                    }
                    let Unique(value) = entries.next_value()?;
                    values.insert(key, value);
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(Strict)
    }
}
pub(crate) fn validate_shape(value: &Value) -> Result<()> {
    let mut nodes = 0usize;
    let mut pending = vec![(value, 0u32)];
    while let Some((value, depth)) = pending.pop() {
        nodes += 1;
        if nodes > MAX_NODES || depth > 128 {
            return Err(ReportError(
                "Report exceeds one million JSON nodes or 128 nesting levels".into(),
            ));
        }
        match value {
            Value::Array(items) => pending.extend(items.iter().map(|item| (item, depth + 1))),
            Value::Object(items) => pending.extend(items.values().map(|item| (item, depth + 1))),
            _ => {}
        }
    }
    Ok(())
}
pub fn strict_json(bytes: &[u8]) -> Result<Value> {
    if bytes.len() > MAX_BYTES {
        return Err(ReportError("Report exceeds 32 MiB input bound".into()));
    }
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let Unique(value) = Unique::deserialize(&mut deserializer)?;
    deserializer.end()?;
    validate_shape(&value)?;
    Ok(value)
}
pub fn canonical_json(value: &Value) -> Result<String> {
    validate_shape(value)?;
    Ok(ici_domain::canonical_json(value)?)
}
/// HTML raw-text elements need JSON escapes even when the document has no executable script.
pub fn script_json(value: &Value) -> Result<String> {
    Ok(canonical_json(value)?
        .replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029"))
}
