use crate::{
    ReportError, Result,
    auxiliary::ImportOptions,
    json::{MAX_RECORDS, strict_json},
    paths::{mapped_path, positive},
};
use serde_json::{Value, json};
fn array<'a>(value: Option<&'a Value>, name: &str) -> Result<&'a [Value]> {
    match value {
        None => Ok(&[]),
        Some(Value::Array(items)) => Ok(items),
        _ => Err(ReportError(format!("SARIF {name} must be an array"))),
    }
}
fn index(value: &Value, name: &str) -> Result<usize> {
    value
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| ReportError(format!("SARIF {name} must be a non-negative index")))
}
fn unicode_column(text: &str, line: u32, column: u32, units: &str) -> Result<u32> {
    let line = text
        .strip_prefix('\u{feff}')
        .unwrap_or(text)
        .split('\n')
        .nth(line as usize - 1)
        .ok_or_else(|| ReportError("SARIF line is outside supplied coordinate source".into()))?
        .trim_end_matches('\r');
    let mut native = 1u32;
    let mut unicode = 1u32;
    for character in line.chars() {
        if native == column {
            return Ok(unicode);
        }
        native = native
            .checked_add(if units == "utf16CodeUnits" {
                character.len_utf16() as u32
            } else {
                1
            })
            .ok_or_else(|| ReportError("SARIF column overflow".into()))?;
        unicode += 1;
        if native > column {
            return Err(ReportError(
                "SARIF column splits a Unicode surrogate pair".into(),
            ));
        }
    }
    if native == column {
        Ok(unicode)
    } else {
        Err(ReportError(
            "SARIF column is outside supplied source line".into(),
        ))
    }
}
fn location(
    physical: &Value,
    run: &Value,
    options: &ImportOptions,
    limitations: &mut Vec<String>,
) -> Result<Option<Value>> {
    if physical.is_null() {
        return Ok(None);
    }
    let artifact = &physical["artifactLocation"];
    let indexed = artifact
        .get("index")
        .map(|value| index(value, "artifact index"))
        .transpose()?
        .map(|index| {
            run["artifacts"]
                .as_array()
                .and_then(|items| items.get(index))
                .and_then(|artifact| artifact.get("location"))
                .ok_or_else(|| {
                    ReportError("SARIF artifact index is outside the run inventory".into())
                })
        })
        .transpose()?;
    let uri = artifact
        .get("uri")
        .or_else(|| indexed.and_then(|value| value.get("uri")));
    let Some(uri) = uri else {
        return Ok(None);
    };
    let uri = uri
        .as_str()
        .ok_or_else(|| ReportError("SARIF artifact URI must be a string".into()))?;
    let base = artifact
        .get("uriBaseId")
        .or_else(|| indexed.and_then(|value| value.get("uriBaseId")))
        .map(|value| {
            value
                .as_str()
                .ok_or_else(|| ReportError("SARIF uriBaseId must be a string".into()))
        })
        .transpose()?;
    let path = mapped_path(uri, base, &options.uri_base_mappings)?;
    let region = &physical["region"];
    let line = region
        .get("startLine")
        .map(|value| positive(value, "SARIF startLine"))
        .transpose()?;
    let end_line = region
        .get("endLine")
        .map(|value| positive(value, "SARIF endLine"))
        .transpose()?;
    if line.zip(end_line).is_some_and(|(start, end)| end < start) {
        return Err(ReportError(
            "SARIF region endLine precedes startLine".into(),
        ));
    }
    let mut result = json!({"path":path,"line":line});
    if region.as_object().is_some_and(|region| !region.is_empty()) {
        result["native_region"] = region.clone();
    }
    let start = region
        .get("startColumn")
        .map(|value| positive(value, "SARIF startColumn"))
        .transpose()?;
    let end = region
        .get("endColumn")
        .map(|value| positive(value, "SARIF endColumn"))
        .transpose()?;
    if (start.is_some() || end.is_some()) && line.is_none() {
        return Err(ReportError("SARIF columns require startLine".into()));
    }
    if line.is_some()
        && end_line.unwrap_or(line.unwrap()) == line.unwrap()
        && start.zip(end).is_some_and(|(start, end)| end < start)
    {
        return Err(ReportError("SARIF source columns are reversed".into()));
    }
    if start.is_some() || end.is_some() {
        let units = run
            .get("columnKind")
            .map(|value| {
                value
                    .as_str()
                    .ok_or_else(|| ReportError("SARIF columnKind must be a string".into()))
            })
            .transpose()?;
        if units.is_some_and(|units| !["unicodeCodePoints", "utf16CodeUnits"].contains(&units)) {
            return Err(ReportError("Unknown SARIF columnKind".into()));
        }
        result["native_region"] = region.clone();
        result["native_column_kind"] = json!(units.unwrap_or("unspecified"));
        let source = options.source_texts.get(&path);
        let can_convert = units == Some("unicodeCodePoints")
            || (units == Some("utf16CodeUnits") && source.is_some());
        if can_convert {
            let convert = |line, column| {
                if let Some(source) = source {
                    unicode_column(source, line, column, units.unwrap())
                } else {
                    Ok(column)
                }
            };
            let start_line = line.unwrap();
            if let Some(start) = start {
                result["start_column"] = json!(convert(start_line, start)?);
            }
            if let Some(end) = end {
                let row = end_line.unwrap_or(start_line);
                if row == start_line && end == start.unwrap_or(1) {
                    result["zero_width"] = json!(true);
                } else if end > 1 {
                    result["end_column"] = json!(convert(row, end)? - 1);
                    result["end_line"] = json!(row);
                } else if row > start_line {
                    result["end_line"] = json!(row - 1);
                } else {
                    result["zero_width"] = json!(true);
                }
            }
            result["column_kind"] = json!("unicodeCodePoints");
            result["end_semantics"] = json!("inclusive");
            if let Some(source) = source {
                result["coordinate_text_sha256"] = json!(&ici_domain::sha256(source)[7..]);
            }
        } else {
            let message = if units.is_none() {
                "SARIF column units were unspecified; original columns retained without conversion"
            } else {
                "SARIF UTF-16 columns retained without conversion because coordinate source text was not supplied"
            };
            if !limitations.iter().any(|item| item == message) {
                limitations.push(message.into());
            }
        }
    } else if let Some(end) = end_line {
        result["end_line"] = json!(end);
    }
    if let Some(base) = base {
        result["native_uri_base_id"] = json!(base);
    }
    Ok(Some(result))
}
pub(crate) fn parse(
    text: &str,
    options: &ImportOptions,
    limitations: &mut Vec<String>,
) -> Result<Vec<Value>> {
    let document = strict_json(text.as_bytes())?;
    if document["version"] != "2.1.0" {
        return Err(ReportError("Expected SARIF 2.1.0".into()));
    }
    let runs = document["runs"]
        .as_array()
        .ok_or_else(|| ReportError("SARIF runs must be an array".into()))?;
    let mut records = vec![];
    for run in runs {
        let provider = run["tool"]["driver"]["name"]
            .as_str()
            .filter(|name| !name.is_empty())
            .ok_or_else(|| ReportError("SARIF run needs a named tool driver".into()))?;
        if run.get("results").is_some_and(Value::is_null) {
            records.push(json!({"kind":"producer-execution","provider":provider,"status":"NOT_EVALUATED","message":"Producer results are unavailable","invocations":run.get("invocations"),"locations":[]}));
            continue;
        }
        for finding in array(run.get("results"), "results")? {
            if !finding.is_object() {
                return Err(ReportError("SARIF result must be an object".into()));
            }
            let mut locations = vec![];
            for entry in array(finding.get("locations"), "locations")?
                .iter()
                .chain(array(finding.get("relatedLocations"), "relatedLocations")?)
            {
                if let Some(location) =
                    location(&entry["physicalLocation"], run, options, limitations)?
                {
                    locations.push(location);
                }
            }
            let rule = if let Some(rule) = finding.get("ruleId") {
                rule.as_str()
                    .ok_or_else(|| ReportError("SARIF ruleId must be a string".into()))?
            } else if let Some(rule) = finding.get("ruleIndex") {
                let index = index(rule, "ruleIndex")?;
                run["tool"]["driver"]["rules"]
                    .as_array()
                    .and_then(|rules| rules.get(index))
                    .and_then(|rule| rule["id"].as_str())
                    .ok_or_else(|| ReportError("SARIF rule index is outside tool rules".into()))?
            } else {
                ""
            };
            let message = finding
                .get("message")
                .and_then(Value::as_object)
                .ok_or_else(|| ReportError("SARIF result.message must be an object".into()))?;
            let message = message
                .get("text")
                .or_else(|| message.get("markdown"))
                .map(|value| {
                    value
                        .as_str()
                        .ok_or_else(|| ReportError("SARIF message must contain text".into()))
                })
                .transpose()?
                .unwrap_or("");
            let suppressions = array(finding.get("suppressions"), "suppressions")?;
            let status = finding
                .get("level")
                .map(|value| {
                    value
                        .as_str()
                        .ok_or_else(|| ReportError("SARIF level must be a string".into()))
                })
                .transpose()?
                .unwrap_or("warning");
            let mut record = json!({"kind":"diagnostic","provider":provider,"rule":rule,"status":status,"message":message,"locations":locations,"suppressed":!suppressions.is_empty()});
            if !suppressions.is_empty() {
                record["suppressions"] = json!(suppressions);
            }
            records.push(record);
            if records.len() > MAX_RECORDS {
                return Err(ReportError("SARIF results exceed 100000".into()));
            }
        }
    }
    Ok(records)
}
