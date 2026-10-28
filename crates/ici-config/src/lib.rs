//! Config discovery and composition. No subprocess, network, or implicit writes.
mod components;
mod discovery;
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
pub use model::*;
pub use resolve::{merge, resolve, resolve_with_environment};

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
