//! Native distribution, explicit setup and bounded system-trust HTTPS transport.
//! No analysis path calls these installation or network operations implicitly.
mod archive;
mod download;
mod files;
mod install;
mod json;
mod model;
mod notices;
mod tools;
pub mod transport;
pub use archive::{create_bundle, inspect_bundle};
pub use download::{download, download_verified};
pub use install::{activate, inspect_installation, install, rollback};
pub use model::{
    BuildInputs, BundleIdentity, BundleInput, Error, FileRecord, Installation, Manifest,
};
pub use notices::{LicenseInventory, LicenseSource, collect_licenses};
pub use tools::{ToolFiles, ToolManifest, ToolRecord, prepare_tool_pack};
