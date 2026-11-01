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
/// Frozen `validate_engine_descriptors`: unique names, known dependencies available in every
/// profile of their dependents, single artifact producers, acyclic order, and consumed artifacts
/// produced by a transitive dependency (context artifacts excepted). Messages are the frozen ones.
pub fn validate_legacy_descriptors(descriptors: &[LegacyDescriptor]) -> Result<(), String> {
    use std::collections::{BTreeMap, BTreeSet};
    if descriptors.is_empty() {
        return Err("verification pipeline must contain at least one engine".into());
    }
    let by_name: BTreeMap<&str, &LegacyDescriptor> = descriptors
        .iter()
        .map(|item| (item.name.as_str(), item))
        .collect();
    if by_name.len() != descriptors.len() {
        return Err("verification pipeline contains duplicate engine names".into());
    }
    let order = |profile: &Profile| match profile {
        Profile::Fast => 0,
        Profile::Standard => 1,
        Profile::Deep => 2,
    };
    let mut producers: BTreeMap<&str, &str> = BTreeMap::new();
    for item in descriptors {
        for dependency in &item.dependencies {
            let Some(target) = by_name.get(dependency.as_str()) else {
                return Err(format!(
                    "engine {} depends on unknown engine {dependency}",
                    item.name
                ));
            };
            let mut missing: Vec<_> = item
                .profiles
                .iter()
                .filter(|profile| !target.profiles.contains(profile))
                .collect();
            if !missing.is_empty() {
                missing.sort_by_key(|profile| order(profile));
                let names: Vec<_> = missing
                    .iter()
                    .map(|profile| match profile {
                        Profile::Fast => "fast",
                        Profile::Standard => "standard",
                        Profile::Deep => "deep",
                    })
                    .collect();
                return Err(format!(
                    "engine {} dependency {dependency} is unavailable in {}",
                    item.name,
                    names.join(", ")
                ));
            }
        }
        for artifact in &item.produces {
            if let Some(previous) = producers.insert(artifact, &item.name) {
                return Err(format!(
                    "artifact {artifact} has multiple producers: {previous}, {}",
                    item.name
                ));
            }
        }
    }
    let mut done: BTreeSet<&str> = BTreeSet::new();
    while done.len() < descriptors.len() {
        let ready: Vec<&str> = descriptors
            .iter()
            .filter(|item| !done.contains(item.name.as_str()))
            .filter(|item| item.dependencies.iter().all(|d| done.contains(d.as_str())))
            .map(|item| item.name.as_str())
            .collect();
        if ready.is_empty() {
            let names: Vec<_> = descriptors
                .iter()
                .filter(|item| !done.contains(item.name.as_str()))
                .map(|item| item.name.as_str())
                .collect();
            return Err(format!(
                "verification pipeline contains a cycle: {}",
                names.join(", ")
            ));
        }
        done.extend(ready);
    }
    fn closure<'a>(
        name: &'a str,
        by_name: &BTreeMap<&'a str, &'a LegacyDescriptor>,
        found: &mut BTreeSet<&'a str>,
    ) {
        for dependency in &by_name[name].dependencies {
            if found.insert(dependency.as_str()) {
                closure(dependency.as_str(), by_name, found);
            }
        }
    }
    for item in descriptors {
        let mut dependencies = BTreeSet::new();
        closure(item.name.as_str(), &by_name, &mut dependencies);
        for artifact in &item.consumes {
            if matches!(
                artifact.as_str(),
                "analysis-context" | "capability-inventory" | "project-model"
            ) {
                continue;
            }
            let Some(producer) = producers.get(artifact.as_str()) else {
                return Err(format!(
                    "engine {} consumes unproduced artifact {artifact}",
                    item.name
                ));
            };
            if !dependencies.contains(producer) {
                return Err(format!(
                    "engine {} consumes {artifact} without depending on {producer}",
                    item.name
                ));
            }
        }
    }
    Ok(())
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
