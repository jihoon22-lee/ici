//! Shared source-located coverage policies, separate from raw coverage decoding.
use crate::{ValidationError, ValidationPlan, coverage::CoverageReport, python::finding, tem};
use ici_domain::{AnalysisOutput, InspectionTarget};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

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
    let options = &plan.context.options;
    let anchor = plan
        .context
        .source_files
        .first()
        .map(String::as_str)
        .unwrap_or("ici.toml");
    let mut unavailable = vec![];
    for (label, rule, actual, threshold) in [
        (
            "Overall line coverage",
            "overall-line",
            tem::percentage(report.counts.covered_lines, report.counts.total_lines),
            options.min_line_cov,
        ),
        (
            "Branch coverage",
            "branch",
            tem::percentage(report.counts.covered_branches, report.counts.total_branches),
            options.min_branch_cov,
        ),
        (
            "Function coverage",
            "functions",
            report
                .counts
                .covered_functions
                .zip(report.counts.total_functions)
                .and_then(|(a, b)| tem::percentage(a, b)),
            options.min_func_cov,
        ),
        ("TEM score", "tem", score, options.min_tem_score),
    ] {
        if let Some(threshold) = threshold {
            if let Some(actual) = actual {
                threshold_target(
                    plan, output, anchor, 1, None, label, rule, actual, threshold,
                );
            } else {
                let empty = match rule {
                    "overall-line" => report.counts.total_lines == 0,
                    "branch" => report.counts.total_branches == 0,
                    "functions" | "tem" => report.counts.total_functions == Some(0),
                    _ => false,
                };
                if !empty {
                    unavailable.push(label);
                }
                let mut target = InspectionTarget::at(anchor, 1);
                target.target_name = label.into();
                target.status = "SKIP".into();
                target.message = if empty {
                    "measured scope has no applicable denominator; no percentage was fabricated"
                } else {
                    "required measurement is unavailable; no percentage was fabricated"
                }
                .into();
                output.targets.push(target);
            }
        }
    }
    if let Some(threshold) = options.min_file_cov {
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
                    &file.path,
                    1,
                    None,
                    "File line coverage",
                    "file",
                    actual,
                    threshold,
                );
            }
        }
    }
    for row in &report.function_rows {
        let path = row["file"].as_str().unwrap_or(anchor);
        let line = row["start_line"]
            .as_u64()
            .and_then(|line| u32::try_from(line).ok())
            .unwrap_or(1);
        let mut target = InspectionTarget::at(path, line);
        target.end_line = row["end_line"]
            .as_u64()
            .and_then(|line| u32::try_from(line).ok());
        target.start_column = row["start_column"]
            .as_u64()
            .and_then(|column| u32::try_from(column).ok());
        target.end_column = row["end_column"]
            .as_u64()
            .and_then(|column| u32::try_from(column).ok());
        target.target_name = format!(
            "Coverage:Function:{}",
            row["name"].as_str().unwrap_or("<unnamed>")
        );
        target.status = match row["covered"].as_bool() {
            Some(true) => "PASS",
            Some(false) => "WARN",
            None => "SKIP",
        }
        .into();
        target
            .metrics
            .insert("covered".into(), row["covered"].clone());
        target.metrics.insert("gated".into(), json!(false));
        output.targets.push(target);
    }
    let changed = changed_lines(&options.changed_lines, &plan.context.source_files)?;
    for (path, lines) in changed {
        let Some(file) = report.files.get(&path) else {
            return Err(ValidationError(
                "changed-line source has no measured coverage".into(),
            ));
        };
        let executable = lines
            .iter()
            .filter(|line| file.executed.contains(line) || file.missing.contains(line))
            .count() as u64;
        let covered = lines
            .iter()
            .filter(|line| file.executed.contains(line))
            .count() as u64;
        if let Some(actual) = tem::percentage(covered, executable) {
            threshold_target(
                plan,
                output,
                &path,
                *lines.first().expect("nonempty selected lines"),
                lines.last().copied(),
                "Changed executable-line coverage",
                "changed-lines",
                actual,
                options.min_changed_line_cov.unwrap_or(100.0),
            );
        } else {
            let mut target =
                InspectionTarget::at(&path, *lines.first().expect("nonempty selected lines"));
            target.status = "SKIP".into();
            target.target_name = "Coverage:Changed lines".into();
            target.message = "selected lines contain no measured executable statement".into();
            output.targets.push(target);
            unavailable.push("Changed executable-line coverage");
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
#[allow(clippy::too_many_arguments)]
fn threshold_target(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
    path: &str,
    line: u32,
    end: Option<u32>,
    label: &str,
    rule: &str,
    actual: f64,
    threshold: f64,
) {
    let passed = actual >= threshold;
    let mut target = InspectionTarget::at(path, line);
    target.end_line = end;
    target.target_name = label.into();
    target.status = if passed { "PASS" } else { "WARN" }.into();
    target.message = format!(
        "{label} {actual:.2} {} configured minimum {threshold:.2}",
        if passed { "meets" } else { "is below" }
    );
    target.metrics.insert("actual".into(), json!(actual));
    target.metrics.insert("threshold".into(), json!(threshold));
    target.metrics.insert("gated".into(), json!(true));
    if !passed {
        output.observation.findings.push(finding(
            plan,
            &format!("ici.test.coverage.{rule}"),
            &target.message,
            path,
            line,
        ));
    }
    output.targets.push(target);
}
