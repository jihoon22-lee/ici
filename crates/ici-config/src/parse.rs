use crate::{ConfigError, Origin};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read},
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};
use toml_edit::{Document, Item};

pub const MAX_CONFIG_BYTES: u64 = 4 * 1024 * 1024;

/// Why [`read_config_text`] returned no text; each is one of the reader's historical refusals.
#[derive(Debug)]
pub enum ConfigRead {
    /// The path does not resolve to anything that can be examined.
    Unavailable(io::Error),
    /// Not a regular file, or larger than [`MAX_CONFIG_BYTES`].
    NotBounded,
    /// A bounded regular file that cannot be read, or is not UTF-8.
    Unreadable(io::Error),
}

/// One configuration file's text through a single nonblocking open: the handle's own `fstat`
/// decides "regular file of at most 4 MiB" and the read stops one byte past the bound, so a
/// file swapped for a FIFO or grown after its path was examined is neither waited on nor read
/// whole. Symlinks are followed, as every configuration reader always has.
pub fn read_config_text(path: &Path) -> Result<String, ConfigRead> {
    let opened = fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NONBLOCK | nix::libc::O_CLOEXEC)
        .open(path);
    let file = match opened {
        Ok(file) => file,
        // Same classes as the stat-then-read reader this replaces: a path that cannot be
        // examined, a path that is not a bounded regular file, or one that cannot be read.
        Err(open) => {
            return Err(match fs::metadata(path) {
                Err(e) => ConfigRead::Unavailable(e),
                Ok(m) if !m.is_file() || m.len() > MAX_CONFIG_BYTES => ConfigRead::NotBounded,
                Ok(_) => ConfigRead::Unreadable(open),
            });
        }
    };
    let metadata = file.metadata().map_err(ConfigRead::Unavailable)?;
    if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
        return Err(ConfigRead::NotBounded);
    }
    let mut bytes = Vec::new();
    file.take(MAX_CONFIG_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(ConfigRead::Unreadable)?;
    if bytes.len() as u64 > MAX_CONFIG_BYTES {
        return Err(ConfigRead::NotBounded);
    }
    String::from_utf8(bytes).map_err(|_| {
        ConfigRead::Unreadable(io::Error::new(
            io::ErrorKind::InvalidData,
            "stream did not contain valid UTF-8",
        ))
    })
}

#[derive(Clone, Debug)]
pub(crate) struct Parsed {
    pub path: PathBuf,
    pub text: String,
    pub value: Value,
    pub origins: BTreeMap<String, Origin>,
}

impl Parsed {
    pub fn read(path: &Path, pyproject: bool, layer: &str) -> Result<Self, ConfigError> {
        let origin = Origin {
            file: path.display().to_string(),
            line: 1,
            column: 1,
            key: String::new(),
            layer: layer.into(),
        };
        let text = read_config_text(path).map_err(|failure| {
            let message = match failure {
                ConfigRead::Unavailable(e) => format!("cannot read configuration: {e}"),
                ConfigRead::NotBounded => {
                    "configuration must be a regular file of at most 4 MiB".into()
                }
                ConfigRead::Unreadable(e) => format!("configuration must be readable UTF-8: {e}"),
            };
            ConfigError::new(message, origin)
        })?;
        Self::parse(path, text, pyproject, layer)
    }

    pub fn parse(
        path: &Path,
        text: String,
        pyproject: bool,
        layer: &str,
    ) -> Result<Self, ConfigError> {
        if text.len() as u64 > MAX_CONFIG_BYTES {
            return Err(ConfigError::new(
                "configuration exceeds 4 MiB",
                at(path, &text, 0, "", layer),
            ));
        }
        let doc = Document::parse(text.as_str()).map_err(|e| {
            ConfigError::new(
                format!("invalid TOML: {}", e.message()),
                at(
                    path,
                    &text,
                    e.span().map(|s| s.start).unwrap_or(0),
                    "",
                    layer,
                ),
            )
        })?;
        let mut origins = BTreeMap::new();
        collect_item(doc.as_item(), "", path, &text, layer, &mut origins);
        let raw: Value = toml_edit::de::from_document(doc.into_mut()).map_err(|e| {
            ConfigError::new(
                format!("invalid configuration value: {e}"),
                at(path, &text, 0, "", layer),
            )
        })?;
        let value = if pyproject {
            let value = raw
                .get("tool")
                .and_then(|v| v.get("ici"))
                .cloned()
                .ok_or_else(|| {
                    ConfigError::new(
                        "pyproject.toml does not declare [tool.ici]",
                        at(path, &text, 0, "tool.ici", layer),
                    )
                })?;
            origins = origins
                .into_iter()
                .filter_map(|(key, mut origin)| {
                    if key == "tool.ici" {
                        origin.key.clear();
                        Some((String::new(), origin))
                    } else {
                        key.strip_prefix("tool.ici.").map(|short| {
                            origin.key = short.to_string();
                            (short.to_string(), origin)
                        })
                    }
                })
                .collect();
            value
        } else {
            raw
        };
        Ok(Self {
            path: path.to_path_buf(),
            text,
            value,
            origins,
        })
    }

    pub fn origin(&self, key: &str) -> Origin {
        if let Some(found) = self.origins.get(key) {
            return found.clone();
        }
        // Array members and nested keys can point at their closest declared container.
        let mut ancestor = key.to_string();
        while let Some((head, _)) = ancestor.rsplit_once('.') {
            ancestor = head.to_string();
            if let Some(found) = self.origins.get(&ancestor) {
                let mut origin = found.clone();
                origin.key = key.to_string();
                return origin;
            }
        }
        at(&self.path, &self.text, 0, key, "root")
    }

    pub fn error(&self, key: &str, message: impl Into<String>) -> ConfigError {
        ConfigError::new(message, self.origin(key))
    }
}

fn at(path: &Path, text: &str, offset: usize, key: &str, layer: &str) -> Origin {
    let offset = offset.min(text.len());
    let prefix = &text.as_bytes()[..offset];
    let line = prefix.iter().filter(|b| **b == b'\n').count() + 1;
    let line_start = prefix
        .iter()
        .rposition(|b| *b == b'\n')
        .map_or(0, |p| p + 1);
    let column = String::from_utf8_lossy(&prefix[line_start..])
        .chars()
        .count()
        + 1;
    Origin {
        file: path.display().to_string(),
        line,
        column,
        key: key.into(),
        layer: layer.into(),
    }
}

fn collect_item(
    item: &Item,
    prefix: &str,
    path: &Path,
    text: &str,
    layer: &str,
    origins: &mut BTreeMap<String, Origin>,
) {
    if !prefix.is_empty() {
        origins.entry(prefix.into()).or_insert_with(|| {
            at(
                path,
                text,
                item.span().map_or(0, |s| s.start),
                prefix,
                layer,
            )
        });
    }
    if let Some(table) = item.as_table_like() {
        for (name, child) in table.iter() {
            let key = if prefix.is_empty() {
                name.to_string()
            } else {
                format!("{prefix}.{name}")
            };
            let start = table
                .key(name)
                .and_then(|k| k.span())
                .map_or_else(|| child.span().map_or(0, |s| s.start), |s| s.start);
            origins.insert(key.clone(), at(path, text, start, &key, layer));
            collect_item(child, &key, path, text, layer, origins);
        }
    } else if let Some(tables) = item.as_array_of_tables() {
        for (index, table) in tables.iter().enumerate() {
            let key = format!("{prefix}[{index}]");
            collect_item(
                &Item::Table(table.clone()),
                &key,
                path,
                text,
                layer,
                origins,
            );
        }
    } else if let Some(array) = item.as_array() {
        for (index, value) in array.iter().enumerate() {
            let key = format!("{prefix}[{index}]");
            collect_item(
                &Item::Value(value.clone()),
                &key,
                path,
                text,
                layer,
                origins,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn a_configuration_read_is_one_bounded_nonblocking_open() {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path();
        let regular = base.join("ici.toml");
        fs::write(&regular, "[workspace]\nname = \"w\"\n").unwrap();
        assert_eq!(
            read_config_text(&regular).unwrap(),
            "[workspace]\nname = \"w\"\n"
        );

        // A FIFO nobody writes to: the open does not wait for a writer, and it is refused.
        let fifo = base.join("fifo.toml");
        nix::unistd::mkfifo(&fifo, nix::sys::stat::Mode::S_IRWXU).unwrap();
        assert!(matches!(
            read_config_text(&fifo),
            Err(ConfigRead::NotBounded)
        ));
        let linked = base.join("linked.toml");
        std::os::unix::fs::symlink(&fifo, &linked).unwrap();
        assert!(matches!(
            read_config_text(&linked),
            Err(ConfigRead::NotBounded)
        ));

        // One byte past the bound is refused; exactly the bound is read.
        let large = base.join("large.toml");
        let at_bound = MAX_CONFIG_BYTES as usize;
        fs::write(&large, vec![b'#'; at_bound]).unwrap();
        assert_eq!(read_config_text(&large).unwrap().len(), at_bound);
        fs::write(&large, vec![b'#'; at_bound + 1]).unwrap();
        assert!(matches!(
            read_config_text(&large),
            Err(ConfigRead::NotBounded)
        ));

        // The historical message classes: missing, not UTF-8, unreadable.
        assert!(matches!(
            read_config_text(&base.join("missing.toml")),
            Err(ConfigRead::Unavailable(_))
        ));
        let binary = base.join("binary.toml");
        fs::write(&binary, [0xff, 0xfe]).unwrap();
        match read_config_text(&binary) {
            Err(ConfigRead::Unreadable(e)) => {
                assert_eq!(e.to_string(), "stream did not contain valid UTF-8")
            }
            other => panic!("{other:?}"),
        }
        let locked = base.join("locked.toml");
        fs::write(&locked, "[workspace]\n").unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
        if fs::File::open(&locked).is_err() {
            assert!(matches!(
                read_config_text(&locked),
                Err(ConfigRead::Unreadable(_))
            ));
        }
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o600)).unwrap();
    }
}
