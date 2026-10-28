//! C/C++ source and build evidence. Planning and source analysis never run project tools.
mod arguments;
mod build;
mod context;
mod input;
mod model;
mod qmake;
mod recorder;
mod replay;
mod replay_policy;
pub use model::*;

pub use arguments::{split_posix, split_windows};
pub use context::{is_translation_unit, load_compilation_context};

pub use replay::{diagnostic_warning_argument, replay_command, replay_environment};

pub use build::{BuildLease, finalize_qmake_capture, plan_build, prepare_owned_shadow};
pub use qmake::{qmake_capture_command, qmake_configure_capture_main};
pub use recorder::{record_compiler_invocation, recorder_main};
