pub mod commands;
pub mod planning;
pub mod registry;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DEFAULT_RESULT: &str = ".ici/next/result.json";
pub const DEFAULT_PAGE: &str = ".ici/next/result.html";
