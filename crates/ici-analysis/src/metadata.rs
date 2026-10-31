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
    let root = request
        .options
        .get("component_root")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim_matches('/')
        .trim_start_matches("./");
    let wanted = if root.is_empty() || root == "." {
        "pyproject.toml".to_owned()
    } else {
        format!("{root}/pyproject.toml")
    };
    let input = if let Some(path) = selected {
        request.inputs.iter().find(|input| input.path == path)
    } else {
        request
            .inputs
            .iter()
            .find(|input| input.path == wanted)
            .or_else(|| {
                request
                    .inputs
                    .iter()
                    .find(|input| input.path == "pyproject.toml")
            })
    };
    let Some(input) = input else {
        return Ok(None);
    };
    if input.path.starts_with('/')
        || input.path.contains(['\\', '\0'])
        || input
            .path
            .split('/')
            .any(|part| part.is_empty() || part == "..")
    {
        return Err(SourceError::new(
            "invalid-metadata-path",
            "Metadata inputs must use contained project-relative paths",
        ));
    }
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
pub struct Specifiers {
    regular: VersionSpecifiers,
    arbitrary: Vec<String>,
}
impl Specifiers {
    fn contains(&self, version: &Version) -> bool {
        self.regular.contains(version)
            && self
                .arbitrary
                .iter()
                .all(|literal| literal.eq_ignore_ascii_case(&version.to_string()))
    }
}
pub fn validate_requires_python(value: &str) -> Result<Option<Specifiers>, SourceError> {
    if value.is_empty() {
        return Ok(None);
    }
    if value.len() > 2 * 1024 * 1024 {
        return Err(SourceError::new(
            "metadata-limit",
            "project.requires-python exceeds the 2 MiB metadata bound",
        ));
    }
    let invalid = || {
        SourceError::new(
            "invalid-requires-python",
            "project.requires-python is not a valid PEP 440 specifier",
        )
    };
    let mut arbitrary = std::collections::BTreeSet::new();
    let mut regular = std::collections::BTreeSet::new();
    for specifier in value
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        if let Some(literal) = specifier.strip_prefix("===") {
            let literal = literal.trim();
            if literal
                .chars()
                .any(|character| character.is_whitespace() || matches!(character, ';' | ')'))
            {
                return Err(invalid());
            }
            arbitrary.insert(literal.to_owned());
        } else {
            regular.insert(specifier);
        }
    }
    let regular = VersionSpecifiers::from_str(&regular.into_iter().collect::<Vec<_>>().join(","))
        .map_err(|_| invalid())?;
    Ok(Some(Specifiers {
        regular,
        arbitrary: arbitrary.into_iter().collect(),
    }))
}
pub fn allows_version(specifier: &str, version: &str) -> Result<bool, SourceError> {
    let parsed = Version::from_str(version).map_err(|_| {
        SourceError::new(
            "invalid-runtime-version",
            "Selected Python version is not valid PEP 440",
        )
    })?;
    Ok(validate_requires_python(specifier)?.is_none_or(|specifiers| specifiers.contains(&parsed)))
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
fn module_name(value: &str) -> bool {
    value.split('.').all(crate::unicode::identifier)
}
pub fn import_names(request: &AnalysisRequest, metadata: Option<&ProjectMetadata>) -> Vec<String> {
    let project = metadata.and_then(|metadata| metadata.document.get("project"));
    let mut names = std::collections::BTreeSet::new();
    for name in strings(project.and_then(|project| project.get("import-names"))) {
        let name = name.split(';').next().unwrap_or("").trim();
        if module_name(name) {
            names.insert(name.to_owned());
        }
    }
    if let Some(scripts) = project
        .and_then(|project| project.get("scripts"))
        .and_then(Item::as_table_like)
    {
        for (_, value) in scripts.iter() {
            if let Some(value) = value.as_str() {
                let module = value.split(':').next().unwrap_or("").trim();
                if module_name(module) {
                    names.insert(module.to_owned());
                }
            }
        }
    }
    if names.is_empty() {
        let root =
            metadata.map(|metadata| metadata.path.rsplit_once('/').map_or("", |(root, _)| root));
        for input in &request.inputs {
            if !input.path.ends_with(".py") && !input.path.ends_with(".pyi") {
                continue;
            }
            let path = if let Some(root) = root {
                if root.is_empty() {
                    input.path.as_str()
                } else {
                    let Some(path) = input.path.strip_prefix(&format!("{root}/")) else {
                        continue;
                    };
                    path
                }
            } else {
                component_relative(request, &input.path)
            };
            let mut parts = path.split('/');
            let Some(mut first) = parts.next() else {
                continue;
            };
            if matches!(first, "src" | "lib" | "python" | "packages")
                && let Some(next) = parts.next()
            {
                first = next;
            }
            let candidate = first.strip_suffix(".py").unwrap_or(first);
            if candidate != "__init__" && module_name(candidate) {
                names.insert(candidate.to_owned());
            }
        }
    }
    names.into_iter().take(64).collect()
}
