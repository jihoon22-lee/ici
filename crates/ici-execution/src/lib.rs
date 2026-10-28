//! Bounded process execution and storage, independent of provider interpretation.
#[cfg(not(target_os = "linux"))]
compile_error!("ici-execution currently supports Linux/WSL process containment only");
pub mod process;
pub mod redaction;
pub mod scheduler;
pub mod storage;
pub use process::{Cancellation, Cleanup, Runner, TaskOutcome};
pub use scheduler::{ScheduleError, ScheduleResult, Scheduler};
