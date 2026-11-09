//! Shared source-located coverage policies, separate from raw coverage decoding.
use crate::{ValidationError, ValidationPlan, coverage::CoverageReport, python::finding, tem};
use ici_domain::{AnalysisOutput, InspectionTarget, python_round_one as round1};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

const MAX_CHANGED_LINES: u64 = 10_000;

/// Frozen `coverage_policy.parse_changed_lines` for the legacy reading: any regular project file,
/// source or not, with frozen's own messages. Each half of a hybrid project then reports only the
/// paths it measured; a path no half measured is frozen's `no executable measured lines` ERROR.
pub fn legacy_changed_lines(
    root: &std::path::Path,
    specs: &[String],
) -> Result<BTreeMap<String, BTreeSet<u32>>, String> {
    let mut parsed = BTreeMap::<String, BTreeSet<u32>>::new();
    let mut total = 0u64;
    let root = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    for (index, value) in specs.iter().enumerate() {
        if value.is_empty() {
            return Err(format!("changed_lines[{index}] must be a non-empty string"));
        }
        let form = || {
            format!("changed_lines[{index}] must use canonical path:line or path:start-end form")
        };
        let (raw_path, raw_range) = value.rsplit_once(':').ok_or_else(form)?;
        let (start, end) = raw_range.split_once('-').unwrap_or((raw_range, ""));
        let number = |text: &str| {
            (!text.is_empty() && !text.starts_with('0') && text.bytes().all(|b| b.is_ascii_digit()))
                .then(|| text.parse::<u64>().unwrap_or(u64::MAX))
        };
        let start_number = number(start);
        let end_number = if raw_range.contains('-') {
            number(end)
        } else {
            start_number
        };
        let (Some(start), Some(end)) = (start_number, end_number) else {
            return Err(form());
        };
        if raw_path.is_empty() {
            return Err(form());
        }
        let canonical = frozen_canonical(raw_path)?;
        if canonical.is_empty() || canonical == "." || canonical != raw_path {
            return Err(format!(
                "changed_lines[{index}] path must already be canonical"
            ));
        }
        let mut lexical = root.clone();
        let mut symlink = false;
        for part in canonical.split('/') {
            lexical.push(part);
            if lexical
                .symlink_metadata()
                .is_ok_and(|meta| meta.file_type().is_symlink())
            {
                symlink = true;
                break;
            }
        }
        // Frozen `resolve_project_path`: a symlink that leaves the root names that first.
        let candidate = root.join(&canonical);
        if symlink && std::fs::canonicalize(&candidate).is_ok_and(|real| !real.starts_with(&root)) {
            return Err(format!("path is outside project root: {canonical}"));
        }
        if symlink || !candidate.is_file() {
            return Err(format!(
                "changed_lines[{index}] path is not a regular project file"
            ));
        }
        if end < start {
            return Err(format!("changed_lines[{index}] range is reversed"));
        }
        let count = end - start + 1;
        if count > MAX_CHANGED_LINES - total || end > u64::from(u32::MAX) {
            return Err(format!(
                "changed lines exceed the bounded {MAX_CHANGED_LINES}-line limit"
            ));
        }
        let lines = parsed.entry(canonical).or_default();
        let incoming = (start as u32)..=(end as u32);
        if incoming.clone().any(|line| lines.contains(&line)) {
            return Err(format!(
                "changed_lines[{index}] duplicates or overlaps an earlier entry"
            ));
        }
        lines.extend(incoming);
        total += count;
    }
    Ok(parsed)
}

/// Frozen `canonical_project_path(raw)` without a root: lexical, slash-separated.
fn frozen_canonical(raw: &str) -> Result<String, String> {
    let slashed = raw.replace('\\', "/");
    let trimmed = slashed.trim_end_matches('/');
    let value = if trimmed.is_empty() { "." } else { trimmed };
    let bytes = value.as_bytes();
    let drive = bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':';
    let repr = ici_analysis::unicode::python_repr(value);
    if drive && bytes.get(2) != Some(&b'/') {
        return Err(format!("drive-relative finding path is ambiguous: {repr}"));
    }
    if value.starts_with('/') || drive {
        return Err(format!(
            "absolute finding path requires project_root: {repr}"
        ));
    }
    let mut parts: Vec<&str> = vec![];
    for part in value.split('/') {
        match part {
            "" | "." => {}
            part if part.len() == 2
                && part.as_bytes()[0].is_ascii_alphabetic()
                && part.ends_with(':') => {}
            ".." => {
                if parts.pop().is_none() {
                    return Err(format!("path escapes the project root: {repr}"));
                }
            }
            part => parts.push(part),
        }
    }
    Ok(if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    })
}

pub fn changed_lines(
    specs: &[String],
    sources: &[String],
) -> Result<BTreeMap<String, BTreeSet<u32>>, ValidationError> {
    let mut result = BTreeMap::<String, BTreeSet<u32>>::new();
    let mut count = 0u32;
    for spec in specs {
        let (path, range) = spec
            .rsplit_once(':')
            .ok_or_else(|| ValidationError("changed lines require path:line[-line]".into()))?;
        ici_domain::validation::relative_path(path, false)
            .map_err(|e| ValidationError(e.to_string()))?;
        if !sources.iter().any(|source| source == path) {
            return Err(ValidationError(
                "changed line path is outside captured source scope".into(),
            ));
        }
        let (start, end) = range.split_once('-').unwrap_or((range, range));
        let parse = |text: &str| -> Result<u32, ValidationError> {
            if text.starts_with('0')
                || text.is_empty()
                || !text.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(ValidationError(
                    "changed line number is not canonical".into(),
                ));
            }
            text.parse::<u32>()
                .map_err(|_| ValidationError("changed line number overflow".into()))
        };
        let start = parse(start)?;
        let end = parse(end)?;
        if end < start
            || end - start >= 10000
            || count
                .checked_add(end - start + 1)
                .is_none_or(|count| count > 10000)
        {
            return Err(ValidationError(
                "changed line range is reversed or exceeds 10000 lines".into(),
            ));
        }
        for line in start..=end {
            if !result.entry(path.into()).or_default().insert(line) {
                return Err(ValidationError("changed line ranges overlap".into()));
            }
        }
        count += end - start + 1;
    }
    Ok(result)
}
pub fn evaluate(
    plan: &ValidationPlan,
    report: &CoverageReport,
    score: Option<f64>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    evaluate_suite(plan, report, score, false, output)
}
/// Frozen `coverage_policy.py` targets plus the frozen `_threshold_breaches` TEM/branch gate.
/// A suite with a failing case already fails; like the frozen engine it does not add a
/// redundant TEM/branch breach, but every aggregate line/file/function target stays.
pub const MAX_POLICY_ROWS: usize = 10_000;
pub const POLICY_ROWS_REFUSAL: &str = "coverage inventory exceeds the bounded 10000-row limit";
pub fn evaluate_suite(
    plan: &ValidationPlan,
    report: &CoverageReport,
    score: Option<f64>,
    suite_failed: bool,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let options = &plan.context.options;
    // Frozen `evaluate_coverage_policy`: an evaluated policy over more than 10 000 coverage-file
    // or function rows is refused before any target (the decoded report's own 16 384-file
    // bound stays the outer parser limit).
    let evaluated = [
        options.min_line_cov,
        options.min_file_cov,
        options.min_branch_cov,
        options.min_func_cov,
        options.min_tem_score,
        options.min_changed_line_cov,
    ]
    .iter()
    .any(Option::is_some);
    if evaluated
        && (report.files.len() > MAX_POLICY_ROWS || report.function_rows.len() > MAX_POLICY_ROWS)
    {
        return Err(ValidationError(POLICY_ROWS_REFUSAL.into()));
    }
    let mut unavailable = vec![];
    let counts = &report.counts;
    let legacy = options.legacy_coverage_inputs;
    // Frozen `_measure_coverage`: a selection `parse_changed_lines` refuses replaces every policy
    // target with one ERROR target; the TEM and branch breaches still follow.
    let refused = if legacy {
        legacy_changed_lines(&plan.context.root, &options.changed_lines).err()
    } else {
        None
    };
    if let Some(error) = &refused {
        let mut target = InspectionTarget::at(".", 1);
        target.target_name = "Coverage:Policy configuration".into();
        target.status = "ERROR".into();
        target.message = format!("Coverage policy could not be evaluated: {error}");
        target.metrics.insert("gated".into(), json!(true));
        output.targets.push(target);
    }
    if let Some(threshold) = options.min_line_cov.filter(|_| refused.is_none()) {
        match tem::percentage(counts.covered_lines, counts.total_lines) {
            Some(actual) => threshold_target(
                plan,
                output,
                Gate {
                    path: ".",
                    line: 1,
                    end: None,
                    name: "Coverage:Overall line",
                    label: "Overall line coverage",
                    rule: "overall-line",
                    actual,
                    threshold,
                    metrics: vec![
                        ("covered_lines", json!(counts.covered_lines)),
                        ("executable_lines", json!(counts.total_lines)),
                    ],
                },
            ),
            None => skip_axis(
                output,
                "Coverage:Overall line",
                true,
                &mut unavailable,
                "Overall line coverage",
            ),
        }
    }
    if let Some(threshold) = options.min_file_cov.filter(|_| refused.is_none()) {
        for file in report
            .files
            .values()
            .filter(|file| file.counts.total_lines >= options.min_file_statements)
        {
            if let Some(actual) =
                tem::percentage(file.counts.covered_lines, file.counts.total_lines)
            {
                threshold_target(
                    plan,
                    output,
                    Gate {
                        path: &file.path,
                        line: 1,
                        end: None,
                        name: "Coverage:File",
                        label: "File line coverage",
                        rule: "file",
                        // Frozen file rows carry `round(cover, 1)` before the gate compares.
                        actual: round1(actual),
                        threshold,
                        metrics: vec![
                            ("covered_lines", json!(file.counts.covered_lines)),
                            ("executable_lines", json!(file.counts.total_lines)),
                            (
                                "missed_lines",
                                json!(file.counts.total_lines - file.counts.covered_lines),
                            ),
                        ],
                    },
                );
            }
        }
    }
    if let Some(threshold) = options.min_func_cov.filter(|_| refused.is_none()) {
        match counts
            .covered_functions
            .zip(counts.total_functions)
            .and_then(|(covered, total)| {
                tem::percentage(covered, total).map(|p| (covered, total, p))
            }) {
            Some((covered, total, actual)) => threshold_target(
                plan,
                output,
                Gate {
                    path: ".",
                    line: 1,
                    end: None,
                    name: "Coverage:Functions",
                    label: "Aggregate function coverage",
                    rule: "functions",
                    actual,
                    threshold,
                    metrics: vec![
                        ("covered_functions", json!(covered)),
                        ("functions", json!(total)),
                    ],
                },
            ),
            None => skip_axis(
                output,
                "Coverage:Functions",
                counts.total_functions == Some(0),
                &mut unavailable,
                "Aggregate function coverage",
            ),
        }
    }
    let mut rows: Vec<&serde_json::Value> = report.function_rows.iter().collect();
    rows.sort_by_key(|row| {
        (
            row["file"].as_str().unwrap_or("").to_owned(),
            row["start_line"].as_u64().unwrap_or(1),
            row["start_column"].as_u64().unwrap_or(0),
            row["name"].as_str().unwrap_or("").to_owned(),
        )
    });
    if refused.is_some() {
        rows.clear();
    }
    for row in rows {
        let path = row["file"].as_str().unwrap_or(".");
        let line = row["start_line"]
            .as_u64()
            .and_then(|line| u32::try_from(line).ok())
            .unwrap_or(1);
        let name = row["name"].as_str().unwrap_or("<unnamed>");
        let mut target = InspectionTarget::at(path, line);
        target.end_line = row["end_line"]
            .as_u64()
            .and_then(|line| u32::try_from(line).ok())
            .or(Some(line));
        target.start_column = row["start_column"]
            .as_u64()
            .and_then(|column| u32::try_from(column).ok());
        target.end_column = row["end_column"]
            .as_u64()
            .and_then(|column| u32::try_from(column).ok());
        target.target_name = format!("Coverage:Function:{name}");
        target.status = match row["covered"].as_bool() {
            Some(true) => "PASS",
            Some(false) => "WARN",
            None => "SKIP",
        }
        .into();
        target.message = match row["covered"].as_bool() {
            Some(true) => {
                format!("Function {name} was exercised by the aggregate project test suite")
            }
            Some(false) => {
                format!("Function {name} was not exercised by the aggregate project test suite")
            }
            None => format!("Function {name} invocation was not measured"),
        };
        target
            .metrics
            .insert("covered".into(), row["covered"].clone());
        target.metrics.insert("gated".into(), json!(false));
        target
            .metrics
            .insert("test_scope".into(), json!("aggregate-project-suite"));
        target.metrics.insert(
            "symbol".into(),
            json!(
                row["symbol"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .unwrap_or(name)
            ),
        );
        output.targets.push(target);
    }
    let changed = if legacy {
        legacy_changed_lines(&plan.context.root, &options.changed_lines).unwrap_or_default()
    } else {
        changed_lines(&options.changed_lines, &plan.context.source_files)?
    };
    for (path, lines) in changed {
        let file = report.files.get(&path);
        if file.is_none() && !legacy {
            return Err(ValidationError(
                "changed-line source has no measured coverage".into(),
            ));
        }
        let first = *lines.first().expect("nonempty selected lines");
        let last = *lines.last().expect("nonempty selected lines");
        // Legacy: a path this half did not measure is frozen's unmeasured ERROR (a hybrid
        // project keeps the half that measured it, `legacy_hybrid`).
        let executable = lines
            .iter()
            .filter(|line| {
                file.is_some_and(|file| file.executed.contains(line) || file.missing.contains(line))
            })
            .count() as u64;
        let covered = lines
            .iter()
            .filter(|line| file.is_some_and(|file| file.executed.contains(line)))
            .count() as u64;
        let threshold = options.min_changed_line_cov.unwrap_or(100.0);
        if let Some(actual) = tem::percentage(covered, executable) {
            threshold_target(
                plan,
                output,
                Gate {
                    path: &path,
                    line: first,
                    end: Some(last),
                    name: "Coverage:Changed lines",
                    label: "Changed executable-line coverage",
                    rule: "changed-lines",
                    actual,
                    threshold,
                    metrics: vec![
                        ("requested_lines", json!(lines.len())),
                        ("covered_lines", json!(covered)),
                        ("executable_lines", json!(executable)),
                        (
                            "non_executable_lines",
                            json!(lines.len() as u64 - executable),
                        ),
                    ],
                },
            );
        } else {
            // Frozen reports ERROR, which the legacy reading keeps; native keeps the check
            // incomplete instead of a verdict.
            let mut target = InspectionTarget::at(&path, first);
            target.end_line = Some(last);
            target.status = if legacy { "ERROR" } else { "SKIP" }.into();
            target.target_name = "Coverage:Changed lines".into();
            target.message = "Changed-line coverage has no executable measured lines".into();
            target
                .metrics
                .insert("requested_lines".into(), json!(lines.len()));
            target.metrics.insert("executable_lines".into(), json!(0));
            target.metrics.insert("threshold".into(), json!(threshold));
            target.metrics.insert("gated".into(), json!(true));
            output.targets.push(target);
            // Frozen's ERROR target is the legacy verdict; native has nothing to gate.
            if !legacy {
                unavailable.push("Changed executable-line coverage");
            }
        }
    }
    // Frozen appends the TEM/branch threshold breaches after the coverage-policy targets.
    for (name, label, rule, actual, threshold, empty) in [
        (
            "Threshold: TEM score",
            "TEM score",
            "tem",
            score,
            options.min_tem_score,
            counts.total_functions == Some(0),
        ),
        (
            "Threshold: Branch coverage",
            "Branch coverage",
            "branch",
            // Frozen legacy `_measure_coverage` reads a measured scope without a branch as 100.0.
            tem::coverage_inputs(counts, options.legacy_coverage_inputs).1,
            options.min_branch_cov,
            counts.total_branches == 0,
        ),
    ] {
        let Some(threshold) = threshold.filter(|_| !suite_failed) else {
            continue;
        };
        match actual {
            Some(actual) => {
                let percent = rule == "branch";
                let shown = |value: f64| {
                    if percent {
                        format!("{value:.1}%")
                    } else {
                        format!("{value:.2}")
                    }
                };
                let passed = actual >= threshold;
                let mut target = InspectionTarget::at(".", 1);
                target.target_name = name.into();
                target.status = if passed { "PASS" } else { "WARN" }.into();
                target.message = format!(
                    "{label} {} {} the configured minimum {}",
                    shown(actual),
                    if passed { "meets" } else { "is below" },
                    shown(threshold)
                );
                target.metrics.insert("actual".into(), json!(actual));
                target.metrics.insert("threshold".into(), json!(threshold));
                if !passed {
                    aggregate_finding(plan, output, rule, ".", 1, &target.message);
                }
                output.targets.push(target);
            }
            None => skip_axis(output, name, empty, &mut unavailable, label),
        }
    }
    if !unavailable.is_empty() {
        return Err(ValidationError(format!(
            "coverage policy has unmeasured inputs: {}",
            unavailable.join(", ")
        )));
    }
    Ok(())
}
fn skip_axis(
    output: &mut AnalysisOutput,
    name: &str,
    empty: bool,
    unavailable: &mut Vec<&'static str>,
    label: &'static str,
) {
    if !empty {
        unavailable.push(label);
    }
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = name.into();
    target.status = "SKIP".into();
    target.message = if empty {
        "measured scope has no applicable denominator; no percentage was fabricated"
    } else {
        "required measurement is unavailable; no percentage was fabricated"
    }
    .into();
    output.targets.push(target);
}
struct Gate<'a> {
    path: &'a str,
    line: u32,
    end: Option<u32>,
    name: &'a str,
    label: &'a str,
    rule: &'a str,
    actual: f64,
    threshold: f64,
    metrics: Vec<(&'a str, serde_json::Value)>,
}
/// Frozen `_threshold_target`: `.1f%` message, `round(actual, 1)` metric, gated.
fn threshold_target(plan: &ValidationPlan, output: &mut AnalysisOutput, gate: Gate<'_>) {
    let passed = gate.actual >= gate.threshold;
    let mut target = InspectionTarget::at(gate.path, gate.line);
    target.end_line = gate.end;
    target.target_name = gate.name.into();
    target.status = if passed { "PASS" } else { "WARN" }.into();
    target.message = format!(
        "{} {:.1}% {} the configured minimum {:.1}%",
        gate.label,
        gate.actual,
        if passed { "meets" } else { "is below" },
        gate.threshold
    );
    target
        .metrics
        .insert("actual".into(), json!(round1(gate.actual)));
    target
        .metrics
        .insert("threshold".into(), json!(gate.threshold));
    target.metrics.insert("gated".into(), json!(true));
    for (key, value) in gate.metrics {
        target.metrics.insert(key.into(), value);
    }
    if !passed {
        aggregate_finding(
            plan,
            output,
            gate.rule,
            gate.path,
            gate.line,
            &target.message,
        );
    }
    output.targets.push(target);
}
/// A modern finding must name a project file, so a project-wide (`.`) target's issue is anchored
/// at the first captured source (else `ici.toml`); the historical view still matches the `.`
/// target through `legacy_path`, keeping one issue with the frozen identity.
fn aggregate_finding(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
    rule: &str,
    path: &str,
    line: u32,
    message: &str,
) {
    let anchor = if path == "." {
        plan.context
            .source_files
            .first()
            .map(String::as_str)
            .unwrap_or("ici.toml")
    } else {
        path
    };
    let item = finding(
        plan,
        &format!("ici.test.coverage.{rule}"),
        message,
        anchor,
        line,
    );
    if anchor != path {
        output.extra["finding_details"][&item.fingerprint] = json!({"legacy_path":path});
    }
    output.observation.findings.push(item);
}
