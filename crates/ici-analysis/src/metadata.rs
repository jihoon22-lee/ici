//! Bounded declarative Python project metadata; no setup script execution.
use crate::source::SourceError;
use ici_domain::AnalysisRequest;
use pep440_rs::{Version, VersionSpecifiers};
use ruff_python_ast::PythonVersion;
use std::str::FromStr;
use toml_edit::{DocumentMut, Item};

pub struct ProjectMetadata {
    pub path: String,
    pub text: String,
    pub document: DocumentMut,
    pub requires_python: String,
}
pub fn component_relative<'a>(request: &AnalysisRequest, path: &'a str) -> &'a str {
    let root = request
        .options
        .get("component_root")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim_matches('/')
        .trim_start_matches("./");
    if root.is_empty() || root == "." {
        path
    } else {
        path.strip_prefix(root)
            .and_then(|path| path.strip_prefix('/'))
            .unwrap_or(path)
    }
}
pub fn load(request: &AnalysisRequest) -> Result<Option<ProjectMetadata>, SourceError> {
    let selected = request
        .options
        .get("metadata_path")
        .and_then(serde_json::Value::as_str);
    let input = request.inputs.iter().find(|input| {
        selected.map_or_else(
            || component_relative(request, &input.path) == "pyproject.toml",
            |path| input.path == path,
        )
    });
    let Some(input) = input else {
        return Ok(None);
    };
    if input.bytes.len() > 2 * 1024 * 1024 {
        return Err(SourceError::new(
            "metadata-limit",
            "pyproject.toml exceeds the 2 MiB bound",
        ));
    }
    let text = std::str::from_utf8(&input.bytes).map_err(|_| {
        SourceError::new("metadata-encoding", "pyproject.toml must be strict UTF-8")
    })?;
    let document = text.parse::<DocumentMut>().map_err(|_| {
        SourceError::new(
            "invalid-metadata",
            "pyproject.toml could not be parsed safely",
        )
    })?;
    if document
        .get("project")
        .is_some_and(|project| !project.is_table_like())
    {
        return Err(SourceError::new(
            "invalid-metadata",
            "pyproject.toml [project] must be a table",
        ));
    }
    let specifier = document
        .get("project")
        .and_then(|project| project.get("requires-python"));
    let requires_python = match specifier {
        Some(specifier) => specifier
            .as_str()
            .ok_or_else(|| {
                SourceError::new(
                    "invalid-requires-python",
                    "project.requires-python must be a string",
                )
            })?
            .to_owned(),
        None => String::new(),
    };
    validate_requires_python(&requires_python)?;
    Ok(Some(ProjectMetadata {
        path: input.path.clone(),
        text: text.to_owned(),
        document,
        requires_python,
    }))
}
pub fn validate_requires_python(value: &str) -> Result<Option<VersionSpecifiers>, SourceError> {
    if value.is_empty() {
        return Ok(None);
    }
    if value.len() > 65_536 {
        return Err(SourceError::new(
            "metadata-limit",
            "project.requires-python exceeds its 64 KiB bound",
        ));
    }
    VersionSpecifiers::from_str(value).map(Some).map_err(|_| {
        SourceError::new(
            "invalid-requires-python",
            "project.requires-python is not a valid PEP 440 specifier",
        )
    })
}
pub fn infer_floor(value: &str) -> Result<Option<PythonVersion>, SourceError> {
    let Some(specifiers) = validate_requires_python(value)? else {
        return Ok(None);
    };
    for major in 3..5 {
        for minor in if major == 3 { 7..21 } else { 0..21 } {
            for patch in 0..100 {
                let version = Version::new([major as u64, minor as u64, patch]);
                if specifiers.contains(&version) {
                    return Ok(Some(PythonVersion { major, minor }));
                }
            }
        }
    }
    Ok(None)
}
pub fn strings(item: Option<&Item>) -> Vec<String> {
    item.and_then(Item::as_array)
        .map_or_else(Vec::new, |values| {
            values
                .iter()
                .filter_map(|value| value.as_str().map(str::to_owned))
                .collect()
        })
}
