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

mod analysis;
mod cycles;
mod source;
mod source_rules;
pub use analysis::CppAnalysisSession;
mod elf;
pub use elf::{
    AbiViolation, ElfFacts, ElfPolicy, abi_violations, analyze_elf, maximum_version, parse_readelf,
    version_key,
};

mod duplicates;
pub use duplicates::{cpp_directive_lines, tokenize_cpp_lines};
mod compiler;
pub use compiler::{
    CompilerBoundaryEvidence, CompilerConfigurationObservation, CompilerFunctionBoundary,
    IncludeEdge, IncludeTrace, analyze_compile_context, compiler_probe_task,
    parse_clang_function_boundaries, parse_include_trace, unused_function_consensus,
};
mod linker;
pub use linker::{
    DiscardedSection, LinkCommand, LinkerObjectEvidence, LinkerTargetEvidence, ObjectFacts,
    analyze_linker_dead, discarded_by_every_linking_target, discover_link_commands,
    eligible_discarded_symbol, linker_identity_task, parse_discarded_sections, parse_link_command,
    parse_object_facts, plan_linker_build, relink_task, unit_has_function_sections,
};

pub use compiler::{
    CompilerIncludeObservation, analyze_compiler_include_cycles, apply_compiler_boundaries,
};
