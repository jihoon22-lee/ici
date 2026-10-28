use crate::resolve::{strings, text};
use crate::{parse::Parsed, paths, *};
use serde_json::Value;
use std::{collections::BTreeMap, path::Path};

pub(crate) fn builds(
    value: &Value,
    root: &Path,
    parsed: &Parsed,
    environment: &BTreeMap<String, String>,
    origins: &BTreeMap<String, Origin>,
) -> Result<Vec<Build>, ConfigError> {
    let mut result = vec![];
    if let Some(table) = value.get("builds").and_then(Value::as_object) {
        for (id, settings) in table {
            let anchor = |name: &str| {
                text(settings, name)
                    .map(|raw| {
                        let origin = origins
                            .get(&format!("builds.{id}.{name}"))
                            .cloned()
                            .unwrap_or_else(|| parsed.origin(&format!("builds.{id}.{name}")));
                        let base = Path::new(&origin.file)
                            .parent()
                            .filter(|p| p.is_absolute())
                            .unwrap_or_else(|| parsed.path.parent().unwrap());
                        paths::anchor(&raw, base, root, environment, &origin, false)
                    })
                    .transpose()
            };
            let artifacts = strings(settings, "artifacts");
            for pattern in &artifacts {
                paths::check_glob(pattern, &parsed.origin(&format!("builds.{id}.artifacts")))?;
            }
            result.push(Build {
                id: id.clone(),
                system: text(settings, "system"),
                project: anchor("project")?,
                directory: anchor("directory")?,
                variant: text(settings, "variant").unwrap_or_else(|| "default".into()),
                prepare: text(settings, "prepare"),
                artifacts,
                settings: settings.clone(),
                origin: parsed.origin(&format!("builds.{id}")),
            });
        }
    }
    Ok(result)
}
pub(crate) fn tools(
    value: &Value,
    root: &Path,
    parsed: &Parsed,
    environment: &BTreeMap<String, String>,
    origins: &BTreeMap<String, Origin>,
) -> Result<BTreeMap<String, ToolSetting>, ConfigError> {
    let mut result = BTreeMap::new();
    if let Some(table) = value.get("tools").and_then(Value::as_object) {
        for (id, settings) in table {
            let origin = origins
                .get(&format!("tools.{id}.path"))
                .cloned()
                .unwrap_or_else(|| parsed.origin(&format!("tools.{id}.path")));
            let base = Path::new(&origin.file).parent().unwrap_or(root);
            let path = text(settings, "path")
                .map(|raw| paths::executable(&raw, base, root, environment, &origin))
                .transpose()?;
            let config = text(settings, "config")
                .map(|raw| {
                    paths::anchor(
                        &raw,
                        parsed.path.parent().unwrap(),
                        root,
                        environment,
                        &parsed.origin(&format!("tools.{id}.config")),
                        false,
                    )
                })
                .transpose()?;
            result.insert(
                id.clone(),
                ToolSetting {
                    id: id.clone(),
                    source: text(settings, "source"),
                    path,
                    config,
                    settings: settings.clone(),
                },
            );
        }
    }
    Ok(result)
}
pub(crate) fn suppressions(
    value: &Value,
    parsed: &Parsed,
) -> Result<Vec<Suppression>, ConfigError> {
    let mut result = vec![];
    if let Some(entries) = value.get("suppressions").and_then(Value::as_array) {
        for (index, value) in entries.iter().enumerate() {
            let path = text(value, "path").unwrap_or_default();
            if !path.is_empty() {
                paths::check_glob(
                    &path,
                    &parsed.origin(&format!("suppressions[{index}].path")),
                )?;
            }
            result.push(Suppression {
                fingerprint: text(value, "fingerprint").unwrap_or_default(),
                rule: text(value, "rule").unwrap_or_default(),
                path,
                component: text(value, "component").unwrap_or_default(),
                reason: text(value, "reason").unwrap_or_default(),
                declared_in: parsed.path.display().to_string(),
            });
        }
    }
    Ok(result)
}
pub(crate) fn publish(value: &Value) -> Option<Publish> {
    value.get("publish").map(|p| Publish {
        repo: text(p, "repo").unwrap_or_default(),
        api_url: text(p, "api_url").unwrap_or_default(),
        server_url: text(p, "server_url").unwrap_or_default(),
        branch: text(p, "branch").unwrap_or_else(|| "gh-pages".into()),
        token_env: text(p, "token_env").unwrap_or_else(|| "GITHUB_TOKEN".into()),
        settings: p.clone(),
    })
}
