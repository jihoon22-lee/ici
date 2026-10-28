//! Project-owned tests, coverage and runtime contracts. All processes use ici-execution.
pub mod artifacts;
pub mod coverage;
pub mod cpp;
pub mod gcov;
pub mod integration;
pub mod model;
pub mod planning;
pub mod policy;
pub mod python;
pub mod sanitizer;
pub mod tem;

pub use artifacts::{CapturedArtifacts, OwnedRun, capture_artifacts};
pub use model::*;
pub use planning::{plan, ready_steps};
pub use python::{observe, observe_for};

#[derive(Clone, Debug, thiserror::Error)]
#[error("{0}")]
pub struct ValidationError(pub String);

pub fn attach_sources(
    plan: &mut ValidationPlan,
    coordinates: std::sync::Arc<ici_providers::SourceCoordinates>,
) {
    plan.source_coordinates = coordinates;
}
