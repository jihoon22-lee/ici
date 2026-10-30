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
                        // Frozen `_build` anchors declared and local-overlay build paths at the
                        // file that declared the build, not at the overlay file.
                        let base = parsed.path.parent().unwrap();
                        paths::anchor(&raw, base, root, environment, &origin, false)
                    })
                    .transpose()
            };
            let artifacts = strings(settings, "artifacts");
            for pattern in &artifacts {
                let origin = parsed.origin(&format!("builds.{id}.artifacts"));
                paths::check_glob(pattern, &origin)?;
                // Frozen `_artifacts`: a canonical relative pattern inside the build directory.
                if pattern.contains('\\')
                    || pattern
                        .split('/')
                        .any(|part| part.is_empty() || part == ".")
                {
                    return Err(ConfigError::new(
                        "artifact glob must be a canonical relative pattern inside the build directory",
                        origin,
                    ));
                }
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
/// Frozen `ici.domain.workspace.BuildUnit`/`Workspace` rules for schema 1 builds: stable
/// lowercase identifiers, output directory/definition below the workspace root, one build per
/// directory, and a declared system and directory (`workspace/model.py`). Sparse v2 keeps its
/// documented spellings (for example `project = "."` for a root CMake project, shadow builds
/// without a directory); every family refuses an id that cannot safely name a shadow directory.
pub(crate) fn validate_builds(
    builds: &[Build],
    family: SchemaFamily,
    parsed: &Parsed,
) -> Result<(), ConfigError> {
    let mut directories: BTreeMap<&str, &str> = BTreeMap::new();
    for build in builds {
        let key = |name: &str| format!("builds.{}.{name}", build.id);
        if build.id.is_empty()
            || build.id.len() > 128
            || build.id == "."
            || build.id.contains(['/', '\\'])
            || build.id.contains("..")
            || build.id.chars().any(char::is_control)
        {
            return Err(parsed.error(
                &format!("builds.{}", build.id),
                "build id must be a bounded name without path separators",
            ));
        }
        if family != SchemaFamily::NextV1 {
            continue;
        }
        let identifiers = [
            (format!("builds.{}", build.id), Some(build.id.as_str())),
            (key("system"), build.system.as_deref()),
            (key("variant"), Some(build.variant.as_str())),
        ];
        for (at, value) in identifiers {
            if let Some(value) = value
                && !identifier(value)
            {
                return Err(parsed.error(
                    &at,
                    format!("build identifiers must be lowercase alphanumeric with . _ - separators: {value:?}"),
                ));
            }
        }
        for (name, value) in [("directory", &build.directory), ("project", &build.project)] {
            if value.as_deref() == Some(".") {
                return Err(parsed.error(
                    &key(name),
                    "a build path must name a path below the workspace root",
                ));
            }
        }
        if build.system.is_none() {
            return Err(parsed.error(&key("system"), "a build must name a system"));
        }
        let Some(directory) = build.directory.as_deref() else {
            return Err(parsed.error(&key("directory"), "a build must name a directory"));
        };
        if let Some(owner) = directories.insert(directory, &build.id) {
            return Err(parsed.error(
                &key("directory"),
                format!(
                    "builds {owner} and {} share directory {directory}",
                    build.id
                ),
            ));
        }
    }
    Ok(())
}
/// Frozen `_IDENTIFIER_RE = [a-z0-9](?:[a-z0-9._-]*[a-z0-9])?`.
fn identifier(value: &str) -> bool {
    let allowed = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit();
    value.starts_with(allowed)
        && value.ends_with(allowed)
        && value.chars().all(|c| allowed(c) || "._-".contains(c))
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
            // Frozen `_suppressions` normalises the spelling (`./src/**` selects `src/**`).
            let path = text(value, "path")
                .unwrap_or_default()
                .split('/')
                .filter(|part| *part != ".")
                .collect::<Vec<_>>()
                .join("/");
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
        // Frozen `_publish` strips trailing slashes from both endpoints.
        api_url: text(p, "api_url")
            .unwrap_or_default()
            .trim_end_matches('/')
            .into(),
        server_url: text(p, "server_url")
            .unwrap_or_default()
            .trim_end_matches('/')
            .into(),
        branch: text(p, "branch").unwrap_or_else(|| "gh-pages".into()),
        token_env: text(p, "token_env").unwrap_or_else(|| "GITHUB_TOKEN".into()),
        settings: p.clone(),
    })
}
