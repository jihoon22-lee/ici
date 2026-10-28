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
