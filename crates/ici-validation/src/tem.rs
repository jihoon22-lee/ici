//! Versioned TEM formula and aggregation through validated raw counts only.
use crate::{ValidationError, coverage::Counts};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct TemInputs {
    pub passed: u64,
    pub total: u64,
    pub line_coverage: Option<f64>,
    pub branch_coverage: Option<f64>,
    pub function_coverage: Option<f64>,
    pub estimated_coverage: Option<f64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TemResult {
    pub score: Option<f64>,
    pub formula: String,
    pub pass_rate: f64,
    pub coverage_label: String,
    pub coverage_shown: Option<f64>,
    pub estimated: bool,
    pub reason: String,
}
pub fn calculate(input: &TemInputs) -> TemResult {
    let mut result = TemResult {
        score: None,
        formula: "tem/1".into(),
        pass_rate: 0.0,
        coverage_label: String::new(),
        coverage_shown: None,
        estimated: false,
        reason: String::new(),
    };
    if input.passed > input.total
        || [
            input.line_coverage,
            input.branch_coverage,
            input.function_coverage,
            input.estimated_coverage,
        ]
        .into_iter()
        .flatten()
        .any(|v| !v.is_finite() || !(0.0..=100.0).contains(&v))
    {
        result.reason = "invalid test or coverage counts".into();
        return result;
    }
    result.pass_rate = if input.total == 0 {
        0.0
    } else {
        input.passed as f64 / input.total as f64
    };
    let factor = if let Some(line) = input.line_coverage {
        result.coverage_label = "Line".into();
        result.coverage_shown = Some(line);
        line.min(80.0) / 80.0
    } else if let Some(branch) = input.branch_coverage {
        result.coverage_label = "Branch".into();
        result.coverage_shown = Some(branch);
        (branch * 1.25).min(80.0) / 80.0
    } else if let Some(estimated) = input.estimated_coverage {
        result.coverage_label = "Line".into();
        result.coverage_shown = Some(estimated);
        result.estimated = true;
        estimated.min(80.0) / 80.0
    } else {
        result.reason = "no coverage evidence — the score is not computed".into();
        return result;
    };
    let Some(function) = input.function_coverage else {
        result.reason = "function coverage was not measured — the score is not computed".into();
        return result;
    };
    let raw = factor * (function / 100.0) * result.pass_rate * 5.0;
    result.score = Some(
        format!("{:.2}", raw.clamp(0.0, 5.0))
            .parse()
            .expect("finite formatted score"),
    );
    result.pass_rate = format!("{:.4}", result.pass_rate)
        .parse()
        .expect("finite rate");
    result
}
pub fn percentage(numerator: u64, denominator: u64) -> Option<f64> {
    (denominator > 0).then(|| numerator as f64 / denominator as f64 * 100.0)
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ComponentCounts {
    pub name: String,
    pub passed: u64,
    pub total: u64,
    pub coverage: Option<Counts>,
}
pub fn aggregate(
    components: &[ComponentCounts],
) -> Result<(TemInputs, Vec<String>), ValidationError> {
    let mut result = TemInputs::default();
    let mut excluded = vec![];
    let (mut lines, mut branches, mut functions) = ((0u64, 0u64), (0u64, 0u64), (0u64, 0u64));
    let mut all_functions = true;
    for component in components {
        if component.passed > component.total {
            return Err(ValidationError("invalid raw test counts".into()));
        }
        let Some(coverage) = &component.coverage else {
            excluded.push(component.name.clone());
            continue;
        };
        if coverage.covered_lines > coverage.total_lines
            || coverage.covered_branches > coverage.total_branches
            || coverage
                .covered_functions
                .zip(coverage.total_functions)
                .is_some_and(|(a, b)| a > b)
        {
            return Err(ValidationError("invalid raw coverage counts".into()));
        }
        result.passed = result
            .passed
            .checked_add(component.passed)
            .ok_or_else(|| ValidationError("test count overflow".into()))?;
        result.total = result
            .total
            .checked_add(component.total)
            .ok_or_else(|| ValidationError("test count overflow".into()))?;
        for (sum, pair) in [
            (&mut lines, (coverage.covered_lines, coverage.total_lines)),
            (
                &mut branches,
                (coverage.covered_branches, coverage.total_branches),
            ),
        ] {
            sum.0 = sum
                .0
                .checked_add(pair.0)
                .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
            sum.1 = sum
                .1
                .checked_add(pair.1)
                .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
        }
        if let Some((covered, total)) = coverage.covered_functions.zip(coverage.total_functions) {
            functions.0 = functions
                .0
                .checked_add(covered)
                .ok_or_else(|| ValidationError("function count overflow".into()))?;
            functions.1 = functions
                .1
                .checked_add(total)
                .ok_or_else(|| ValidationError("function count overflow".into()))?;
        } else {
            all_functions = false;
        }
    }
    result.line_coverage = percentage(lines.0, lines.1);
    result.branch_coverage = percentage(branches.0, branches.1);
    result.function_coverage = if all_functions {
        percentage(functions.0, functions.1)
    } else {
        None
    };
    Ok((result, excluded))
}
