//! Static metadata used by both common packaging and explicit native compilation.
use super::*;
use toml_edit::DocumentMut;

pub(crate) struct SourceMetadata {
    pub metadata_snapshot: InputSnapshot,
    pub name: String,
    pub version: Option<String>,
    pub version_source: String,
    pub scripts: BTreeMap<String, String>,
}

pub(crate) fn source_metadata(
    project: &ResolvedProject,
    base: &Path,
) -> Result<SourceMetadata, String> {
    let mut metadata_paths = vec![];
    for directory in [base, project.root.as_path()] {
        for name in ["ici.toml", "dev.toml", "pyproject.toml"] {
            let path = directory.join(name);
            if std::fs::symlink_metadata(&path).is_ok() {
                let relative = relative(&project.root, &path)?;
                if !metadata_paths.contains(&relative) {
                    metadata_paths.push(relative);
                }
            }
        }
    }
    let metadata_snapshot = InputSnapshot::take(
        &project.root,
        &metadata_paths,
        &[],
        InputLimits {
            file_bytes: 2 * 1024 * 1024,
            total_bytes: 12 * 1024 * 1024,
        },
    )
    .map_err(|error| error.to_string())?;
    let mut name = None;
    let mut version = None;
    let mut version_source = String::new();
    let mut scripts = BTreeMap::new();
    let mut scripts_loaded = false;
    for path in &metadata_paths {
        let input = &metadata_snapshot.files[path].input;
        let text = std::str::from_utf8(&input.bytes)
            .map_err(|_| format!("project metadata {path} is not UTF-8"))?;
        let document = text
            .parse::<DocumentMut>()
            .map_err(|error| format!("project metadata {path}: {error}"))?;
        let pyproject = path.ends_with("pyproject.toml");
        let table = if pyproject {
            document
                .get("project")
                .and_then(|item| item.as_table_like())
        } else {
            Some(document.as_table() as &dyn toml_edit::TableLike)
        };
        if pyproject && document.get("project").is_some() && table.is_none() {
            return Err(format!("{path} [project] must be a table"));
        }
        if let Some(table) = table {
            if let Some(value) = table.get("name") {
                let value = value
                    .as_str()
                    .ok_or_else(|| format!("{path} project name is not a string"))?;
                validate_name(value)?;
                if name.is_none() {
                    name = Some(value.to_owned());
                }
            }
            if let Some(value) = table.get("version") {
                let value = value
                    .as_str()
                    .ok_or_else(|| format!("{path} project version is not a string"))?;
                let value = normalize_version(value)?;
                if version.is_none() {
                    version = Some(value);
                    version_source = format!("{path}:version");
                }
            }
            if pyproject && !scripts_loaded {
                if let Some(entries) = table.get("scripts") {
                    let entries = entries
                        .as_table_like()
                        .ok_or_else(|| format!("{path} project.scripts is not a table"))?;
                    for (name, target) in entries.iter() {
                        scripts.insert(
                            name.to_owned(),
                            target
                                .as_str()
                                .ok_or_else(|| {
                                    format!("{path} project.scripts.{name} is not a string")
                                })?
                                .to_owned(),
                        );
                    }
                }
                scripts_loaded = true;
            }
        }
    }
    let name = name.unwrap_or_else(|| {
        base.file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned()
    });
    validate_name(&name)?;
    Ok(SourceMetadata {
        metadata_snapshot,
        name,
        version,
        version_source,
        scripts,
    })
}
