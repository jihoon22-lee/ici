//! R0 experiment only: system roots, bounded response, no redirects or credentials.
use std::{env, io::Read, process::ExitCode, time::Duration};

fn main() -> ExitCode {
    let Some(url) = env::args().nth(1) else {
        eprintln!("usage: ici-transport-spike HTTPS_URL");
        return ExitCode::from(2);
    };
    let client = reqwest::blocking::Client::builder()
        .use_rustls_tls()
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(3))
        .user_agent("ici-rust-transport-r0")
        .build();
    let result = client.and_then(|client| client.get(&url).send());
    match result {
        Ok(response) => {
            let status = response.status().as_u16();
            let mut body = String::new();
            if response.take(4097).read_to_string(&mut body).is_err() || body.len() > 4096 {
                println!(
                    "{}",
                    serde_json::json!({"publication":"FAILED", "reason":"invalid_or_oversize_body"})
                );
                return ExitCode::from(2);
            }
            let passed = (200..300).contains(&status);
            println!(
                "{}",
                serde_json::json!({"publication": if passed { "SUCCEEDED" } else { "FAILED" }, "http_status": status, "body": body})
            );
            if passed {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(2)
            }
        }
        Err(error) => {
            // Error Display contains URLs; credentials and server bodies must not leak.
            let reason = if error.is_timeout() {
                "timeout"
            } else {
                "transport"
            };
            println!(
                "{}",
                serde_json::json!({"publication":"FAILED", "reason":reason})
            );
            ExitCode::from(2)
        }
    }
}
