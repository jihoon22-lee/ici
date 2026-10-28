//! Pure saved-result projections. Renderers never re-run checks or read project sources.
mod error;
mod json;
mod stored;
pub use error::{ReportError, Result};
pub use json::{canonical_json, script_json, strict_json};
pub use stored::{LegacyDocument, LegacyKind, StoredResult};
