//! Explicit setup/download only. The ordinary publisher transport never redirects.
use crate::{
    model::{Error, digest, error, valid_digest},
    transport::{self, Request},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Duration,
};

/// Follow at most five HTTPS redirects, dropping all caller headers on origin
/// changes. The payload must match an independently trusted immutable digest.
pub fn download_verified(mut request: Request, expected_sha256: &str) -> Result<Vec<u8>, Error> {
    if request.method != "GET" || !request.body.is_empty() || !valid_digest(expected_sha256) {
        return Err(error(
            "download-request",
            "Verified setup download requires GET and a trusted SHA-256",
        ));
    }
    let mut visited = BTreeSet::new();
    let started = std::time::Instant::now();
    let timeout = request.timeout;
    for hop in 0..=5 {
        if !visited.insert(request.url.clone()) {
            return Err(error(
                "download-redirect",
                "Setup download contains a redirect loop",
            ));
        }
        let remaining = timeout
            .checked_sub(started.elapsed())
            .filter(|duration| !duration.is_zero())
            .ok_or_else(|| error("download-timeout", "Verified setup download timed out"))?;
        request.timeout = remaining;
        let response = transport::send(&request)
            .map_err(|error| error.to_string())
            .map_err(|message| error("download-transport", message))?;
        if matches!(response.status, 301 | 302 | 303 | 307 | 308) {
            if hop == 5 {
                return Err(error(
                    "download-redirect",
                    "Setup download exceeded five redirect hops",
                ));
            }
            let location = response
                .headers
                .get("location")
                .filter(|value| !value.contains('\n'))
                .ok_or_else(|| {
                    error(
                        "download-redirect",
                        "Setup redirect has no single Location value",
                    )
                })?;
            let current = reqwest::Url::parse(&request.url)
                .map_err(|_| error("download-request", "Invalid setup URL"))?;
            let next = current
                .join(location)
                .map_err(|_| error("download-redirect", "Invalid setup redirect URL"))?;
            if next.scheme() != "https" || !next.username().is_empty() || next.password().is_some()
            {
                return Err(error(
                    "download-redirect",
                    "Setup redirects must remain credential-free HTTPS URLs",
                ));
            }
            if current.origin() != next.origin() {
                request.headers.clear();
            }
            request.url = next.to_string();
            continue;
        }
        if response.status != 200 {
            return Err(error(
                "download-status",
                format!("Setup download returned HTTP {}", response.status),
            ));
        }
        if digest(&response.bytes) != expected_sha256 {
            return Err(error(
                "download-digest",
                "Downloaded bytes do not match the trusted expected SHA-256",
            ));
        }
        return Ok(response.bytes);
    }
    Err(error("download-redirect", "Setup download did not finish"))
}
pub fn download(
    url: &str,
    expected_sha256: &str,
    max_bytes: usize,
    timeout: Duration,
) -> Result<Vec<u8>, Error> {
    download_verified(
        Request {
            method: "GET".into(),
            url: url.into(),
            headers: BTreeMap::new(),
            body: vec![],
            max_bytes,
            timeout,
        },
        expected_sha256,
    )
}
