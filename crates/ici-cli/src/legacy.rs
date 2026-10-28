//! Historical commands select common checks; they do not own a second analysis implementation.
use crate::registry::Invocation;
use ici_domain::{CheckDefinition, Profile};
use std::collections::{BTreeMap, BTreeSet};

pub fn engine(command: &str) -> Option<&'static str> {
    Some(match command {
        "line" => "line",
        "lint" => "lint",
        "test" => "test",
        "type" => "type",
        "complexity" => "complexity",
        "cognitive" => "cognitive",
        "cycle" => "cycle",
        "resource" => "resource",
        "security" => "security",
        "dead" => "dead",
        "dup" => "dup",
        "exception" => "exception",
        "python-compat" => "python_compat",
        "sanitize" => "sanitize",
        "thread-sanitize" => "thread_sanitize",
        "build" => "build",
        _ => return None,
    })
}
pub fn is_legacy(invocation: &Invocation) -> bool {
    invocation.command() == "verify" || engine(&invocation.command()).is_some()
}
pub fn select(
    invocation: &Invocation,
    project: &mut ici_config::ResolvedProject,
    checks: &[CheckDefinition],
) -> Vec<String> {
    let Some(family) = engine(&invocation.command()) else {
        return vec![];
    };
    let mut selected: BTreeSet<_> = checks
        .iter()
        .filter(|check| ici_application::plan::engine_name(&check.id) == Some(family))
        .map(|check| check.id.clone())
        .collect();
    loop {
        let needs: BTreeSet<_> = checks
            .iter()
            .filter(|check| selected.contains(&check.id))
            .flat_map(|check| check.needs.iter().cloned())
            .collect();
        let old = selected.len();
        selected.extend(
            checks
                .iter()
                .filter(|check| {
                    check
                        .provides
                        .iter()
                        .any(|provided| needs.contains(provided))
                })
                .map(|check| check.id.clone()),
        );
        if selected.len() == old {
            break;
        }
    }
    // Typing an individual command explicitly selects that family even when omitted from verify.
    for id in &selected {
        let mut settings = project
            .checks
            .get(id)
            .cloned()
            .unwrap_or(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: serde_json::json!({}),
            });
        settings.enabled = true;
        settings.settings["enabled"] = true.into();
        project.checks.insert(id.clone(), settings);
        for component in &mut project.components {
            if let Some(setting) = component.checks.get_mut(id) {
                setting.enabled = true;
                setting.settings["enabled"] = true.into();
            }
        }
    }
    selected.into_iter().collect()
}
pub fn individual_profile(invocation: &Invocation) -> Option<Profile> {
    engine(&invocation.command()).map(|_| Profile::Deep)
}

/// Estimated annotation evidence is independent of required external type checking.
pub fn annotation_check(
    invocation: &Invocation,
    project: &mut ici_config::ResolvedProject,
) -> Option<CheckDefinition> {
    if !is_legacy(invocation) {
        return None;
    }
    let mut definition = ici_domain::builtin_catalog()
        .checks
        .into_iter()
        .find(|check| check.id == "python.type")?;
    definition.id = "python.annotations".into();
    definition.title = "Estimated Python return annotation inspection".into();
    definition.tool = None;
    definition.required = false;
    definition.needs.clear();
    definition.provides.clear();
    if let Some(mut setting) = project.checks.get("python.type").cloned() {
        setting.required = false;
        setting.settings["required"] = false.into();
        project.checks.insert(definition.id.clone(), setting);
    }
    for component in &mut project.components {
        if let Some(mut setting) = component.checks.get("python.type").cloned() {
            setting.required = false;
            setting.settings["required"] = false.into();
            component.checks.insert(definition.id.clone(), setting);
        }
    }
    Some(definition)
}
