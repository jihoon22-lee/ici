//! Pure saved-result projections. Renderers never re-run checks or read project sources.
mod error;
mod json;
mod stored;
pub use error::{ReportError, Result};
pub use json::{
    STORED_INTEGER_RANGE, canonical_json, check_stored_integers, script_json, strict_json,
};
pub use stored::{LegacyDocument, LegacyKind, StoredResult};
mod projection;
mod safe;
pub use projection::{
    ProjectionContext, legacy_compiler_absent, legacy_engine, legacy_exit_code, legacy_no_sources,
    legacy_no_tests_collected, legacy_policy_refused, legacy_projection, legacy_refresh_suite,
    legacy_test_view, legacy_verdict, legacy_worst_status,
};
mod baseline_delta;
mod legacy_baseline;
pub use baseline_delta::{
    baseline_gate_failures, baseline_gated, legacy_baseline_comparison, severity_regressed,
};
pub use legacy_baseline::{
    COVERAGE_REGRESSION_RULE, LegacyRecord, coverage_regression_record, frozen_baseline_comparison,
    legacy_records,
};
mod legacy_console;
mod legacy_support;
pub use legacy_console::legacy_engine_console;
pub use legacy_support::{LegacySupportInputs, legacy_support_declarations, legacy_support_matrix};
mod facets;
mod render;
mod view;
pub use render::{
    bounded_summary, github_annotations, legacy_github_annotations, render_console, render_html,
    render_json, render_markdown,
};
pub use view::{ConsoleGroupBy, RenderOptions};
mod legacy_markdown;
pub use legacy_markdown::{MAX_SUMMARY_BYTES, bounded_summary_payload, legacy_summary_markdown};
mod legacy_sarif;
mod sarif;
pub use legacy_sarif::{
    MAX_LEGACY_SARIF_RESULTS, MAX_LEGACY_SARIF_RULES, legacy_sarif_document, render_legacy_sarif,
};
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
