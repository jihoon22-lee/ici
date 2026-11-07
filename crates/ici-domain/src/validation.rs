use thiserror::Error;

#[derive(Debug, Error)]
#[error("{0}")]
pub struct DomainError(pub String);
pub type Result<T> = std::result::Result<T, DomainError>;
pub trait Validate {
    fn validate(&self) -> Result<()>;
}
pub fn require(condition: bool, message: impl Into<String>) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(DomainError(message.into()))
    }
}
pub fn text(value: &str, name: &str) -> Result<()> {
    require(
        !value.is_empty() && value == value.trim(),
        format!("{name} must not be empty"),
    )
}
pub fn digest(value: &str, name: &str) -> Result<()> {
    require(
        value.strip_prefix("sha256:").is_some_and(|hex| {
            hex.len() == 64
                && hex
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        }),
        format!("{name} must be a SHA-256 digest"),
    )
}
pub fn relative_path(value: &str, allow_dot: bool) -> Result<()> {
    require(
        !value.is_empty()
            && !value.starts_with('/')
            && !value.contains('\\')
            && !value.contains('\0')
            && !value.contains(':')
            && (allow_dot && value == "."
                || value
                    .split('/')
                    .all(|p| !p.is_empty() && p != "." && p != "..")),
        format!("path must be project-relative: {value:?}"),
    )
}
pub fn unique(values: &[String], name: &str) -> Result<()> {
    let mut seen = std::collections::BTreeSet::new();
    for value in values {
        text(value, name)?;
        require(seen.insert(value), format!("duplicate {name}: {value}"))?;
    }
    Ok(())
}

pub fn identifier(value: &str, name: &str) -> Result<()> {
    let alnum = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
    require(
        value.as_bytes().first().is_some_and(|b| alnum(*b))
            && value.as_bytes().last().is_some_and(|b| alnum(*b))
            && value.bytes().all(|b| alnum(b) || b"._-".contains(&b)),
        format!("{name} must be lowercase alphanumeric with . _ - separators"),
    )
}
