//! Development driver prints only typed errors and response digests, never request secrets.
use ici_distribution::{
    self as distribution,
    transport::{self, Request},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Read, time::Duration};
fn request(value: &Value) -> Option<Request> {
    let headers = value
        .get("headers")
        .and_then(Value::as_object)
        .map(|headers| {
            headers
                .iter()
                .map(|(name, value)| Some((name.clone(), value.as_str()?.to_owned())))
                .collect::<Option<BTreeMap<_, _>>>()
        })
        .unwrap_or_else(|| Some(BTreeMap::new()))?;
    Some(Request {
        method: value
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or("GET")
            .into(),
        url: value["url"].as_str()?.into(),
        headers,
        body: value
            .get("body")
            .and_then(Value::as_str)
            .unwrap_or("")
            .as_bytes()
            .to_vec(),
        max_bytes: value
            .get("max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(4096) as usize,
        timeout: Duration::from_millis(
            value
                .get("timeout_ms")
                .and_then(Value::as_u64)
                .unwrap_or(3000),
        ),
    })
}
fn run() -> Value {
    let mut bytes = vec![];
    if std::io::stdin()
        .take(131_073)
        .read_to_end(&mut bytes)
        .is_err()
        || bytes.len() > 131_072
    {
        return json!({"ok":false,"reason":"fixture_input"});
    }
    let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
        return json!({"ok":false,"reason":"fixture_input"});
    };
    let Some(request) = request(&value) else {
        return json!({"ok":false,"reason":"fixture_input"});
    };
    if value["mode"] == "download" {
        return match distribution::download_verified(
            request,
            value["sha256"].as_str().unwrap_or(""),
        ) {
            Ok(bytes) => {
                json!({"ok":true,"body_bytes":bytes.len(),"body_sha256":format!("{:x}",Sha256::digest(bytes))})
            }
            Err(error) => json!({"ok":false,"reason":error.code}),
        };
    }
    match transport::send(&request) {
        Ok(response) => {
            json!({"ok":true,"status":response.status,"body_bytes":response.bytes.len(),"body_sha256":format!("{:x}",Sha256::digest(&response.bytes)),"fixture_header_sha256":response.headers.get("x-fixture").map(|value|format!("{:x}",Sha256::digest(value.as_bytes())))})
        }
        Err(error) => json!({"ok":false,"reason":format!("{error:?}")}),
    }
}
fn main() {
    println!("{}", run());
}
