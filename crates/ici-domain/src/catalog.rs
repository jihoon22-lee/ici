//! Frozen check/descriptor metadata, consumed without probing or installing tools.
use crate::Profile;
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CheckDefinition {
    pub id: String,
    pub title: String,
    pub language: String,
    pub tool: Option<String>,
    pub required: bool,
    pub profiles: Vec<Profile>,
    pub needs: Vec<String>,
    pub provides: Vec<String>,
    #[serde(rename = "static")]
    pub is_static: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LegacyDescriptor {
    pub name: String,
    pub factory_name: String,
    pub dependencies: Vec<String>,
    pub produces: Vec<String>,
    pub consumes: Vec<String>,
    pub profiles: Vec<Profile>,
    pub execution: String,
    pub build_variant: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Catalog {
    pub oracle_source: String,
    pub checks: Vec<CheckDefinition>,
    pub legacy: Vec<LegacyDescriptor>,
}
pub fn builtin_catalog() -> Catalog {
    serde_json::from_str(include_str!("../data/registry.json"))
        .expect("frozen registry validated by catalog tests")
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_current_registry_keeps_runtime_separate() {
        let registry = builtin_catalog();
        assert_eq!(registry.checks.len(), 36);
        assert_eq!(registry.legacy.len(), 19);
        assert!(
            registry
                .checks
                .iter()
                .filter(|c| c.id.ends_with(".test") || c.id == "integration")
                .all(|c| !c.is_static)
        );
        assert_eq!(
            registry
                .checks
                .iter()
                .map(|c| &c.id)
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
            36
        );
    }
}
