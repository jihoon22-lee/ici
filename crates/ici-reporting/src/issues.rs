//! Conservative display grouping. Canonical saved findings and fingerprints never change.
use crate::{
    Result, StoredResult,
    safe::plain,
    view::{View, informational, suppressed},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};
#[derive(Clone, Debug, Serialize)]
pub struct IssueGroup {
    /// Historical reporter-neutral group fields, derived only from saved records.
    pub view: Value,
    pub findings: Vec<Value>,
    pub source_indices: Vec<usize>,
}
#[derive(Clone, Debug, Serialize)]
pub struct IssueProjection {
    pub groups: Vec<IssueGroup>,
    pub total_findings: usize,
    pub limitations: Vec<String>,
}
#[derive(Clone)]
struct Record {
    engine: String,
    finding: Value,
    index: usize,
}
fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}
fn severity(finding: &Value) -> u8 {
    match text(finding, "severity") {
        "critical" | "fatal" => 0,
        "high" | "error" => 1,
        "medium" | "warning" => 2,
        "low" => 3,
        _ => 4,
    }
}
fn python(finding: &Value) -> bool {
    let path = text(&finding["primary_location"], "path").to_lowercase();
    path.ends_with(".py") || path.ends_with(".pyi")
}
fn engine_for(index: usize, finding: &Value, view: &View) -> String {
    if let Some(engine) = view.finding_engines.get(index) {
        return engine.clone();
    }
    if let Some(run) = &view.run
        && let Some(check) = run["checks"].as_array().and_then(|checks| {
            checks
                .iter()
                .find(|check| check["task_id"] == finding["task_id"])
        })
        && let Some(name) = crate::legacy_engine(text(check, "check_id"))
    {
        return name.into();
    }
    let rule = text(finding, "rule_id");
    if let Some(engine) = rule
        .strip_prefix("ici.legacy.")
        .and_then(|rule| rule.strip_suffix(".target"))
    {
        return engine.into();
    }
    if let Some(engine) = rule
        .strip_prefix("ici.python.")
        .and_then(|rule| rule.split('.').next())
    {
        return match engine {
            "correctness" => "exception",
            other => other,
        }
        .into();
    }
    finding
        .get("provider")
        .or_else(|| finding.get("tool_name"))
        .and_then(Value::as_str)
        .unwrap_or("native")
        .into()
}
fn span(value: &Value) -> Value {
    json!({"path":value["path"],"start_line":value["start_line"],"end_line":value["end_line"],"start_column":value["start_column"],"end_column":value["end_column"],"label":value["label"].as_str().unwrap_or("")})
}
fn bounds(value: &Value) -> (u64, u64) {
    let start = value["start_line"].as_u64().unwrap_or(1);
    (start, value["end_line"].as_u64().unwrap_or(start))
}
fn precise(value: &Value) -> Option<((u64, u64), (u64, u64))> {
    let start = (
        value["start_line"].as_u64()?,
        value["start_column"].as_u64()?,
    );
    let end = (value["end_line"].as_u64()?, value["end_column"].as_u64()?);
    if start.0 == 0 || start.1 == 0 || end.0 == 0 || end.1 == 0 || end < start {
        None
    } else {
        Some((start, end))
    }
}
fn locations(records: &[Record]) -> Vec<Value> {
    let mut locations = vec![];
    for record in records {
        for source in std::iter::once(&record.finding["primary_location"]).chain(
            record.finding["related_locations"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default(),
        ) {
            let (start, end) = bounds(source);
            let label = text(source, "label");
            locations.push(json!({"path":source["path"],"start_line":start,"end_line":end,"labels":if label.is_empty(){vec![]}else{vec![label]}}));
        }
    }
    locations.sort_by_key(|location| {
        (
            plain(&location["path"]),
            bounds(location),
            plain(&location["labels"]),
        )
    });
    let mut merged: Vec<Value> = vec![];
    for location in locations {
        if let Some(last) = merged.last_mut().filter(|last| {
            last["path"] == location["path"] && bounds(&location).0 <= bounds(last).1
        }) {
            last["end_line"] = json!(bounds(last).1.max(bounds(&location).1));
            let labels: BTreeSet<_> = last["labels"]
                .as_array()
                .expect("array")
                .iter()
                .chain(location["labels"].as_array().expect("array"))
                .map(plain)
                .collect();
            last["labels"] = json!(labels);
        } else {
            merged.push(location);
        }
    }
    merged
}
fn representative(records: &[Record]) -> &Record {
    records
        .iter()
        .min_by_key(|record| {
            (
                severity(&record.finding),
                plain(&record.finding["primary_location"]["path"]),
                bounds(&record.finding["primary_location"]).0,
                record.engine.clone(),
                plain(&record.finding["fingerprint"]),
            )
        })
        .expect("nonempty group")
}
fn source_rule(finding: &Value) -> String {
    for key in ["tool_rule_id", "native_rule_id"] {
        if !text(finding, key).trim().is_empty() {
            return text(finding, key).trim().into();
        }
    }
    let label = text(&finding["primary_location"], "label").trim();
    if !label.is_empty() {
        label.into()
    } else {
        text(finding, "rule_id").trim().into()
    }
}
fn provenance(record: &Record) -> String {
    let tool = record
        .finding
        .get("tool_name")
        .or_else(|| record.finding.get("provider"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let version = record
        .finding
        .get("tool_version")
        .or_else(|| record.finding.get("rule_version"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let tool = [tool, version]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    format!(
        "{}/{}{}",
        record.engine,
        source_rule(&record.finding),
        if tool.is_empty() {
            String::new()
        } else {
            format!(" ({tool})")
        }
    )
}
fn group(records: &[Record], merged: bool) -> IssueGroup {
    let rep = representative(records);
    let mut producer_counts = BTreeMap::<String, usize>::new();
    for record in records {
        *producer_counts.entry(record.engine.clone()).or_default() += 1;
    }
    let context = records.iter().find_map(trusted).unwrap_or_default();
    let (id, mergeable) = identity(rep, &context);
    let mut related = rep.finding["related_locations"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(span)
        .collect::<Vec<_>>();
    if merged {
        related.clear();
        let mut seen = BTreeSet::new();
        for record in records {
            for (index, source) in std::iter::once(&record.finding["primary_location"])
                .chain(
                    record.finding["related_locations"]
                        .as_array()
                        .map(Vec::as_slice)
                        .unwrap_or_default(),
                )
                .enumerate()
            {
                if record.index == rep.index && index == 0 {
                    continue;
                }
                let source = span(source);
                let key = source.to_string();
                if seen.insert(key) {
                    related.push(source);
                }
            }
        }
    }
    let fingerprints: BTreeSet<_> = records
        .iter()
        .map(|record| plain(&record.finding["fingerprint"]))
        .collect();
    let provenance: BTreeSet<_> = if merged || (python(&rep.finding) && mergeable) {
        records.iter().map(provenance).collect()
    } else {
        BTreeSet::new()
    };
    IssueGroup {
        view: json!({"engine_name":producer_counts.keys().cloned().collect::<Vec<_>>().join(" + "),"rule_id":if python(&rep.finding)&&mergeable{id}else{plain(&rep.finding["rule_id"])},"category":rep.finding["category"],"severity":rep.finding["severity"],"confidence":rep.finding["confidence"],"fingerprints":fingerprints,"message":rep.finding["message"],"snippet":rep.finding["snippet"].as_str().unwrap_or(""),"locations":locations(records),"original_finding_count":records.len(),"clone_group_id":"","producer_counts":producer_counts.into_iter().collect::<Vec<_>>(),"provenance":provenance,"primary_location":span(&rep.finding["primary_location"]),"related_locations":related}),
        findings: records
            .iter()
            .map(|record| record.finding.clone())
            .collect(),
        source_indices: std::iter::once(rep.index)
            .chain(
                records
                    .iter()
                    .filter(|record| record.index != rep.index)
                    .map(|record| record.index),
            )
            .collect(),
    }
}
#[derive(Deserialize)]
struct Mapping {
    canonical_rule_id: String,
    aliases: Vec<String>,
    always_mergeable: bool,
}
fn mappings() -> &'static Vec<Mapping> {
    static MAPPINGS: OnceLock<Vec<Mapping>> = OnceLock::new();
    MAPPINGS.get_or_init(|| {
        serde_json::from_str(include_str!("../assets/python-rule-aliases.json"))
            .expect("frozen reviewed aliases")
    })
}
fn norm(value: &str) -> String {
    let mut out = String::new();
    for ch in value.trim().to_lowercase().chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
        } else if !out.is_empty() && !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_end_matches('-').into()
}
fn contextual(mapping: &Mapping, source: &str, context: &str) -> (String, bool) {
    let id = &mapping.canonical_rule_id;
    let context = norm(context);
    let generic = matches!(
        id.as_str(),
        "ici.python.security.weak-hash" | "ici.python.security.dynamic-execution"
    );
    if source.trim() == id && !generic {
        return (id.clone(), true);
    }
    let remap = match (id.as_str(), context.as_str()) {
        ("ici.python.security.weak-hash", "md5" | "weak-md5") => {
            Some("ici.python.security.weak-md5")
        }
        ("ici.python.security.weak-hash", "sha1" | "weak-sha1") => {
            Some("ici.python.security.weak-sha1")
        }
        ("ici.python.security.dynamic-execution", "eval") => Some("ici.python.security.eval"),
        ("ici.python.security.dynamic-execution", "exec") => Some("ici.python.security.exec"),
        _ => None,
    };
    if let Some(id) = remap {
        return (id.into(), true);
    }
    if generic {
        return (id.clone(), false);
    }
    let (suffix, contexts): (&str, &[&str]) = match id.as_str() {
        "ici.python.exception.base-exception" => ("baseexception", &["baseexception"]),
        "ici.python.resource.open-without-context" => ("openwithoutwith", &["confirmed-leak"]),
        "ici.python.security.shell-true" => ("shelltrue", &["subprocess"]),
        "ici.python.security.command-processor" => ("commandprocessor", &["os-system", "os-popen"]),
        "ici.python.complexity.cyclomatic" => ("", &["cyclomatic"]),
        _ => return (id.clone(), mapping.always_mergeable),
    };
    let source = norm(source);
    (
        id.clone(),
        !suffix.is_empty() && (source == suffix || source.ends_with(&format!("-{suffix}")))
            || contexts.contains(&context.as_str()),
    )
}
fn identity(record: &Record, context: &str) -> (String, bool) {
    let finding = &record.finding;
    let rule = text(finding, "rule_id").trim();
    if let Some(mapping) = mappings()
        .iter()
        .find(|mapping| mapping.canonical_rule_id == rule)
    {
        return contextual(mapping, rule, context);
    }
    if rule.starts_with("ici.python.")
        && rule
            .chars()
            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '.' | '-'))
    {
        return (rule.into(), true);
    }
    let mut candidates = vec![];
    for source in [
        text(finding, "tool_rule_id"),
        text(finding, "native_rule_id"),
        text(&finding["primary_location"], "label"),
        rule,
    ] {
        let source = source.trim();
        if source.is_empty() {
            continue;
        }
        candidates.push(source.to_string());
        if let Some((_, tail)) = source.split_once('[')
            && let Some((code, _)) = tail.split_once(']')
            && code
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-'))
        {
            candidates.push(code.into());
        }
        if let Some((_, tail)) = source.rsplit_once(':')
            && !tail.trim().is_empty()
        {
            candidates.push(tail.trim().into());
        }
    }
    for candidate in &candidates {
        if let Some(mapping) = mappings().iter().find(|mapping| {
            mapping
                .aliases
                .iter()
                .any(|alias| norm(alias) == norm(candidate))
        }) {
            let (id, mut mergeable) = contextual(mapping, candidate, context);
            if id == "ici.python.complexity.cyclomatic" {
                if !["", "complexity", "lint", "ruff"].contains(&record.engine.as_str()) {
                    return (format!("ici.python.external.{}", norm(candidate)), false);
                }
                if record.engine == "complexity" && context.is_empty() {
                    mergeable = true;
                }
            }
            return (id, mergeable);
        }
    }
    match record.engine.as_str() {
        "complexity" => ("ici.python.complexity.cyclomatic".into(), true),
        "cognitive" => ("ici.python.cognitive.complexity".into(), true),
        "dead" if norm(text(&finding["primary_location"], "label")) == "unreachablecode" => {
            ("ici.python.dead.unreachable-statement".into(), true)
        }
        _ => {
            if rule.starts_with("ici.")
                && (rule.chars().all(|ch| {
                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '.' | '-')
                }))
            {
                (rule.into(), false)
            } else {
                (
                    format!(
                        "ici.python.external.{}",
                        norm(candidates.first().map(String::as_str).unwrap_or("unknown"))
                    ),
                    false,
                )
            }
        }
    }
}
fn trusted(record: &Record) -> Option<String> {
    let source = record
        .finding
        .get("tool_rule_id")
        .or_else(|| record.finding.get("native_rule_id"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(text(&record.finding["primary_location"], "label"))
        .to_lowercase();
    let source = source.rsplit(':').next().unwrap_or("");
    Some(
        match (record.engine.as_str(), source) {
            ("exception", "baseexception") => "baseexception",
            ("resource", "openwithoutwith") => "confirmed-leak",
            ("security", "weakcryptomd5") => "md5",
            ("security", "weakcryptosha1") => "sha1",
            ("security", "shelltrue") | ("lint", "s602" | "s604") => "subprocess",
            ("security", "commandprocessor") | ("lint", "s605") => "os.system",
            ("lint", "s307") => "eval",
            _ => return None,
        }
        .into(),
    )
}
fn can_merge(left: &Record, right: &Record) -> bool {
    if left.engine == right.engine || !python(&left.finding) || !python(&right.finding) {
        return false;
    }
    let l = &left.finding["primary_location"];
    let r = &right.finding["primary_location"];
    if l["path"] != r["path"] {
        return false;
    }
    let (Some((ls, le)), Some((rs, re))) = (precise(l), precise(r)) else {
        return false;
    };
    if ls > re || rs > le {
        return false;
    }
    let lc = trusted(left);
    let rc = trusted(right);
    let (lid, lm) = identity(left, lc.as_deref().or(rc.as_deref()).unwrap_or(""));
    let (rid, rm) = identity(right, rc.as_deref().or(lc.as_deref()).unwrap_or(""));
    lm && rm && lid == rid
}
fn find(parents: &mut [usize], mut index: usize) -> usize {
    while parents[index] != index {
        parents[index] = parents[parents[index]];
        index = parents[index];
    }
    index
}
pub fn project_issue_groups(stored: &StoredResult) -> Result<IssueProjection> {
    let view = View::new(stored)?;
    project(&view)
}
fn clone_groups(view: &View, records: &[Record]) -> (Vec<IssueGroup>, BTreeSet<usize>) {
    let mut consumed = BTreeSet::new();
    let mut groups = vec![];
    let mut raw_groups = vec![];
    for engine in &view.engines {
        if engine["engine_name"] != "dup" {
            continue;
        }
        for group in engine["extra"]["clone_groups"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            raw_groups.push((group, None));
        }
        for check in engine["extra"]["native_checks"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            for group in check["extra"]["clone_groups"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default()
            {
                raw_groups.push((group, check["task_id"].as_str()));
            }
        }
    }
    for (raw, task) in raw_groups {
        let id = match &raw["id"] {
            Value::String(value) if !value.is_empty() => value.clone(),
            Value::Number(value) if value.is_i64() || value.is_u64() => value.to_string(),
            _ => continue,
        };
        let expected = format!("CloneGroup#{id}");
        let mut source_records = vec![];
        for occurrence in raw["occurrences"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let Some(path) = occurrence["file_path"].as_str().filter(|path| {
                !path.is_empty()
                    && !path.starts_with('/')
                    && !path.contains('\\')
                    && !path.split('/').any(|part| part == "..")
            }) else {
                continue;
            };
            let Some(start) = occurrence["start_line"].as_u64().filter(|start| *start > 0) else {
                continue;
            };
            let end = occurrence["end_line"]
                .as_u64()
                .filter(|end| *end >= start)
                .unwrap_or(start);
            source_records.push(Record{engine:"dup".into(),index:usize::MAX,finding:json!({"primary_location":{"path":path,"start_line":start,"end_line":end,"label":""},"related_locations":[]})});
        }
        if source_records.is_empty() {
            continue;
        }
        let matched: Vec<_> = records
            .iter()
            .filter(|record| {
                record.engine == "dup"
                    && !consumed.contains(&record.index)
                    && text(&record.finding["primary_location"], "label") == expected
                    && task.is_none_or(|task| {
                        record.finding["task_id"]
                            .as_str()
                            .is_none_or(|actual| actual == task)
                    })
                    && source_records.iter().any(|source| {
                        let source = &source.finding["primary_location"];
                        let primary = &record.finding["primary_location"];
                        let (ls, le) = bounds(source);
                        let (rs, re) = bounds(primary);
                        source["path"] == primary["path"] && ls <= re && rs <= le
                    })
            })
            .cloned()
            .collect();
        if matched.is_empty() {
            continue;
        }
        consumed.extend(matched.iter().map(|record| record.index));
        let mut result = group(&matched, false);
        let merged = locations(&source_records);
        let line_text = if raw["lines_count"].is_i64() || raw["lines_count"].is_u64() {
            format!("{} matched lines", raw["lines_count"])
        } else {
            "matched code".into()
        };
        result.view["message"] = json!(format!(
            "Clone group {id}: {line_text} across {} location(s)",
            merged.len()
        ));
        result.view["clone_group_id"] = json!(id);
        result.view["locations"] = json!(merged);
        if let Some(snippet) = raw["snippet"].as_str() {
            result.view["snippet"] = json!(snippet);
        }
        groups.push(result);
    }
    (groups, consumed)
}
pub(crate) fn project(view: &View) -> Result<IssueProjection> {
    let records: Vec<_> = view
        .findings
        .iter()
        .enumerate()
        .filter(|(_, finding)| !suppressed(finding) && !informational(finding))
        .map(|(index, finding)| Record {
            engine: engine_for(index, finding, view),
            finding: finding.clone(),
            index,
        })
        .collect();
    let total_findings = records.len();
    let (mut clone_groups, consumed) = clone_groups(view, &records);
    let mut components: Vec<Vec<Record>> = vec![];
    let mut buckets = BTreeMap::<(String, String, String, String), Vec<Record>>::new();
    for record in records
        .into_iter()
        .filter(|record| !consumed.contains(&record.index))
    {
        buckets
            .entry((
                record.engine.clone(),
                plain(&record.finding["rule_id"]),
                plain(&record.finding["fingerprint"]),
                plain(&record.finding["primary_location"]["path"]),
            ))
            .or_default()
            .push(record);
    }
    for mut records in buckets.into_values() {
        records.sort_by_key(|record| {
            (
                bounds(&record.finding["primary_location"]),
                plain(&record.finding["message"]),
            )
        });
        let mut end = 0;
        let mut current = vec![];
        for record in records {
            let (start, next_end) = bounds(&record.finding["primary_location"]);
            if !current.is_empty() && start > end {
                components.push(current);
                current = vec![];
            }
            end = if current.is_empty() {
                next_end
            } else {
                end.max(next_end)
            };
            current.push(record);
        }
        if !current.is_empty() {
            components.push(current);
        }
    }
    let mut parents: Vec<_> = (0..components.len()).collect();
    let mut candidates = BTreeMap::<(String, String), BTreeSet<usize>>::new();
    for (index, records) in components.iter().enumerate() {
        for record in records {
            if !python(&record.finding) || precise(&record.finding["primary_location"]).is_none() {
                continue;
            }
            let (id, _) = identity(record, "");
            if id.starts_with("ici.python.external.") {
                continue;
            }
            let ids: Vec<String> = match id.as_str() {
                "ici.python.security.weak-hash" => vec![
                    "ici.python.security.weak-md5".into(),
                    "ici.python.security.weak-sha1".into(),
                ],
                "ici.python.security.dynamic-execution" => vec![
                    "ici.python.security.eval".into(),
                    "ici.python.security.exec".into(),
                ],
                _ => vec![id],
            };
            for id in ids {
                candidates
                    .entry((id, plain(&record.finding["primary_location"]["path"])))
                    .or_default()
                    .insert(index);
            }
        }
    }
    let mut comparisons = 0;
    let mut limited = false;
    'buckets: for indices in candidates.into_values() {
        let mut indices: Vec<_> = indices.into_iter().collect();
        indices.sort_by_key(|index| {
            components[*index]
                .iter()
                .map(|record| bounds(&record.finding["primary_location"]).0)
                .min()
                .unwrap_or(1)
        });
        for (offset, left) in indices.iter().enumerate() {
            let left_end = components[*left]
                .iter()
                .map(|record| bounds(&record.finding["primary_location"]).1)
                .max()
                .unwrap_or(1);
            for right in &indices[offset + 1..] {
                let right_start = components[*right]
                    .iter()
                    .map(|record| bounds(&record.finding["primary_location"]).0)
                    .min()
                    .unwrap_or(1);
                if right_start > left_end {
                    break;
                }
                comparisons += 1;
                if comparisons > 1_000_000 {
                    limited = true;
                    break 'buckets;
                }
                let mut merge = false;
                'records: for left_record in &components[*left] {
                    for right_record in &components[*right] {
                        comparisons += 1;
                        if comparisons > 1_000_000 {
                            limited = true;
                            break 'buckets;
                        }
                        if can_merge(left_record, right_record) {
                            merge = true;
                            break 'records;
                        }
                    }
                }
                if merge {
                    let l = find(&mut parents, *left);
                    let r = find(&mut parents, *right);
                    parents[r] = l;
                }
            }
        }
    }
    let mut clusters = BTreeMap::<usize, Vec<usize>>::new();
    for index in 0..components.len() {
        let root = find(&mut parents, index);
        clusters.entry(root).or_default().push(index);
    }
    let mut groups: Vec<_> = clusters
        .into_values()
        .map(|indices| {
            let records: Vec<_> = indices
                .iter()
                .flat_map(|index| components[*index].clone())
                .collect();
            group(&records, indices.len() > 1)
        })
        .collect();
    groups.append(&mut clone_groups);
    groups.sort_by_key(|group| {
        let rep = &group.view;
        let first = &rep["locations"][0];
        (
            severity(rep),
            plain(&rep["engine_name"]),
            plain(&rep["category"]),
            plain(&rep["rule_id"]),
            plain(&first["path"]),
            bounds(first),
            plain(&rep["fingerprints"]),
            plain(&rep["message"]),
        )
    });
    Ok(IssueProjection {
        groups,
        total_findings,
        limitations: if limited {
            vec!["Display grouping stopped after one million candidate comparisons; all canonical findings remain present".into()]
        } else {
            vec![]
        },
    })
}
