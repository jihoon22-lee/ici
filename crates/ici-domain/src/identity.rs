use crate::{
    Finding,
    validation::{self, DomainError, Result, Validate},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const FINGERPRINT_VERSION: &str = "ici.next.fingerprint.v3";
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceSnapshot {
    pub digest: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub files: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub generated: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub external_inputs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub commit: Option<String>,
    #[serde(default)]
    pub dirty: bool,
}
impl Validate for SourceSnapshot {
    fn validate(&self) -> Result<()> {
        validation::digest(&self.digest, "snapshot digest")?;
        for path in self.files.iter().chain(&self.generated) {
            validation::relative_path(path, false)?;
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunIdentity {
    pub source: SourceSnapshot,
    pub policy_digest: String,
    pub toolchain_digest: String,
    #[serde(default)]
    pub fingerprint_version: String,
}
impl Validate for RunIdentity {
    fn validate(&self) -> Result<()> {
        self.source.validate()?;
        validation::digest(&self.policy_digest, "policy digest")?;
        validation::digest(&self.toolchain_digest, "toolchain digest")
    }
}
pub fn sha256(bytes: impl AsRef<[u8]>) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes.as_ref()))
}
/// Object sorting is explicit even if a transitive dependency enables preserve_order.
pub fn canonical_json<T: Serialize>(value: &T) -> Result<String> {
    let mut value = serde_json::to_value(value).map_err(|e| DomainError(e.to_string()))?;
    value.sort_all_objects();
    serde_json::to_string(&value).map_err(|e| DomainError(e.to_string()))
}
/// Python json.dumps(..., separators=(",", ":")) defaults to ensure_ascii=True.
/// Fingerprint v3 hashes those UTF-16 escapes, not literal UTF-8 non-ASCII text.
pub fn ascii_json(value: &Value) -> Result<String> {
    let json = canonical_json(value)?;
    let mut ascii = String::with_capacity(json.len());
    for ch in json.chars() {
        if ch.is_ascii() {
            ascii.push(ch);
        } else {
            let mut units = [0_u16; 2];
            for unit in ch.encode_utf16(&mut units) {
                use std::fmt::Write;
                write!(ascii, "\\u{unit:04x}").expect("writing a String cannot fail");
            }
        }
    }
    Ok(ascii)
}
pub fn attributed_fingerprint(finding: &Finding, units: &[String]) -> Result<String> {
    let span = &finding.primary_location;
    let mut fields = vec![
        serde_json::json!(FINGERPRINT_VERSION),
        serde_json::json!(finding.component_id),
        serde_json::json!(finding.analysis_unit_id),
        serde_json::json!(finding.variant),
        serde_json::json!(finding.provider),
        serde_json::json!(finding.rule_id),
        serde_json::json!(finding.fingerprint),
        serde_json::json!(finding.message),
        serde_json::json!(span.path),
        serde_json::json!(span.start_line),
        serde_json::json!(span.end_line),
        serde_json::json!(span.start_column),
        serde_json::json!(span.end_column),
    ];
    if units.len() > 1 {
        fields.push(serde_json::json!(units));
    }
    Ok(sha256(ascii_json(&Value::Array(fields))?))
}
