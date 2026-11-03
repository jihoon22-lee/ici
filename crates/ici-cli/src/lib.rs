pub mod artifact_build;
pub mod commands;
pub mod compilation_command;
pub mod coverage_regression;
pub mod cutover;
pub mod doctor;
pub mod events;
pub mod execute;
pub mod export_context;
pub mod inputs;
pub mod legacy;
pub mod legacy_context;
pub mod legacy_execute;
mod legacy_extra;
pub mod legacy_lines;
mod legacy_python_compat;
mod legacy_python_inventory;
mod legacy_scan_tests;
mod legacy_support;
mod legacy_toolchain;
mod plan_text;
pub mod planning;
pub mod preparation;
pub mod publish;
pub mod registry;
pub mod report;
pub mod run_text;
pub mod setup;
pub mod vcs;
pub mod wheel_inputs;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DEFAULT_RESULT: &str = ".ici/rust-v1/result.json";
pub const DEFAULT_PAGE: &str = ".ici/rust-v1/result.html";

pub mod validation;

pub mod reuse;

pub mod cpp;
pub mod cpp_validation;
pub mod files;
