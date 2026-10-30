use crate::{
    validation::{self, DomainError, Result},
    *,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
pub const RUN_SCHEMA_ID: &str = "ici.next.run";
pub const RUN_SCHEMA_VERSION: u32 = 2;
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Producer {
    pub ici_version: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bundle_digest: Option<String>,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopeSelection {
    pub kind: ScopeKind,
    pub selected_components: Vec<String>,
    pub selected_languages: Vec<String>,
    pub required_components: Vec<String>,
    pub omitted_components: Vec<String>,
    pub full_required_satisfied: bool,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionSummary {
    pub required_complete: bool,
    pub cancelled: bool,
    pub blocked_task_ids: Vec<String>,
    pub failed_task_ids: Vec<String>,
    pub reused_task_ids: Vec<String>,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GateOutcome {
    pub selected: GateVerdict,
    pub workspace: GateVerdict,
    pub has_violations: bool,
    pub reasons: Vec<String>,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublicationOutcome {
    pub state: PublicationState,
    pub detail: String,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BaselineComparison {
    pub state: BaselineState,
    pub origin: String,
    pub reason: String,
    pub new: Vec<String>,
    pub unchanged: Vec<String>,
    pub resolved: Vec<String>,
    pub carried: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CheckRecord {
    pub task_id: String,
    pub check_id: String,
    pub component_id: String,
    pub language: String,
    pub scope_digest: String,
    pub state: TaskState,
    pub evidence_complete: bool,
    pub analysis_unit_id: Option<String>,
    pub variant: Option<String>,
}
impl CheckRecord {
    pub fn same_scope(&self, other: &Self) -> bool {
        self.task_id == other.task_id
            && self.check_id == other.check_id
            && self.component_id == other.component_id
            && self.language == other.language
            && self.scope_digest == other.scope_digest
            && self.analysis_unit_id == other.analysis_unit_id
            && self.variant == other.variant
    }
}
impl Validate for CheckRecord {
    fn validate(&self) -> Result<()> {
        for (value, name) in [
            (&self.task_id, "task_id"),
            (&self.check_id, "check_id"),
            (&self.component_id, "component_id"),
            (&self.language, "language"),
        ] {
            validation::text(value, name)?;
        }
        validation::digest(&self.scope_digest, "check scope")?;
        validation::require(
            !self.evidence_complete || self.state == TaskState::Succeeded,
            "only succeeded checks carry complete evidence",
        )
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunResult {
    pub schema_id: String,
    pub schema_version: u32,
    pub run_id: String,
    pub producer: Producer,
    pub identity: RunIdentity,
    pub scope: ScopeSelection,
    pub execution: ExecutionSummary,
    pub gate: GateOutcome,
    pub findings: Vec<Finding>,
    pub metrics: Vec<Measurement>,
    pub publication: PublicationOutcome,
    pub limitations: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline: Option<BaselineComparison>,
    #[serde(default)]
    pub checks: Option<Vec<CheckRecord>>,
}
impl RunResult {
    pub fn exit_code(&self) -> i32 {
        if self.execution.cancelled {
            130
        } else {
            self.gate.selected.exit_code()
        }
    }
    pub fn to_value(&self) -> Result<Value> {
        self.validate()?;
        let mut value = serde_json::to_value(self).map_err(|e| DomainError(e.to_string()))?;
        if self.schema_version == 1 {
            value
                .as_object_mut()
                .expect("serialized struct")
                .remove("checks");
        }
        Ok(value)
    }
    pub fn to_json(&self) -> Result<String> {
        canonical_json(&self.to_value()?)
    }
    pub fn from_json(text: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(text)
            .map_err(|e| DomainError(format!("result is not valid JSON: {e}")))?;
        Self::from_value(value)
    }
    pub fn from_value(value: Value) -> Result<Self> {
        validation::require(
            value["schema_id"] == RUN_SCHEMA_ID,
            "expected ici.next.run result",
        )?;
        let version = value["schema_version"]
            .as_u64()
            .ok_or_else(|| DomainError("schema_version must be an integer".into()))?;
        validation::require(
            version == 1 || version == 2,
            format!("unsupported result schema {version}"),
        )?;
        let checks = value.get("checks");
        validation::require(
            if version == 1 {
                checks.is_none()
            } else {
                checks.is_some()
            },
            "v1 must omit checks; v2 requires checks (null means unknown)",
        )?;
        if let Some(Value::Array(records)) = checks {
            for record in records {
                validation::require(
                    record.get("analysis_unit_id").is_some() && record.get("variant").is_some(),
                    "check requires explicit analysis_unit_id and variant",
                )?;
            }
        }
        let result: Self = serde_json::from_value(value).map_err(|e| DomainError(e.to_string()))?;
        result.validate()?;
        Ok(result)
    }
}
impl Validate for RunResult {
    fn validate(&self) -> Result<()> {
        validation::require(
            self.schema_id == RUN_SCHEMA_ID
                && (self.schema_version == 1 || self.schema_version == 2),
            "unsupported result envelope",
        )?;
        validation::text(&self.run_id, "run_id")?;
        validation::text(&self.producer.ici_version, "ici_version")?;
        if let Some(digest) = &self.producer.bundle_digest {
            validation::digest(digest, "bundle digest")?;
        }
        self.identity.validate()?;
        validation::require(
            self.scope.kind != ScopeKind::Full || self.scope.full_required_satisfied,
            "full scope must satisfy required components",
        )?;
        validation::require(
            !self.execution.required_complete
                || (!self.execution.cancelled && self.execution.blocked_task_ids.is_empty()),
            "cancelled or blocked work cannot be complete",
        )?;
        for (items, name) in [
            (&self.scope.selected_components, "selected component"),
            (&self.scope.required_components, "required component"),
            (&self.scope.omitted_components, "omitted component"),
            (&self.scope.selected_languages, "selected language"),
        ] {
            validation::unique(items, name)?;
            // Frozen `ScopeSelection`: every entry is an identifier.
            for item in items {
                validation::identifier(item, name)?;
            }
        }
        // Frozen `ExecutionSummary`: blocked, failed and reused task ids are identifiers.
        for (items, name) in [
            (&self.execution.blocked_task_ids, "blocked task id"),
            (&self.execution.failed_task_ids, "failed task id"),
            (&self.execution.reused_task_ids, "reused task id"),
        ] {
            for item in items {
                validation::identifier(item, name)?;
            }
        }
        validation::require(
            !self.scope.full_required_satisfied
                || (self.scope.kind == ScopeKind::Full && self.scope.omitted_components.is_empty()),
            "partial scope cannot satisfy full workspace",
        )?;
        validation::require(
            self.gate.workspace != GateVerdict::Pass || self.scope.full_required_satisfied,
            "workspace PASS requires full scope evidence",
        )?;
        validation::require(
            self.gate.selected != GateVerdict::Pass
                || (!self.gate.has_violations && self.execution.required_complete),
            "incomplete or violating result cannot PASS",
        )?;
        validation::require(
            !matches!(
                self.gate.selected,
                GateVerdict::Fail | GateVerdict::Incomplete
            ) || !self.gate.reasons.is_empty(),
            "FAIL/INCOMPLETE must explain why",
        )?;
        validation::require(
            !self.gate.has_violations || self.findings.iter().any(Finding::counts_against_gate),
            "violations require measured unsuppressed findings",
        )?;
        for finding in &self.findings {
            finding.validate()?;
        }
        for metric in &self.metrics {
            metric.validate()?;
        }
        if let Some(checks) = &self.checks {
            validation::require(self.schema_version == 2, "v1 cannot carry check records")?;
            let mut ids = std::collections::BTreeSet::new();
            for check in checks {
                check.validate()?;
                validation::require(ids.insert(&check.task_id), "duplicate check task")?;
            }
        }
        if let Some(baseline) = &self.baseline {
            validation::require(
                baseline.state != BaselineState::Incompatible
                    || (baseline.new.is_empty()
                        && baseline.unchanged.is_empty()
                        && baseline.resolved.is_empty()
                        && baseline.carried.is_empty()),
                "incompatible baseline cannot carry delta",
            )?;
        }
        Ok(())
    }
}
