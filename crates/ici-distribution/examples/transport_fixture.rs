//! Actual private CA/proxy fixture driver; never part of an installed payload.
use ici_distribution::transport::{self, Request, TransportError};
use std::{collections::BTreeMap, process::ExitCode, time::Duration};
fn main() -> ExitCode {
    let Some(url) = std::env::args().nth(1) else {
        return ExitCode::from(2);
    };
    match transport::send(&Request {
        method: "GET".into(),
        url,
        headers: BTreeMap::new(),
        body: vec![],
        max_bytes: 4096,
        timeout: Duration::from_secs(3),
    }) {
        Ok(response) => {
            let passed = (200..300).contains(&response.status);
            println!(
                "{}",
                serde_json::json!({"publication":if passed {"SUCCEEDED"}else{"FAILED"},"http_status":response.status,"body":String::from_utf8_lossy(&response.bytes)})
            );
            if passed {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(2)
            }
        }
        Err(error) => {
            let reason = match error {
                TransportError::Timeout => "timeout",
                TransportError::ResponseTooLarge => "invalid_or_oversize_body",
                _ => "transport",
            };
            println!(
                "{}",
                serde_json::json!({"publication":"FAILED","reason":reason})
            );
            ExitCode::from(2)
        }
    }
}
