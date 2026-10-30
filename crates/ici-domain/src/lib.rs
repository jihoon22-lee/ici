//! Pure domain and wire contracts. No filesystem, process, or network access.
pub mod analysis;
pub mod catalog;
pub mod enums;
pub mod event;
pub mod finding;
pub mod identity;
pub mod result;
pub mod task;
pub mod tem;
pub mod validation;

pub use analysis::*;
pub use catalog::*;
pub use enums::*;
pub use event::*;
pub use finding::*;
pub use identity::*;
pub use result::*;
pub use task::*;
pub use validation::{DomainError, Validate};
