//! Bounded legacy GCOV text evidence. Missing geometry stays explicitly unavailable.
use crate::{
    ValidationError, ValidationPlan,
    coverage::{Counts, CoverageReport, FileCoverage},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

#[derive(Clone, Debug, Default)]
pub struct TextReport {
    pub source: String,
    pub graph: String,
    pub data: String,
    pub lines: BTreeMap<u32, bool>,
    pub branches: BTreeMap<(u32, String, u32), bool>,
    pub functions: BTreeMap<String, bool>,
    pub ignored_throw_branches: u64,
    pub records: usize,
}
fn error(message: impl Into<String>) -> ValidationError {
    ValidationError(message.into())
}
fn number(value: &str) -> Result<u64, ValidationError> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(error("GCOV text count is not a nonnegative integer"));
    }
    value.parse().map_err(|_| error("GCOV text count overflow"))
}
fn percentage(value: &str) -> Result<(), ValidationError> {
    if !value
        .strip_suffix('%')
        .is_some_and(|value| number(value).is_ok_and(|value| value <= 100))
    {
        return Err(error("GCOV text percentage is malformed"));
    }
    Ok(())
}
pub fn parse(bytes: &[u8]) -> Result<TextReport, ValidationError> {
    if bytes.is_empty() || bytes.len() > 64 * 1024 * 1024 {
        return Err(error("GCOV text report is empty or exceeds 64 MiB"));
    }
    let mut result = TextReport::default();
    let mut current_line = None;
    let mut function = String::new();
    for (index, raw) in bytes.split(|byte| *byte == b'\n').enumerate() {
        if index > 2_000_000 || raw.len() > 1024 * 1024 {
            return Err(error("GCOV text line or record bound exceeded"));
        }
        let raw = raw.strip_suffix(b"\r").unwrap_or(raw);
        let mut parts = raw.splitn(3, |byte| *byte == b':');
        let first = parts.next().unwrap_or_default();
        let second = parts.next();
        let third = parts.next();
        if let (Some(line), Some(source)) = (second, third) {
            let count = std::str::from_utf8(first)
                .map_err(|_| error("GCOV text count is not ASCII"))?
                .trim();
            let line = std::str::from_utf8(line)
                .map_err(|_| error("GCOV text source line is not ASCII"))?
                .trim();
            let line = number(line)?;
            if line == 0 {
                if count != "-" {
                    return Err(error("GCOV text header count is invalid"));
                }
                let value = std::str::from_utf8(source)
                    .map_err(|_| error("GCOV text source header is not UTF-8"))?
                    .trim();
                for (prefix, target) in [
                    ("Source:", &mut result.source),
                    ("Graph:", &mut result.graph),
                    ("Data:", &mut result.data),
                ] {
                    if let Some(value) = value.strip_prefix(prefix) {
                        if !target.is_empty() || value.trim().is_empty() || index > 7 {
                            return Err(error(
                                "GCOV text source/data header is duplicated or misplaced",
                            ));
                        }
                        *target = value.trim().into();
                    }
                }
                continue;
            }
            let line = u32::try_from(line).map_err(|_| error("GCOV text source line overflow"))?;
            current_line = Some(line);
            let covered = match count {
                "-" => continue,
                "#####" | "=====" | "$$$$$" => false,
                _ => number(count.strip_suffix('*').unwrap_or(count))? > 0,
            };
            *result.lines.entry(line).or_default() |= covered;
            result.records += 1;
        } else {
            let text = std::str::from_utf8(raw)
                .map_err(|_| error("GCOV text protocol is not UTF-8"))?
                .trim();
            if text.is_empty() || text.bytes().all(|byte| byte == b'-') {
                continue;
            }
            let fields = text.split_whitespace().collect::<Vec<_>>();
            if fields.first() == Some(&"function") {
                if fields.len() != 9
                    || fields[2] != "called"
                    || fields[4] != "returned"
                    || fields[6..8] != ["blocks", "executed"]
                {
                    return Err(error("GCOV text function record is malformed"));
                }
                let called = number(fields[3])?;
                percentage(fields[5])?;
                percentage(fields[8])?;
                function = fields[1].into();
                *result.functions.entry(function.clone()).or_default() |= called > 0;
                current_line = None;
                result.records += 1;
            } else if fields.first() == Some(&"branch") {
                if fields.len() < 4 {
                    return Err(error("GCOV text branch record is malformed"));
                }
                let branch = u32::try_from(number(fields[1])?)
                    .map_err(|_| error("GCOV text branch identity overflow"))?;
                let line =
                    current_line.ok_or_else(|| error("GCOV text branch has no source line"))?;
                let covered = if fields[2] == "taken" {
                    let raw = fields[3];
                    if raw.ends_with('%') {
                        percentage(raw)?;
                    }
                    number(raw.trim_end_matches('%'))? > 0
                } else if fields[2..4] == ["never", "executed"] {
                    false
                } else {
                    return Err(error("GCOV text branch verdict is malformed"));
                };
                if text.contains("(throw)") {
                    result.ignored_throw_branches += 1;
                } else {
                    *result
                        .branches
                        .entry((line, function.clone(), branch))
                        .or_default() |= covered;
                }
                result.records += 1;
            } else if fields.first() == Some(&"call") {
                if fields.len() < 4
                    || fields[2] != "returned" && fields[2..4] != ["never", "executed"]
                {
                    return Err(error("GCOV text call record is malformed"));
                }
                number(fields[1])?;
                if fields[2] == "returned" {
                    if fields[3].ends_with('%') {
                        percentage(fields[3])?;
                    } else {
                        number(fields[3])?;
                    }
                }
                result.records += 1;
            } else {
                return Err(error("GCOV text protocol contains an unknown record"));
            }
        }
        if result.lines.len() > 1_000_000
            || result.branches.len() > 1_000_000
            || result.functions.len() > 100_000
        {
            return Err(error("GCOV text source record bound exceeded"));
        }
    }
    if result.source.is_empty()
        || result.graph.is_empty()
        || result.data.is_empty()
        || result.data == "-"
    {
        return Err(error(
            "GCOV text lacks source, notes or execution-data identity",
        ));
    }
    Ok(result)
}

fn source_path(name: &str, plan: &ValidationPlan) -> Result<Option<String>, ValidationError> {
    if let Some(path) = crate::gcov::source_path(name, &plan.context.cwd.to_string_lossy(), plan)? {
        return Ok(Some(path));
    }
    if Path::new(name).is_absolute() {
        return Ok(None);
    }
    let parts = Path::new(name)
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    let candidates = (0..parts.len())
        .map(|index| parts[index..].join("/"))
        .filter(|suffix| plan.context.source_files.contains(suffix))
        .collect::<BTreeSet<_>>();
    if candidates.len() > 1 {
        return Err(error(
            "GCOV text source suffix maps to multiple declared files",
        ));
    }
    Ok(candidates.into_iter().next())
}

pub fn merge(
    reports: &[TextReport],
    plan: &ValidationPlan,
) -> Result<(CoverageReport, Value), ValidationError> {
    if reports.is_empty() || reports.len() > 4096 {
        return Err(error("GCOV text report set is empty or exceeds 4096 files"));
    }
    let mut merged = BTreeMap::<String, TextReport>::new();
    let mut origins = BTreeSet::new();
    let mut excluded = BTreeSet::new();
    let mut records = 0usize;
    let mut tally = [0usize; 3];
    let mut throw_branches = 0u64;
    for report in reports {
        let raw = Path::new(&report.graph);
        if ![
            plan.context.root.join(raw),
            plan.context.cwd.join(raw),
            plan.context.owned_run_dir.join(raw),
        ]
        .iter()
        .any(|candidate| {
            plan.context
                .targets
                .iter()
                .any(|target| crate::gcov::lexical(candidate) == plan.context.root.join(target))
        }) {
            return Err(error(
                "GCOV text notes identity is outside the requested producer notes",
            ));
        }
        records = records
            .checked_add(report.records)
            .ok_or_else(|| error("GCOV text record count overflow"))?;
        if records > 16_000_000 {
            return Err(error("aggregate GCOV text record budget exceeded"));
        }
        for (index, value) in [
            report.lines.len(),
            report.branches.len(),
            report.functions.len(),
        ]
        .into_iter()
        .enumerate()
        {
            tally[index] += value;
        }
        if tally
            .iter()
            .zip([4_000_000, 8_000_000, 500_000])
            .any(|(actual, maximum)| *actual > maximum)
        {
            return Err(error(
                "aggregate GCOV text source/branch/function budget exceeded",
            ));
        }
        if !origins.insert((&report.graph, &report.source)) {
            return Err(error("duplicate GCOV text notes/source identity"));
        }
        throw_branches += report.ignored_throw_branches;
        let Some(path) = source_path(&report.source, plan)? else {
            excluded.insert(report.source.clone());
            continue;
        };
        let file = merged.entry(path).or_default();
        for (line, covered) in &report.lines {
            *file.lines.entry(*line).or_default() |= *covered;
        }
        for (branch, covered) in &report.branches {
            *file.branches.entry(branch.clone()).or_default() |= *covered;
        }
        for (function, covered) in &report.functions {
            *file.functions.entry(function.clone()).or_default() |= *covered;
        }
    }
    if plan
        .context
        .source_files
        .iter()
        .any(|path| !merged.contains_key(path))
    {
        return Err(error("GCOV text omits declared source files"));
    }
    let mut output=CoverageReport {
        files:BTreeMap::new(),counts:Counts::default(),function_rows:vec![],excluded_files:excluded.into_iter().collect(),
        limitations:vec!["legacy GCOV text does not report function columns or end lines; function targets use a source-unit line-1 anchor".into(),"legacy GCOV text branch identity uses source line, function symbol and branch number; compiler-generated exception-unwind branches are excluded".into(),"relative source relocation may use a unique exact declared-source suffix".into()],
    };
    let mut functions = 0u64;
    let mut covered_functions = 0u64;
    let mut all_functions = true;
    for (path, file) in merged {
        let executed = file
            .lines
            .iter()
            .filter_map(|(line, covered)| covered.then_some(*line))
            .collect::<BTreeSet<_>>();
        let missing = file
            .lines
            .iter()
            .filter_map(|(line, covered)| (!covered).then_some(*line))
            .collect::<BTreeSet<_>>();
        let counts = Counts {
            covered_lines: executed.len() as u64,
            total_lines: file.lines.len() as u64,
            covered_branches: file.branches.values().filter(|covered| **covered).count() as u64,
            total_branches: file.branches.len() as u64,
            covered_functions: (!file.functions.is_empty())
                .then(|| file.functions.values().filter(|covered| **covered).count() as u64),
            total_functions: (!file.functions.is_empty()).then_some(file.functions.len() as u64),
        };
        output.counts.covered_lines += counts.covered_lines;
        output.counts.total_lines += counts.total_lines;
        output.counts.covered_branches += counts.covered_branches;
        output.counts.total_branches += counts.total_branches;
        functions += counts.total_functions.unwrap_or(0);
        covered_functions += counts.covered_functions.unwrap_or(0);
        all_functions &= counts.total_functions.is_some() || counts.total_lines == 0;
        for (name, covered) in file.functions {
            output.function_rows.push(json!({"file":path,"name":name,"symbol":name,"start_line":1,"end_line":null,"start_column":null,"end_column":null,"geometry":"source-unit-anchor","covered":covered,"missing_lines":[]}));
        }
        output.files.insert(
            path.clone(),
            FileCoverage {
                path,
                executed,
                missing,
                counts,
                functions: None,
            },
        );
    }
    if output.counts.total_lines == 0 || output.function_rows.len() > 10000 {
        return Err(error(
            "GCOV text has no executable line evidence or exceeds 10000 function targets",
        ));
    }
    output.counts.covered_functions = all_functions.then_some(covered_functions);
    output.counts.total_functions = all_functions.then_some(functions);
    if !all_functions {
        output.limitations.push("legacy GCOV text contains executable source without function execution records; function coverage and TEM remain unavailable".into());
    }
    let provenance = json!({"format":"gcov-text","report_count":reports.len(),"records":records,"expected_sources":plan.context.source_files.len(),"covered_sources":output.files.len(),"function_geometry":"source-unit-line-1-anchor","source_mapping":"project-relative-or-unique-declared-suffix","branch_identity":"source-line-function-branch-number","throw_branches_excluded":true,"excluded_throw_branches":throw_branches,"limitations":output.limitations});
    Ok((output, provenance))
}
