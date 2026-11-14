use crate::{
    ReportError, Result,
    json::MAX_RECORDS,
    paths::{external_path, positive},
};
use serde_json::{Value, json};
pub(crate) fn parse(text: &str) -> Result<Vec<Value>> {
    let mut records = vec![];
    let mut current: Option<Value> = None;
    let mut lines = 0;
    for line in text.lines() {
        if let Some(path) = line.strip_prefix("SF:") {
            if current.is_some() {
                return Err(ReportError("LCOV source record lacks end_of_record".into()));
            }
            current = Some(
                json!({"kind":"coverage","provider":"lcov","location":{"path":external_path(path)?,"line":null},"lines":[]}),
            );
        } else if let Some(row) = line.strip_prefix("DA:") {
            let record = current
                .as_mut()
                .ok_or_else(|| ReportError("LCOV DA record has no source file".into()))?;
            let mut pieces = row.split(',');
            let line = pieces
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .map(Value::from)
                .ok_or_else(|| ReportError("Invalid LCOV line".into()))?;
            let line = positive(&line, "LCOV line")?;
            let hits = pieces
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .ok_or_else(|| ReportError("LCOV hits must be non-negative".into()))?;
            lines += 1;
            if lines > 1_000_000 {
                return Err(ReportError("LCOV exceeds one million line records".into()));
            }
            let mut value = json!({"line":line,"hits":hits});
            if let Some(checksum) = pieces.next() {
                value["checksum"] = json!(checksum);
            }
            record["lines"].as_array_mut().unwrap().push(value);
        } else if line == "end_of_record" {
            let record = current
                .take()
                .ok_or_else(|| ReportError("LCOV end_of_record has no source".into()))?;
            records.push(record);
            if records.len() > MAX_RECORDS {
                return Err(ReportError("LCOV exceeds 100000 source records".into()));
            }
        }
    }
    if current.is_some() || records.is_empty() {
        return Err(ReportError("LCOV requires complete source records".into()));
    }
    Ok(records)
}
