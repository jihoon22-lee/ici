//! Pure saved-result projections. Renderers never re-run checks or read project sources.
mod error;
mod json;
mod stored;
pub use error::{ReportError, Result};
pub use json::{canonical_json, script_json, strict_json};
pub use stored::{LegacyDocument, LegacyKind, StoredResult};
mod projection;
mod safe;
pub use projection::{
    ProjectionContext, legacy_engine, legacy_no_tests_collected, legacy_projection,
};
mod facets;
mod render;
mod view;
pub use render::{
    bounded_summary, github_annotations, render_console, render_html, render_json, render_markdown,
};
pub use view::{ConsoleGroupBy, RenderOptions};
mod sarif;
pub use sarif::{render_sarif, sarif_document};
mod migration;
pub use migration::migrate_report_payload;
mod issues;
pub use issues::{IssueGroup, IssueProjection, project_issue_groups};
mod navigation;
mod overview;
pub use navigation::source_link;

mod auxiliary;
mod junit_import;
mod lcov_import;
mod paths;
mod sarif_import;
pub use auxiliary::{
    AuxiliaryDocument, AuxiliarySource, ImportOptions, ImportProvenance, import_report,
    load_auxiliary,
};
