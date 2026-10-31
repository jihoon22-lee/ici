use crate::{ReportError, Result, json::MAX_RECORDS, paths::external_path};
use quick_xml::{
    NsReader, XmlVersion,
    events::{BytesStart, Event},
    name::ResolveResult,
};
use serde_json::{Value, json};
use std::collections::BTreeMap;
#[derive(Default)]
struct Detail {
    message: String,
    text: String,
}
#[derive(Default)]
struct Case {
    attributes: BTreeMap<String, String>,
    failure: Option<Detail>,
    error: Option<Detail>,
    skipped: bool,
    detail: Option<(String, usize)>,
}
fn attributes(event: &BytesStart<'_>) -> Result<BTreeMap<String, String>> {
    let mut values = BTreeMap::new();
    for attribute in event.attributes() {
        let attribute = attribute
            .map_err(|error| ReportError(format!("Malformed JUnit attribute: {error}")))?;
        if values.len() >= 128 {
            return Err(ReportError("JUnit element exceeds 128 attributes".into()));
        }
        let key = std::str::from_utf8(attribute.key.as_ref())
            .map_err(|_| ReportError("JUnit attribute name is not UTF-8".into()))?;
        let value = attribute
            .normalized_value(XmlVersion::Explicit1_0)
            .map_err(|error| ReportError(format!("Malformed JUnit attribute value: {error}")))?;
        values.insert(key.to_owned(), value.into_owned());
    }
    Ok(values)
}
fn start(
    name: &str,
    event: &BytesStart<'_>,
    depth: usize,
    current: &mut Option<Case>,
) -> Result<()> {
    if name == "testcase" {
        if current.is_some() {
            return Err(ReportError("JUnit testcase elements must not nest".into()));
        }
        *current = Some(Case {
            attributes: attributes(event)?,
            ..Default::default()
        });
    } else if let Some(case) = current.as_mut() {
        match name {
            "failure" | "error" => {
                let attributes = attributes(event)?;
                let detail = Detail {
                    message: attributes.get("message").cloned().unwrap_or_default(),
                    text: String::new(),
                };
                if name == "failure" {
                    if case.failure.is_some() {
                        return Err(ReportError("JUnit testcase repeats failure detail".into()));
                    }
                    case.failure = Some(detail);
                } else {
                    if case.error.is_some() {
                        return Err(ReportError("JUnit testcase repeats error detail".into()));
                    }
                    case.error = Some(detail);
                }
                case.detail = Some((name.into(), depth));
            }
            "skipped" => case.skipped = true,
            _ => {}
        }
    }
    Ok(())
}
fn finish(
    name: &str,
    depth: usize,
    current: &mut Option<Case>,
    records: &mut Vec<Value>,
) -> Result<()> {
    if name == "testcase" {
        let case = current
            .take()
            .ok_or_else(|| ReportError("JUnit testcase end has no start".into()))?;
        let status = if case.error.is_some() {
            "error"
        } else if case.failure.is_some() {
            "failed"
        } else if case.skipped {
            "skipped"
        } else {
            "passed"
        };
        let detail = case.error.as_ref().or(case.failure.as_ref());
        let message = detail
            .map(|detail| {
                if detail.message.is_empty() {
                    detail.text.as_str()
                } else {
                    detail.message.as_str()
                }
            })
            .unwrap_or("");
        let location = case
            .attributes
            .get("file")
            .filter(|path| !path.is_empty())
            .map(|path| external_path(path))
            .transpose()?
            .map(|path| json!({"path":path,"line":null}));
        let native_line = case
            .attributes
            .get("line")
            .map(|line| {
                line.parse::<u64>()
                    .map_err(|_| ReportError("JUnit native line must be non-negative".into()))
            })
            .transpose()?;
        records.push(json!({"kind":"test","provider":"junit","name":case.attributes.get("name").map_or("",String::as_str),"suite":case.attributes.get("classname").map_or("",String::as_str),"native_line":native_line,"line_base":"producer-defined","status":status,"duration":case.attributes.get("time"),"message":message,"locations":location.into_iter().collect::<Vec<_>>()}));
        if records.len() > MAX_RECORDS {
            return Err(ReportError("JUnit exceeds 100000 testcase records".into()));
        }
    } else if let Some(case) = current.as_mut()
        && case
            .detail
            .as_ref()
            .is_some_and(|(tag, level)| tag == name && *level == depth)
    {
        case.detail = None;
    }
    Ok(())
}
fn text(value: &str, current: &mut Option<Case>) {
    if let Some(case) = current.as_mut()
        && let Some((kind, _)) = &case.detail
    {
        let detail = if kind == "error" {
            case.error.as_mut()
        } else {
            case.failure.as_mut()
        };
        if let Some(detail) = detail {
            detail.text.push_str(value);
        }
    }
}
pub(crate) fn parse(input: &str) -> Result<Vec<Value>> {
    let uppercase = input.to_ascii_uppercase();
    if uppercase.contains("<!DOCTYPE") || uppercase.contains("<!ENTITY") {
        return Err(ReportError(
            "JUnit DTD/entity declarations are unsupported".into(),
        ));
    }
    let mut reader = NsReader::from_str(input);
    let mut stack = vec![];
    let mut root = false;
    let mut declaration_seen = false;
    let mut current = None;
    let mut records = vec![];
    let mut events = 0;
    loop {
        let (namespace, event) = reader
            .read_resolved_event()
            .map_err(|error| ReportError(format!("Malformed JUnit XML: {error}")))?;
        events += 1;
        if events > 1_000_000 {
            return Err(ReportError("JUnit exceeds one million XML events".into()));
        }
        if matches!(namespace, ResolveResult::Unknown(_)) {
            return Err(ReportError(
                "JUnit element uses an unbound XML namespace prefix".into(),
            ));
        }
        match event {
            Event::Start(event) => {
                let name = std::str::from_utf8(event.local_name().as_ref())
                    .map_err(|_| ReportError("JUnit tag name is not UTF-8".into()))?
                    .to_owned();
                if stack.is_empty() {
                    if root || !["testsuite", "testsuites"].contains(&name.as_str()) {
                        return Err(ReportError(
                            "Expected one JUnit testsuite/testsuites root".into(),
                        ));
                    }
                    root = true;
                }
                stack.push(name.clone());
                if stack.len() > 128 {
                    return Err(ReportError("JUnit nesting exceeds 128 levels".into()));
                }
                start(&name, &event, stack.len(), &mut current)?;
            }
            Event::Empty(event) => {
                let name = std::str::from_utf8(event.local_name().as_ref())
                    .map_err(|_| ReportError("JUnit tag name is not UTF-8".into()))?
                    .to_owned();
                if stack.is_empty() {
                    if root || !["testsuite", "testsuites"].contains(&name.as_str()) {
                        return Err(ReportError(
                            "Expected one JUnit testsuite/testsuites root".into(),
                        ));
                    }
                    root = true;
                }
                start(&name, &event, stack.len() + 1, &mut current)?;
                finish(&name, stack.len() + 1, &mut current, &mut records)?;
            }
            Event::End(event) => {
                let name = std::str::from_utf8(event.local_name().as_ref())
                    .map_err(|_| ReportError("JUnit tag name is not UTF-8".into()))?
                    .to_owned();
                let expected = stack
                    .pop()
                    .ok_or_else(|| ReportError("Unexpected JUnit closing tag".into()))?;
                if expected != name {
                    return Err(ReportError(
                        "JUnit closing tag does not match open tag".into(),
                    ));
                }
                finish(&name, stack.len() + 1, &mut current, &mut records)?;
            }
            Event::Text(value) => {
                let value = value
                    .xml10_content()
                    .map_err(|error| ReportError(format!("Malformed JUnit text: {error}")))?;
                if stack.is_empty() && !value.trim().is_empty() {
                    return Err(ReportError(
                        "JUnit text occurs outside the root element".into(),
                    ));
                }
                text(&value, &mut current);
            }
            Event::CData(value) => {
                let value = value
                    .xml10_content()
                    .map_err(|error| ReportError(format!("Malformed JUnit CDATA: {error}")))?;
                if stack.is_empty() {
                    return Err(ReportError("JUnit CDATA occurs outside the root".into()));
                }
                text(&value, &mut current);
            }
            Event::GeneralRef(reference) => {
                if stack.is_empty() {
                    return Err(ReportError(
                        "JUnit character reference occurs outside the root".into(),
                    ));
                }
                let value = if let Some(character) =
                    reference.resolve_char_ref().map_err(|error| {
                        ReportError(format!("Malformed JUnit character reference: {error}"))
                    })? {
                    character.to_string()
                } else {
                    let name = reference.decode().map_err(|error| {
                        ReportError(format!("Malformed JUnit reference: {error}"))
                    })?;
                    quick_xml::escape::resolve_predefined_entity(&name)
                        .ok_or_else(|| {
                            ReportError("JUnit custom entity references are unsupported".into())
                        })?
                        .to_owned()
                };
                text(&value, &mut current);
            }
            Event::Decl(declaration) => {
                if root || declaration_seen {
                    return Err(ReportError(
                        "JUnit XML declaration is repeated or occurs after the root".into(),
                    ));
                }
                declaration_seen = true;
                let version = declaration.version().map_err(|error| {
                    ReportError(format!("Malformed JUnit XML declaration: {error}"))
                })?;
                if version.as_ref() != b"1.0" {
                    return Err(ReportError("JUnit XML version must be 1.0".into()));
                }
            }
            Event::DocType(_) => {
                return Err(ReportError("JUnit DTD declarations are unsupported".into()));
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if !root || !stack.is_empty() || current.is_some() {
        return Err(ReportError("JUnit root or testcase is incomplete".into()));
    }
    Ok(records)
}
