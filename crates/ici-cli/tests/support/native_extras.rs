#![allow(dead_code)]
//! Native `extra` keys the legacy engine view keeps beside the frozen ones, shared by the legacy
//! replays (`#[path = "support/native_extras.rs"] mod native_extras;`). Each is native evidence
//! frozen v0.13.0 did not write; the historical readers ignore unknown `extra` keys
//! (python.json `capability:legacy-command-config-result-projection` difference_reason).
/// Native check evidence: the attributed native checks, per-target scopes, the finding identity
/// map, the parser/source identity of the snapshot and the per-reason exclusion counts.
pub const NATIVE_EXTRAS: [&str; 10] = [
    "excluded_source_counts",
    "finding_identity_map",
    "heuristic_edges",
    "import_edges",
    "module_index",
    "native_checks",
    "parser_identity",
    "reported_cycles",
    "source_encodings",
    "target_scopes",
];
/// Native Python analysis counters the legacy view keeps (measured by the native checks):
/// files read by dead-code analysis, its Python issue count, the resource issue count, the
/// duplicated/total code lines behind `duplication_pct`; and the native test diagnostics
/// (`no_tests_collected` names the languages of the frozen no-tests FAIL, the aggregation
/// limitation says why the native test evidence is incomplete).
pub const NATIVE_COUNTERS: [&str; 7] = [
    "duplicated_lines",
    "files_checked",
    "no_tests_collected",
    "python_issues",
    "resource_issues",
    "test_aggregation_limitation",
    "total_code_lines",
];
/// Native execution evidence of a check that ran a project process (the Python ResourceWarning
/// pytest run of `sanitize`): its steps, their argv/exit records and the parsed warnings.
pub const NATIVE_STEPS: [&str; 4] = [
    "conditional_steps",
    "optional_steps",
    "python_resource_warnings",
    "steps",
];
