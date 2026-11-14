//! Supporting external evidence is stored separately and can never complete an ici gate.
use crate::{
    ReportError, Result,
    json::{self, MAX_BYTES, MAX_RECORDS},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
#[derive(Clone, Debug, Default)]
pub struct ImportOptions {
    pub uri_base_mappings: BTreeMap<String, String>,
    /// Explicit decoded source text, used only for column-unit conversion.
    pub source_texts: BTreeMap<String, String>,
}
#[derive(Clone, Debug)]
pub struct ImportProvenance {
    pub source_path: String,
    pub scope: String,
    pub imported_at: String,
    pub observed_at: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuxiliarySource {
    pub path: String,
    pub sha256: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuxiliaryDocument {
    pub schema_id: String,
    pub schema_version: u32,
    pub format: String,
    pub source: AuxiliarySource,
    pub imported_at: String,
    pub observed_at: Option<String>,
    pub scope: String,
    pub evidence: String,
    pub gate: String,
    pub limitations: Vec<String>,
    pub records: Vec<Value>,
}
impl AuxiliaryDocument {
    pub fn validate(&self) -> Result<()> {
        if self.schema_id != "ici.auxiliary"
            || self.schema_version != 1
            || self.evidence != "imported"
            || self.gate != "NOT_EVALUATED"
        {
            return Err(ReportError(
                "Supporting evidence must be ici.auxiliary v1 imported/NOT_EVALUATED".into(),
            ));
        }
        if !["sarif", "junit", "lcov"].contains(&self.format.as_str())
            || self.scope.trim().is_empty()
            || self.records.len() > MAX_RECORDS
            || self.records.iter().any(|record| !record.is_object())
        {
            return Err(ReportError(
                "Invalid supporting evidence format, scope or records".into(),
            ));
        }
        if self.source.sha256.len() != 64
            || !self
                .source
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(ReportError(
                "Invalid supporting evidence source checksum".into(),
            ));
        }
        timestamp(&self.imported_at)?;
        if let Some(observed) = &self.observed_at {
            timestamp(observed)?;
        }
        json::validate_shape(&serde_json::to_value(self)?)?;
        if serde_json::to_vec(self)?.len() > MAX_BYTES {
            return Err(ReportError(
                "Supporting evidence document exceeds 32 MiB serialized bound".into(),
            ));
        }
        Ok(())
    }
    pub fn to_value(&self) -> Result<Value> {
        self.validate()?;
        Ok(serde_json::to_value(self)?)
    }
    pub fn to_json(&self) -> Result<String> {
        json::canonical_json(&self.to_value()?)
    }
}
pub(crate) fn timestamp(value: &str) -> Result<()> {
    let valid = chrono::DateTime::parse_from_rfc3339(value).is_ok()
        || [
            "%Y-%m-%dT%H:%M%:z",
            "%Y-%m-%d %H:%M:%S%:z",
            "%Y-%m-%d %H:%M%:z",
        ]
        .iter()
        .any(|format| chrono::DateTime::parse_from_str(value, format).is_ok());
    if !valid {
        return Err(ReportError(
            "Supporting evidence timestamps require an explicit timezone".into(),
        ));
    }
    Ok(())
}
pub fn import_report(
    format: &str,
    bytes: &[u8],
    provenance: &ImportProvenance,
    options: &ImportOptions,
) -> Result<AuxiliaryDocument> {
    if bytes.len() > MAX_BYTES {
        return Err(ReportError(
            "Supporting report exceeds 32 MiB import bound".into(),
        ));
    }
    if provenance.scope.trim().is_empty() {
        return Err(ReportError(
            "Supporting evidence scope must describe the producing run".into(),
        ));
    }
    timestamp(&provenance.imported_at)?;
    if let Some(observed) = &provenance.observed_at {
        timestamp(observed)?;
    }
    let text = std::str::from_utf8(bytes)
        .map_err(|_| ReportError("Supporting report must be UTF-8".into()))?
        .trim_start_matches('\u{feff}');
    let mut limitations = vec![
        "External producer evidence; ici did not execute or verify this run".into(),
        if provenance.observed_at.is_some() {
            "Observation time supplied by importer caller"
        } else {
            "Observation time is unknown"
        }
        .into(),
    ];
    let records = match format {
        "sarif" => crate::sarif_import::parse(text, options, &mut limitations)?,
        "junit" => crate::junit_import::parse(text)?,
        "lcov" => crate::lcov_import::parse(text)?,
        _ => {
            return Err(ReportError(
                "Supporting format must be sarif, junit or lcov".into(),
            ));
        }
    };
    let document = AuxiliaryDocument {
        schema_id: "ici.auxiliary".into(),
        schema_version: 1,
        format: format.into(),
        source: AuxiliarySource {
            path: provenance.source_path.clone(),
            sha256: ici_domain::sha256(bytes)[7..].into(),
        },
        imported_at: provenance.imported_at.clone(),
        observed_at: provenance.observed_at.clone(),
        scope: provenance.scope.clone(),
        evidence: "imported".into(),
        gate: "NOT_EVALUATED".into(),
        limitations,
        records,
    };
    document.validate()?;
    Ok(document)
}
pub fn load_auxiliary(bytes: &[u8]) -> Result<AuxiliaryDocument> {
    let value = json::strict_json(bytes)?;
    let document: AuxiliaryDocument = serde_json::from_value(value)?;
    document.validate()?;
    Ok(document)
}
