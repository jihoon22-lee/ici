//! Project-owned tests, coverage and runtime contracts. All processes use ici-execution.
pub mod artifacts;
pub mod coverage;
pub mod coverage_baseline;
pub mod cpp;
mod finding_details;
pub mod gcov;
mod gcov_format;
mod gcov_legacy_json;
pub mod gcov_text;
pub mod integration;
pub mod make_check;
pub mod model;
mod next_elf;
mod next_gcov;
pub mod next_sanitizer;
mod next_suite;
pub mod planning;
pub mod policy;
pub mod python;
pub mod python_coverage;
mod python_quality;
mod python_sanitize;
pub mod python_unittest;
pub mod runtime;
pub mod sanitizer;
mod sanitizer_suite;
mod standalone;
pub mod tem;
mod test_metrics;

pub use artifacts::{CapturedArtifacts, OwnedRun, capture_artifacts, capture_binary_headers};
pub use model::*;
pub use planning::{plan, ready_steps};
pub use python::{observe, observe_for};
pub use python_sanitize::{legacy_missing_scope, legacy_python_scope_missing};

#[derive(Clone, Debug, thiserror::Error)]
#[error("{0}")]
pub struct ValidationError(pub String);

pub fn attach_sources(
    plan: &mut ValidationPlan,
    coordinates: std::sync::Arc<ici_providers::SourceCoordinates>,
) {
    plan.source_coordinates = coordinates.clone();
    for runtime in &mut plan.runtime_plans {
        if let Some(child) = &mut runtime.plan {
            attach_sources(child, coordinates.clone());
        }
    }
}
