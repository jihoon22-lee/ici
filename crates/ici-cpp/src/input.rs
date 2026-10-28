use crate::CppError;
use nix::{
    fcntl::{OFlag, open, openat},
    sys::stat::Mode,
};
use serde::{
    Deserialize, Deserializer,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::Value;
use std::{
    fs::File,
    io::Read,
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
};

pub(crate) fn normalize(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            _ => result.push(part.as_os_str()),
        }
    }
    result
}
pub(crate) fn scoped(
    root: &Path,
    base: &Path,
    raw: &str,
) -> Result<(String, String, PathBuf), CppError> {
    if raw.is_empty()
        || raw.contains(['\0', '\\'])
        || (raw.as_bytes().get(1) == Some(&b':') && raw.as_bytes()[0].is_ascii_alphabetic())
    {
        return Err(CppError::new(
            "foreign-path-syntax",
            "compile path must be a nonempty POSIX path",
        ));
    }
    let path = Path::new(raw);
    let lexical = normalize(&if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    });
    let mut existing = lexical.as_path();
    let mut suffix = vec![];
    while !existing.exists() {
        if let Some(name) = existing.file_name() {
            suffix.push(name.to_os_string());
        }
        existing = existing
            .parent()
            .ok_or_else(|| CppError::new("invalid-path", "cannot anchor compile path"))?;
    }
    let mut resolved = existing
        .canonicalize()
        .map_err(|e| CppError::new("invalid-path", e.to_string()))?;
    for part in suffix.into_iter().rev() {
        resolved.push(part);
    }
    let (value, scope) = match resolved.strip_prefix(root) {
        Ok(relative) => (relative.to_path_buf(), "project"),
        Err(_) => (resolved.clone(), "external"),
    };
    let value = value
        .to_str()
        .ok_or_else(|| CppError::new("invalid-path", "compile path is not UTF-8"))?;
    Ok((
        if value.is_empty() {
            ".".into()
        } else {
            value.into()
        },
        scope.into(),
        resolved,
    ))
}

/// Open through directory descriptors with O_NOFOLLOW at every step, then verify stable identity.
pub(crate) fn read_contained(root: &Path, path: &Path, limit: usize) -> Result<Vec<u8>, CppError> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| CppError::new("outside-project", "input escapes project"))?;
    let parts: Vec<_> = relative.components().collect();
    if parts.is_empty() || parts.iter().any(|p| !matches!(p, Component::Normal(_))) {
        return Err(CppError::new(
            "invalid-path",
            "input must name a contained regular file",
        ));
    }
    let directory_flags =
        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC;
    let mut directory = open(root, directory_flags, Mode::empty())
        .map_err(|e| CppError::new("unreadable", e.to_string()))?;
    for part in &parts[..parts.len() - 1] {
        directory = openat(
            &directory,
            Path::new(part.as_os_str()),
            directory_flags,
            Mode::empty(),
        )
        .map_err(|e| CppError::new("unreadable", e.to_string()))?;
    }
    let descriptor = openat(
        &directory,
        Path::new(parts.last().unwrap().as_os_str()),
        OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC | OFlag::O_NONBLOCK,
        Mode::empty(),
    )
    .map_err(|e| {
        CppError::new(
            if e == nix::errno::Errno::ENOENT {
                "missing"
            } else {
                "unreadable"
            },
            e.to_string(),
        )
    })?;
    let mut file = File::from(descriptor);
    let before = file
        .metadata()
        .map_err(|e| CppError::new("unreadable", e.to_string()))?;
    if !before.is_file() {
        return Err(CppError::new("not-file", "input is not a regular file"));
    }
    if before.len() > limit as u64 {
        return Err(CppError::new("too-large", "input exceeds byte limit"));
    }
    let mut bytes = Vec::with_capacity((before.len() as usize).min(limit));
    (&mut file)
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| CppError::new("unreadable", e.to_string()))?;
    let after = file
        .metadata()
        .map_err(|e| CppError::new("unreadable", e.to_string()))?;
    if bytes.len() > limit {
        return Err(CppError::new("too-large", "input exceeds byte limit"));
    }
    if (
        before.dev(),
        before.ino(),
        before.len(),
        before.mtime(),
        before.mtime_nsec(),
    ) != (
        after.dev(),
        after.ino(),
        after.len(),
        after.mtime(),
        after.mtime_nsec(),
    ) {
        return Err(CppError::new("changed", "input changed while it was read"));
    }
    Ok(bytes)
}

/// serde_json alone accepts duplicate map keys; evidence readers must reject them.
pub(crate) fn strict_json(bytes: &[u8]) -> Result<Value, CppError> {
    let mut parser = serde_json::Deserializer::from_slice(bytes);
    let value = StrictValue::deserialize(&mut parser)
        .map_err(|e| CppError::new("malformed-json", e.to_string()))?;
    parser
        .end()
        .map_err(|e| CppError::new("malformed-json", e.to_string()))?;
    Ok(value.0)
}
struct StrictValue(Value);
impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        d.deserialize_any(StrictVisitor)
    }
}
struct StrictVisitor;
impl<'de> Visitor<'de> for StrictVisitor {
    type Value = StrictValue;
    fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("JSON without duplicate keys")
    }
    fn visit_bool<E: de::Error>(self, v: bool) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Bool(v)))
    }
    fn visit_i64<E: de::Error>(self, v: i64) -> Result<Self::Value, E> {
        Ok(StrictValue(v.into()))
    }
    fn visit_u64<E: de::Error>(self, v: u64) -> Result<Self::Value, E> {
        Ok(StrictValue(v.into()))
    }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Self::Value, E> {
        serde_json::Number::from_f64(v)
            .map(|v| StrictValue(Value::Number(v)))
            .ok_or_else(|| E::custom("non-finite JSON number"))
    }
    fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
        Ok(StrictValue(v.into()))
    }
    fn visit_string<E: de::Error>(self, v: String) -> Result<Self::Value, E> {
        Ok(StrictValue(v.into()))
    }
    fn visit_none<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Null))
    }
    fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Null))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> Result<Self::Value, A::Error> {
        let mut values = vec![];
        while let Some(value) = sequence.next_element::<StrictValue>()? {
            values.push(value.0);
            if values.len() > 1_000_000 {
                return Err(de::Error::custom("JSON array exceeds limit"));
            }
        }
        Ok(StrictValue(Value::Array(values)))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        let mut values = serde_json::Map::new();
        while let Some((key, value)) = map.next_entry::<String, StrictValue>()? {
            if values.insert(key, value.0).is_some() {
                return Err(de::Error::custom("duplicate JSON key"));
            }
        }
        Ok(StrictValue(Value::Object(values)))
    }
}
