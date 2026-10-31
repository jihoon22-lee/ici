//! Coverage.py JSON validation and raw-count aggregation; no AST calculation is delegated.
use crate::{ValidationError, ValidationRequest};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Counts {
    pub covered_lines: u64,
    pub total_lines: u64,
    pub covered_branches: u64,
    pub total_branches: u64,
    pub covered_functions: Option<u64>,
    pub total_functions: Option<u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FileCoverage {
    pub path: String,
    pub executed: BTreeSet<u32>,
    pub missing: BTreeSet<u32>,
    pub counts: Counts,
    pub functions: Option<BTreeMap<String, Value>>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CoverageReport {
    pub files: BTreeMap<String, FileCoverage>,
    pub counts: Counts,
    pub function_rows: Vec<Value>,
    pub excluded_files: Vec<String>,
    pub limitations: Vec<String>,
}
fn count(value: &Value, key: &str) -> Result<u64, ValidationError> {
    value[key]
        .as_u64()
        .ok_or_else(|| ValidationError(format!("coverage {key} must be a nonnegative integer")))
}
fn counts(value: &Value) -> Result<Counts, ValidationError> {
    let result = Counts {
        covered_lines: count(value, "covered_lines")?,
        total_lines: count(value, "num_statements")?,
        covered_branches: count(value, "covered_branches")?,
        total_branches: count(value, "num_branches")?,
        ..Counts::default()
    };
    let missing = count(value, "missing_lines")?;
    if result.covered_lines.checked_add(missing) != Some(result.total_lines)
        || result.covered_branches > result.total_branches
    {
        return Err(ValidationError(
            "coverage count parts do not match denominators".into(),
        ));
    }
    Ok(result)
}
fn lines(value: &Value, expected: u64) -> Result<BTreeSet<u32>, ValidationError> {
    let items = value
        .as_array()
        .ok_or_else(|| ValidationError("coverage line set missing".into()))?;
    if items.len() as u64 != expected {
        return Err(ValidationError("coverage line set/count mismatch".into()));
    }
    let mut result = BTreeSet::new();
    for item in items {
        let line = item
            .as_u64()
            .and_then(|value| u32::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| ValidationError("coverage line must be a positive integer".into()))?;
        if !result.insert(line) {
            return Err(ValidationError("duplicate coverage line".into()));
        }
    }
    Ok(result)
}
fn add(target: &mut Counts, part: &Counts) -> Result<(), ValidationError> {
    target.covered_lines = target
        .covered_lines
        .checked_add(part.covered_lines)
        .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
    target.total_lines = target
        .total_lines
        .checked_add(part.total_lines)
        .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
    target.covered_branches = target
        .covered_branches
        .checked_add(part.covered_branches)
        .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
    target.total_branches = target
        .total_branches
        .checked_add(part.total_branches)
        .ok_or_else(|| ValidationError("coverage count overflow".into()))?;
    Ok(())
}
pub fn parse_python(
    bytes: &[u8],
    request: &ValidationRequest,
) -> Result<CoverageReport, ValidationError> {
    let document: Value = serde_json::from_slice(bytes)
        .map_err(|error| ValidationError(format!("coverage JSON: {error}")))?;
    if document["meta"]["branch_coverage"] != true {
        return Err(ValidationError(
            "coverage report does not contain measured branch coverage".into(),
        ));
    }
    let raw_files = document["files"]
        .as_object()
        .filter(|files| !files.is_empty())
        .ok_or_else(|| ValidationError("coverage files missing or empty".into()))?;
    if raw_files.len() > 16384 {
        return Err(ValidationError("coverage file limit exceeded".into()));
    }
    let mut result = CoverageReport {
        files: BTreeMap::new(),
        counts: Counts::default(),
        function_rows: vec![],
        excluded_files: vec![],
        limitations: vec![],
    };
    let declared: BTreeSet<_> = request.source_files.iter().cloned().collect();
    if declared.is_empty() {
        return Err(ValidationError(
            "coverage requires a declared source denominator".into(),
        ));
    }
    let mut all_counts = Counts::default();
    let mut seen = BTreeSet::new();
    for (name, info) in raw_files {
        let path = crate::planning::relative(name, &request.root, &request.cwd)?;
        if !seen.insert(path.clone()) {
            return Err(ValidationError(
                "coverage aliases duplicate one file".into(),
            ));
        }
        let counts = counts(&info["summary"])?;
        let executed = lines(&info["executed_lines"], counts.covered_lines)?;
        let missing = lines(
            &info["missing_lines"],
            counts.total_lines - counts.covered_lines,
        )?;
        if !executed.is_disjoint(&missing) {
            return Err(ValidationError(
                "executed and missing coverage lines overlap".into(),
            ));
        }
        for (key, expected) in [
            ("executed_branches", counts.covered_branches),
            (
                "missing_branches",
                counts.total_branches - counts.covered_branches,
            ),
        ] {
            if let Some(arcs) = info.get(key) {
                let arcs = arcs
                    .as_array()
                    .ok_or_else(|| ValidationError("coverage branches must be arrays".into()))?;
                if arcs.len() as u64 != expected {
                    return Err(ValidationError("coverage branch count mismatch".into()));
                }
                let mut unique = BTreeSet::new();
                for arc in arcs {
                    let pair = arc
                        .as_array()
                        .filter(|pair| pair.len() == 2)
                        .ok_or_else(|| {
                            ValidationError("coverage arc must have two lines".into())
                        })?;
                    let from = pair[0].as_i64().ok_or_else(|| {
                        ValidationError("coverage arc line must be integer".into())
                    })?;
                    let to = pair[1].as_i64().ok_or_else(|| {
                        ValidationError("coverage arc line must be integer".into())
                    })?;
                    if !unique.insert((from, to)) {
                        return Err(ValidationError("duplicate coverage branch".into()));
                    }
                }
            }
        }
        add(&mut all_counts, &counts)?;
        if declared.contains(&path) {
            add(&mut result.counts, &counts)?;
            let functions = info
                .get("functions")
                .and_then(Value::as_object)
                .map(|items| items.iter().map(|(k, v)| (k.clone(), v.clone())).collect());
            result.files.insert(
                path.clone(),
                FileCoverage {
                    path,
                    executed,
                    missing,
                    counts,
                    functions,
                },
            );
        } else {
            result.excluded_files.push(path);
        }
    }
    if all_counts != counts(&document["totals"])? {
        return Err(ValidationError(
            "coverage totals differ from file parts".into(),
        ));
    }
    if declared.iter().any(|path| !result.files.contains_key(path)) {
        return Err(ValidationError(
            "coverage report omits declared source files; their denominator is unmeasured".into(),
        ));
    }
    if !request.functions.is_empty() {
        let mut covered = 0;
        let mut complete = true;
        for function in &request.functions {
            if function.start_line == 0
                || function.body_start_line < function.start_line
                || function.end_line < function.body_start_line
            {
                return Err(ValidationError("invalid native function body facts".into()));
            }
            let file = result.files.get(&function.path).ok_or_else(|| {
                ValidationError("function facts refer outside coverage source scope".into())
            })?;
            let metadata = file
                .functions
                .as_ref()
                .and_then(|items| items.get(&function.qualified_name));
            let ran = if let Some(metadata) = metadata {
                if metadata["start_line"].as_u64() != Some(u64::from(function.start_line)) {
                    return Err(ValidationError(
                        "coverage function metadata disagrees with native source facts".into(),
                    ));
                }
                let summary = counts(&metadata["summary"])?;
                let executed = lines(&metadata["executed_lines"], summary.covered_lines)?;
                let missing = lines(
                    &metadata["missing_lines"],
                    summary.total_lines - summary.covered_lines,
                )?;
                if !executed.is_disjoint(&missing) {
                    return Err(ValidationError(
                        "function executed/missing lines overlap".into(),
                    ));
                }
                // Def/decorator/default-expression hits do not prove a call. For a same-line
                // body coverage.py records a function exit arc independently of the header hit.
                let body_start = function
                    .body_start_line
                    .max(function.start_line.saturating_add(1));
                let body_hit = body_start <= function.end_line
                    && executed
                        .range(body_start..=function.end_line)
                        .next()
                        .is_some();
                let entry_line = function.entry_line.unwrap_or(function.start_line);
                if entry_line == 0 || entry_line > function.start_line {
                    return Err(ValidationError("invalid native function entry line".into()));
                }
                let exit_arc = |key: &str| {
                    metadata[key].as_array().is_some_and(|arcs| {
                        arcs.iter().any(|arc| {
                            arc.as_array().is_some_and(|pair| {
                                pair.len() == 2 && pair[1].as_i64() == Some(-i64::from(entry_line))
                            })
                        })
                    })
                };
                if body_hit || exit_arc("executed_branches") {
                    Some(true)
                } else if function.body_start_line > function.start_line
                    && missing
                        .range(function.body_start_line..=function.end_line)
                        .next()
                        .is_some()
                    || exit_arc("missing_branches")
                {
                    Some(false)
                } else {
                    None
                }
            } else {
                // Older coverage JSON lacks per-function sections. A native multiline body
                // hit is still execution evidence; definition/header hits are never enough.
                if function.body_start_line > function.start_line {
                    if file
                        .executed
                        .range(function.body_start_line..=function.end_line)
                        .next()
                        .is_some()
                    {
                        Some(true)
                    } else if file
                        .missing
                        .range(function.body_start_line..=function.end_line)
                        .next()
                        .is_some()
                    {
                        Some(false)
                    } else {
                        None
                    }
                } else {
                    None
                }
            };
            if let Some(ran) = ran {
                covered += u64::from(ran);
            } else {
                complete = false;
            }
            result.function_rows.push(serde_json::json!({"file":function.path,"name":function.name,"start_line":function.start_line,"end_line":function.end_line,"covered":ran,"missing_lines":file.missing.range(function.body_start_line..=function.end_line).take(30).collect::<Vec<_>>()}));
        }
        if complete {
            result.counts.covered_functions = Some(covered);
            result.counts.total_functions = Some(request.functions.len() as u64);
        } else {
            result.limitations.push("function invocation coverage is not fully measured; definition/header hits alone are insufficient".into());
        }
    } else if request.options.function_facts_complete {
        result.counts.covered_functions = Some(0);
        result.counts.total_functions = Some(0);
    }
    Ok(result)
}
