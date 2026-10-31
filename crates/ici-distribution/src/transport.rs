//! System/private CA and proxy semantics are provided by native-roots reqwest.
use std::{collections::BTreeMap, fmt, io::Read, time::Duration};

const MAX_BYTES: usize = 512 * 1024 * 1024;
/// Deliberately has no Debug implementation: URLs, headers and bodies may hold credentials.
pub struct Request {
    pub method: String,
    pub url: String,
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
    pub max_bytes: usize,
    pub timeout: Duration,
}
/// Non-success HTTP statuses remain response data; the caller owns publication policy.
pub struct Response {
    pub status: u16,
    /// Lowercase names; repeated header values are separated by a newline.
    pub headers: BTreeMap<String, String>,
    pub bytes: Vec<u8>,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransportError {
    InvalidRequest,
    Timeout,
    Transport,
    ResponseTooLarge,
}
impl fmt::Display for TransportError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidRequest => "invalid HTTPS transport request",
            Self::Timeout => "HTTPS request timed out",
            Self::Transport => "HTTPS transport failed",
            Self::ResponseTooLarge => "HTTPS response exceeds the configured byte bound",
        })
    }
}
impl std::error::Error for TransportError {}
fn classify(error: reqwest::Error) -> TransportError {
    if error.is_timeout() {
        TransportError::Timeout
    } else {
        TransportError::Transport
    }
}
/// One bounded request, with no redirects or retries and no URL/body/credential error output.
pub fn send(request: &Request) -> Result<Response, TransportError> {
    if request.url.len() > 8192
        || request.body.len() > MAX_BYTES
        || request.max_bytes == 0
        || request.max_bytes > MAX_BYTES
        || request.timeout.is_zero()
        || request.timeout > Duration::from_secs(300)
        || request.headers.len() > 128
        || request
            .headers
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > 65_536
    {
        return Err(TransportError::InvalidRequest);
    }
    let url = reqwest::Url::parse(&request.url).map_err(|_| TransportError::InvalidRequest)?;
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.host_str().is_none()
    {
        return Err(TransportError::InvalidRequest);
    }
    let method = reqwest::Method::from_bytes(request.method.as_bytes())
        .map_err(|_| TransportError::InvalidRequest)?;
    let client = reqwest::blocking::Client::builder()
        .use_rustls_tls()
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(request.timeout.min(Duration::from_secs(10)))
        .timeout(request.timeout)
        .user_agent(concat!("ici/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(classify)?;
    let mut builder = client.request(method, url);
    for (name, value) in &request.headers {
        let name = reqwest::header::HeaderName::from_bytes(name.as_bytes())
            .map_err(|_| TransportError::InvalidRequest)?;
        let mut value = reqwest::header::HeaderValue::from_str(value)
            .map_err(|_| TransportError::InvalidRequest)?;
        value.set_sensitive(true);
        builder = builder.header(name, value);
    }
    if !request.body.is_empty() {
        builder = builder.body(request.body.clone());
    }
    let mut response = builder.send().map_err(classify)?;
    if response
        .content_length()
        .is_some_and(|size| size > request.max_bytes as u64)
    {
        return Err(TransportError::ResponseTooLarge);
    }
    let status = response.status().as_u16();
    let mut headers: BTreeMap<String, String> = BTreeMap::new();
    for (name, value) in response.headers() {
        let value: String = value
            .as_bytes()
            .iter()
            .map(|byte| char::from(*byte))
            .collect();
        headers
            .entry(name.as_str().to_owned())
            .and_modify(|existing| {
                existing.push('\n');
                existing.push_str(&value)
            })
            .or_insert(value);
    }
    let mut bytes = vec![];
    let mut buffer = [0; 65_536];
    loop {
        let count = response.read(&mut buffer).map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut
                || error
                    .get_ref()
                    .and_then(|error| error.downcast_ref::<reqwest::Error>())
                    .is_some_and(reqwest::Error::is_timeout)
            {
                TransportError::Timeout
            } else {
                TransportError::Transport
            }
        })?;
        if count == 0 {
            break;
        }
        if bytes.len().saturating_add(count) > request.max_bytes {
            return Err(TransportError::ResponseTooLarge);
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
    Ok(Response {
        status,
        headers,
        bytes,
    })
}
