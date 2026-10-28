//! Request selection, planning, and result policy. Execution and presentation are separate.
pub mod assemble;
pub mod baseline;
pub mod cache;
pub mod graph;
pub mod plan;
pub mod request;
pub mod snapshot;
pub mod verification;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ApplicationError(pub String);
pub type Result<T> = std::result::Result<T, ApplicationError>;
