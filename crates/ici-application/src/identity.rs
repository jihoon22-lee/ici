use crate::{ApplicationError, Result};
use ici_config::ResolvedProject;
use ici_domain::{TaskSpec, builtin_catalog, canonical_json, sha256};
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    path::{Component, Path, PathBuf},
};

/// Team policy is independent of personal output/cache/tool locations and request selection.
pub fn policy_digest(project: &ResolvedProject) -> Result<String> {
    let catalog = builtin_catalog();
    let components:Vec<_>=project.components.iter().map(|component| {
        let checks:Vec<_>=catalog.checks.iter().filter(|check|component.languages.contains(&check.language) || (check.language=="integration" && !component.integrations.is_empty())).map(|check| {
            let (enabled,required)=crate::plan::configured_check(project,component,check);
            json!({"id":check.id,"enabled":enabled,"required":required})
        }).collect();
        json!({"id":component.id,"languages":component.languages,"checks":checks,"engines":component.settings.get("engines"),"integrations":component.integrations})
    }).collect();
    let suppressions:Vec<_>=project.suppressions.iter().map(|suppression|json!({"fingerprint":suppression.fingerprint,"rule":suppression.rule,"path":suppression.path,"component":suppression.component})).collect();
    digest(
        &json!({"namespace":"ici.rust.policy.v1","components":components,"suppressions":suppressions}),
    )
}
pub fn digest(value: &impl Serialize) -> Result<String> {
    Ok(sha256(
        canonical_json(value).map_err(|error| ApplicationError(error.to_string()))?,
    ))
}
/// A check scope identifies *which inputs and policy* were inspected, not their source bytes.
/// Hashing source contents here would prevent a newly fixed file from resolving old findings.
#[derive(Clone, Debug, Serialize)]
pub struct ScopeIdentity {
    pub check_id: String,
    pub component_id: String,
    pub language: String,
    pub files: Vec<String>,
    pub source_roots: Vec<String>,
    pub native_configuration: BTreeMap<String, String>,
    pub context: Value,
    pub invocation: Option<InvocationIdentity>,
}
impl ScopeIdentity {
    pub fn digest(&self) -> Result<String> {
        let mut scope = self.clone();
        scope.files.sort();
        scope.files.dedup();
        digest(&("ici.rust.check-scope.v1", scope))
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct InvocationIdentity {
    pub provider: String,
    pub cwd: String,
    pub argv: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub tool_digest: Option<String>,
    pub analysis_unit_ids: Vec<String>,
    pub variant: Option<String>,
}
impl InvocationIdentity {
    pub fn new(task: &TaskSpec, environment: &BTreeMap<String, String>, root: &Path) -> Self {
        let mut units = task.analysis_unit_ids.clone();
        units.sort();
        units.dedup();
        let path_lists = [
            "PATH",
            "PYTHONPATH",
            "LD_LIBRARY_PATH",
            "LIBRARY_PATH",
            "CPATH",
            "CPLUS_INCLUDE_PATH",
            "PKG_CONFIG_PATH",
            "NODE_PATH",
        ];
        let environment = environment
            .iter()
            .map(|(key, value)| {
                let value = if path_lists.contains(&key.as_str()) {
                    std::env::split_paths(value)
                        .map(|path| workspace_reference(&path.to_string_lossy(), root))
                        .collect::<Vec<_>>()
                        .join(":")
                } else {
                    workspace_reference(value, root)
                };
                (key.clone(), value)
            })
            .collect();
        Self {
            provider: task.provider.clone(),
            cwd: workspace_reference(&task.cwd, root),
            argv: task
                .argv
                .iter()
                .map(|value| argument(value, root))
                .collect(),
            environment,
            tool_digest: task.tool_digest.clone(),
            analysis_unit_ids: units,
            variant: task.variant.clone(),
        }
    }
}
pub fn workspace_reference(value: &str, root: &Path) -> String {
    let path = Path::new(value);
    if !path.is_absolute() {
        return value.into();
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    match normalized.strip_prefix(root) {
        Ok(relative) if relative.as_os_str().is_empty() => "${workspace}".into(),
        Ok(relative) => format!("${{workspace}}/{}", relative.to_string_lossy()),
        Err(_) => value.into(),
    }
}
fn argument(value: &str, root: &Path) -> String {
    if value.starts_with('-')
        && let Some((key, argument)) = value.split_once('=')
    {
        let normalized = if argument.len() > 1
            && argument.starts_with(['\'', '"'])
            && argument.as_bytes().first() == argument.as_bytes().last()
        {
            format!(
                "{}{}{}",
                &argument[..1],
                workspace_reference(&argument[1..argument.len() - 1], root),
                &argument[argument.len() - 1..]
            )
        } else {
            workspace_reference(argument, root)
        };
        return format!("{key}={normalized}");
    }
    for option in [
        "-isystem",
        "-idirafter",
        "-iquote",
        "-include",
        "-imacros",
        "-isysroot",
        "-I",
        "-L",
        "-F",
        "-B",
        "@",
    ] {
        if let Some(operand) = value.strip_prefix(option) {
            return format!("{option}{}", workspace_reference(operand, root));
        }
    }
    workspace_reference(value, root)
}
