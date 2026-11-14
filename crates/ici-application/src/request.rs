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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub checks: Vec<String>,
    /// Historical engine commands (`ici verify`, `ici <engine>`) select checks by the frozen
    /// legacy descriptor profiles. Derived from the invocation, never part of the wire request.
    #[serde(skip)]
    pub legacy_profiles: bool,
    /// Under `legacy_profiles` on a Legacy-schema project: frozen `SanitizeEngine` keeps its
    /// Python scope without a selected Python source (`legacy_scope::legacy_python_sanitize_scope`,
    /// evaluated once by the caller). It puts `python.sanitize` in play for the root component
    /// whatever its declared languages; no other check reads it.
    #[serde(skip)]
    pub legacy_python_sanitize_scope: bool,
    /// Under `legacy_profiles` on a Legacy-schema project with a root component: the suites frozen
    /// `TestEngine._run_project_tests` attempts (`legacy_scope::legacy_test_scope`, evaluated once
    /// by the caller). It decides which of the root component's `python.test`/`python.coverage`
    /// and `cpp.test`/`cpp.coverage` are in play; no other check reads it.
    #[serde(skip)]
    pub legacy_test_scope: Option<crate::legacy_scope::LegacyTestScope>,
}
/// Frozen `{id!r}`: Python's repr of a str, single-quoted unless the text holds only `'`.
fn python_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}
impl RunRequest {
    pub fn scope<'a>(&self, project: &'a ResolvedProject) -> Result<Vec<&'a Component>> {
        for id in &self.components {
            if !project.components.iter().any(|c| &c.id == id) {
                return Err(ApplicationError(format!(
                    "no component {}; declared: {}",
                    python_repr(id),
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
            && (self.checks.is_empty() || self.checks.contains(&check.id))
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
    /// Frozen `uncovered_scope`: unselected required components (sorted), then each required
    /// component whose declared languages the language filter dropped, in model order.
    pub fn coverage_gaps(&self, project: &ResolvedProject, scope: &[&Component]) -> Vec<String> {
        let mut gaps = vec![];
        let mut unselected: Vec<_> = project
            .components
            .iter()
            .filter(|c| c.required && !scope.iter().any(|selected| selected.id == c.id))
            .map(|c| c.id.as_str())
            .collect();
        unselected.sort();
        for id in unselected {
            gaps.push(format!(
                "--require-full: required component '{id}' was not in the selected scope"
            ));
        }
        if !self.languages.is_empty() {
            for component in project.components.iter().filter(|c| c.required) {
                let missing: BTreeSet<_> = component
                    .languages
                    .iter()
                    .filter(|language| !self.languages.contains(language))
                    .collect();
                if !missing.is_empty() {
                    gaps.push(format!(
                        "--require-full: {} declares {} but the request selected only {}",
                        component.id,
                        missing.into_iter().cloned().collect::<Vec<_>>().join(", "),
                        self.languages.join(", ")
                    ));
                }
            }
        }
        gaps
    }
}
