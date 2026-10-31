//! Read-only explanation of historical configuration precedence; never a merge input.
use crate::{Origin, parse::Parsed};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize)]
pub struct LegacyLayer {
    pub label: String,
    pub path: PathBuf,
    pub error: Option<String>,
}
#[derive(Clone, Debug, Serialize)]
pub struct LegacyValue {
    pub label: String,
    pub value: Value,
    pub origin: Origin,
}
#[derive(Clone, Debug, Serialize)]
pub struct LegacyConflict {
    pub key: String,
    pub winner: LegacyValue,
    pub overridden: Vec<LegacyValue>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct LegacyMigrationReport {
    pub sources: Vec<LegacyLayer>,
    pub contested: Vec<LegacyConflict>,
    pub policy_is_contested: bool,
}

pub fn legacy_migration_report(
    base: &Path,
    environment: &BTreeMap<String, String>,
) -> LegacyMigrationReport {
    let mut candidates = vec![];
    let home = environment.get("HOME").map(PathBuf::from);
    if let Some(config) = environment
        .get("XDG_CONFIG_HOME")
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .or_else(|| home.as_ref().map(|p| p.join(".config")))
    {
        candidates.push(("XDG global", config.join("ici/config.toml")));
    }
    candidates.push(("project ici.toml", base.join("ici.toml")));
    candidates.push(("dev.toml", base.join("dev.toml")));
    if let Some(explicit) = environment.get("ICI_CONFIG").filter(|s| !s.is_empty()) {
        let path = if let Some(relative) = explicit.strip_prefix("~/") {
            home.as_ref()
                .map(|p| p.join(relative))
                .unwrap_or_else(|| PathBuf::from(explicit))
        } else {
            PathBuf::from(explicit)
        };
        candidates.push((
            "ICI_CONFIG",
            if path.is_absolute() {
                path
            } else {
                base.join(path)
            },
        ));
    }
    let mut report = LegacyMigrationReport::default();
    let mut seen: BTreeMap<String, Vec<LegacyValue>> = BTreeMap::new();
    for (label, path) in candidates {
        if !path.exists() {
            continue;
        }
        let parsed = Parsed::read(&path, false, label);
        report.sources.push(LegacyLayer {
            label: label.into(),
            path,
            error: parsed.as_ref().err().map(ToString::to_string),
        });
        if let Ok(parsed) = parsed {
            leaves(&parsed.value, "", label, &parsed, &mut seen);
        }
    }
    for (key, mut values) in seen {
        let winner = values.pop().expect("at least one declaration");
        values.retain(|previous| previous.value != winner.value);
        if values.is_empty() {
            continue;
        }
        report.policy_is_contested |= matches!(
            key.rsplit('.').next(),
            Some("required" | "enabled" | "mode" | "threshold" | "min_score")
        );
        report.contested.push(LegacyConflict {
            key,
            winner,
            overridden: values,
        });
    }
    report
}

fn leaves(
    value: &Value,
    prefix: &str,
    label: &str,
    parsed: &Parsed,
    seen: &mut BTreeMap<String, Vec<LegacyValue>>,
) {
    if let Some(table) = value.as_object() {
        for (key, child) in table {
            let key = if prefix.is_empty() {
                key.clone()
            } else {
                format!("{prefix}.{key}")
            };
            leaves(child, &key, label, parsed, seen);
        }
    } else {
        seen.entry(prefix.into()).or_default().push(LegacyValue {
            label: label.into(),
            value: value.clone(),
            origin: parsed.origin(prefix),
        });
    }
}
