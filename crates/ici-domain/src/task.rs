use crate::{
    validation::{self, Result},
    *,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TaskSpec {
    pub id: String,
    pub kind: TaskKind,
    pub provider: String,
    pub argv: Vec<String>,
    pub cwd: String,
    #[serde(default)]
    pub analysis_unit_ids: Vec<String>,
    #[serde(default)]
    pub env_overlay: BTreeMap<String, String>,
    /// None inherits the caller's environment; Some explicitly lists inherited keys.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub env_inherit: Option<Vec<String>>,
    #[serde(default)]
    pub input_refs: Vec<String>,
    #[serde(default)]
    pub output_specs: Vec<String>,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub resource_keys: Vec<String>,
    #[serde(default)]
    pub work_dirs: Vec<String>,
    #[serde(default)]
    pub requires: Vec<String>,
    pub timeout_seconds: Option<f64>,
    pub output_limit_bytes: Option<usize>,
    pub tool_digest: Option<String>,
    pub cacheable: bool,
    pub variant: Option<String>,
}
impl TaskSpec {
    pub fn command(
        id: impl Into<String>,
        provider: impl Into<String>,
        argv: Vec<String>,
        cwd: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            kind: TaskKind::Analyze,
            provider: provider.into(),
            argv,
            cwd: cwd.into(),
            analysis_unit_ids: vec![],
            env_overlay: BTreeMap::new(),
            env_inherit: None,
            input_refs: vec![],
            output_specs: vec![],
            depends_on: vec![],
            resource_keys: vec![],
            work_dirs: vec![],
            requires: vec![],
            timeout_seconds: Some(300.0),
            output_limit_bytes: Some(8 * 1024 * 1024),
            tool_digest: None,
            cacheable: false,
            variant: None,
        }
    }
    pub fn mutating(&self) -> bool {
        self.kind == TaskKind::Prepare
    }
    pub fn share_key(&self) -> Result<String> {
        let mut payload =
            serde_json::to_value(self).map_err(|e| crate::DomainError(e.to_string()))?;
        let fields = payload.as_object_mut().expect("task serializes as object");
        fields.remove("id");
        fields.remove("analysis_unit_ids");
        Ok(sha256(canonical_json(&payload)?))
    }
}
impl Validate for TaskSpec {
    fn validate(&self) -> Result<()> {
        validation::text(&self.id, "task id")?;
        validation::text(&self.provider, "provider")?;
        validation::text(&self.cwd, "cwd")?;
        validation::require(!self.argv.is_empty(), "command must not be empty")?;
        for arg in &self.argv {
            validation::require(!arg.contains('\0'), "argv contains NUL")?;
        }
        validation::require(!self.argv[0].is_empty(), "executable must not be empty")?;
        validation::unique(&self.depends_on, "dependency")?;
        validation::require(
            !self.depends_on.contains(&self.id),
            "task cannot depend on itself",
        )?;
        validation::require(
            self.timeout_seconds
                .is_none_or(|v| v.is_finite() && v > 0.0),
            "timeout must be finite and positive",
        )?;
        validation::require(
            !self.mutating() || !self.cacheable,
            "prepare tasks cannot be cacheable",
        )?;
        if let Some(digest) = &self.tool_digest {
            validation::digest(digest, "tool digest")?;
        }
        for (name, value) in &self.env_overlay {
            validation::require(
                !name.is_empty() && !name.contains(['=', '\0']) && !value.contains('\0'),
                "invalid environment overlay",
            )?;
        }
        if let Some(names) = &self.env_inherit {
            validation::unique(names, "inherited environment key")?;
            for name in names {
                validation::require(
                    !name.is_empty() && !name.contains(['=', '\0']),
                    "invalid inherited environment key",
                )?;
            }
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Observation {
    pub task_id: String,
    pub provider: String,
    pub state: TaskState,
    #[serde(default)]
    pub findings: Vec<Finding>,
    #[serde(default)]
    pub measurements: Vec<Measurement>,
    pub exit_code: Option<i32>,
    pub signal: Option<String>,
    #[serde(default)]
    pub timed_out: bool,
    #[serde(default)]
    pub truncated: bool,
    pub duration_seconds: Option<f64>,
    #[serde(default)]
    pub tool_versions: Vec<(String, String)>,
    #[serde(default)]
    pub limitations: Vec<String>,
}
impl Observation {
    pub fn new(task_id: impl Into<String>, provider: impl Into<String>) -> Self {
        Self {
            task_id: task_id.into(),
            provider: provider.into(),
            state: TaskState::Succeeded,
            findings: vec![],
            measurements: vec![],
            exit_code: None,
            signal: None,
            timed_out: false,
            truncated: false,
            duration_seconds: None,
            tool_versions: vec![],
            limitations: vec![],
        }
    }
    pub fn evidence_is_complete(&self) -> bool {
        self.state == TaskState::Succeeded && !self.timed_out && !self.truncated
    }
}
impl Validate for Observation {
    fn validate(&self) -> Result<()> {
        validation::text(&self.task_id, "task_id")?;
        validation::text(&self.provider, "provider")?;
        validation::require(
            self.duration_seconds
                .is_none_or(|v| v.is_finite() && v >= 0.0),
            "invalid duration",
        )?;
        for finding in &self.findings {
            finding.validate()?;
        }
        for measurement in &self.measurements {
            measurement.validate()?;
        }
        Ok(())
    }
}
