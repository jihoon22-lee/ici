pub mod artifact_build;
pub mod commands;
pub mod compilation_command;
pub mod coverage_regression;
pub mod doctor;
pub mod events;
pub mod execute;
pub mod export_context;
pub mod inputs;
pub mod legacy;
pub mod legacy_execute;
pub mod planning;
pub mod preparation;
pub mod publish;
pub mod registry;
pub mod report;
pub mod setup;
pub mod vcs;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DEFAULT_RESULT: &str = ".ici/rust-v1/result.json";
pub const DEFAULT_PAGE: &str = ".ici/rust-v1/result.html";

pub mod validation;

pub mod reuse;

pub mod cpp;
pub mod cpp_validation;
pub mod files;
