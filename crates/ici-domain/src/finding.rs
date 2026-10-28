use crate::{
    EvidenceLevel,
    validation::{self, Result, Validate},
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceSpan {
    pub path: String,
    pub start_line: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end_line: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_column: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end_column: Option<u32>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub label: String,
}
impl SourceSpan {
    pub fn line(path: impl Into<String>, start_line: u32) -> Self {
        Self {
            path: path.into(),
            start_line,
            end_line: None,
            start_column: None,
            end_column: None,
            label: String::new(),
        }
    }
}
impl Validate for SourceSpan {
    fn validate(&self) -> Result<()> {
        validation::relative_path(&self.path, false)?;
        validation::require(self.start_line > 0, "line must be 1-indexed")?;
        validation::require(
            self.end_line.is_none_or(|line| line >= self.start_line),
            "end line precedes start",
        )?;
        validation::require(
            self.start_column.is_none_or(|col| col > 0)
                && self.end_column.is_none_or(|col| col > 0),
            "columns must be 1-indexed",
        )
    }
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct FindingSuppression {
    pub suppressed: bool,
    pub kind: String,
    pub reason: String,
    pub origin: String,
}
impl FindingSuppression {
    fn inactive(&self) -> bool {
        !self.suppressed
    }
}
impl Validate for FindingSuppression {
    fn validate(&self) -> Result<()> {
        validation::require(
            !self.suppressed || !self.reason.is_empty(),
            "suppression must have a reason",
        )
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Finding {
    pub fingerprint: String,
    pub rule_id: String,
    pub message: String,
    pub severity: String,
    pub confidence: String,
    pub primary_location: SourceSpan,
    pub provider: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub native_rule_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub rule_version: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub category: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub related_locations: Vec<SourceSpan>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub component_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub analysis_unit_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub variant: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    pub evidence: EvidenceLevel,
    #[serde(default, skip_serializing_if = "FindingSuppression::inactive")]
    pub suppression: FindingSuppression,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub limitations: Vec<String>,
}
impl Finding {
    pub fn counts_against_gate(&self) -> bool {
        !self.suppression.suppressed && self.evidence == EvidenceLevel::Measured
    }
    pub fn new(
        rule_id: impl Into<String>,
        message: impl Into<String>,
        location: SourceSpan,
        provider: impl Into<String>,
    ) -> Self {
        Self {
            fingerprint: String::new(),
            rule_id: rule_id.into(),
            message: message.into(),
            severity: "warning".into(),
            confidence: "high".into(),
            primary_location: location,
            provider: provider.into(),
            native_rule_id: String::new(),
            rule_version: String::new(),
            category: String::new(),
            tags: vec![],
            related_locations: vec![],
            component_id: None,
            analysis_unit_id: None,
            variant: None,
            task_id: None,
            evidence: EvidenceLevel::Measured,
            suppression: FindingSuppression::default(),
            limitations: vec![],
        }
    }
}
impl Validate for Finding {
    fn validate(&self) -> Result<()> {
        for (value, name) in [
            (&self.fingerprint, "fingerprint"),
            (&self.rule_id, "rule_id"),
            (&self.message, "message"),
            (&self.severity, "severity"),
            (&self.confidence, "confidence"),
            (&self.provider, "provider"),
        ] {
            validation::text(value, name)?;
        }
        self.primary_location.validate()?;
        for span in &self.related_locations {
            span.validate()?;
        }
        self.suppression.validate()
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Measurement {
    pub name: String,
    pub value: f64,
    pub evidence: EvidenceLevel,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub unit: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub numerator: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub denominator: Option<u64>,
}
impl Measurement {
    pub fn measured(name: impl Into<String>, value: f64) -> Self {
        Self {
            name: name.into(),
            value,
            evidence: EvidenceLevel::Measured,
            unit: String::new(),
            numerator: None,
            denominator: None,
        }
    }
}
impl Validate for Measurement {
    fn validate(&self) -> Result<()> {
        validation::text(&self.name, "metric name")?;
        validation::require(self.value.is_finite(), "metric must be finite")?;
        validation::require(
            self.numerator.is_none() || self.denominator != Some(0),
            "metric denominator must not be zero",
        )
    }
}
