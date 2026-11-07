//! Navigation uses explicit caller context, never filesystem discovery or source reads.
use crate::{RenderOptions, ReportError, Result};
use serde_json::{Value, json};
const SEGMENT: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');
pub(crate) fn relative(path: &str) -> bool {
    !path.is_empty()
        && path != "[external]"
        && !path.starts_with('/')
        && !path.contains(['\\', ':'])
        && !path.chars().any(char::is_control)
        && path
            .split('/')
            .all(|part| !part.is_empty() && !matches!(part, "." | ".."))
}
fn repository(value: &str) -> Result<String> {
    let rest = value
        .strip_prefix("https://")
        .or_else(|| value.strip_prefix("http://"))
        .ok_or_else(|| {
            ReportError("Source repository links require an explicit HTTP(S) repository URL".into())
        })?;
    let authority = rest.split('/').next().unwrap_or("");
    if authority.is_empty()
        || authority.contains('@')
        || !authority
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | ':' | '[' | ']'))
        || value.contains(['?', '#', '\\'])
        || value
            .chars()
            .any(|ch| ch.is_control() || ch.is_whitespace())
        || rest.split('/').any(|part| matches!(part, "." | ".."))
    {
        return Err(ReportError("Source repository URL must be an HTTP(S) origin/path without credentials, query or fragment".into()));
    }
    let scheme = if value.starts_with("https://") {
        "https://"
    } else {
        "http://"
    };
    let tail = rest
        .strip_prefix(authority)
        .unwrap_or("")
        .trim_start_matches('/')
        .trim_end_matches('/')
        .trim_end_matches(".git");
    let mut encoded = vec![];
    for part in tail.split('/').filter(|part| !part.is_empty()) {
        for (index, byte) in part.as_bytes().iter().enumerate() {
            if *byte == b'%'
                && !(part
                    .as_bytes()
                    .get(index + 1)
                    .is_some_and(u8::is_ascii_hexdigit)
                    && part
                        .as_bytes()
                        .get(index + 2)
                        .is_some_and(u8::is_ascii_hexdigit))
            {
                return Err(ReportError(
                    "Source repository URL has an invalid percent escape".into(),
                ));
            }
        }
        let part = percent_encoding::percent_decode_str(part)
            .decode_utf8()
            .map_err(|_| ReportError("Source repository URL path must be UTF-8".into()))?;
        if matches!(part.as_ref(), "." | "..") || part.chars().any(char::is_control) {
            return Err(ReportError(
                "Source repository URL contains an unsafe path component".into(),
            ));
        }
        encoded.push(percent_encoding::utf8_percent_encode(&part, SEGMENT).to_string());
    }
    Ok(format!(
        "{scheme}{authority}{}",
        if encoded.is_empty() {
            String::new()
        } else {
            format!("/{}", encoded.join("/"))
        }
    ))
}
pub(crate) fn config(options: &RenderOptions) -> Result<Option<Value>> {
    let root = if let Some(root) = &options.source_root {
        let bytes = root.as_bytes();
        let windows = bytes.first().is_some_and(u8::is_ascii_alphabetic)
            && bytes.get(1) == Some(&b':')
            && bytes
                .get(2)
                .is_some_and(|byte| matches!(byte, b'/' | b'\\'));
        if !(root.starts_with('/') || windows) || root.chars().any(char::is_control) {
            return Err(ReportError("Source navigation root must be an explicit absolute path without control characters".into()));
        }
        Some(root)
    } else {
        None
    };
    let repo = options
        .repository_url
        .as_deref()
        .map(repository)
        .transpose()?;
    if repo.is_some() != options.commit_ref.is_some() {
        return Err(ReportError(
            "Source repository URL and commit_ref must be supplied together".into(),
        ));
    }
    if options.commit_ref.as_deref().is_some_and(|value| {
        value.is_empty()
            || value
                .chars()
                .any(|ch| ch.is_control() || ch.is_whitespace())
    }) {
        return Err(ReportError(
            "Source commit_ref must be a nonempty revision without whitespace".into(),
        ));
    }
    if root.is_none() && repo.is_none() {
        return Ok(None);
    }
    Ok(Some(
        json!({"root":root,"repository_url":repo,"commit_ref":options.commit_ref}),
    ))
}
pub fn source_link(
    path: &str,
    start_line: u32,
    end_line: Option<u32>,
    options: &RenderOptions,
) -> Result<Option<String>> {
    let _ = config(options)?;
    if !relative(path) {
        return Ok(None);
    }
    let Some(repo) = options
        .repository_url
        .as_deref()
        .map(repository)
        .transpose()?
    else {
        return Ok(None);
    };
    if start_line == 0 || end_line.is_some_and(|end| end < start_line) {
        return Err(ReportError(
            "Source link requires an ordered 1-based line region".into(),
        ));
    }
    let revision = percent_encoding::utf8_percent_encode(
        options.commit_ref.as_deref().expect("validated revision"),
        SEGMENT,
    );
    let path = path
        .split('/')
        .map(|part| percent_encoding::utf8_percent_encode(part, SEGMENT).to_string())
        .collect::<Vec<_>>()
        .join("/");
    let end = end_line
        .filter(|end| *end != start_line)
        .map_or(String::new(), |end| format!("-L{end}"));
    Ok(Some(format!(
        "{repo}/blob/{revision}/{path}#L{start_line}{end}"
    )))
}
