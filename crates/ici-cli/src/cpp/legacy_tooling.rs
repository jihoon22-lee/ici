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
    /// The frozen `ToolEvidence` record of the run (`_record_process`, its `error` the run's
    /// execution or parse error).
    pub evidence: Value,
}

/// One clang tool frozen reports unavailable: its `<Tool>Unavailable` label, message, whether
/// the tool is required, and the frozen `ToolEvidence` without a command.
pub type MissingTool = (&'static str, String, bool, Value);

/// Frozen `_validated_context` / `_ready_tool` evidence of an unavailable clang tool: no command
/// was executed; `path` and `version` are the resolved tool and its probed banner when known.
pub fn missing_tool_evidence(label: &str, message: &str, path: &str, version: &str) -> Value {
    let name = if label == "ClazyUnavailable" {
        "clazy"
    } else {
        "clang-tidy"
    };
    let (path, version, error) = if message
        .ends_with("requires an approved capability inventory and exact compilation context")
    {
        ("", "", "analysis context unavailable")
    } else if message.ends_with("requires an exact compilation database") {
        (
            path,
            version,
            "exact compilation database unavailable; no command was executed",
        )
    } else if message.ends_with("requires an approved clang++ capability") {
        (
            path,
            version,
            "approved clang++ capability unavailable; no command was executed",
        )
    } else {
        (
            path,
            version,
            "tool not found or capability probe incomplete; no command was executed",
        )
    };
    json!({"name":name,"path":path,"version":version,"argv":[],"returncode":null,
        "timed_out":false,"truncated":false,"error":error})
}

/// Frozen `regular_executable`: an approved tool runs by its resolved path (symlinks followed).
pub fn resolved_executable(path: &Path) -> String {
    path.canonicalize()
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

/// Frozen `ProcessResult.returncode` of an observed run: 124 after a timeout, the negative
/// signal number of a signalled process, -1 when the process could not be started.
pub fn frozen_returncode(outcome: &TaskOutcome) -> Value {
    if outcome.timed_out {
        return json!(124);
    }
    match (outcome.exit_code, outcome.signal) {
        (Some(code), _) => json!(code),
        (None, Some(signal)) => json!(-signal),
        (None, None) if outcome.error.is_some() => json!(-1),
        _ => Value::Null,
    }
}

/// Frozen `ToolEvidence` of one observed process.
pub fn process_evidence(
    name: &str,
    version: &str,
    argv: &[String],
    outcome: &TaskOutcome,
    error: &str,
) -> Value {
    json!({"name":name,"path":argv.first().cloned().unwrap_or_default(),"version":version,
        "argv":argv,"returncode":frozen_returncode(outcome),"timed_out":outcome.timed_out,
        "truncated":outcome.truncated,"error":error})
}

fn tool(clazy: bool) -> &'static str {
    if clazy { "clazy" } else { "clang-tidy" }
}

/// Frozen `core/toolchain.py` capability probe of one resolved clang tool: `<tool> --version`
/// (5 s, 65 536 output bytes) must exit 0 untruncated and report a parseable version
/// (`parse_tool_version`: clazy needs its own `clazy[-standalone] [version] N.N` banner,
/// clang-tidy a compiler or generic dotted version). A tool that fails the probe is the frozen
/// `<Tool>Unavailable`, never a per-source execution error. Returns the frozen capability
/// `version`: the first matching output line, at most 200 characters, or `None` when the probe
/// fails or reports no version.
///
/// Known frozen defect, corrected: frozen probes with the full environment and then runs the
/// tool with a replaced minimal one, so a tool needing `LD_LIBRARY_PATH` passes the probe and
/// fails every run. The probe here uses exactly the run tasks' environment (inherited runner
/// environment plus the replay overlay).
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
    (argv, version): (&[String], &str),
    outcome: &TaskOutcome,
    output: &AnalysisOutput,
) -> LegacyToolRun {
    let label = if clazy { "Clazy" } else { "ClangTidy" };
    let evidence = |error: &str| process_evidence(tool(clazy), version, argv, outcome, error);
    let failure = |message: String| LegacyToolRun {
        task: outcome.task_id.clone(),
        targets: vec![target(
            source,
            format!("{label}ExecutionError"),
            "ERROR",
            message.clone(),
        )],
        evidence: evidence(&message),
        error: Some(message),
    };
    if let Some(message) = process_error(clazy, outcome, source) {
        return failure(message);
    }
    if clazy && let Some(reason) = legacy_clazy_refusal(outcome, output) {
        return failure(format!(
            "clazy output was not parseable: {source}: {reason}"
        ));
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
        evidence: evidence(""),
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

/// What the frozen lint tooling block needs beside the plans: the probe evidence of each tool
/// task (`g++ stdlib include search` records in run order), the lint configuration and whether
/// clazy runs through its compiler wrapper.
pub struct ToolingFacts<'a> {
    pub probes: &'a BTreeMap<String, Vec<Value>>,
    pub config: Option<&'a Value>,
    pub wrapper: bool,
}

/// Frozen `_checks` of `analysis/_clazy.py`: the joined `clazy_checks`, else `clazy_profile`.
pub fn clazy_profile(config: Option<&Value>) -> String {
    match config
        .and_then(|config| config.get("clazy_checks"))
        .and_then(Value::as_array)
    {
        Some(checks) => checks
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(","),
        None => config
            .and_then(|config| config.get("clazy_profile"))
            .and_then(Value::as_str)
            .unwrap_or("level0")
            .to_owned(),
    }
}

fn configured_mode(config: Option<&Value>, clazy: bool) -> String {
    config
        .and_then(|config| config.get(if clazy { "clazy" } else { "clang_tidy" }))
        .and_then(Value::as_str)
        .unwrap_or("auto")
        .to_owned()
}

/// Frozen LintEngine clang tool counters of tools that did not get a plan: a disabled tool keeps
/// its configured mode; an unavailable clang-tidy is `unavailable`, an unavailable clazy too
/// unless it is required (`_problem` ERROR sets `error`).
pub fn unplanned_counters(missing: &[MissingTool], config: Option<&Value>) -> Value {
    let mut counters = serde_json::Map::new();
    for clazy in [false, true] {
        let label = if clazy {
            "ClazyUnavailable"
        } else {
            "ClangTidyUnavailable"
        };
        let mode = match missing.iter().find(|(name, ..)| *name == label) {
            Some((_, _, required, _)) if clazy && *required => "error".to_owned(),
            Some(_) => "unavailable".to_owned(),
            None => configured_mode(config, clazy),
        };
        tool_counters(&mut counters, clazy, &mode, 0, 0, "none", config);
    }
    Value::Object(counters)
}

fn tool_counters(
    counters: &mut serde_json::Map<String, Value>,
    clazy: bool,
    mode: &str,
    configurations: usize,
    sources: usize,
    provider: &str,
    config: Option<&Value>,
) {
    let prefix = if clazy { "clazy" } else { "clang_tidy" };
    counters.insert(format!("{prefix}_mode"), json!(mode));
    if clazy {
        counters.insert("clazy_provider".into(), json!(provider));
        counters.insert("clazy_profile".into(), json!(clazy_profile(config)));
    }
    counters.insert(
        format!("{prefix}_configurations_checked"),
        json!(configurations),
    );
    counters.insert(format!("{prefix}_sources_checked"), json!(sources));
}

/// The frozen lint tooling block: per tool (clang-tidy, then clazy) its `<Tool>Unavailable`
/// targets, then the planned run: a project-level stop, or the production sources without a
/// recorded command, then each selected unit in frozen order (a budget stop, the error that
/// replaced its run, or its run's targets), with the tool errors and warnings the LintEngine
/// summary reads, the frozen `ToolEvidence` (the unavailable record, then per unit its first
/// GCC include-search records and its run record) and the LintEngine clang tool counters.
pub fn legacy_tooling_projection(
    output: &mut AnalysisOutput,
    runs: Vec<LegacyToolRun>,
    missing: &[MissingTool],
    uncovered: &[String],
    plans: &[ToolPlan],
    stops: &BTreeMap<bool, BudgetStop>,
    facts: &ToolingFacts<'_>,
) {
    let mut runs: BTreeMap<String, LegacyToolRun> = runs
        .into_iter()
        .map(|run| (run.task.clone(), run))
        .collect();
    let mut targets = vec![];
    let mut errors = vec![];
    let mut evidence = vec![];
    let mut counters = serde_json::Map::new();
    for clazy in [false, true] {
        let label = if clazy {
            "ClazyUnavailable"
        } else {
            "ClangTidyUnavailable"
        };
        let prefix = if clazy { "Clazy" } else { "ClangTidy" };
        let first_error = errors.len();
        let mut unavailable = false;
        for (_, message, required, record) in missing.iter().filter(|(name, ..)| *name == label) {
            targets.push(target(
                ".",
                label.into(),
                if *required { "ERROR" } else { "WARN" },
                message.clone(),
            ));
            evidence.push(record.clone());
            unavailable = true;
            if *required {
                errors.push(message.clone());
            }
        }
        let Some(plan) = plans.iter().find(|plan| plan.clazy == clazy) else {
            let mode = match missing.iter().find(|(name, ..)| *name == label) {
                Some((_, _, required, _)) if clazy && *required => "error".to_owned(),
                Some(_) if unavailable => "unavailable".to_owned(),
                _ => configured_mode(facts.config, clazy),
            };
            tool_counters(&mut counters, clazy, &mode, 0, 0, "none", facts.config);
            continue;
        };
        // Frozen `_ready_tool` sets the clazy provider; a source-count or context-ingestion stop
        // comes before it.
        let ready = plan.terminal.iter().all(|(_, _, message)| {
            !message.ends_with("source count exceeds the bounded limit")
                && !message
                    .ends_with("did not run because compilation context ingestion has errors")
        });
        let provider = match (clazy && ready, facts.wrapper) {
            (false, _) => "none",
            (true, true) => "compiler-wrapper",
            (true, false) => "standalone",
        };
        let (mut configurations, mut sources) = (0, std::collections::BTreeSet::new());
        let mut recorded = std::collections::BTreeSet::new();
        if !plan.terminal.is_empty() {
            for (path, name, message) in &plan.terminal {
                targets.push(target(path, name.clone(), "ERROR", message.clone()));
                errors.push(message.clone());
            }
            tool_counters(&mut counters, clazy, "error", 0, 0, provider, facts.config);
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
            tool_counters(&mut counters, clazy, "error", 0, 0, provider, facts.config);
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
            // Frozen `_record_gcc_projection_evidence`: each include search once per tool.
            for probe in unit
                .task
                .as_ref()
                .and_then(|task| facts.probes.get(task))
                .into_iter()
                .flatten()
            {
                if recorded.insert(probe["argv"].to_string()) {
                    evidence.push(probe.clone());
                }
            }
            match &unit.step {
                UnitStep::Error { name, message } => {
                    targets.push(target(&unit.source, name.clone(), "ERROR", message.clone()));
                    errors.push(message.clone());
                }
                UnitStep::Run(task) => {
                    if let Some(run) = runs.remove(task) {
                        targets.extend(run.targets);
                        evidence.push(run.evidence);
                        if run.error.is_none() {
                            configurations += 1;
                            sources.insert(unit.source.clone());
                        }
                        errors.extend(run.error);
                    }
                }
            }
        }
        let mode = if errors.len() > first_error {
            "error"
        } else {
            "exact"
        };
        tool_counters(
            &mut counters,
            clazy,
            mode,
            configurations,
            sources.len(),
            provider,
            facts.config,
        );
    }
    let warnings: Vec<&String> = missing
        .iter()
        .filter(|(_, _, required, _)| !*required)
        .map(|(_, message, ..)| message)
        .collect();
    output.extra["legacy_tool_warnings"] = json!(warnings);
    output.extra["legacy_tool_errors"] = json!(errors);
    output.extra["legacy_lint_counters"] = Value::Object(counters);
    output.extra["tool_evidence"] = json!(evidence);
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
    // Security correction (decisions.md "clang-tidy 설정의 escape된 인자 키"): frozen
    // `_UNSAFE_CONFIG_ARGUMENT_RE` only matched raw word-bounded key text, so LLVM YAML
    // double-quoted escapes (`"\x45xtraArgs"`) injected compiler arguments. The shared native
    // guard keeps that case-insensitive word-bounded key match and also refuses NUL bytes and
    // any file holding both a double quote and a backslash, with frozen's refusal text/target.
    if crate::cpp::clang_tidy_config_is_unsafe(&content) {
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
    /// The unit's tool task, also when its step became an error after planning (a failed GCC
    /// include search): its probe evidence is keyed by it.
    pub task: Option<String>,
}

/// The frozen run of one clang tool over its selected units (`run_clang_tidy` / `run_clazy`).
#[derive(Clone, Debug)]
pub struct ToolPlan {
    pub clazy: bool,
    /// Project-level errors (path, target, message) that stop this tool before unit selection
    /// (`<Tool>ContextError` for unreadable or too many sources and context ingestion errors,
    /// `ClangTidyConfigError` for the configured config file).
    pub terminal: Vec<(String, String, String)>,
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
    if native.code == COMPILER_CHANGED {
        return (native.code.clone(), native.message.clone());
    }
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
    // Frozen `parse_compiler_include_search` wording of the failures native can name exactly.
    let message = match native.message.as_str() {
        "Compiler include search block is missing, duplicated or malformed" => {
            "compiler include search block is missing, duplicated, or malformed".to_owned()
        }
        "Compiler include search has no directories" => {
            "compiler include search contains no directories".to_owned()
        }
        "Compiler include search exceeds 64 entries" => {
            "compiler include search contains too many directories".to_owned()
        }
        other => other.to_owned(),
    };
    ("gcc-include-probe-unparseable".into(), message)
}

/// Frozen `gcc_standard_library_for_replay`: the driver changed while its search ran.
pub(super) const COMPILER_CHANGED: &str = "gcc-include-probe-compiler-changed";
pub(super) const COMPILER_CHANGED_MESSAGE: &str =
    "GCC include search compiler identity changed during probing.";

/// Frozen `_parse_clazy_text` refusals the native parser does not make itself, in line order.
/// `None` leaves the output to the native parser, which also reports unknown lines.
fn legacy_clazy_refusal(outcome: &TaskOutcome, output: &AnalysisOutput) -> Option<String> {
    use std::sync::OnceLock;
    static PATTERNS: OnceLock<[Regex; 5]> = OnceLock::new();
    let [diagnostic, fix_it, marker, rule_shape, ancillary_line] = PATTERNS.get_or_init(|| {
        [
            r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*(?P<kind>fatal error|error|warning|note|remark):\s*(?P<message>\S.*?)(?:\s+\[(?P<rule>[-A-Za-z0-9_.]+)\])?$",
            r#"^fix-it:"(?:[^"\\]|\\.)*":\{[1-9]\d*:[1-9]\d*-[1-9]\d*:[1-9]\d*\}:"(?:[^"\\]|\\.)*"$"#,
            r"\[-W[^\]\r\n]*",
            r"^-Wclazy-[a-z0-9](?:[a-z0-9_.-]{0,254}[a-z0-9])?$",
            r"^(?:In file included from|from |.+:\s+(?:At global scope|In (?:function|member function|constructor|destructor|lambda function|instantiation of)(?: .*)?):$|.+:[1-9]\d*(?::[1-9]\d*)?:\s+required (?:from|by)\s+\S.*$|[1-9]\d* (?:warning|warnings|error|errors) generated\.$|compilation terminated\.$)",
        ]
        .map(|pattern| Regex::new(pattern).expect("frozen clazy pattern"))
    });
    let stdout = String::from_utf8_lossy(&outcome.stdout);
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    // The bounds are `clazy_parse_error`'s, ahead of any line.
    if stdout.chars().count() + stderr.chars().count() > 1_000_000
        || stdout.contains('\0')
        || stderr.contains('\0')
    {
        return None;
    }
    let unrecognized = output
        .observation
        .limitations
        .first()
        .and_then(|limitation| limitation.split_once("unrecognized C++ diagnostic output: "))
        .map(|(_, line)| line.trim().to_owned());
    let text = [stdout.trim(), stderr.trim()]
        .into_iter()
        .filter(|stream| !stream.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    let (mut inherited, mut compiler_family, mut saw, mut ancillary) = (false, false, false, false);
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if let Some(captures) = diagnostic.captures(line) {
            let kind = &captures["kind"];
            if marker.is_match(&captures["message"]) {
                return Some("clazy diagnostic contains a conflicting rule marker".into());
            }
            let rule = captures.name("rule").map_or("", |rule| rule.as_str());
            if !rule.is_empty() && !rule.starts_with("-Wclazy-") {
                if !rule.starts_with("-W") {
                    return Some("compiler diagnostic rule is unsupported or unsafe".into());
                }
                if !matches!(kind, "warning" | "remark" | "note") {
                    return Some("compiler error cannot be excluded from clazy output".into());
                }
                (inherited, compiler_family) = (false, true);
            } else if !rule.is_empty() {
                if !rule_shape.is_match(rule) {
                    return Some("clazy diagnostic rule is unsupported or unsafe".into());
                }
                (inherited, compiler_family) = (true, false);
            } else if kind == "note" && compiler_family {
            } else if kind == "note" && inherited {
                compiler_family = false;
            } else {
                return Some("clazy diagnostic has no check identifier".into());
            }
            saw = true;
        } else if fix_it.is_match(line) {
            return Some("clazy fix-it output is not supported".into());
        } else if unrecognized.as_deref() == Some(line) {
            return None;
        } else if ancillary_line.is_match(line) {
            ancillary = true;
        }
    }
    (ancillary && !saw).then(|| "clazy context output has no located diagnostic".into())
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
