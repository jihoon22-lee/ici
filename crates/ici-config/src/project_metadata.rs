//! Frozen `core/project.read_project_metadata`: the project name and version of `ici.toml`,
//! `dev.toml` and `[project]` of `pyproject.toml`. The first value of each wins, and every present
//! value must be valid, so a later file's bad spelling refuses the run too. Frozen also loads
//! `dev.toml` as configuration first, so what that loader refuses in it stays a configuration error.
use crate::parse::{BoundedRead, MAX_CONFIG_BYTES, read_bounded_text};
use serde_json::Value;
use std::{
    fs, io,
    path::{Component, Path, PathBuf},
};

const FILES: [&str; 3] = ["ici.toml", "dev.toml", "pyproject.toml"];
/// The bound of [`lookup_name`], which reads no more than it historically did.
const LOOKUP_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProjectMetadata {
    /// `None`: no file sets one; see [`ProjectMetadata::legacy_name`].
    pub name: Option<String>,
    /// `None`: no file sets one; the caller picks the fallback (frozen: Git, or `v1.0.0`).
    pub version: Option<String>,
}

/// Frozen's message for the first refusal.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetadataRefusal {
    pub message: String,
    /// Frozen's configuration loader refused it (`Configuration error: `, exit 2), not the metadata
    /// reader behind it.
    pub configuration: bool,
}

impl From<String> for MetadataRefusal {
    fn from(message: String) -> Self {
        Self {
            message,
            configuration: false,
        }
    }
}

fn configuration_refusal(message: String) -> MetadataRefusal {
    MetadataRefusal {
        message,
        configuration: true,
    }
}

impl ProjectMetadata {
    /// The name a historical run records: the metadata's, or the directory's under the same rule.
    pub fn legacy_name(&self, root: &Path) -> Result<String, MetadataRefusal> {
        match &self.name {
            Some(name) => Ok(name.clone()),
            None => {
                let root = fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
                let directory = root
                    .file_name()
                    .map_or_else(String::new, |name| name.to_string_lossy().into_owned());
                Ok(validate_name(&Value::String(directory))?)
            }
        }
    }
}

/// The validated project name and version found in the metadata files.
pub fn read_project_metadata(root: &Path) -> Result<ProjectMetadata, MetadataRefusal> {
    let root = fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let mut name = None;
    let mut version = None;
    for file in FILES {
        let Some(path) = metadata_path(&root, file)? else {
            continue;
        };
        let document = read_document(&path, file)?;
        let table = metadata_table(&document, file)?;
        if file == "dev.toml" {
            loaded_as_configuration(table)?;
        }
        if let Some(value) = table.get("name") {
            let candidate = validate_name(value)?;
            name.get_or_insert(candidate);
        }
        if let Some(value) = table.get("version") {
            let candidate = validate_version(value)?;
            version.get_or_insert(candidate);
        }
    }
    Ok(ProjectMetadata { name, version })
}

/// Frozen's configuration loader requires these to be non-empty strings in `dev.toml`, as in
/// `ici.toml`. Only the two keys this reader takes: `dev.toml` is not native configuration.
fn loaded_as_configuration(table: &Value) -> Result<(), MetadataRefusal> {
    for key in ["name", "version"] {
        if let Some(value) = table.get(key)
            && value.as_str().is_none_or(|text| text.trim().is_empty())
        {
            return Err(configuration_refusal(format!(
                "{key} must be a non-empty string"
            )));
        }
    }
    Ok(())
}

/// A version from outside the project files (frozen validates `git describe` the same way).
pub fn validate_project_version(value: &str) -> Result<String, MetadataRefusal> {
    Ok(validate_version(&Value::String(value.to_owned()))?)
}

/// The name a command without metadata validation shows: the top-level `name` of `dev.toml`, then
/// `[project]` of `pyproject.toml`, before the directory name. A file that is unreadable, oversized
/// or outside the root contributes nothing.
pub(crate) fn lookup_name(root: &Path) -> Option<String> {
    let root = fs::canonicalize(root).ok()?;
    [("dev.toml", "/name"), ("pyproject.toml", "/project/name")]
        .into_iter()
        .find_map(|(file, pointer)| {
            let path = metadata_path(&root, file).ok()??;
            let text = read_bounded_text(&path, LOOKUP_BYTES).ok()?;
            let value = toml_edit::de::from_str::<Value>(&text).ok()?;
            value
                .pointer(pointer)
                .and_then(Value::as_str)
                .filter(|found| !found.trim().is_empty())
                .map(str::to_owned)
        })
}

/// Frozen `_metadata_path`: a path resolving outside the root is refused even when it dangles; one
/// that dangles inside the root is simply absent.
fn metadata_path(root: &Path, file: &str) -> Result<Option<PathBuf>, MetadataRefusal> {
    let joined = root.join(file);
    let outside = || format!("path is outside project root: {file}");
    match fs::canonicalize(&joined) {
        Ok(real) if real.starts_with(root) => Ok(Some(real)),
        Ok(_) => Err(outside().into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            if dangling_target(&joined).starts_with(root) {
                Ok(None)
            } else {
                Err(outside().into())
            }
        }
        Err(error) => Err(format!("could not inspect project metadata {file}: {error}").into()),
    }
}

/// Where a path that does not exist points, following symlinks as far as they go.
fn dangling_target(path: &Path) -> PathBuf {
    let mut current = path.to_path_buf();
    for _ in 0..40 {
        let Ok(target) = fs::read_link(&current) else {
            break;
        };
        let base = current.parent().unwrap_or(Path::new("/"));
        let mut next = PathBuf::new();
        for part in base.join(target).components() {
            match part {
                Component::ParentDir => {
                    next.pop();
                }
                Component::CurDir => {}
                _ => next.push(part.as_os_str()),
            }
        }
        current = next;
    }
    current
}

/// Frozen `_read_toml` names the resolved file in its errors and `_metadata_path` the requested
/// one. Its reader has no bound; this one keeps the bound of every other configuration read.
fn read_document(path: &Path, file: &str) -> Result<Value, MetadataRefusal> {
    let name = path
        .file_name()
        .map_or_else(String::new, |name| name.to_string_lossy().into_owned());
    if file == "dev.toml" {
        return read_configuration_document(path);
    }
    let text = read_bounded_text(path, MAX_CONFIG_BYTES).map_err(|failure| match failure {
        BoundedRead::NotRegular => format!("project metadata path is not a file: {file}"),
        BoundedRead::TooLarge => {
            format!("could not read project metadata {name}: larger than 4 MiB")
        }
        BoundedRead::Unavailable(error) | BoundedRead::Unreadable(error) => {
            if error.kind() == io::ErrorKind::InvalidData {
                format!("could not parse project metadata {name}: {error}")
            } else {
                format!("could not read project metadata {name}: {error}")
            }
        }
    })?;
    Ok(toml_edit::de::from_str::<Value>(&text).map_err(|error| {
        format!(
            "could not parse project metadata {name}: {}",
            error.message()
        )
    })?)
}

/// Frozen's configuration loader reads `dev.toml` before the metadata reader sees it, and names the
/// whole path.
fn read_configuration_document(path: &Path) -> Result<Value, MetadataRefusal> {
    let shown = path.display();
    let text = read_bounded_text(path, MAX_CONFIG_BYTES).map_err(|failure| {
        configuration_refusal(match failure {
            BoundedRead::NotRegular => format!("configuration path is not a file: {shown}"),
            BoundedRead::TooLarge => {
                format!("could not read configuration {shown}: larger than 4 MiB")
            }
            BoundedRead::Unavailable(error) | BoundedRead::Unreadable(error) => {
                if error.kind() == io::ErrorKind::InvalidData {
                    format!("could not parse configuration {shown}: {error}")
                } else {
                    format!("could not read configuration {shown}: {error}")
                }
            }
        })
    })?;
    toml_edit::de::from_str::<Value>(&text).map_err(|error| {
        configuration_refusal(format!(
            "could not parse configuration {shown}: {}",
            error.message()
        ))
    })
}

fn metadata_table<'a>(document: &'a Value, file: &str) -> Result<&'a Value, MetadataRefusal> {
    if file != "pyproject.toml" {
        return Ok(document);
    }
    match document.get("project") {
        None => Ok(&Value::Null),
        Some(project) if project.is_object() => Ok(project),
        Some(_) => Err(String::from("project metadata [project] must be a table").into()),
    }
}

/// Frozen `_PROJECT_VALUE_PATTERN` with `fullmatch`: unlike `$`, no trailing newline passes.
fn matches_pattern(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn validate_name(value: &Value) -> Result<String, String> {
    match value.as_str() {
        Some(name) if name != "." && name != ".." && matches_pattern(name) => Ok(name.to_owned()),
        _ => Err(format!("invalid project name: {}", python_repr(value))),
    }
}

fn validate_version(value: &Value) -> Result<String, String> {
    let refused = || format!("invalid project version: {}", python_repr(value));
    let text = value.as_str().ok_or_else(refused)?;
    let plain = text.strip_prefix('v').unwrap_or(text);
    if matches_pattern(plain) {
        Ok(format!("v{plain}"))
    } else {
        Err(refused())
    }
}

/// Python `repr` of a parsed TOML value, for frozen's `{value!r}` in its refusals.
fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => string_repr(text),
        Value::Array(items) => {
            let items: Vec<_> = items.iter().map(python_repr).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(items) => {
            let items: Vec<_> = items
                .iter()
                .map(|(key, item)| format!("{}: {}", string_repr(key), python_repr(item)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
    }
}

fn string_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut text = String::from(quote);
    for character in value.chars() {
        match character {
            '\\' => text.push_str("\\\\"),
            '\n' => text.push_str("\\n"),
            '\r' => text.push_str("\\r"),
            '\t' => text.push_str("\\t"),
            c if c == quote => {
                text.push('\\');
                text.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                text.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => text.push(c),
        }
    }
    text.push(quote);
    text
}
