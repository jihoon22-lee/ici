//! External tool resolution and diagnostic adapters. Process execution stays in ici-execution.
pub mod diagnostics;
pub mod native_config;
pub mod plan;
pub mod resolver;
#[derive(Clone, Debug, thiserror::Error)]
#[error("{0}")]
pub struct ProviderError(pub String);
pub use coordinates::{SourceCoordinates, attach_sources, source_coordinates};
pub use diagnostics::{observe, observe_full};
pub use plan::{
    DiagnosticContext, NativeOptions, ProviderKind, ProviderPlan, ProviderRequest, plan,
    transform_compiler_argv,
};
mod coordinates;
