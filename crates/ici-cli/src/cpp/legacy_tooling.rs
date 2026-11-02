//! Frozen `LintEngine` clang-tidy / Clazy targets (analysis/_clang_tidy.py, analysis/_clazy.py)
//! over the native `cpp.tidy` provider runs of a legacy `ici verify` with an exact context.
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement, TaskKind, TaskSpec};
use ici_execution::{
    TaskOutcome,
    process::{Cancellation, Runner},
};
use regex::Regex;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::{
    collections::BTreeMap,
    path::Path,
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};

/// Frozen `_clang_tidy._TIMEOUT_SECONDS` / `_clazy._TIMEOUT_SECONDS`: the per-unit limit.
const UNIT_TIMEOUT: Duration = Duration::from_secs(120);
/// Frozen `_GLOBAL_TIMEOUT_SECONDS` of both tools: one budget per tool over its selected units.
const GLOBAL_BUDGET: Duration = Duration::from_secs(600);
static UNIT_OVERRIDE_MILLIS: AtomicU64 = AtomicU64::new(u64::MAX);
static GLOBAL_OVERRIDE_MILLIS: AtomicU64 = AtomicU64::new(u64::MAX);

fn millis(value: Option<Duration>) -> u64 {
    value.map_or(u64::MAX, |value| {
        u64::try_from(value.as_millis()).unwrap_or(u64::MAX - 1)
    })
}

/// Test hook: replaces the legacy clang-tidy / Clazy per-unit timeout and global budget for
/// this process (`None` restores the frozen 120 s / 600 s). The command line cannot reach it.
#[doc(hidden)]
pub fn set_legacy_tooling_budget_for_tests(unit: Option<Duration>, global: Option<Duration>) {
    UNIT_OVERRIDE_MILLIS.store(millis(unit), Ordering::SeqCst);
    GLOBAL_OVERRIDE_MILLIS.store(millis(global), Ordering::SeqCst);
}

fn overridden(value: &AtomicU64, default: Duration) -> Duration {
    match value.load(Ordering::SeqCst) {
        u64::MAX => default,
        millis => Duration::from_millis(millis),
    }
}

/// The per-unit timeout of one legacy clang tool run.
pub fn unit_timeout() -> Duration {
    overridden(&UNIT_OVERRIDE_MILLIS, UNIT_TIMEOUT)
}

/// The global budget of one legacy clang tool over all of its units.
pub fn global_budget() -> Duration {
    overridden(&GLOBAL_OVERRIDE_MILLIS, GLOBAL_BUDGET)
}

/// One tool run on one recorded translation unit, projected as frozen targets.
pub struct LegacyToolRun {
    pub task: String,
    pub targets: Vec<InspectionTarget>,
    pub error: Option<String>,
}

fn tool(clazy: bool) -> &'static str {
    if clazy { "clazy" } else { "clang-tidy" }
}

/// Frozen `core/toolchain.py` capability probe of one resolved clang tool: `<tool> --version`
/// (5 s, 65 536 output bytes) must exit 0 untruncated and report a parseable version
/// (`parse_tool_version`: clazy needs its own `clazy[-standalone] [version] N.N` banner,
/// clang-tidy a compiler or generic dotted version). A tool that fails the probe is the frozen
/// `<Tool>Unavailable`, never a per-source execution error.
///
/// Known frozen defect, corrected: frozen probes with the full environment and then runs the
/// tool with a replaced minimal one, so a tool needing `LD_LIBRARY_PATH` passes the probe and
/// fails every run. The probe here uses exactly the run tasks' environment (inherited runner
/// environment plus the replay overlay).
pub fn legacy_capability_probe(
    executable: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    clazy: bool,
) -> bool {
    legacy_version_banner(executable, cwd, environment, tool(clazy)).is_some()
}

/// The frozen capability `version` of one probed tool (`<tool> --version` in the run
/// environment): the first output line matching its `parse_tool_version` patterns, at most 200
/// characters, or `None` when the probe fails or reports no version.
pub fn legacy_version_banner(
    executable: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    name: &str,
) -> Option<String> {
    let mut task = TaskSpec::command(
        format!("legacy-lint.{name}.version"),
        name,
        vec![
            executable.to_string_lossy().into_owned(),
            "--version".into(),
        ],
        cwd.to_string_lossy(),
    );
    task.kind = TaskKind::Probe;
    task.timeout_seconds = Some(5.0);
    task.output_limit_bytes = Some(65_536);
    task.env_overlay = ici_cpp::replay_environment();
    let outcome =
        Runner::with_environment(environment.clone()).run(&task, &Cancellation::default());
    if outcome.timed_out
        || outcome.truncated
        || outcome.error.is_some()
        || outcome.exit_code != Some(0)
    {
        return None;
    }
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&outcome.stdout),
        String::from_utf8_lossy(&outcome.stderr)
    );
    version_line(name == "clazy", &text)
}

/// Frozen `parse_tool_version`: pattern by pattern, the first line holding a version (clazy only
/// its own banner; compilers and clang tools a compiler banner, then any dotted version).
fn version_line(clazy: bool, text: &str) -> Option<String> {
    let patterns: &[&str] = if clazy {
        &[r"(?i)\bclazy(?:-standalone)?\s+(?:version\s*:?\s*)?\d+(?:\.\d+)+\b"]
    } else {
        &[
            r"(?i)\b(?:Apple\s+|Ubuntu\s+)?(?:gcc|g\+\+|clang(?:\+\+)?)(?:\s+version)?(?:\s+\([^)]*\))?\s+\d+(?:\.\d+)+",
            // `(?<![A-Za-z0-9_])\d+(?:\.\d+)+`: existence is the same with a consumed boundary.
            r"(?:^|[^A-Za-z0-9_])\d+(?:\.\d+)+",
        ]
    };
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect();
    patterns.iter().find_map(|pattern| {
        let pattern = Regex::new(pattern).expect("constant regex");
        lines
            .iter()
            .find(|line| pattern.is_match(line))
            .map(|line| line.chars().take(200).collect())
    })
}

/// Frozen clazy `_failure_summary`: diagnostic kinds, processing error and output presence.
fn clazy_failure_summary(outcome: &TaskOutcome) -> String {
    let location =
        Regex::new(r"^.+?:[1-9]\d*(?::[1-9]\d*)?:\s*(fatal error|error|warning|note|remark):")
            .expect("constant regex");
    let prefix = Regex::new(
        r"^(?:(?:clang(?:\+\+)?|clazy(?:-standalone)?)(?:-[0-9.]+)?:\s*)?(fatal error|error|warning|note|remark):",
    )
    .expect("constant regex");
    let processing = Regex::new(r"^Error while processing(?:\s|$)").expect("constant regex");
    let kinds = ["fatal error", "error", "warning", "note", "remark"];
    let mut counts = [0usize; 5];
    let (mut processing_error, mut has_output) = (false, false);
    for stream in [&outcome.stdout, &outcome.stderr] {
        let text = String::from_utf8_lossy(stream);
        has_output |= !text.trim().is_empty();
        for line in text.lines().map(str::trim) {
            if let Some(kind) = location
                .captures(line)
                .or_else(|| prefix.captures(line))
                .map(|captures| captures[1].to_owned())
            {
                counts[kinds.iter().position(|item| *item == kind).expect("kind")] += 1;
            }
            processing_error |= processing.is_match(line);
        }
    }
    let flag = |value: bool| if value { "yes" } else { "no" };
    format!(
        "fatal={}, error={}, warning={}, note={}, remark={}, processing_error={}, output={}",
        counts[0],
        counts[1],
        counts[2],
        counts[3],
        counts[4],
        flag(processing_error),
        flag(has_output)
    )
}

/// Frozen `_process_error` of one clang-tidy / clazy run.
fn process_error(clazy: bool, outcome: &TaskOutcome, source: &str) -> Option<String> {
    let name = tool(clazy);
    if outcome.timed_out {
        return Some(format!("{name} timed out: {source}"));
    }
    if outcome.truncated {
        return Some(format!("{name} output was truncated: {source}"));
    }
    match outcome.exit_code {
        Some(0) => None,
        Some(code) if code > 0 && clazy => Some(format!(
            "clazy failed with exit code {code} ({}): {}",
            clazy_failure_summary(outcome),
            source.chars().take(256).collect::<String>()
        )),
        Some(code) if code > 0 => {
            Some(format!("clang-tidy failed with exit code {code}: {source}"))
        }
        _ => Some(format!("{name} terminated unexpectedly: {source}")),
    }
}

fn target(path: &str, name: String, status: &str, message: String) -> InspectionTarget {
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = name;
    target.status = status.into();
    target.message = message;
    target
}

/// Frozen targets of one run: an `<Tool>ExecutionError`, one target per primary diagnostic
/// (`ClangTidy:` / `ClangAnalyzer:` / `Clazy:` and `ClazyNote:` notes), or the clean
/// `<Tool>:<configuration digest>` PASS.
pub fn legacy_tool_run(
    clazy: bool,
    source: &str,
    digest: &str,
    outcome: &TaskOutcome,
    output: &AnalysisOutput,
) -> LegacyToolRun {
    let label = if clazy { "Clazy" } else { "ClangTidy" };
    let failure = |message: String| LegacyToolRun {
        task: outcome.task_id.clone(),
        targets: vec![target(
            source,
            format!("{label}ExecutionError"),
            "ERROR",
            message.clone(),
        )],
        error: Some(message),
    };
    if let Some(message) = process_error(clazy, outcome, source) {
        return failure(message);
    }
    if !output.observation.evidence_is_complete() {
        // Frozen clazy names the parser error; clang-tidy only the source.
        return failure(if clazy {
            format!(
                "clazy output was not parseable: {source}: {}",
                clazy_parse_error(outcome, output)
            )
        } else {
            format!("clang-tidy output was not parseable: {source}")
        });
    }
    let details = output.extra.get("finding_details");
    let mut targets = vec![];
    for finding in &output.observation.findings {
        let rule = finding.native_rule_id.as_str();
        // Frozen `_select_clazy_rule`: compiler `-W` warnings in clazy output are not clazy's.
        if clazy && rule.starts_with("-W") && !rule.starts_with("-Wclazy-") {
            continue;
        }
        let Some(message) = details
            .and_then(|details| details.get(&finding.fingerprint))
            .and_then(|detail| detail.get("legacy_message"))
            .and_then(Value::as_str)
        else {
            return failure(format!(
                "{} output was not parseable: {source}",
                tool(clazy)
            ));
        };
        let kind = message.split(": ").next().unwrap_or_default();
        let status = if matches!(kind, "error" | "fatal error") {
            "FAIL"
        } else {
            "WARN"
        };
        let (name, note) = if clazy {
            let rule = format!("clazy-{}", rule.trim_start_matches("-Wclazy-"));
            (format!("Clazy:{rule}"), Some(format!("ClazyNote:{rule}")))
        } else if rule.starts_with("clang-analyzer-") {
            (format!("ClangAnalyzer:{rule}"), None)
        } else {
            (format!("ClangTidy:{rule}"), None)
        };
        let mut primary = target(&finding.primary_location.path, name, status, message.into());
        primary.start_line = finding.primary_location.start_line;
        primary.start_column = finding.primary_location.start_column;
        targets.push(primary);
        // Frozen clang-tidy notes stay related diagnostics; clazy notes are their own targets.
        if let Some(note) = note {
            for related in &finding.related_locations {
                if !related.label.starts_with("note: ") {
                    continue;
                }
                let mut item = target(&related.path, note.clone(), "WARN", related.label.clone());
                item.start_line = related.start_line;
                item.start_column = related.start_column;
                targets.push(item);
            }
        }
    }
    if targets.is_empty() {
        targets.push(target(
            source,
            format!("{label}:{digest}"),
            "PASS",
            format!("{} completed without diagnostics.", tool(clazy)),
        ));
    }
    LegacyToolRun {
        task: outcome.task_id.clone(),
        targets,
        error: None,
    }
}

/// Frozen clazy notes are diagnostics of their own (`ClazyNote:<rule>` findings with the
/// note's location and message); the primary clazy finding has no related locations.
pub fn legacy_clazy_note_findings(output: &mut AnalysisOutput) {
    let mut notes = vec![];
    for finding in &mut output.observation.findings {
        let rule = format!(
            "clazy-{}",
            finding.native_rule_id.trim_start_matches("-Wclazy-")
        );
        let (kept, moved): (Vec<_>, Vec<_>) = std::mem::take(&mut finding.related_locations)
            .into_iter()
            .partition(|related| !related.label.starts_with("note: "));
        finding.related_locations = kept;
        for related in moved {
            let mut note = finding.clone();
            note.fingerprint = ici_domain::sha256(format!(
                "{}\0{}:{}:{:?}:{}",
                finding.fingerprint,
                related.path,
                related.start_line,
                related.start_column,
                related.label
            ));
            note.message.clone_from(&related.label);
            note.primary_location = related.clone();
            note.primary_location.label = String::new();
            note.related_locations = vec![];
            notes.push((finding.fingerprint.clone(), note, rule.clone()));
        }
    }
    let Some(details) = output
        .extra
        .get_mut("finding_details")
        .and_then(Value::as_object_mut)
    else {
        return;
    };
    let mut counts = BTreeMap::<(String, String), usize>::new();
    for (_, note, rule) in &notes {
        *counts
            .entry((note.primary_location.path.clone(), rule.clone()))
            .or_default() += 1;
    }
    let primaries: BTreeMap<String, Value> = output
        .observation
        .findings
        .iter()
        .map(|finding| {
            (
                finding.fingerprint.clone(),
                json!(finding.related_locations.iter().map(|span| json!({"path":span.path,"start_line":span.start_line,"start_column":span.start_column,"end_line":span.end_line,"end_column":span.end_column,"label":span.label})).collect::<Vec<_>>()),
            )
        })
        .collect();
    for (primary, _, _) in &notes {
        if let Some(detail) = details.get_mut(primary) {
            detail["related_locations"] = primaries[primary].clone();
        }
    }
    for (primary, note, rule) in notes {
        let mut detail = details.get(&primary).cloned().unwrap_or_else(|| json!({}));
        detail["legacy_message"] = json!(note.message);
        detail["legacy_label"] = json!(if counts
            [&(note.primary_location.path.clone(), rule.clone())]
            == 1
        {
            format!("ClazyNote:{rule}")
        } else {
            String::new()
        });
        detail["legacy_severity"] = json!("medium");
        detail["remediation"] = json!("");
        detail["fixes"] = json!([]);
        detail["related_locations"] = json!([]);
        details.insert(note.fingerprint.clone(), detail);
        output.observation.findings.push(note);
    }
}

/// The frozen lint tooling block: per tool (clang-tidy, then clazy) its `<Tool>Unavailable`
/// targets, then the planned run: a project-level stop, or the production sources without a
/// recorded command, then each selected unit in frozen order (a budget stop, the error that
/// replaced its run, or its run's targets), with the tool errors and warnings the LintEngine
/// summary reads.
pub fn legacy_tooling_projection(
    output: &mut AnalysisOutput,
    runs: Vec<LegacyToolRun>,
    missing: &[(&str, String, bool)],
    uncovered: &[String],
    plans: &[ToolPlan],
    stops: &BTreeMap<bool, BudgetStop>,
) {
    let mut runs: BTreeMap<String, LegacyToolRun> = runs
        .into_iter()
        .map(|run| (run.task.clone(), run))
        .collect();
    let mut targets = vec![];
    let mut errors = vec![];
    for clazy in [false, true] {
        let label = if clazy {
            "ClazyUnavailable"
        } else {
            "ClangTidyUnavailable"
        };
        let prefix = if clazy { "Clazy" } else { "ClangTidy" };
        for (_, message, required) in missing.iter().filter(|(name, _, _)| *name == label) {
            targets.push(target(
                ".",
                label.into(),
                if *required { "ERROR" } else { "WARN" },
                message.clone(),
            ));
            if *required {
                errors.push(message.clone());
            }
        }
        let Some(plan) = plans.iter().find(|plan| plan.clazy == clazy) else {
            continue;
        };
        if let Some((name, message)) = &plan.terminal {
            targets.push(target(".", name.clone(), "ERROR", message.clone()));
            errors.push(message.clone());
            continue;
        }
        // Frozen `_selected_context_units`: each production source without a recorded command
        // is an ERROR before the replayed units.
        if !uncovered.is_empty() {
            errors.push(format!(
                "{} context is missing {} production source command(s)",
                tool(clazy),
                uncovered.len()
            ));
            targets.extend(uncovered.iter().map(|source| {
                target(
                    source,
                    format!("{prefix}ContextMissing"),
                    "ERROR",
                    "No exact translation-unit command covers this production source.".into(),
                )
            }));
        }
        if plan.units.is_empty() {
            let message = format!(
                "{} context contains no replayable production units",
                tool(clazy)
            );
            targets.push(target(
                ".",
                format!("{prefix}ContextError"),
                "ERROR",
                message.clone(),
            ));
            errors.push(message);
            continue;
        }
        for (index, unit) in plan.units.iter().enumerate() {
            if let Some(stop) = stops.get(&clazy).filter(|stop| stop.index == index) {
                targets.push(target(
                    &unit.source,
                    format!("{prefix}BudgetError"),
                    "ERROR",
                    stop.message.clone(),
                ));
                errors.push(stop.message.clone());
                break;
            }
            match &unit.step {
                UnitStep::Error { name, message } => {
                    targets.push(target(&unit.source, name.clone(), "ERROR", message.clone()));
                    errors.push(message.clone());
                }
                UnitStep::Run(task) => {
                    if let Some(run) = runs.remove(task) {
                        targets.extend(run.targets);
                        errors.extend(run.error);
                    }
                }
            }
        }
    }
    let warnings: Vec<&String> = missing
        .iter()
        .filter(|(_, _, required)| !*required)
        .map(|(_, message, _)| message)
        .collect();
    output.extra["legacy_tool_warnings"] = json!(warnings);
    output.extra["legacy_tool_errors"] = json!(errors);
    if errors.is_empty() && !warnings.is_empty() {
        // Frozen LintEngine: an unavailable optional tool leaves the lint verdict estimated.
        let mut available = Measurement::measured("clang_tooling_available", 0.0);
        available.evidence = ici_domain::EvidenceLevel::Estimated;
        output.observation.measurements.push(available);
    }
    let count = |status: &str| targets.iter().any(|target| target.status == status);
    output.status = if !errors.is_empty() {
        "ERROR"
    } else if count("FAIL") {
        "FAIL"
    } else if count("WARN") || !warnings.is_empty() {
        "WARN"
    } else {
        "PASS"
    }
    .into();
    output.summary = if errors.is_empty() {
        warnings
            .iter()
            .map(|message| message.as_str())
            .collect::<Vec<_>>()
            .join("; ")
    } else {
        // Frozen LintEngine `"; ".join(tool_errors[:3])`.
        errors
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ")
    };
    output.targets = targets;
}

/// Frozen `_validate_config_file` error type names of `Path.resolve(strict=True)` on Python 3.10.
fn python_os_error(error: &std::io::Error) -> &'static str {
    use std::io::ErrorKind;
    match error.kind() {
        ErrorKind::NotFound => "FileNotFoundError",
        ErrorKind::PermissionDenied => "PermissionError",
        ErrorKind::NotADirectory => "NotADirectoryError",
        // Python 3.10 `resolve(strict=True)` reports a symlink loop (Linux ELOOP 40) as RuntimeError.
        _ if error.raw_os_error() == Some(40) => "RuntimeError",
        _ => "OSError",
    }
}

/// Frozen `_validate_config_file`: the resolved clang-tidy configuration (a symlink resolves to
/// its target) or the frozen refusal message.
fn validate_config_file(root: &Path, value: &Path, explicit: bool) -> Result<PathBuf, String> {
    let path = value.canonicalize().map_err(|error| {
        format!(
            "{} clang-tidy config could not be resolved: {}",
            if explicit { "Configured" } else { "Discovered" },
            python_os_error(&error)
        )
    })?;
    if !path.starts_with(root) {
        return Err("clang-tidy config resolves outside the project root".into());
    }
    let metadata = std::fs::metadata(&path).map_err(|error| {
        format!(
            "{} clang-tidy config could not be resolved: {}",
            if explicit { "Configured" } else { "Discovered" },
            python_os_error(&error)
        )
    })?;
    if !metadata.is_file() {
        return Err("clang-tidy config is not a regular file".into());
    }
    if metadata.len() > 1_048_576 {
        return Err("clang-tidy config exceeds the 1 MiB safety limit".into());
    }
    let content = std::fs::read(&path).map_err(|error| {
        format!(
            "clang-tidy config could not be read: {}",
            python_os_error(&error)
        )
    })?;
    if content.contains(&0) {
        return Err("clang-tidy config contains a null byte".into());
    }
    // Frozen `_UNSAFE_CONFIG_ARGUMENT_RE` over bytes: ASCII word boundaries, case-insensitive.
    let unsafe_argument =
        regex::bytes::Regex::new(r"(?i-u)\b(?:ExtraArgs(?:Before)?|InheritParentConfig)\b")
            .expect("constant regex");
    if unsafe_argument.is_match(&content) {
        return Err(
            "clang-tidy config may not add compiler arguments or inherit parent config".into(),
        );
    }
    Ok(path)
}

/// Frozen `_explicit_config`: `clang_tidy_config` relative to the resolved project root.
pub fn legacy_explicit_config(root: &Path, value: Option<&str>) -> Result<Option<PathBuf>, String> {
    let Some(value) = value.filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let lexical = Path::new(value);
    let path = if lexical.is_absolute() {
        lexical.to_path_buf()
    } else {
        root.join(lexical)
    };
    validate_config_file(root, &path, true).map(Some)
}

/// Frozen `_discovered_config`: the nearest `.clang-tidy` (present or a symlink) from the
/// resolved source's directory up to the project root.
pub fn legacy_discovered_config(root: &Path, source: &Path) -> Result<Option<PathBuf>, String> {
    let mut current = source.parent().map(Path::to_path_buf);
    while let Some(directory) = current {
        if !directory.starts_with(root) {
            break;
        }
        let candidate = directory.join(".clang-tidy");
        if candidate.symlink_metadata().is_ok() {
            return validate_config_file(root, &candidate, false).map(Some);
        }
        if directory == root {
            break;
        }
        current = directory.parent().map(Path::to_path_buf);
    }
    Ok(None)
}

/// What frozen does with one selected unit of one clang tool.
#[derive(Clone, Debug)]
pub enum UnitStep {
    /// The unit's tool task.
    Run(String),
    /// The `<Tool><kind>` error target at the unit's source and its LintEngine tool error.
    Error { name: String, message: String },
}

#[derive(Clone, Debug)]
pub struct ToolUnit {
    pub source: String,
    pub step: UnitStep,
}

/// The frozen run of one clang tool over its selected units (`run_clang_tidy` / `run_clazy`).
#[derive(Clone, Debug)]
pub struct ToolPlan {
    pub clazy: bool,
    /// A project-level error that stops this tool before unit selection (`<Tool>ContextError`
    /// for context ingestion errors, `ClangTidyConfigError` for the configured config file).
    pub terminal: Option<(String, String)>,
    pub units: Vec<ToolUnit>,
}

/// The unit at which one tool's global budget ran out.
#[derive(Clone, Debug)]
pub struct BudgetStop {
    pub index: usize,
    pub message: String,
}

/// Frozen `_execute_selected_units`: per tool, units in frozen order under one deadline; before
/// each unit the remaining budget is checked and a spent budget stops the tool at that unit. A
/// unit that runs gets `min(120 s, remaining)`.
pub fn run_budgeted(
    plans: &[ToolPlan],
    tasks: &BTreeMap<String, TaskSpec>,
    mut run: impl FnMut(TaskSpec) -> std::result::Result<TaskOutcome, String>,
) -> std::result::Result<(Vec<TaskOutcome>, BTreeMap<bool, BudgetStop>), String> {
    let mut outcomes = vec![];
    let mut stops = BTreeMap::new();
    for plan in plans {
        let deadline = std::time::Instant::now() + global_budget();
        for (index, unit) in plan.units.iter().enumerate() {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                stops.insert(
                    plan.clazy,
                    BudgetStop {
                        index,
                        message: format!(
                            "{} global time budget left {} unit(s) unexamined",
                            tool(plan.clazy),
                            plan.units.len() - index
                        ),
                    },
                );
                break;
            }
            if let UnitStep::Run(id) = &unit.step
                && let Some(task) = tasks.get(id)
            {
                let mut task = task.clone();
                task.timeout_seconds = Some(remaining.min(unit_timeout()).as_secs_f64());
                outcomes.push(run(task)?);
            }
        }
    }
    Ok((outcomes, stops))
}

/// Frozen `gcc_standard_library_projection` error code and message for the selected driver's
/// C++ and C include searches, in frozen order (the C++ search first).
pub fn legacy_gcc_projection_error(
    cpp: &TaskOutcome,
    c: &TaskOutcome,
    native: &ici_cpp::CppError,
) -> (String, String) {
    for outcome in [cpp, c] {
        if outcome.timed_out {
            return (
                "gcc-include-probe-timeout".into(),
                "GCC include search timed out.".into(),
            );
        }
        if outcome.truncated {
            return (
                "gcc-include-probe-truncated".into(),
                "GCC include search output was truncated.".into(),
            );
        }
        if outcome.exit_code != Some(0) {
            return (
                "gcc-include-probe-failed".into(),
                "GCC include search failed.".into(),
            );
        }
    }
    if native.code == "gcc-stdlib-unresolved" {
        return (
            native.code.clone(),
            "GCC include search did not identify C++ standard-library directories.".into(),
        );
    }
    (
        "gcc-include-probe-unparseable".into(),
        native.message.clone(),
    )
}

/// Frozen `parse_clazy_diagnostics` error text of a clazy run whose output is not parseable:
/// the bounds checks first, then the first line the parser cannot read.
fn clazy_parse_error(outcome: &TaskOutcome, output: &AnalysisOutput) -> String {
    let stdout = String::from_utf8_lossy(&outcome.stdout);
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    if stdout.chars().count() + stderr.chars().count() > 1_000_000 {
        return "clazy diagnostic output exceeds the bounded size".into();
    }
    if stdout.contains('\0') || stderr.contains('\0') {
        return "clazy diagnostic output contains a null byte".into();
    }
    let native = output
        .observation
        .limitations
        .first()
        .cloned()
        .unwrap_or_default();
    match native.split_once("unrecognized C++ diagnostic output: ") {
        Some((_, line)) => format!(
            "unrecognized clazy output line: {}",
            ici_analysis::unicode::python_repr(line.trim())
        ),
        None => native,
    }
}
