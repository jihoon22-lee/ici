use crate::{ApplicationError, Result};
use ici_config::{Component, ResolvedProject};
use ici_domain::{CheckDefinition, Profile};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RunRequest {
    pub components: Vec<String>,
    pub languages: Vec<String>,
    pub profile: Option<Profile>,
    pub require_full: bool,
    pub static_only: bool,
}
impl RunRequest {
    pub fn scope<'a>(&self, project: &'a ResolvedProject) -> Result<Vec<&'a Component>> {
        for id in &self.components {
            if !project.components.iter().any(|c| &c.id == id) {
                return Err(ApplicationError(format!(
                    "no component {id:?}; declared: {}",
                    project
                        .components
                        .iter()
                        .map(|c| c.id.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                )));
            }
        }
        for language in &self.languages {
            if !matches!(
                language.as_str(),
                "python" | "cpp" | "rust" | "javascript" | "typescript" | "integration"
            ) {
                return Err(ApplicationError(format!("unknown language {language:?}")));
            }
        }
        Ok(project
            .components
            .iter()
            .filter(|c| self.components.is_empty() || self.components.contains(&c.id))
            .collect())
    }
    pub fn wants(&self, check: &CheckDefinition) -> bool {
        (!self.static_only || check.is_static)
            && (self.languages.is_empty() || self.languages.contains(&check.language))
    }
    pub fn requested_languages(&self, scope: &[&Component]) -> Vec<String> {
        scope
            .iter()
            .flat_map(|c| &c.languages)
            .filter(|l| self.languages.is_empty() || self.languages.contains(l))
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }
    pub fn coverage_gaps(&self, project: &ResolvedProject, scope: &[&Component]) -> Vec<String> {
        let mut gaps = vec![];
        for component in project.components.iter().filter(|c| c.required) {
            if !scope.iter().any(|c| c.id == component.id) {
                gaps.push(format!(
                    "required component {} was not selected",
                    component.id
                ));
            } else if !self.languages.is_empty() {
                for language in &component.languages {
                    if !self.languages.contains(language) {
                        gaps.push(format!(
                            "{}: required language {language} was not selected",
                            component.id
                        ));
                    }
                }
            }
        }
        gaps
    }
}
