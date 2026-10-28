use crate::{
    canonical_json,
    validation::{self, DomainError, Result, Validate},
};
use serde::{Deserialize, Serialize};
pub const EVENT_SCHEMA_ID: &str = "ici.next.event";
pub const EVENT_SCHEMA_VERSION: u32 = 1;
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RunEvent {
    pub schema_id: String,
    pub schema_version: u32,
    pub run_id: String,
    pub seq: u64,
    pub event_type: String,
    pub timestamp: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub component_id: Option<String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub message: String,
}
const TYPES: [&str; 7] = [
    "run.started",
    "plan.ready",
    "task.started",
    "task.progress",
    "task.completed",
    "diagnostic",
    "run.completed",
];
impl Validate for RunEvent {
    fn validate(&self) -> Result<()> {
        validation::require(
            self.schema_id == EVENT_SCHEMA_ID && self.schema_version == EVENT_SCHEMA_VERSION,
            "unsupported event envelope",
        )?;
        validation::text(&self.run_id, "run_id")?;
        validation::text(&self.timestamp, "timestamp")?;
        validation::require(
            TYPES.contains(&self.event_type.as_str()),
            "unknown producer event type",
        )?;
        validation::require(
            !self.event_type.starts_with("task.") || self.task_id.is_some(),
            "task event must identify task",
        )?;
        validation::require(
            self.message.chars().count() <= 4096,
            "event message exceeds 4096 characters",
        )
    }
}
impl RunEvent {
    pub fn to_json(&self) -> Result<String> {
        self.validate()?;
        canonical_json(self)
    }
}
pub fn read_events(text: &str) -> Result<(Vec<RunEvent>, Vec<String>)> {
    let mut events = vec![];
    let mut notes = vec![];
    let mut previous: Option<u64> = None;
    for (number, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let value: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => {
                notes.push(format!(
                    "line {}: not valid JSON (truncated stream?)",
                    number + 1
                ));
                continue;
            }
        };
        validation::require(
            value["schema_id"] == EVENT_SCHEMA_ID && value["schema_version"].as_u64() == Some(1),
            "unsupported event envelope",
        )?;
        if !value["event_type"]
            .as_str()
            .is_some_and(|kind| TYPES.contains(&kind))
        {
            notes.push(format!("line {}: unknown event_type, ignored", number + 1));
            continue;
        }
        let event: RunEvent =
            serde_json::from_value(value).map_err(|e| DomainError(e.to_string()))?;
        event.validate()?;
        if let Some(last) = previous
            && Some(event.seq) != last.checked_add(1)
        {
            notes.push(format!(
                "line {}: seq jumped from {last} to {}",
                number + 1,
                event.seq
            ));
        }
        previous = Some(event.seq);
        events.push(event);
    }
    Ok((events, notes))
}
