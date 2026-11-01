//! Frozen `LintEngine` clang-tidy / Clazy targets (analysis/_clang_tidy.py, analysis/_clazy.py)
//! over the native `cpp.tidy` provider runs of a legacy `ici verify` with an exact context.
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::{Value, json};

/// One tool run on one recorded translation unit, projected as frozen targets.
pub struct LegacyToolRun {
    pub clazy: bool,
    pub unit: usize,
    pub targets: Vec<InspectionTarget>,
    pub error: Option<String>,
}

fn tool(clazy: bool) -> &'static str {
    if clazy { "clazy" } else { "clang-tidy" }
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
    unit: usize,
    source: &str,
    digest: &str,
    outcome: &TaskOutcome,
    output: &AnalysisOutput,
) -> LegacyToolRun {
    let label = if clazy { "Clazy" } else { "ClangTidy" };
    let failure = |message: String| LegacyToolRun {
        clazy,
        unit,
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
        return failure(format!(
            "{} output was not parseable: {source}",
            tool(clazy)
        ));
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
        clazy,
        unit,
        targets,
        error: None,
    }
}

/// The frozen lint tooling block: clang-tidy runs (or `ClangTidyUnavailable`) in unit order,
/// then clazy runs (or `ClazyUnavailable`), with the tool errors and warnings the LintEngine
/// summary reads.
pub fn legacy_tooling_projection(
    output: &mut AnalysisOutput,
    mut runs: Vec<LegacyToolRun>,
    missing: &[(&str, String, bool)],
    uncovered: &[String],
) {
    runs.sort_by_key(|run| (run.clazy, run.unit));
    let mut targets = vec![];
    let mut errors = vec![];
    for clazy in [false, true] {
        let label = if clazy {
            "ClazyUnavailable"
        } else {
            "ClangTidyUnavailable"
        };
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
        // Frozen `_selected_context_units`: a tool that ran reports each production source
        // without a recorded command as an ERROR before its replayed units.
        if !uncovered.is_empty() && runs.iter().any(|run| run.clazy == clazy) {
            errors.push(format!(
                "{} context is missing {} production source command(s)",
                tool(clazy),
                uncovered.len()
            ));
            targets.extend(uncovered.iter().map(|source| {
                target(
                    source,
                    format!(
                        "{}ContextMissing",
                        if clazy { "Clazy" } else { "ClangTidy" }
                    ),
                    "ERROR",
                    "No exact translation-unit command covers this production source.".into(),
                )
            }));
        }
        for run in runs.iter().filter(|run| run.clazy == clazy) {
            targets.extend(run.targets.iter().cloned());
            errors.extend(run.error.clone());
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
