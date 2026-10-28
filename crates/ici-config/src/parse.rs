use crate::{ConfigError, Origin};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
use toml_edit::{Document, Item};

pub const MAX_CONFIG_BYTES: u64 = 4 * 1024 * 1024;

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
        let metadata = fs::metadata(path).map_err(|e| {
            ConfigError::new(format!("cannot read configuration: {e}"), origin.clone())
        })?;
        if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
            return Err(ConfigError::new(
                "configuration must be a regular file of at most 4 MiB",
                origin,
            ));
        }
        let text = fs::read_to_string(path).map_err(|e| {
            ConfigError::new(format!("configuration must be readable UTF-8: {e}"), origin)
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
