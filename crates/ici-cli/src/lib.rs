pub mod commands;
pub mod events;
pub mod execute;
pub mod inputs;
pub mod planning;
pub mod registry;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DEFAULT_RESULT: &str = ".ici/rust-v1/result.json";
pub const DEFAULT_PAGE: &str = ".ici/rust-v1/result.html";

pub mod validation;

pub mod reuse;
