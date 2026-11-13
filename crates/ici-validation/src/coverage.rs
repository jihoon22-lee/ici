//! Coverage.py JSON validation and raw-count aggregation; no AST calculation is delegated.
use crate::{ValidationError, ValidationRequest};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

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
    if let Some(count) = value[key].as_u64() {
        return Ok(count);
    }
    // A nonnegative integral count past u64 (serde_json reads it as a float): frozen's Python
    // `int` accepts it, native refuses it explicitly (root ruling 2026-11-14, fail-closed, as
    // the stored-result reader's integer range).
    if value[key]
        .as_f64()
        .is_some_and(|count| count.fract() == 0.0 && count >= u64::MAX as f64)
    {
        return Err(ValidationError(format!(
            "coverage {key} exceeds the supported integer range 0..=18446744073709551615"
        )));
    }
    Err(ValidationError(format!(
        "coverage {key} must be a nonnegative integer"
    )))
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
/// The key frozen `_relative_coverage_path` gives a coverage.py file name: `PurePath` drops
/// repeated separators and interior `.` components but keeps `..` and a relative name's
/// spelling, so `relative_to` succeeds only for an absolute name lexically under the root.
fn frozen_key(name: &str, root: &Path) -> String {
    let path = Path::new(name);
    if path.is_absolute()
        && let Ok(relative) = path.strip_prefix(root)
    {
        return relative
            .components()
            .map(|part| part.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
    }
    name.to_owned()
}
/// The string a non-standard constant is read as while the document is checked.
const NONFINITE: &str = "\u{0}ici:nonfinite";

/// Python `json.loads` (frozen `parse_coverage_json`) also reads the constants `NaN`,
/// `Infinity` and `-Infinity`. Root ruling 2026-11-14 (`nan-in-meta`): such a constant is
/// accepted inside the top-level `meta` object, which no computed result reads (frozen reads no
/// `meta` key; native reads only `meta.branch_coverage`, which must be `true`), and read there
/// as `null`. Anywhere else (a count, a line set, totals) the document stays strict JSON and is
/// refused with serde_json's error.
fn python_json(bytes: &[u8]) -> Result<Value, serde_json::Error> {
    let error = match serde_json::from_slice(bytes) {
        Ok(document) => return Ok(document),
        Err(error) => error,
    };
    let (text, replaced) = nonfinite_constants(bytes);
    if replaced == 0 {
        return Err(error);
    }
    let Ok(mut document) = serde_json::from_slice::<Value>(&text) else {
        return Err(error);
    };
    fn sentinels(value: &Value) -> usize {
        match value {
            Value::String(text) => usize::from(text == NONFINITE),
            Value::Array(items) => items.iter().map(sentinels).sum(),
            Value::Object(items) => items.values().map(sentinels).sum(),
            _ => 0,
        }
    }
    fn clear(value: &mut Value) {
        match value {
            Value::String(text) if text == NONFINITE => *value = Value::Null,
            Value::Array(items) => items.iter_mut().for_each(clear),
            Value::Object(items) => items.values_mut().for_each(clear),
            _ => {}
        }
    }
    // Every replaced constant (and no sentinel the document spelled itself) is a `meta` value.
    let in_meta = document.get("meta").map_or(0, sentinels);
    if sentinels(&document) != replaced || in_meta != replaced {
        return Err(error);
    }
    if let Some(meta) = document.get_mut("meta") {
        clear(meta);
    }
    Ok(document)
}

/// The document with each `NaN`, `Infinity` and `-Infinity` token outside a string spelled as
/// the [`NONFINITE`] string, and how many were replaced.
fn nonfinite_constants(bytes: &[u8]) -> (Vec<u8>, usize) {
    let sentinel = serde_json::to_string(NONFINITE).expect("a string serializes");
    let mut text = Vec::with_capacity(bytes.len());
    let mut replaced = 0;
    let (mut index, mut string, mut escaped) = (0, false, false);
    while index < bytes.len() {
        let byte = bytes[index];
        if string {
            text.push(byte);
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                string = false;
            }
            index += 1;
            continue;
        }
        if byte == b'"' {
            string = true;
        } else if let Some(token) = [&b"NaN"[..], b"Infinity", b"-Infinity"]
            .into_iter()
            .find(|token| bytes[index..].starts_with(token))
        {
            text.extend_from_slice(sentinel.as_bytes());
            replaced += 1;
            index += token.len();
            continue;
        }
        text.push(byte);
        index += 1;
    }
    (text, replaced)
}
pub fn parse_python(
    bytes: &[u8],
    request: &ValidationRequest,
) -> Result<CoverageReport, ValidationError> {
    let document =
        python_json(bytes).map_err(|error| ValidationError(format!("coverage JSON: {error}")))?;
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
        if request.options.legacy_pytest_result && frozen_key(name, &request.root) != path {
            // Frozen `_relative_coverage_path` (coverage_support.py:120-124) keys a file by
            // `Path(fname).relative_to(project_root)` when that succeeds and by `fname` as written
            // otherwise, and `_coverage_paths_match` then compares those keys with the
            // discovered sources exactly: a `./main.py`, `src/../main.py` or `src/./a.py`
            // spelling matches no discovered source and frozen refuses the report (root ruling
            // 2026-11-14, coverage-json-strictness-oracle.json `dot-relative-name`).
            return Err(ValidationError(
                "coverage report names files outside the discovered sources".into(),
            ));
        }
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
    // Frozen `parse_coverage_json` (coverage_support.py:224, :226): no statement in the totals is
    // no evidence. Legacy also compares the reported files with the discovered sources exactly
    // (`_coverage_paths_match`, expected_files); the next provider passes no expected set.
    if all_counts.total_lines == 0 {
        return Err(ValidationError(
            "coverage report counts no executable statement".into(),
        ));
    }
    if request.options.legacy_pytest_result && !result.excluded_files.is_empty() {
        return Err(ValidationError(
            "coverage report names files outside the discovered sources".into(),
        ));
    }
    if declared.iter().any(|path| !result.files.contains_key(path)) {
        return Err(ValidationError(
            "coverage report omits declared source files; their denominator is unmeasured".into(),
        ));
    }
    if !request.functions.is_empty() {
        let mut covered = 0;
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
            // Frozen `compute_python_function_coverage` (coverage_support.py:436-447), shared by
            // the legacy engine and the next coverage provider: a function is covered when one
            // line of `range(body[0].lineno, end_lineno + 1)` executed. A one-line function's
            // body starts on its `def` line, which runs at import, so line data reads it covered
            // whether or not it was called. Line data alone cannot tell the two apart, so a
            // stricter rule would also read a called one-liner uncovered.
            let ran = file
                .executed
                .range(function.body_start_line..=function.end_line)
                .next()
                .is_some();
            covered += u64::from(ran);
            result.function_rows.push(serde_json::json!({"file":function.path,"name":function.name,"start_line":function.start_line,"end_line":function.end_line,"covered":ran,"missing_lines":file.missing.range(function.body_start_line..=function.end_line).take(30).collect::<Vec<_>>()}));
        }
        if request.options.legacy_pytest_result {
            // Frozen `compute_python_function_coverage` (coverage_support.py:434): per source
            // file, `ast.walk` breadth-first order, i.e. by `def` depth, then source position.
            let mut files: Vec<&str> = vec![];
            let mut keyed: Vec<_> = request
                .functions
                .iter()
                .zip(std::mem::take(&mut result.function_rows))
                .map(|(function, row)| {
                    let file = files
                        .iter()
                        .position(|path| *path == function.path)
                        .unwrap_or_else(|| {
                            files.push(&function.path);
                            files.len() - 1
                        });
                    ((file, function.ast_depth), row)
                })
                .collect();
            keyed.sort_by_key(|(key, _)| *key);
            result.function_rows = keyed.into_iter().map(|(_, row)| row).collect();
        }
        result.counts.covered_functions = Some(covered);
        result.counts.total_functions = Some(request.functions.len() as u64);
    } else if request.options.function_facts_complete {
        result.counts.covered_functions = Some(0);
        result.counts.total_functions = Some(0);
    }
    Ok(result)
}
