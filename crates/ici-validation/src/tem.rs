//! Versioned TEM formula and aggregation through validated raw counts only.
use crate::{ValidationError, coverage::Counts};
use serde::{Deserialize, Serialize};

pub use ici_domain::tem::{TemInputs, TemResult};
/// The frozen formula (`ici_domain::tem::calculate`), refusing counts and percents a validated
/// report cannot carry before it is applied.
pub fn calculate(input: &TemInputs) -> TemResult {
    let invalid = input.passed > input.total
        || [
            input.line_coverage,
            input.branch_coverage,
            input.function_coverage,
            input.estimated_coverage,
        ]
        .into_iter()
        .flatten()
        .any(|v| !v.is_finite() || !(0.0..=100.0).contains(&v));
    if invalid {
        return TemResult {
            score: None,
            formula: ici_domain::tem::FORMULA_VERSION.into(),
            pass_rate: 0.0,
            coverage_label: String::new(),
            coverage_shown: None,
            estimated: false,
            reason: "invalid test or coverage counts".into(),
        };
    }
    ici_domain::tem::calculate(input)
}
/// Frozen legacy engines feed TEM the coverage rows' `round(cover, 1)` line and branch
/// percentages; the next formula uses the raw ratio. Function coverage stays unrounded in both.
pub fn legacy_inputs(input: &TemInputs) -> TemInputs {
    let round1 = |value: Option<f64>| value.map(ici_domain::python_round_one);
    TemInputs {
        line_coverage: round1(input.line_coverage),
        branch_coverage: round1(input.branch_coverage),
        ..input.clone()
    }
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
