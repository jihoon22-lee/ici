//! Baseline-only coverage policy over validated raw denominators. No I/O or execution.
use crate::{ValidationError, ValidationPlan, coverage::CoverageReport, policy};
use ici_domain::{AnalysisOutput, Finding, InspectionTarget, SourceSpan, sha256};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub const SNAPSHOT_SCHEMA: &str = "ici.coverage-policy";
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RatioState {
    Measured,
    Empty,
    Unavailable,
    NotSelected,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoverageRatio {
    pub covered: Option<u64>,
    pub total: Option<u64>,
    pub state: RatioState,
    pub reason: String,
}
impl CoverageRatio {
    pub fn counts(covered: Option<u64>, total: Option<u64>) -> Self {
        let state = match (covered, total) {
            (Some(_), Some(0)) => RatioState::Empty,
            (Some(_), Some(_)) => RatioState::Measured,
            _ => RatioState::Unavailable,
        };
        Self {
            covered,
            total,
            reason: match state {
                RatioState::Empty => "known empty coverage denominator",
                RatioState::Unavailable => "coverage denominator was not measured",
                _ => "",
            }
            .into(),
            state,
        }
    }
    pub fn percentage(&self) -> Option<f64> {
        self.covered
            .zip(self.total)
            .filter(|(covered, total)| *total > 0 && covered <= total)
            .map(|(covered, total)| covered as f64 / total as f64 * 100.0)
    }
    fn validate(&self) -> Result<(), ValidationError> {
        let valid = match self.state {
            RatioState::Measured => self
                .covered
                .zip(self.total)
                .is_some_and(|(covered, total)| total > 0 && covered <= total),
            RatioState::Empty => self.covered == Some(0) && self.total == Some(0),
            RatioState::Unavailable => {
                (self.covered.is_none() || self.total.is_none()) && !self.reason.is_empty()
            }
            RatioState::NotSelected => {
                self.covered.is_none() && self.total.is_none() && !self.reason.is_empty()
            }
        };
        if valid {
            Ok(())
        } else {
            Err(ValidationError(
                "invalid coverage ratio state/counts".into(),
            ))
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoveragePolicySnapshot {
    pub schema_id: String,
    pub schema_version: u32,
    pub task_id: String,
    pub component_id: String,
    pub provider: String,
    pub anchor_path: String,
    /// Percentage fields retain the historical coverage-policy presentation shape.
    pub metrics: BTreeMap<String, f64>,
    pub files: BTreeMap<String, f64>,
    pub regression_tolerance: Option<f64>,
    pub raw_metrics: BTreeMap<String, CoverageRatio>,
    pub raw_files: BTreeMap<String, CoverageRatio>,
    pub min_file_statements: u64,
}
impl CoveragePolicySnapshot {
    pub fn from_value(value: Value) -> Result<Self, ValidationError> {
        let snapshot: Self = serde_json::from_value(value)
            .map_err(|error| ValidationError(format!("coverage policy snapshot: {error}")))?;
        snapshot.validate()?;
        Ok(snapshot)
    }
    pub fn validate(&self) -> Result<(), ValidationError> {
        if self.schema_id != SNAPSHOT_SCHEMA
            || self.schema_version != 1
            || !matches!(self.provider.as_str(), "coverage" | "gcov")
            || self.task_id.is_empty()
            || self.task_id.len() > 512
            || self.component_id.is_empty()
            || self.component_id.len() > 128
            || self.min_file_statements == 0
            || self.raw_files.len() > 10000
            || self.files.len() > 10000
            || self
                .regression_tolerance
                .is_some_and(|value| !value.is_finite() || !(0.0..=100.0).contains(&value))
        {
            return Err(ValidationError(
                "invalid or unsupported coverage policy snapshot contract".into(),
            ));
        }
        ici_domain::validation::relative_path(&self.anchor_path, false)
            .map_err(|error| ValidationError(error.to_string()))?;
        let axes = ["line", "branch", "function", "changed_line"];
        if self.raw_metrics.len() != axes.len()
            || axes
                .iter()
                .any(|axis| !self.raw_metrics.contains_key(*axis))
            || self
                .metrics
                .keys()
                .any(|axis| !axes.contains(&axis.as_str()))
        {
            return Err(ValidationError(
                "coverage snapshot has unknown or missing metric axes".into(),
            ));
        }
        for ratio in self.raw_metrics.values().chain(self.raw_files.values()) {
            ratio.validate()?;
        }
        for path in self.raw_files.keys() {
            ici_domain::validation::relative_path(path, false)
                .map_err(|error| ValidationError(error.to_string()))?;
        }
        let expected_metrics = self
            .raw_metrics
            .iter()
            .filter_map(|(name, ratio)| ratio.percentage().map(|value| (name.clone(), value)))
            .collect::<BTreeMap<_, _>>();
        let expected_files = self
            .raw_files
            .iter()
            .filter(|(_, ratio)| {
                ratio
                    .total
                    .is_some_and(|total| total >= self.min_file_statements)
            })
            .filter_map(|(path, ratio)| ratio.percentage().map(|value| (path.clone(), value)))
            .collect::<BTreeMap<_, _>>();
        let agrees = |actual: &BTreeMap<String, f64>, expected: &BTreeMap<String, f64>| {
            actual.len() == expected.len()
                && actual.iter().all(|(key, value)| {
                    value.is_finite()
                        && expected
                            .get(key)
                            .is_some_and(|expected| (value - expected).abs() <= 1e-9)
                })
        };
        if !agrees(&self.metrics, &expected_metrics) || !agrees(&self.files, &expected_files) {
            return Err(ValidationError(
                "coverage snapshot percentages disagree with raw counts or file floor".into(),
            ));
        }
        let mut sum = Some((0u64, 0u64));
        for ratio in self.raw_files.values() {
            sum = sum.and_then(|(covered, total)| {
                ratio
                    .covered
                    .zip(ratio.total)
                    .and_then(|(a, b)| covered.checked_add(a).zip(total.checked_add(b)))
            });
        }
        if let Some(sum) = sum
            && self.raw_metrics["line"]
                .covered
                .zip(self.raw_metrics["line"].total)
                != Some(sum)
        {
            return Err(ValidationError(
                "coverage snapshot aggregate line counts disagree with file denominators".into(),
            ));
        }
        Ok(())
    }
}
pub fn snapshot(
    plan: &ValidationPlan,
    report: &CoverageReport,
) -> Result<CoveragePolicySnapshot, ValidationError> {
    let mut raw_metrics: BTreeMap<String, CoverageRatio> = BTreeMap::from([
        (
            "line".into(),
            CoverageRatio::counts(
                Some(report.counts.covered_lines),
                Some(report.counts.total_lines),
            ),
        ),
        (
            "branch".into(),
            CoverageRatio::counts(
                Some(report.counts.covered_branches),
                Some(report.counts.total_branches),
            ),
        ),
        (
            "function".into(),
            CoverageRatio::counts(
                report.counts.covered_functions,
                report.counts.total_functions,
            ),
        ),
    ]);
    let changed = policy::changed_lines(
        &plan.context.options.changed_lines,
        &plan.context.source_files,
    )?;
    let changed_ratio = if changed.is_empty() {
        CoverageRatio {
            covered: None,
            total: None,
            state: RatioState::NotSelected,
            reason: "no changed-line selection was configured".into(),
        }
    } else {
        let (mut covered, mut total) = (0u64, 0u64);
        for (path, lines) in changed {
            let file = report.files.get(&path).ok_or_else(|| {
                ValidationError("changed-line source has no measured coverage".into())
            })?;
            covered += lines
                .iter()
                .filter(|line| file.executed.contains(line))
                .count() as u64;
            total += lines
                .iter()
                .filter(|line| file.executed.contains(line) || file.missing.contains(line))
                .count() as u64;
        }
        CoverageRatio::counts(Some(covered), Some(total))
    };
    raw_metrics.insert("changed_line".into(), changed_ratio);
    let raw_files = report
        .files
        .iter()
        .map(|(path, file)| {
            (
                path.clone(),
                CoverageRatio::counts(
                    Some(file.counts.covered_lines),
                    Some(file.counts.total_lines),
                ),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let result = CoveragePolicySnapshot {
        schema_id: SNAPSHOT_SCHEMA.into(),
        schema_version: 1,
        task_id: plan.context.task_id.clone(),
        component_id: plan.context.component_id.clone(),
        provider: plan.kind.provider().into(),
        anchor_path: plan
            .context
            .source_files
            .first()
            .cloned()
            .or_else(|| report.files.keys().next().cloned())
            .unwrap_or_else(|| "ici.toml".into()),
        metrics: raw_metrics
            .iter()
            .filter_map(|(name, ratio)| ratio.percentage().map(|value| (name.clone(), value)))
            .collect(),
        files: raw_files
            .iter()
            .filter(|(_, ratio)| {
                ratio
                    .total
                    .is_some_and(|total| total >= plan.context.options.min_file_statements)
            })
            .filter_map(|(path, ratio)| ratio.percentage().map(|value| (path.clone(), value)))
            .collect(),
        regression_tolerance: plan.context.options.max_coverage_regression,
        raw_metrics,
        raw_files,
        min_file_statements: plan.context.options.min_file_statements,
    };
    result.validate()?;
    Ok(result)
}
pub fn attach(
    plan: &ValidationPlan,
    report: &CoverageReport,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    output.extra["coverage_policy"] = json!(snapshot(plan, report)?);
    Ok(())
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct CoverageRegression {
    pub policy_enabled: bool,
    /// True means returned regressions are eligible for the baseline-only fail-on-new policy.
    /// The caller must additionally establish native provider/completed-scope compatibility.
    pub gating_applicable: bool,
    pub comparison_complete: bool,
    pub compared: usize,
    pub findings: Vec<Finding>,
    pub targets: Vec<InspectionTarget>,
    pub limitations: Vec<String>,
}
/// Call only after the application establishes native baseline and completed check compatibility.
pub fn compare(
    current: &CoveragePolicySnapshot,
    previous: Option<&CoveragePolicySnapshot>,
) -> Result<CoverageRegression, ValidationError> {
    current.validate()?;
    let mut result = CoverageRegression {
        policy_enabled: current.regression_tolerance.is_some(),
        comparison_complete: true,
        ..Default::default()
    };
    let Some(tolerance) = current.regression_tolerance else {
        return Ok(result);
    };
    let Some(previous) = previous else {
        result.comparison_complete = false;
        result.limitations.push("baseline has no typed coverage policy snapshot; coverage regressions were not compared".into());
        return Ok(result);
    };
    previous.validate()?;
    if current.component_id != previous.component_id
        || current.task_id != previous.task_id
        || current.provider != previous.provider
    {
        result.comparison_complete = false;
        result.limitations.push("coverage snapshot provider/component/task identity differs; regressions were not compared".into());
        return Ok(result);
    }
    for (name, prior) in &previous.raw_metrics {
        compare_ratio(
            current,
            &mut result,
            &format!("aggregate-{name}"),
            &current.anchor_path,
            prior,
            current.raw_metrics.get(name),
            tolerance,
        );
    }
    for (path, prior) in &previous.raw_files {
        let now = current.raw_files.get(path);
        if !previous.files.contains_key(path)
            || now.is_some_and(|ratio| {
                ratio
                    .total
                    .is_some_and(|total| total < current.min_file_statements)
            })
        {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "Coverage regression:file-line".into();
            target.status = "SKIP".into();
            target.message =
                "per-file regression is not evaluated below the configured statement floor".into();
            target.metrics.insert("baseline_only".into(), json!(true));
            target.metrics.insert("baseline_raw".into(), json!(prior));
            target.metrics.insert("current_raw".into(), json!(now));
            result.targets.push(target);
            continue;
        }
        compare_ratio(
            current,
            &mut result,
            "file-line",
            path,
            prior,
            now,
            tolerance,
        );
    }
    result.gating_applicable = result.compared > 0;
    Ok(result)
}
fn compare_ratio(
    current: &CoveragePolicySnapshot,
    result: &mut CoverageRegression,
    kind: &str,
    path: &str,
    prior: &CoverageRatio,
    now: Option<&CoverageRatio>,
    tolerance: f64,
) {
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = format!("Coverage regression:{kind}");
    target.metrics.insert("baseline_only".into(), json!(true));
    target
        .metrics
        .insert("gated_by".into(), json!("fail-on-new"));
    target
        .metrics
        .insert("allowed_drop".into(), json!(tolerance));
    target.metrics.insert("baseline_raw".into(), json!(prior));
    target.metrics.insert("current_raw".into(), json!(now));
    let values = prior
        .percentage()
        .zip(now.and_then(CoverageRatio::percentage));
    if let Some((before, after)) = values {
        result.compared += 1;
        let drop = before - after;
        target.metrics.insert("baseline".into(), json!(before));
        target.metrics.insert("actual".into(), json!(after));
        target.metrics.insert("drop".into(), json!(drop));
        if drop > tolerance {
            target.status = "WARN".into();
            target.message = format!(
                "Coverage regressed from {before:.4}% to {after:.4}% (allowed drop {tolerance:.4}%)"
            );
            let rule = format!("ici.test.coverage-regression.{kind}");
            let mut location = SourceSpan::line(path, 1);
            location.label = format!("coverage:{kind}");
            let mut finding = Finding::new(&rule, &target.message, location, &current.provider);
            finding.fingerprint = sha256(format!(
                "ici.coverage-regression/1\0{}\0{}\0{rule}\0{path}",
                current.component_id, current.provider
            ));
            finding.severity = "medium".into();
            finding.native_rule_id = rule;
            finding.rule_version = "coverage-regression/1".into();
            finding.component_id = Some(current.component_id.clone());
            finding.task_id = Some(current.task_id.clone());
            finding.tags = vec!["baseline-only".into(), "advisory".into()];
            result.findings.push(finding);
        } else {
            target.status = "PASS".into();
            target.message = format!(
                "Coverage drop {drop:.4}% is within the configured {tolerance:.4}% tolerance"
            );
        }
    } else {
        target.status = "SKIP".into();
        let unavailable = now.is_none()
            || prior.state == RatioState::Unavailable
            || now.is_some_and(|ratio| ratio.state == RatioState::Unavailable);
        target.message = if unavailable {
            "coverage baseline comparison has a missing/unmeasured denominator"
        } else {
            "coverage metric is not selected or has a known empty denominator"
        }
        .into();
        if unavailable {
            result.comparison_complete = false;
            result
                .limitations
                .push(format!("{kind} at {path}: {}", target.message));
        }
    }
    result.targets.push(target);
}
