use crate::{ReportError, Result};
/// One URI decode and a strict project-relative path. No filesystem or URL fetching.
pub(crate) fn external_path(raw: &str) -> Result<String> {
    if raw.is_empty() {
        return Err(ReportError("External source path must not be empty".into()));
    }
    if raw.contains(['\0', '\r', '\n', '?', '#'])
        || raw.split('/').next().is_some_and(|part| part.contains(':'))
    {
        return Err(ReportError(
            "External source location must be a project-relative file URI".into(),
        ));
    }
    let bytes = raw.as_bytes();
    for index in 0..bytes.len() {
        if bytes[index] == b'%'
            && !(bytes.get(index + 1).is_some_and(u8::is_ascii_hexdigit)
                && bytes.get(index + 2).is_some_and(u8::is_ascii_hexdigit))
        {
            return Err(ReportError(
                "External source URI has an invalid percent escape".into(),
            ));
        }
    }
    let decoded = percent_encoding::percent_decode_str(raw)
        .decode_utf8()
        .map_err(|_| ReportError("External source URI is not UTF-8".into()))?;
    if decoded.starts_with('/')
        || decoded.contains(['\\', '\0', '\r', '\n'])
        || decoded.split('/').any(|part| part == "..")
        || decoded
            .split('/')
            .next()
            .is_some_and(|part| part.contains(':'))
    {
        return Err(ReportError(
            "External source location leaves project-relative scope".into(),
        ));
    }
    let parts: Vec<_> = decoded
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    if parts.is_empty() {
        return Err(ReportError(
            "External source location does not name a file".into(),
        ));
    }
    Ok(parts.join("/"))
}
pub(crate) fn mapped_path(
    uri: &str,
    base: Option<&str>,
    mappings: &std::collections::BTreeMap<String, String>,
) -> Result<String> {
    let relative = external_path(uri)?;
    if let Some(base) = base {
        let prefix = mappings.get(base).ok_or_else(|| {
            ReportError(format!(
                "SARIF uriBaseId {base:?} requires an explicit project-relative mapping"
            ))
        })?;
        if prefix == "." || prefix.is_empty() {
            return Ok(relative);
        }
        return external_path(&format!("{}/{relative}", prefix.trim_end_matches('/')));
    }
    Ok(relative)
}
pub(crate) fn positive(value: &serde_json::Value, label: &str) -> Result<u32> {
    value
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .filter(|value| *value > 0)
        .ok_or_else(|| ReportError(format!("{label} must be a positive bounded integer")))
}
