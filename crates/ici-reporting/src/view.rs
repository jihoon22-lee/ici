use crate::{Result, StoredResult, safe};
use serde_json::{Value, json};
#[derive(Clone, Debug)]
pub struct RenderOptions {
    pub title: String,
    pub max_findings: usize,
    pub max_target_rows: usize,
    pub max_annotations: usize,
    pub max_markdown_bytes: usize,
    pub auxiliary: Vec<crate::AuxiliaryDocument>,
}
impl Default for RenderOptions {
    fn default() -> Self {
        Self {
            title: "ici analysis".into(),
            max_findings: 5,
            max_target_rows: 100,
            max_annotations: 50,
            max_markdown_bytes: 900_000,
            auxiliary: vec![],
        }
    }
}
pub(crate) struct View {
    pub document: Value,
    pub run: Option<Value>,
    pub engines: Vec<Value>,
    pub findings: Vec<Value>,
}
impl View {
    pub fn new(stored: &StoredResult) -> Result<Self> {
        let document = safe::redact(&stored.to_value()?);
        let run = if document["schema_id"] == "ici.next.run" {
            Some(document.clone())
        } else {
            document
                .get("native_run")
                .filter(|value| value["schema_id"] == "ici.next.run")
                .cloned()
        };
        let engines = if run.as_ref() == Some(&document) {
            vec![]
        } else if let Some(engines) = document["results"].as_array() {
            engines.clone()
        } else {
            vec![document.clone()]
        };
        let findings = if !engines.is_empty() {
            engines
                .iter()
                .flat_map(|engine| engine["findings"].as_array().cloned().unwrap_or_default())
                .collect()
        } else {
            document["findings"].as_array().cloned().unwrap_or_default()
        };
        Ok(Self {
            document,
            run,
            engines,
            findings,
        })
    }
    pub fn verdict(&self) -> String {
        if let Some(run) = &self.run {
            safe::plain(&run["gate"]["selected"])
        } else {
            self.document["suite_status"]
                .as_str()
                .or_else(|| self.document["status"].as_str())
                .unwrap_or("UNKNOWN")
                .into()
        }
    }
    pub fn axes(&self) -> Value {
        if let Some(run) = &self.run {
            json!({"scope":run["scope"],"execution":run["execution"],"gate":run["gate"],
                "publication":run["publication"],"limitations":run["limitations"],"producer":run["producer"],
                "run_id":run["run_id"],"exit_code":if run["execution"]["cancelled"]==true {130} else {match run["gate"]["selected"].as_str(){Some("FAIL")=>1,Some("INCOMPLETE")=>3,_=>0}},
                "checks":run["checks"]})
        } else {
            json!({"historical_status":self.verdict(),"scope":"Historical archive; full native scope was not recorded", "analysis_context":self.document["analysis_context"]})
        }
    }
    pub fn baseline(&self) -> &Value {
        if let Some(run) = &self.run {
            &run["baseline"]
        } else {
            &self.document["baseline_comparison"]
        }
    }
    pub fn metrics(&self) -> &Value {
        if let Some(run) = &self.run {
            &run["metrics"]
        } else {
            &self.document["tem_score"]
        }
    }
}
pub(crate) fn suppressed(finding: &Value) -> bool {
    finding["suppression"]["suppressed"] == true
}
pub(crate) fn informational(finding: &Value) -> bool {
    matches!(
        finding["severity"].as_str(),
        Some("info" | "information" | "note" | "none")
    )
}
pub(crate) fn location(finding: &Value) -> String {
    let span = &finding["primary_location"];
    let mut result = format!(
        "{}:{}",
        span["path"].as_str().unwrap_or("unknown"),
        span["start_line"].as_u64().unwrap_or(1)
    );
    if let Some(column) = span["start_column"].as_u64() {
        result.push_str(&format!(":{column}"));
    }
    result
}
pub(crate) fn marks(finding: &Value) -> String {
    let mut marks = vec![];
    for key in [
        "evidence",
        "confidence",
        "component_id",
        "task_id",
        "variant",
    ] {
        if let Some(value) = finding[key].as_str().filter(|value| !value.is_empty()) {
            marks.push(format!("{key}: {value}"));
        }
    }
    if suppressed(finding) {
        marks.push(format!(
            "suppressed: {} ({})",
            safe::plain(&finding["suppression"]["reason"]),
            safe::plain(&finding["suppression"]["origin"])
        ));
    }
    marks.join(" · ")
}
