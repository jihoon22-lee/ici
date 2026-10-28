use crate::{Result, StoredResult, safe};
use serde_json::{Value, json};
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConsoleGroupBy {
    #[default]
    Engine,
    Severity,
    Category,
    File,
    Rule,
}
#[derive(Clone, Debug)]
pub struct RenderOptions {
    pub title: String,
    pub max_findings: usize,
    pub max_target_rows: usize,
    pub max_annotations: usize,
    pub max_markdown_bytes: usize,
    pub auxiliary: Vec<crate::AuxiliaryDocument>,
    pub verbose: bool,
    pub group_by: ConsoleGroupBy,
    pub max_locations: usize,
    pub console_width: usize,
    pub source_root: Option<String>,
    pub repository_url: Option<String>,
    pub commit_ref: Option<String>,
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
            verbose: false,
            group_by: ConsoleGroupBy::Engine,
            max_locations: 4,
            console_width: 80,
            source_root: None,
            repository_url: None,
            commit_ref: None,
        }
    }
}
pub(crate) struct View {
    pub document: Value,
    pub run: Option<Value>,
    pub engines: Vec<Value>,
    pub findings: Vec<Value>,
    pub finding_engines: Vec<String>,
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
                .flat_map(|engine| decorated_findings(engine, run.as_ref()))
                .collect()
        } else {
            document["findings"].as_array().cloned().unwrap_or_default()
        };
        let finding_engines = engines
            .iter()
            .flat_map(|engine| {
                std::iter::repeat_n(
                    engine["engine_name"]
                        .as_str()
                        .unwrap_or("unknown")
                        .to_string(),
                    engine["findings"].as_array().map_or(0, Vec::len),
                )
            })
            .collect();
        Ok(Self {
            document,
            run,
            engines,
            findings,
            finding_engines,
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
fn decorated_findings(engine: &Value, run: Option<&Value>) -> Vec<Value> {
    use std::collections::{BTreeMap, VecDeque};
    let modern: BTreeMap<_, _> = run
        .and_then(|run| run["findings"].as_array())
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(|finding| Some((finding["fingerprint"].as_str()?, finding)))
        .collect();
    let mut mappings = BTreeMap::<&str, VecDeque<&Value>>::new();
    for mapping in engine["extra"]["finding_identity_map"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        if let Some(fp) = mapping["legacy_fingerprint"].as_str() {
            mappings.entry(fp).or_default().push_back(mapping);
        }
    }
    let checks: BTreeMap<_, _> = engine["extra"]["native_checks"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(|check| Some((check["task_id"].as_str()?, check)))
        .collect();
    engine["findings"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .map(|mut finding| {
            finding["evidence"] = engine["evidence"].clone();
            if let Some(mapping) = finding["fingerprint"]
                .as_str()
                .and_then(|fp| mappings.get_mut(fp))
                .and_then(VecDeque::pop_front)
            {
                if let Some(native) = mapping["modern_fingerprint"]
                    .as_str()
                    .and_then(|fp| modern.get(fp))
                {
                    for key in [
                        "evidence",
                        "component_id",
                        "analysis_unit_id",
                        "variant",
                        "task_id",
                        "limitations",
                    ] {
                        if let Some(value) = native.get(key) {
                            finding[key] = value.clone();
                        }
                    }
                    if let Some(origin) = native
                        .get("suppression")
                        .and_then(|value| value.get("origin"))
                    {
                        finding["suppression"]["origin"] = origin.clone();
                    }
                }
                if let Some(details) = mapping["task_id"]
                    .as_str()
                    .and_then(|task| checks.get(task))
                    .and_then(|check| {
                        check["extra"]["finding_details"]
                            .get(mapping["raw_fingerprint"].as_str().unwrap_or(""))
                    })
                    && let Some(fixes) = details.get("fixes")
                {
                    finding["fixes"] = fixes.clone();
                }
            }
            finding
        })
        .collect()
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
