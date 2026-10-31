//! Config discovery and composition. No subprocess, network, or implicit writes.
mod components;
mod discovery;
mod migration_report;
mod model;
mod native;
mod parse;
mod paths;
mod resolve;
mod schema;
mod schema_keys;
mod settings;
mod write;
pub use discovery::DEFAULT_EXCLUDES;
pub use migration_report::{
    LegacyConflict, LegacyLayer, LegacyMigrationReport, LegacyValue, legacy_migration_report,
};
pub use model::*;
pub use resolve::{
    inspect_configuration_with_environment, merge, resolve,
    resolve_legacy_command_with_environment, resolve_with_environment,
};

pub use schema_keys::{LEGACY_KEYS, LOCAL_KEYS, NEXT_KEYS};

pub use write::{
    ExplainedValue, MigrationPreview, Scaffold, explain, migrate_preview, scaffold,
    scaffold_with_environment, write_migration, write_scaffold,
};

/// Pure syntax/schema validation without discovery, tools, or writes.
/// Source scope and cross-file references are validated by `resolve`.
pub fn parse_document(
    path: &std::path::Path,
    text: &str,
    pyproject: bool,
) -> Result<serde_json::Value, ConfigError> {
    let parsed = parse::Parsed::parse(path, text.into(), pyproject, "root")?;
    let family = schema::family(&parsed)?;
    schema::validate(&parsed, family)?;
    Ok(parsed.value)
}

pub use native::discover_type_provider;

/// Inventory supporting runtime/test source inputs using the same bounded, no-follow discovery
/// and nested-workspace boundaries as configured source selection. Selection filters still decide
/// which production files a check measures.
pub fn component_source_inventory(
    project: &ResolvedProject,
    component: &Component,
) -> Result<Vec<String>, ConfigError> {
    let mut excludes: Vec<_> = DEFAULT_EXCLUDES
        .iter()
        .map(|pattern| (*pattern).to_owned())
        .collect();
    excludes.extend(component.exclude.iter().cloned());
    discovery::scan(&project.root, &component.root, &excludes, &component.origin)
        .map(|scan| scan.files)
}

/// Match component-relative source globs through the same POSIX/literal-separator policy as
/// configured source discovery. Callers supply an already bounded inventory.
pub fn matching_source_paths(
    component: &Component,
    candidates: &[String],
    patterns: &[String],
) -> Result<Vec<String>, ConfigError> {
    let matcher = discovery::matcher(patterns, &component.origin)?;
    let prefix = format!("{}/", component.root);
    Ok(candidates
        .iter()
        .filter(|path| {
            let relative = if component.root == "." {
                path.as_str()
            } else {
                path.strip_prefix(&prefix).unwrap_or(path)
            };
            matcher.is_match(relative)
        })
        .cloned()
        .collect())
}
