//! Shared pure analysis boundary. Inputs are snapshotted bytes, not a Python worker API.
use crate::Observation;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SourceInput {
    pub path: String,
    pub bytes: Vec<u8>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AnalysisRequest {
    pub task_id: String,
    pub check_id: String,
    pub component_id: String,
    pub language: String,
    /// Project-relative paths, immutable content captured by the application.
    pub inputs: Vec<SourceInput>,
    /// Resolved rule options. External tool configuration is not copied here.
    pub options: Value,
    /// Explicit project target, otherwise the documented tested grammar default.
    pub target_version: String,
}
/// All inspected locations, including PASS. Legacy projection must not invent these.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InspectionTarget {
    pub file_path: String,
    pub start_line: u32,
    pub end_line: Option<u32>,
    pub target_name: String,
    pub status: String,
    pub message: String,
    pub snippet: String,
    pub metrics: BTreeMap<String, Value>,
    pub start_column: Option<u32>,
    pub end_column: Option<u32>,
}
impl InspectionTarget {
    pub fn at(path: impl Into<String>, line: u32) -> Self {
        Self {
            file_path: path.into(),
            start_line: line,
            end_line: None,
            target_name: String::new(),
            status: "PASS".into(),
            message: String::new(),
            snippet: String::new(),
            metrics: BTreeMap::new(),
            start_column: None,
            end_column: None,
        }
    }
}
/// One computation feeds modern observations and the richer historical report projection.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AnalysisOutput {
    pub observation: Observation,
    pub targets: Vec<InspectionTarget>,
    pub summary: String,
    pub status: String,
    pub score: Option<f64>,
    pub max_score: Option<f64>,
    pub extra: Value,
}
impl AnalysisOutput {
    pub fn new(task_id: impl Into<String>, provider: impl Into<String>) -> Self {
        Self {
            observation: Observation::new(task_id, provider),
            targets: vec![],
            summary: String::new(),
            status: "PASS".into(),
            score: None,
            max_score: None,
            extra: Value::Object(Default::default()),
        }
    }
}

/// Frozen Python `round(value, 1)` for the historical one-decimal percentages: both round the
/// exact binary value, so ties of the binary value go to even and `99.95` stays `100.0`.
pub fn python_round_one(value: f64) -> f64 {
    format!("{value:.1}").parse().unwrap_or(value)
}

#[cfg(test)]
mod round_tests {
    #[test]
    fn matches_python_round_on_exact_and_inexact_ties() {
        // Expected values printed by CPython 3 `round(v, 1)`.
        for (value, expected) in [
            (0.25, 0.2),
            (0.75, 0.8),
            (0.05, 0.1),
            (0.15, 0.1),
            (1.25, 1.2),
            (12.25, 12.2),
            (99.95, 100.0),
            (83.333_333_333_333_33, 83.3),
            (66.666_666_666_666_67, 66.7),
            (100.0, 100.0),
            (0.0, 0.0),
            (1e-9, 0.0),
            (45.05, 45.0),
            (26.75, 26.8),
        ] {
            assert_eq!(super::python_round_one(value), expected, "{value}");
        }
    }
}
