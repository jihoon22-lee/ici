use crate::{ConfigError, Origin};
use std::{
    collections::BTreeMap,
    path::{Component, Path, PathBuf},
};

pub(crate) fn substitute(
    raw: &str,
    environment: &BTreeMap<String, String>,
    origin: &Origin,
) -> Result<String, ConfigError> {
    let mut result = String::new();
    let mut rest = raw;
    while let Some(start) = rest.find("${env:") {
        result.push_str(&rest[..start]);
        let suffix = &rest[start + 6..];
        let end = suffix.find('}').ok_or_else(|| {
            ConfigError::new("unclosed environment path reference", origin.clone())
        })?;
        let name = &suffix[..end];
        if !crate::schema::valid_env_name(name) {
            return Err(ConfigError::new(
                "invalid environment variable reference",
                origin.clone(),
            ));
        }
        let value = environment.get(name).ok_or_else(|| {
            ConfigError::new(
                format!("environment variable {name} is not set"),
                origin.clone(),
            )
        })?;
        result.push_str(value);
        rest = &suffix[end + 1..];
    }
    result.push_str(rest);
    if result.contains('\0') {
        return Err(ConfigError::new(
            "path must not contain NUL",
            origin.clone(),
        ));
    }
    Ok(result)
}

pub(crate) fn normalise(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for item in path.components() {
        match item {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            _ => result.push(item.as_os_str()),
        }
    }
    result
}

pub(crate) fn portable(path: &Path, origin: &Origin) -> Result<String, ConfigError> {
    let text = path
        .to_str()
        .ok_or_else(|| ConfigError::new("path must be valid UTF-8", origin.clone()))?;
    if text.contains('\\') {
        return Err(ConfigError::new(
            "paths must use POSIX separators",
            origin.clone(),
        ));
    }
    Ok(if text.is_empty() {
        ".".into()
    } else {
        text.into()
    })
}

pub(crate) fn anchor(
    raw: &str,
    base: &Path,
    root: &Path,
    environment: &BTreeMap<String, String>,
    origin: &Origin,
    allow_outside: bool,
) -> Result<String, ConfigError> {
    let value = substitute(raw, environment, origin)?;
    if value.is_empty() || value.contains('\\') {
        return Err(ConfigError::new(
            "path must be a nonempty POSIX path",
            origin.clone(),
        ));
    }
    let path = Path::new(&value);
    if path.is_absolute() && !allow_outside {
        return Err(ConfigError::new(
            "absolute project path is not portable; use a relative path",
            origin.clone(),
        ));
    }
    let candidate = normalise(
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            base.join(path)
        }
        .as_path(),
    );
    let mut existing = candidate.as_path();
    let mut suffix = Vec::new();
    while !existing.exists() {
        if let Some(name) = existing.file_name() {
            suffix.push(name.to_os_string());
        }
        existing = existing
            .parent()
            .ok_or_else(|| ConfigError::new("cannot resolve path", origin.clone()))?;
    }
    let mut checked = existing
        .canonicalize()
        .map_err(|e| ConfigError::new(format!("cannot resolve path: {e}"), origin.clone()))?;
    for name in suffix.into_iter().rev() {
        checked.push(name);
    }
    if let Ok(relative) = checked.strip_prefix(root) {
        return portable(relative, origin);
    }
    if allow_outside {
        portable(&checked, origin)
    } else {
        Err(ConfigError::new(
            "path escapes the workspace",
            origin.clone(),
        ))
    }
}

pub(crate) fn executable(
    raw: &str,
    base: &Path,
    root: &Path,
    environment: &BTreeMap<String, String>,
    origin: &Origin,
) -> Result<String, ConfigError> {
    let substituted = substitute(raw, environment, origin)?;
    if !substituted.contains('/') && !substituted.contains('\\') {
        if substituted.is_empty() {
            return Err(ConfigError::new(
                "executable must not be empty",
                origin.clone(),
            ));
        }
        return Ok(substituted);
    }
    anchor(&substituted, base, root, environment, origin, true)
}

pub(crate) fn check_glob(raw: &str, origin: &Origin) -> Result<(), ConfigError> {
    if raw.is_empty()
        || raw.contains('\\')
        || raw.starts_with('/')
        || raw.split('/').any(|s| s == "..")
        || raw.contains('\0')
    {
        return Err(ConfigError::new(
            "glob must stay inside the component using POSIX paths",
            origin.clone(),
        ));
    }
    globset::GlobBuilder::new(raw)
        .literal_separator(true)
        .backslash_escape(false)
        .build()
        .map(|_| ())
        .map_err(|e| ConfigError::new(format!("invalid glob: {e}"), origin.clone()))
}
