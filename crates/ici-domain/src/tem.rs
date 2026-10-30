//! TEM, the versioned test-effectiveness formula (frozen `application/tem.py`), and the run
//! metrics and limitations every next result appends (frozen `application/report.py`
//! `tem_metrics`).
use crate::{Measurement, Observation};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Bumped only when the formula or its inputs change meaning.
pub const FORMULA_VERSION: &str = "tem/1";
const MAX_SCORE: f64 = 5.0;
const COVERAGE_CAP: f64 = 80.0;
/// Historical branch-to-line conversion, preserved verbatim from the stable engine.
const BRANCH_TO_LINE: f64 = 1.25;

/// Percent inputs; `None` is "not measured", distinct from a measured 0.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct TemInputs {
    pub passed: u64,
    pub total: u64,
    pub line_coverage: Option<f64>,
    pub branch_coverage: Option<f64>,
    pub function_coverage: Option<f64>,
    pub estimated_coverage: Option<f64>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TemResult {
    pub score: Option<f64>,
    pub formula: String,
    pub pass_rate: f64,
    pub coverage_label: String,
    pub coverage_shown: Option<f64>,
    pub estimated: bool,
    pub reason: String,
}

fn round_to(value: f64, places: usize) -> f64 {
    format!("{value:.places$}")
        .parse()
        .expect("finite formatted value")
}

/// Frozen `calculate`: the score, or the recorded reason it cannot be computed.
pub fn calculate(input: &TemInputs) -> TemResult {
    let mut result = TemResult {
        score: None,
        formula: FORMULA_VERSION.into(),
        pass_rate: 0.0,
        coverage_label: String::new(),
        coverage_shown: None,
        estimated: false,
        reason: String::new(),
    };
    if input.passed > input.total {
        result.reason = format!(
            "invalid test counts (passed={}, total={})",
            input.passed, input.total
        );
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
        line.min(COVERAGE_CAP) / COVERAGE_CAP
    } else if let Some(branch) = input.branch_coverage {
        result.coverage_label = "Branch".into();
        result.coverage_shown = Some(branch);
        (branch * BRANCH_TO_LINE).min(COVERAGE_CAP) / COVERAGE_CAP
    } else if let Some(estimated) = input.estimated_coverage {
        result.coverage_label = "Line".into();
        result.coverage_shown = Some(estimated);
        result.estimated = true;
        estimated.min(COVERAGE_CAP) / COVERAGE_CAP
    } else {
        result.reason = "no coverage evidence — the score is not computed".into();
        return result;
    };
    let Some(function) = input.function_coverage else {
        result.reason = "function coverage was not measured — the score is not computed".into();
        return result;
    };
    let raw = factor * (function / 100.0) * result.pass_rate * MAX_SCORE;
    result.score = Some(round_to(raw, 2).clamp(0.0, MAX_SCORE));
    result.pass_rate = round_to(result.pass_rate, 4);
    result
}

/// The component a task belongs to, from its `{component}.{python|cpp}.…` id; else "".
pub fn component_of(task_id: &str) -> &str {
    [".python.", ".cpp."]
        .iter()
        .find_map(|marker| task_id.split_once(marker).map(|(component, _)| component))
        .unwrap_or("")
}

const LINES: [&str; 2] = ["coverage.lines", "coverage.cpp.lines"];
const BRANCHES: [&str; 2] = ["coverage.branches", "coverage.cpp.branches"];
const FUNCTIONS: [&str; 2] = ["coverage.functions", "coverage.cpp.functions"];

fn cases(measurement: &Measurement) -> Option<(u64, u64)> {
    let total = measurement.denominator.filter(|total| *total > 0)?;
    measurement
        .name
        .ends_with(".cases")
        .then(|| (measurement.numerator.unwrap_or(0), total))
}

/// Frozen `inputs_from`: raw pairs win over bare percents; the last coverage measurement wins.
fn inputs_from(measurements: &[&Measurement]) -> TemInputs {
    let mut inputs = TemInputs::default();
    let percent = |item: &Measurement| match (item.numerator, item.denominator) {
        (Some(numerator), Some(total)) if total > 0 => numerator as f64 / total as f64 * 100.0,
        _ => item.value,
    };
    for item in measurements {
        if let Some((passed, total)) = cases(item) {
            inputs.passed += passed;
            inputs.total += total;
        } else if LINES.contains(&item.name.as_str()) {
            inputs.line_coverage = Some(percent(item));
        } else if BRANCHES.contains(&item.name.as_str()) {
            inputs.branch_coverage = Some(percent(item));
        } else if FUNCTIONS.contains(&item.name.as_str()) {
            inputs.function_coverage = Some(percent(item));
        }
    }
    inputs
}

/// Frozen `ComponentCounts`: the only mergeable form, raw counts with no percent-only evidence.
#[derive(Default)]
struct Counts {
    name: String,
    passed: u64,
    total: u64,
    lines: (Option<u64>, Option<u64>),
    branches: (Option<u64>, Option<u64>),
    functions: (Option<u64>, Option<u64>),
}

fn counts_from(name: &str, measurements: &[&Measurement]) -> Counts {
    let mut counts = Counts {
        name: name.into(),
        ..Counts::default()
    };
    for item in measurements {
        let pair = (item.numerator, item.denominator);
        if let Some((passed, total)) = cases(item) {
            counts.passed += passed;
            counts.total += total;
        } else if LINES.contains(&item.name.as_str()) {
            counts.lines = pair;
        } else if BRANCHES.contains(&item.name.as_str()) {
            counts.branches = pair;
        } else if FUNCTIONS.contains(&item.name.as_str()) {
            counts.functions = pair;
        }
    }
    counts
}

/// Frozen `aggregate`: merge raw counts only; a component without line or branch counts is
/// named and left out rather than blended into a denominator it did not earn.
fn aggregate(components: &[Counts]) -> (TemInputs, Vec<String>) {
    let (mergeable, excluded): (Vec<&Counts>, Vec<&Counts>) = components
        .iter()
        .partition(|item| item.lines.0.is_some() || item.branches.0.is_some());
    let excluded = excluded.iter().map(|item| item.name.clone()).collect();
    if mergeable.is_empty() {
        return (TemInputs::default(), excluded);
    }
    let percent = |pairs: Vec<(Option<u64>, Option<u64>)>| {
        let numerator: u64 = pairs.iter().filter_map(|pair| pair.0).sum();
        let denominator: u64 = pairs.iter().filter_map(|pair| pair.1).sum();
        (denominator > 0).then(|| numerator as f64 / denominator as f64 * 100.0)
    };
    let merged = TemInputs {
        passed: mergeable.iter().map(|item| item.passed).sum(),
        total: mergeable.iter().map(|item| item.total).sum(),
        line_coverage: percent(mergeable.iter().map(|item| item.lines).collect()),
        branch_coverage: percent(mergeable.iter().map(|item| item.branches).collect()),
        function_coverage: mergeable
            .iter()
            .all(|item| item.functions.0.is_some() && item.functions.1.is_some())
            .then(|| percent(mergeable.iter().map(|item| item.functions).collect()))
            .flatten(),
        estimated_coverage: None,
    };
    (merged, excluded)
}

fn metric(name: String, value: f64, unit: String) -> Measurement {
    Measurement {
        unit,
        ..Measurement::measured(name, value)
    }
}

/// Frozen `tem_metrics`: one score per component that measured tests and coverage, plus a
/// workspace score merged from raw counts. A component whose evidence cannot produce a score is
/// a limitation, never a 0 or a 100. Tasks outside a `.python.`/`.cpp.` id group as "workspace".
pub fn run_metrics(observations: &[Observation]) -> (Vec<Measurement>, Vec<String>) {
    let mut grouped: BTreeMap<&str, Vec<&Measurement>> = BTreeMap::new();
    for observation in observations {
        grouped
            .entry(component_of(&observation.task_id))
            .or_default()
            .extend(&observation.measurements);
    }
    let (mut metrics, mut limitations, mut components) = (vec![], vec![], vec![]);
    for (name, measurements) in &grouped {
        let label = if name.is_empty() { "workspace" } else { name };
        let result = calculate(&inputs_from(measurements));
        let Some(score) = result.score else {
            limitations.push(format!("{label}: TEM N/A — {}", result.reason));
            continue;
        };
        let estimated = if result.estimated { " est" } else { "" };
        metrics.push(metric(
            format!("tem.{label}"),
            score,
            format!("{FORMULA_VERSION} {}{estimated}", result.coverage_label),
        ));
        components.push(counts_from(label, measurements));
    }
    if components.len() > 1 {
        let (merged, excluded) = aggregate(&components);
        let workspace = calculate(&merged);
        if let Some(score) = workspace.score {
            metrics.push(metric(
                "tem.workspace".into(),
                score,
                FORMULA_VERSION.into(),
            ));
        }
        for name in excluded {
            limitations.push(format!(
                "{name}: excluded from workspace TEM — no raw coverage counts"
            ));
        }
        if workspace.score.is_none() {
            limitations.push(format!("workspace: TEM N/A — {}", workspace.reason));
        }
    }
    (metrics, limitations)
}
