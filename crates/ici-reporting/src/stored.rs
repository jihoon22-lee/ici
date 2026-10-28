use crate::{
    ReportError, Result,
    json::{MAX_RECORDS, strict_stored_json, validate_stored_shape},
};
use ici_domain::{RunResult, Validate};
use serde_json::Value;
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LegacyKind {
    Suite,
    Engine,
}
/// The complete historical payload stays intact, including producer extension fields.
#[derive(Clone, Debug, PartialEq)]
pub struct LegacyDocument {
    payload: Value,
    pub kind: LegacyKind,
}
impl LegacyDocument {
    pub fn from_value(payload: Value) -> Result<Self> {
        validate_stored_shape(&payload)?;
        let object = payload
            .as_object()
            .ok_or_else(|| ReportError("Legacy result must be an object".into()))?;
        if object.get("schema_id").is_some() {
            return Err(ReportError(
                "Legacy results do not use a next/auxiliary schema_id".into(),
            ));
        }
        let schema = object.get("schema_version");
        let archive = object.get("version");
        if !(schema == Some(&Value::String("ici.result/v3".into()))
            || (schema.is_none() && archive == Some(&Value::from(3))))
        {
            return Err(ReportError(
                "Expected legacy ici.result/v3 or historical version:3 archive".into(),
            ));
        }
        if archive.is_some_and(|value| value != &Value::from(3)) {
            return Err(ReportError(
                "Legacy version conflicts with ici.result/v3".into(),
            ));
        }
        if let Some(run) = object
            .get("native_run")
            .filter(|run| run["schema_id"] == "ici.next.run")
        {
            // A legacy extension may carry modern facts, but it must not bypass
            // the strict reader and turn malformed execution/scope into a verdict.
            RunResult::from_value(run.clone())?;
        }
        let kind = if let Some(results) = object.get("results") {
            let results = results
                .as_array()
                .ok_or_else(|| ReportError("Legacy suite results must be an array".into()))?;
            if results.len() > MAX_RECORDS {
                return Err(ReportError("Legacy suite exceeds 100000 engines".into()));
            }
            for engine in results {
                validate_engine(engine)?;
            }
            status(object.get("suite_status"), "suite_status")?;
            LegacyKind::Suite
        } else {
            validate_engine(&payload)?;
            LegacyKind::Engine
        };
        for key in ["duration", "tem_score", "max_tem_score"] {
            number(object.get(key), key)?;
        }
        for key in [
            "passed_count",
            "warned_count",
            "failed_count",
            "error_count",
            "skipped_count",
            "total_count",
        ] {
            if object
                .get(key)
                .is_some_and(|value| value.as_u64().is_none())
            {
                return Err(ReportError(format!(
                    "Legacy {key} must be a non-negative integer"
                )));
            }
        }
        Ok(Self { payload, kind })
    }
    pub fn as_value(&self) -> &Value {
        &self.payload
    }
    pub fn to_value(&self) -> Value {
        self.payload.clone()
    }
    pub fn engines(&self) -> Vec<&Value> {
        match self.kind {
            LegacyKind::Suite => self.payload["results"]
                .as_array()
                .expect("validated suite")
                .iter()
                .collect(),
            LegacyKind::Engine => vec![&self.payload],
        }
    }
}
#[derive(Clone, Debug, PartialEq)]
pub enum StoredResult {
    Run(Box<RunResult>),
    Legacy(LegacyDocument),
}
impl StoredResult {
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        Self::from_value(strict_stored_json(bytes)?)
    }
    /// Explicit historical report boundary. The strict native/archive reader above
    /// does not migrate schemas, and this adapter never supplies native evidence.
    pub fn from_report_bytes(bytes: &[u8]) -> Result<Self> {
        let value = strict_stored_json(bytes)?;
        if value["schema_version"] != "ici.result/v2" {
            return Self::from_value(value);
        }
        if value.get("schema_id").is_some() || value.get("native_run").is_some() {
            return Err(ReportError(
                "Historical v2 reports cannot carry native run evidence".into(),
            ));
        }
        if let Some(results) = value.get("results") {
            status(value.get("suite_status"), "suite_status")?;
            let engines = results
                .as_array()
                .ok_or_else(|| ReportError("Historical results must be an array".into()))?;
            for engine in engines {
                text(engine.get("engine_name"), "engine_name", true)?;
                status(engine.get("status"), "status")?;
            }
        } else {
            text(value.get("engine_name"), "engine_name", true)?;
            status(value.get("status"), "status")?;
        }
        Ok(Self::Legacy(crate::migrate_report_payload(&value, None)?))
    }
    pub fn from_value(value: Value) -> Result<Self> {
        validate_stored_shape(&value)?;
        if value.get("schema_id").is_some() {
            return Ok(Self::Run(Box::new(RunResult::from_value(value)?)));
        }
        Ok(Self::Legacy(LegacyDocument::from_value(value)?))
    }
    pub fn from_run(run: RunResult) -> Result<Self> {
        run.validate()?;
        Ok(Self::Run(Box::new(run)))
    }
    pub fn to_value(&self) -> Result<Value> {
        match self {
            Self::Run(run) => Ok(run.to_value()?),
            Self::Legacy(legacy) => Ok(legacy.to_value()),
        }
    }
    pub fn to_json(&self) -> Result<String> {
        Ok(ici_domain::canonical_json(&self.to_value()?)?)
    }
    pub fn schema(&self) -> &str {
        match self {
            Self::Run(_) => "ici.next.run",
            Self::Legacy(_) => "ici.result/v3",
        }
    }
}
fn status(value: Option<&Value>, field: &str) -> Result<()> {
    if !value
        .and_then(Value::as_str)
        .is_some_and(|status| ["PASS", "WARN", "FAIL", "ERROR", "SKIP"].contains(&status))
    {
        return Err(ReportError(format!(
            "Legacy {field} must be PASS, WARN, FAIL, ERROR or SKIP"
        )));
    }
    Ok(())
}
fn text(value: Option<&Value>, field: &str, required: bool) -> Result<()> {
    match value {
        Some(Value::String(value)) if !required || !value.is_empty() => Ok(()),
        None if !required => Ok(()),
        _ => Err(ReportError(format!(
            "Legacy {field} must be {}string",
            if required { "a nonempty " } else { "a " }
        ))),
    }
}
fn number(value: Option<&Value>, field: &str) -> Result<()> {
    if value.is_some_and(|value| {
        !value.is_null() && value.as_f64().is_none_or(|number| !number.is_finite())
    }) {
        return Err(ReportError(format!(
            "Legacy {field} must be a finite number or null"
        )));
    }
    Ok(())
}
fn region(value: &Value, path: &str) -> Result<()> {
    let line = value["start_line"]
        .as_u64()
        .filter(|line| *line > 0 && *line <= u32::MAX as u64)
        .ok_or_else(|| ReportError(format!("Legacy {path}.start_line must be positive")))?;
    let end = nullable_position(value.get("end_line"), path)?;
    let start_column = nullable_position(value.get("start_column"), path)?;
    let end_column = nullable_position(value.get("end_column"), path)?;
    if end.is_some_and(|end| end < line)
        || (end.unwrap_or(line) == line
            && start_column
                .zip(end_column)
                .is_some_and(|(start, end)| end < start))
    {
        return Err(ReportError(format!(
            "Legacy {path} has a reversed source region"
        )));
    }
    Ok(())
}
fn nullable_position(value: Option<&Value>, path: &str) -> Result<Option<u64>> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_u64()
            .filter(|value| *value > 0 && *value <= u32::MAX as u64)
            .map(Some)
            .ok_or_else(|| {
                ReportError(format!(
                    "Legacy {path} position must be a positive integer or null"
                ))
            }),
    }
}
fn validate_engine(engine: &Value) -> Result<()> {
    let engine = engine
        .as_object()
        .ok_or_else(|| ReportError("Legacy engine must be an object".into()))?;
    if engine
        .get("schema_version")
        .is_some_and(|version| version != "ici.result/v3")
    {
        return Err(ReportError(
            "Unsupported nested legacy engine schema".into(),
        ));
    }
    text(engine.get("engine_name"), "engine_name", true)?;
    status(engine.get("status"), "engine.status")?;
    text(engine.get("summary"), "engine.summary", false)?;
    for key in ["score", "max_score", "duration"] {
        number(engine.get(key), key)?;
    }
    if engine
        .get("required")
        .is_some_and(|value| !value.is_boolean())
    {
        return Err(ReportError("Legacy engine.required must be boolean".into()));
    }
    if engine.get("evidence").is_some_and(|value| {
        !value.as_str().is_some_and(|evidence| {
            ["MEASURED", "ESTIMATED", "NOT_RUN", "NOT_APPLICABLE"].contains(&evidence)
        })
    }) {
        return Err(ReportError("Legacy engine evidence is unknown".into()));
    }
    for key in ["extra", "metrics"] {
        if engine.get(key).is_some_and(|value| !value.is_object()) {
            return Err(ReportError(format!(
                "Legacy engine.{key} must be an object"
            )));
        }
    }
    if let Some(targets) = engine.get("targets") {
        let targets = targets
            .as_array()
            .ok_or_else(|| ReportError("Legacy targets must be an array".into()))?;
        if targets.len() > MAX_RECORDS {
            return Err(ReportError("Legacy targets exceed 100000".into()));
        }
        for target in targets {
            if !target.is_object() {
                return Err(ReportError("Legacy target must be an object".into()));
            }
            text(target.get("file_path"), "target.file_path", true)?;
            region(target, "target")?;
            status(target.get("status"), "target.status")?;
            for key in ["target_name", "message", "snippet"] {
                text(target.get(key), key, false)?;
            }
            if target
                .get("metrics")
                .is_some_and(|value| !value.is_object())
            {
                return Err(ReportError(
                    "Legacy target.metrics must be an object".into(),
                ));
            }
        }
    }
    if let Some(findings) = engine.get("findings") {
        let findings = findings
            .as_array()
            .ok_or_else(|| ReportError("Legacy findings must be an array".into()))?;
        if findings.len() > MAX_RECORDS {
            return Err(ReportError("Legacy findings exceed 100000".into()));
        }
        for finding in findings {
            text(finding.get("rule_id"), "finding.rule_id", true)?;
            text(finding.get("message"), "finding.message", false)?;
            text(finding.get("fingerprint"), "finding.fingerprint", true)?;
            text(
                finding
                    .get("primary_location")
                    .and_then(|location| location.get("path")),
                "finding.path",
                true,
            )?;
            region(&finding["primary_location"], "finding")?;
        }
    }
    Ok(())
}
