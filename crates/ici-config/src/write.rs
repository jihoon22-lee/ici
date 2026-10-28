use crate::{ConfigError, Origin, ResolvedProject, SchemaFamily, parse::Parsed, schema};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    io::Write,
    path::{Path, PathBuf},
};
use toml_edit::{DocumentMut, value};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Scaffold {
    pub root: PathBuf,
    pub text: String,
    pub existing: Option<PathBuf>,
    pub components: Vec<String>,
    pub source_count: usize,
    pub limitations: Vec<String>,
}

/// Propose only. The exact same resolver supplies no-config, sparse-v2, and init scope.
pub fn scaffold(cwd: &Path) -> Result<Scaffold, ConfigError> {
    scaffold_with_environment(cwd, &std::env::vars().collect())
}
pub fn scaffold_with_environment(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<Scaffold, ConfigError> {
    let resolved = crate::resolve_with_environment(cwd, None, None, environment)?;
    Ok(Scaffold {
        root: resolved.root,
        text: "# ici uses discovered sources and native tool settings. Override only intent.\nschema_version = 2\n".into(),
        existing: resolved.config_source,
        components: resolved.components.iter().map(|c| c.id.clone()).collect(),
        source_count: resolved.components.iter().map(|c| c.files.len()).sum(),
        limitations: resolved.limitations,
    })
}

pub fn write_scaffold(proposal: &Scaffold, output: Option<&Path>) -> Result<PathBuf, ConfigError> {
    if let Some(existing) = &proposal.existing {
        return Err(ConfigError::new(
            "configuration already exists; use migrate preview instead of replacing it",
            origin(existing, "init"),
        ));
    }
    let output = output
        .map(|p| {
            if p.is_absolute() {
                p.to_path_buf()
            } else {
                proposal.root.join(p)
            }
        })
        .unwrap_or_else(|| proposal.root.join("ici.toml"));
    write_new(&output, &proposal.text)?;
    Ok(output)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MigrationPreview {
    pub source: PathBuf,
    pub source_sha256: String,
    pub from_schema: SchemaFamily,
    pub to_schema: u32,
    pub proposed_text: String,
    pub notes: Vec<String>,
}

/// Create a preview while preserving the source file and all TOML comments.
pub fn migrate_preview(source: &Path) -> Result<MigrationPreview, ConfigError> {
    let pyproject = source
        .file_name()
        .is_some_and(|name| name == "pyproject.toml");
    let parsed = Parsed::read(source, pyproject, "root")?;
    let family = schema::family(&parsed)?;
    schema::validate(&parsed, family)?;
    let mut document: DocumentMut = parsed
        .text
        .parse()
        .map_err(|e| parsed.error("", format!("invalid TOML: {e}")))?;
    let target = if pyproject {
        document["tool"]["ici"].as_table_like_mut()
    } else {
        Some(document.as_table_mut() as &mut dyn toml_edit::TableLike)
    }
    .ok_or_else(|| parsed.error("", "configuration must be a table"))?;
    target.insert("schema_version", value(2));
    let mut notes = vec![
        "Preview only: the original configuration and saved results/baselines are unchanged".into(),
    ];
    if family == SchemaFamily::Legacy {
        let declared = parsed
            .value
            .pointer("/project/source_dirs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_else(|| {
                serde_json::from_str::<Value>(include_str!("legacy-defaults.json"))
                    .expect("defaults")["project"]["source_dirs"]
                    .as_array()
                    .unwrap()
                    .clone()
            });
        let mut sources = toml_edit::Array::new();
        for item in declared {
            sources.push(item.as_str().expect("validated source directory"));
        }
        let mut component = toml_edit::Table::new();
        component.insert("id", value("project"));
        component.insert("root", value("."));
        component.insert("source_dirs", value(sources));
        if let Some(kind) = parsed
            .value
            .pointer("/project/type")
            .or_else(|| parsed.value.get("type"))
            .and_then(Value::as_str)
        {
            let names: &[&str] = match kind {
                "python" => &["python"],
                "cpp" => &["cpp"],
                _ => &["python", "cpp"],
            };
            let mut languages = toml_edit::Array::new();
            for name in names {
                languages.push(*name);
            }
            component.insert("languages", value(languages));
        }
        if let Some(runtime) = parsed
            .value
            .pointer("/engines/test/python")
            .and_then(Value::as_str)
        {
            let mut python = toml_edit::Table::new();
            python.insert("executable", value(runtime));
            component.insert("python", toml_edit::Item::Table(python));
        }
        let mut components = toml_edit::ArrayOfTables::new();
        components.push(component);
        target.insert("components", toml_edit::Item::ArrayOfTables(components));
        notes.push("Legacy engine settings remain in the same tables; an explicit project component preserves source_dirs, language selection and project runtime, freezing the former scope instead of silently widening it".into());
        notes.push("Legacy global/dev.toml policy is not imported automatically; review those files and move intended team policy explicitly".into());
    }
    if family == SchemaFamily::NextV1 {
        notes.push("Declared components/checks/builds are preserved; default generated/dependency exclusions now remain active unless exclude is explicitly replaced".into());
    }
    let proposed_text = document.to_string();
    let proposed = Parsed::parse(source, proposed_text.clone(), pyproject, "root")?;
    schema::validate(&proposed, SchemaFamily::SparseV2)?;
    Ok(MigrationPreview {
        source: source.into(),
        source_sha256: ici_domain::sha256(parsed.text.as_bytes()),
        from_schema: family,
        to_schema: 2,
        proposed_text,
        notes,
    })
}

/// Publish to a new output only; also detects edits made after the preview.
pub fn write_migration(preview: &MigrationPreview, output: &Path) -> Result<PathBuf, ConfigError> {
    let source = std::fs::read(&preview.source).map_err(|e| {
        ConfigError::new(
            format!("cannot reread migration source: {e}"),
            origin(&preview.source, "migrate"),
        )
    })?;
    if ici_domain::sha256(&source) != preview.source_sha256 {
        return Err(ConfigError::new(
            "source changed after migration preview; regenerate the preview",
            origin(&preview.source, "migrate"),
        ));
    }
    if output == preview.source
        || (output.exists() && output.canonicalize().ok() == preview.source.canonicalize().ok())
    {
        return Err(ConfigError::new(
            "migration output must be a new file distinct from the original",
            origin(output, "migrate"),
        ));
    }
    write_new(output, &preview.proposed_text)?;
    Ok(output.into())
}

fn write_new(path: &Path, text: &str) -> Result<(), ConfigError> {
    let origin = origin(path, "write");
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| {
        ConfigError::new(
            format!("cannot prepare new configuration: {e}"),
            origin.clone(),
        )
    })?;
    temporary
        .write_all(text.as_bytes())
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| {
            ConfigError::new(
                format!("cannot write new configuration: {e}"),
                origin.clone(),
            )
        })?;
    temporary.persist_noclobber(path).map_err(|e| {
        ConfigError::new(
            format!(
                "refusing to overwrite or unable to publish new configuration: {}",
                e.error
            ),
            origin,
        )
    })?;
    Ok(())
}

fn origin(path: &Path, key: &str) -> Origin {
    Origin {
        file: path.display().to_string(),
        key: key.into(),
        ..Origin::defaults(key)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExplainedValue {
    pub key: String,
    pub value: Value,
    pub origin: Origin,
}

pub fn explain(project: &ResolvedProject) -> Vec<ExplainedValue> {
    fn visit(
        value: &Value,
        prefix: &str,
        project: &ResolvedProject,
        values: &mut Vec<ExplainedValue>,
    ) {
        if let Some(table) = value.as_object() {
            for (key, child) in table {
                if key.starts_with('_') {
                    continue;
                }
                visit(child, &schema::join(prefix, key), project, values);
            }
        } else {
            values.push(ExplainedValue {
                key: prefix.into(),
                value: value.clone(),
                origin: project
                    .origins
                    .get(prefix)
                    .cloned()
                    .unwrap_or_else(|| Origin::defaults(prefix)),
            });
        }
    }
    let mut values = vec![];
    visit(&project.effective, "", project, &mut values);
    for component in &project.components {
        for (suffix, value) in [
            ("root", serde_json::json!(component.root)),
            ("languages", serde_json::json!(component.languages)),
            ("files", serde_json::json!(component.files)),
        ] {
            let key = format!("resolved_components.{}.{suffix}", component.id);
            values.push(ExplainedValue {
                key,
                value,
                origin: component.origin.clone(),
            });
        }
    }
    values.sort_by(|a, b| a.key.cmp(&b.key));
    values
}
