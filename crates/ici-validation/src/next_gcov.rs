//! Frozen next C++ coverage read (`adapters/providers/gcov.py` with the `base.observe` order):
//! an unfinished gcov is never parsed, a non-zero exit fails with frozen's contract wording,
//! every report the run wrote is aggregated without source filtering, and the line, branch and
//! function percentages are rounded like frozen (`round(x, 1)`).
use crate::{ValidationError, ValidationPlan, artifacts::CapturedArtifacts};
use ici_domain::{AnalysisOutput, EvidenceLevel, Measurement};
use ici_execution::TaskOutcome;
use std::collections::BTreeMap;

pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    // Frozen names the gcov process by its task id, which is the check id.
    let name = &plan.context.task_id;
    let id = format!("{name}.report");
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError(format!("gcov step {id} absent")))?;
    if let Some(reason) = crate::next_elf::unfinished(name, outcome, 900, 1_000_000) {
        return Err(ValidationError(reason));
    }
    if let Some(code) = outcome.exit_code.filter(|code| *code != 0) {
        return Err(ValidationError(format!(
            "{name} failed with exit code {code}"
        )));
    }
    let reports: Vec<_> = artifacts
        .files
        .values()
        .filter(|artifact| artifact.producer_task_id == id)
        .collect();
    if reports.is_empty() {
        return Err(ValidationError(
            "gcov produced no reports — the .gcda are absent or the binaries were never instrumented"
                .into(),
        ));
    }
    if let Some(error) = artifacts.errors.values().next() {
        return Err(ValidationError(format!("gcov report unreadable: {error}")));
    }
    let (mut lines, mut covered_lines) = (0u64, 0u64);
    let (mut branches, mut covered_branches) = (0u64, 0u64);
    let (mut functions, mut covered_functions) = (0u64, 0u64);
    for artifact in reports {
        let report = crate::gcov::parse_gzip(&artifact.bytes, 64 * 1024 * 1024)
            .map_err(|error| ValidationError(format!("gcov evidence rejected: {error}")))?;
        for file in &report.files {
            for function in &file.functions {
                functions += 1;
                covered_functions += u64::from(function.execution_count > 0);
            }
            for line in &file.lines {
                lines += 1;
                covered_lines += u64::from(line.count > 0);
                for branch in &line.branches {
                    branches += 1;
                    covered_branches += u64::from(branch.count > 0);
                }
            }
        }
    }
    if lines == 0 {
        return Err(ValidationError("gcov reports held no line data".into()));
    }
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    if stderr.contains("stamp mismatch") || stderr.contains("version mismatch") {
        output
            .observation
            .limitations
            .push("gcov reported stale instrumentation (.gcda/.gcno mismatch)".into());
    }
    let percent = |covered: u64, total: u64| -> Measurement {
        Measurement {
            name: String::new(),
            value: ici_domain::python_round_one(covered as f64 / total as f64 * 100.0),
            evidence: EvidenceLevel::Measured,
            unit: "%".into(),
            numerator: Some(covered),
            denominator: Some(total),
        }
    };
    if functions > 0 {
        output.observation.measurements.push(Measurement {
            name: "coverage.cpp.functions".into(),
            ..percent(covered_functions, functions)
        });
    }
    output.observation.measurements.push(Measurement {
        name: "coverage.cpp.lines".into(),
        ..percent(covered_lines, lines)
    });
    // A zero branch denominator is a bare 0.0, never a refused 0/0 pair.
    output.observation.measurements.push(if branches > 0 {
        Measurement {
            name: "coverage.cpp.branches".into(),
            ..percent(covered_branches, branches)
        }
    } else {
        Measurement {
            name: "coverage.cpp.branches".into(),
            value: 0.0,
            evidence: EvidenceLevel::Measured,
            unit: "%".into(),
            numerator: None,
            denominator: None,
        }
    });
    output.summary = format!("gcov: {covered_lines}/{lines} lines");
    Ok(())
}
