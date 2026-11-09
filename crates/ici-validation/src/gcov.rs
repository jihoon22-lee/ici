//! Strict bounded gcov JSON/gzip evidence and cross-translation-unit raw-count merging.
use crate::{
    CapturedArtifacts, ValidationError, ValidationPlan,
    coverage::{Counts, CoverageReport, FileCoverage},
    planning,
    python::pair,
};
use ici_domain::AnalysisOutput;
use ici_execution::TaskOutcome;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Read,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovReport {
    #[serde(default)]
    pub current_working_directory: String,
    pub data_file: String,
    pub format_version: String,
    pub gcc_version: String,
    pub files: Vec<GcovFile>,
    #[serde(skip)]
    pub decompressed_bytes: usize,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovFile {
    pub file: String,
    pub functions: Vec<GcovFunction>,
    pub lines: Vec<GcovLine>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovFunction {
    pub blocks: u64,
    pub blocks_executed: u64,
    pub demangled_name: String,
    pub name: String,
    pub start_line: u32,
    pub end_line: u32,
    pub start_column: u32,
    pub end_column: u32,
    pub execution_count: u64,
    pub total_prime_paths: Option<u64>,
    pub covered_prime_paths: Option<u64>,
    pub prime_path_coverage: Option<Vec<PrimePath>>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrimePath {
    pub id: u32,
    pub sequence: Vec<PrimeStep>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrimeStep {
    pub block_id: u32,
    pub locations: Vec<PrimeLocation>,
    pub edge_kind: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrimeLocation {
    pub file: String,
    pub line_numbers: Vec<u32>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovLine {
    pub count: u64,
    pub line_number: u32,
    pub unexecuted_block: bool,
    pub block_ids: Option<Vec<u32>>,
    #[serde(default)]
    pub function_name: String,
    #[serde(default)]
    pub branches: Vec<GcovBranch>,
    #[serde(default)]
    pub calls: Vec<GcovCall>,
    #[serde(default)]
    pub conditions: Vec<GcovCondition>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovBranch {
    pub count: u64,
    pub fallthrough: bool,
    pub throw: bool,
    pub source_block_id: Option<u32>,
    pub destination_block_id: Option<u32>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovCall {
    pub returned: u64,
    pub source_block_id: u32,
    pub destination_block_id: u32,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GcovCondition {
    pub count: u64,
    pub covered: u64,
    pub not_covered_false: Vec<u32>,
    pub not_covered_true: Vec<u32>,
}

fn error(message: impl Into<String>) -> ValidationError {
    ValidationError(message.into())
}

/// Frozen legacy `TestEngine._consume_cpp_coverage` words a rejected gcov JSON report set as
/// `f"gcov JSON evidence rejected ({exc.code}): {exc}"`, and `str(GcovJsonError)` is
/// `f"{code}: {message}"` (analysis/gcov_json.py `GcovJsonError.__init__`).
fn legacy_rejection(code: &str, message: impl std::fmt::Display) -> ValidationError {
    error(format!(
        "gcov JSON evidence rejected ({code}): {code}: {message}"
    ))
}
/// Frozen `_read_report_within_budget` before reading the next report: a spent cumulative
/// budget (64 MiB compressed, 256 MiB decompressed) is "exhausted"; a report that fits the
/// 8 MiB per-file bound but not what is left of the compressed budget "was exceeded".
fn legacy_byte_budget(
    compressed: usize,
    decompressed: usize,
    next: usize,
) -> Option<ValidationError> {
    if compressed >= 64 * 1024 * 1024 || decompressed >= 256 * 1024 * 1024 {
        return Some(legacy_rejection(
            "aggregate_limit",
            "cumulative gcov JSON byte budget is exhausted",
        ));
    }
    (compressed + next > 64 * 1024 * 1024 && next <= 8 * 1024 * 1024).then(|| {
        legacy_rejection(
            "aggregate_limit",
            "cumulative gcov JSON byte budget was exceeded",
        )
    })
}
/// Frozen `_require_complete_source_coverage`: the first eight missing sources in sorted order.
const LEGACY_MISSING_SOURCES: &str =
    "gcov JSON evidence rejected (incomplete_source_coverage): incomplete_source_coverage: ";
/// Frozen `_GcovJsonTally.absorb` record ceilings, in its check order.
const LEGACY_RECORD_LABELS: [&str; 5] = ["file", "function", "line", "branch", "call"];
fn legacy_missing_sources(missing: &[&String]) -> String {
    let preview = missing
        .iter()
        .take(8)
        .map(|source| source.as_str())
        .collect::<Vec<_>>()
        .join(", ");
    let suffix = if missing.len() > 8 {
        format!(" (+{} more)", missing.len() - 8)
    } else {
        String::new()
    };
    format!(
        "{LEGACY_MISSING_SOURCES}coverage evidence is missing {} source(s): {preview}{suffix}",
        missing.len()
    )
}
pub(crate) fn suite_ids(plan: &ValidationPlan) -> Result<Vec<String>, ValidationError> {
    let canonical = plan
        .context
        .options
        .shared_test_task_id
        .as_ref()
        .ok_or_else(|| error("GCOV canonical suite identity is absent"))?;
    let sources = &plan.context.options.shared_cpp_test_sources;
    if sources.len() > 128
        || !sources.is_empty()
            && (!sources.contains_key(canonical)
                || plan.context.options.shared_test_kind
                    != Some(crate::ValidationKind::CppExecutable))
    {
        return Err(error(
            "GCOV physical suite mapping requires at most 128 plain executables and a mapped canonical batch identity",
        ));
    }
    for (id, source) in sources {
        ici_domain::validation::relative_path(source, false)
            .map_err(|failure| error(failure.to_string()))?;
        if id.len() > 512 || !id.ends_with(".suite") || id.chars().any(char::is_control) {
            return Err(error("GCOV physical suite identity is invalid"));
        }
    }
    Ok(if sources.is_empty() {
        vec![canonical.clone()]
    } else {
        sources.keys().cloned().collect()
    })
}
pub fn parse_gzip(
    bytes: &[u8],
    maximum_decompressed: usize,
) -> Result<GcovReport, ValidationError> {
    if bytes.len() > 8 * 1024 * 1024 || !bytes.starts_with(&[0x1f, 0x8b]) {
        return Err(error("gcov gzip is missing or exceeds 8 MiB"));
    }
    if maximum_decompressed == 0 || maximum_decompressed > 64 * 1024 * 1024 {
        return Err(error("gcov decompression budget must be 1..64 MiB"));
    }
    let mut decoder = flate2::bufread::GzDecoder::new(bytes);
    let mut output = vec![];
    decoder
        .by_ref()
        .take(maximum_decompressed as u64 + 1)
        .read_to_end(&mut output)
        .map_err(|e| error(format!("gcov gzip: {e}")))?;
    if output.len() > maximum_decompressed {
        return Err(error("gcov decompressed byte limit exceeded"));
    }
    if !decoder.into_inner().is_empty() {
        return Err(error("gcov gzip has concatenated members or trailing data"));
    }
    let mut parsed = parse_json(&output)?;
    parsed.decompressed_bytes = output.len();
    Ok(parsed)
}
pub fn parse_json(bytes: &[u8]) -> Result<GcovReport, ValidationError> {
    if bytes.len() > 64 * 1024 * 1024 {
        return Err(error("gcov JSON exceeds 64 MiB"));
    }
    let value = strict_json::parse(bytes)?;
    for file in value["files"].as_array().into_iter().flatten() {
        for function in file["functions"].as_array().into_iter().flatten() {
            for key in [
                "total_prime_paths",
                "covered_prime_paths",
                "prime_path_coverage",
            ] {
                if function.get(key).is_some_and(Value::is_null) {
                    return Err(error("gcov optional fields cannot be explicit null"));
                }
            }
        }
        for line in file["lines"].as_array().into_iter().flatten() {
            if value["format_version"] == "1" && line.get("calls").is_some() {
                return Err(error("gcov v1 cannot contain call fields"));
            }
            if line.get("block_ids").is_some_and(Value::is_null) {
                return Err(error("gcov block_ids cannot be null"));
            }
            for branch in line["branches"].as_array().into_iter().flatten() {
                for key in ["source_block_id", "destination_block_id"] {
                    if branch.get(key).is_some_and(Value::is_null) {
                        return Err(error("gcov block IDs cannot be null"));
                    }
                }
            }
        }
    }
    let document: GcovReport =
        serde_json::from_value(value).map_err(|e| error(format!("gcov schema: {e}")))?;
    if !matches!(document.format_version.as_str(), "1" | "2") {
        return Err(error("unsupported gcov format_version"));
    }
    let gcc = regex::Regex::new(r"^\d+(?:\.\d+)*(?:[ \t]+[^ \t].*)?$").expect("constant regex");
    if !gcc.is_match(&document.gcc_version) || document.data_file.is_empty() {
        return Err(error("gcov GCC version/data file missing"));
    }
    if document.files.len() > 4096 {
        return Err(error("gcov file record limit exceeded"));
    }
    let mut files = BTreeSet::new();
    for file in &document.files {
        if file.file.is_empty() || !files.insert(&file.file) {
            return Err(error("gcov source path empty or duplicated"));
        }
        if file.functions.len() > 100000 || file.lines.len() > 1000000 {
            return Err(error("gcov source record bound exceeded"));
        }
        let mut functions = BTreeSet::new();
        for function in &file.functions {
            if function.blocks_executed > function.blocks
                || function.start_line == 0
                || function.end_line == 0
                || function.start_column == 0
                || function.end_column == 0
                || (function.end_line, function.end_column)
                    < (function.start_line, function.start_column)
            {
                return Err(error("gcov function counts/source range invalid"));
            }
            if !functions.insert((
                &function.name,
                function.start_line,
                function.start_column,
                function.end_line,
                function.end_column,
            )) {
                return Err(error("duplicate gcov function identity"));
            }
            match (
                function.total_prime_paths,
                function.covered_prime_paths,
                &function.prime_path_coverage,
            ) {
                (None, None, None) => {}
                (Some(total), Some(covered), Some(paths)) => {
                    if covered > total || paths.len() > 1000000 || paths.len() as u64 > total {
                        return Err(error("gcov prime path counts invalid"));
                    }
                    let mut ids = BTreeSet::new();
                    for path in paths {
                        if !ids.insert(path.id)
                            || path.sequence.is_empty()
                            || path.sequence.len() > 1000000
                        {
                            return Err(error("gcov prime path identity/sequence invalid"));
                        }
                        for step in &path.sequence {
                            if step.locations.len() > 1000000 {
                                return Err(error("gcov prime path location bound exceeded"));
                            }
                            for location in &step.locations {
                                if location.file.is_empty()
                                    || location.line_numbers.len() > 1000000
                                    || location.line_numbers.contains(&0)
                                {
                                    return Err(error("gcov prime path source lines invalid"));
                                }
                            }
                        }
                    }
                }
                _ => return Err(error("gcov prime path fields must be supplied together")),
            }
        }
        for line in &file.lines {
            if line.line_number == 0
                || line.branches.len() > 1000000
                || line.calls.len() > 1000000
                || line.conditions.len() > 1000000
                || line
                    .block_ids
                    .as_ref()
                    .is_some_and(|ids| ids.len() > 1000000)
            {
                return Err(error("gcov line/edge bounds exceeded"));
            }
            if document.format_version == "2" && line.block_ids.is_none() {
                return Err(error("gcov v2 requires block_ids"));
            }
            if document.format_version == "1" && !line.calls.is_empty() {
                return Err(error("gcov v1 cannot contain call records"));
            }
            for branch in &line.branches {
                if branch.source_block_id.is_some() != branch.destination_block_id.is_some()
                    || document.format_version == "2" && branch.source_block_id.is_none()
                {
                    return Err(error("gcov branch block identity missing"));
                }
            }
            for condition in &line.conditions {
                if condition.covered > condition.count
                    || condition.not_covered_false.len() > 1000000
                    || condition.not_covered_true.len() > 1000000
                {
                    return Err(error("gcov condition count bound exceeded"));
                }
            }
        }
    }
    Ok(document)
}

type BranchKey = (u32, String, Option<u32>, Option<u32>, bool, Option<usize>);
type FunctionKey = (String, u32, u32, u32, u32);
#[derive(Default)]
struct Merged {
    lines: BTreeMap<u32, bool>,
    branches: BTreeMap<BranchKey, bool>,
    functions: BTreeMap<FunctionKey, Value>,
}
pub fn merge(
    reports: &[GcovReport],
    plan: &ValidationPlan,
) -> Result<CoverageReport, ValidationError> {
    let legacy = plan.context.options.legacy_gcov_source_wording;
    if legacy && reports.is_empty() {
        // Frozen `_bounded_gcov_json_report_paths`.
        return Err(legacy_rejection(
            "missing_data",
            "no .gcov.json.gz reports were found",
        ));
    }
    if legacy && reports.len() > 4096 {
        return Err(legacy_rejection(
            "aggregate_limit",
            "gcov JSON report count exceeds 4096",
        ));
    }
    if reports.is_empty() || reports.len() > 4096 {
        return Err(error("gcov report set is empty or too large"));
    }
    let mut versions = BTreeSet::new();
    let mut gcc_versions = BTreeSet::new();
    let mut origins = BTreeSet::new();
    let mut tally = [0usize; 5];
    let mut merged = BTreeMap::<String, Merged>::new();
    let mut excluded = BTreeSet::new();
    for report in reports {
        // Frozen `_require_uniform_toolchain`: each report against those read before it.
        let mixed_format = !versions.is_empty() && !versions.contains(&report.format_version);
        let mixed_gcc = !gcc_versions.is_empty() && !gcc_versions.contains(&report.gcc_version);
        versions.insert(&report.format_version);
        gcc_versions.insert(&report.gcc_version);
        if !origins.insert((&report.current_working_directory, &report.data_file)) {
            return Err(error("duplicate gcov data origin"));
        }
        tally[0] += report.files.len();
        for file in &report.files {
            tally[1] += file.functions.len();
            tally[2] += file.lines.len();
            for line in &file.lines {
                tally[3] += line.branches.len();
                tally[4] += line.calls.len();
            }
        }
        let maxima = [16384, 500000, 4000000, 8000000, 8000000];
        if let Some(index) = (0..5).find(|index| tally[*index] > maxima[*index]) {
            if legacy {
                return Err(legacy_rejection(
                    "aggregate_limit",
                    format!(
                        "cumulative gcov JSON {} records exceed {}",
                        LEGACY_RECORD_LABELS[index], maxima[index]
                    ),
                ));
            }
            return Err(error("aggregate gcov record budget exceeded"));
        }
        if legacy && mixed_format {
            return Err(legacy_rejection(
                "inconsistent_report_set",
                "gcov JSON reports contain mixed format versions",
            ));
        }
        if legacy && mixed_gcc {
            return Err(legacy_rejection(
                "inconsistent_report_set",
                "gcov JSON reports contain mixed GCC versions",
            ));
        }
        for file in &report.files {
            let Some(path) = source_path(&file.file, &report.current_working_directory, plan)
                .map_err(|rejected| {
                    if legacy && rejected.0 == AMBIGUOUS_SOURCE {
                        // Frozen `_relative_gcov_json_source` names the declared path's repr.
                        legacy_rejection(
                            "ambiguous_source_path",
                            format!(
                                "source path {} maps to multiple project files",
                                ici_analysis::unicode::python_repr(&file.file)
                            ),
                        )
                    } else {
                        rejected
                    }
                })?
            else {
                excluded.insert(file.file.clone());
                continue;
            };
            let target = merged.entry(path.clone()).or_default();
            for line in &file.lines {
                *target.lines.entry(line.line_number).or_default() |= line.count > 0;
                for (index, branch) in line.branches.iter().enumerate() {
                    if branch.throw {
                        continue;
                    }
                    let key = (
                        line.line_number,
                        line.function_name.clone(),
                        branch.source_block_id,
                        branch.destination_block_id,
                        branch.fallthrough,
                        if branch.source_block_id.is_none() {
                            Some(index)
                        } else {
                            None
                        },
                    );
                    *target.branches.entry(key).or_default() |= branch.count > 0;
                }
            }
            for function in &file.functions {
                let key = (
                    function.name.clone(),
                    function.start_line,
                    function.start_column,
                    function.end_line,
                    function.end_column,
                );
                let previous = target
                    .functions
                    .get(&key)
                    .is_some_and(|value| value["covered"] == true);
                let column = |line, value| {
                    plan.source_coordinates
                        .byte_column(&path, line, value)
                        .map_err(|error| ValidationError(error.to_string()))
                };
                // Frozen `_merge_gcov_json_report` keeps gcov's own (byte) columns, also for a
                // source it cannot decode; native reports Unicode columns.
                let (start_column, end_column) = if legacy {
                    (Some(function.start_column), Some(function.end_column))
                } else {
                    (
                        column(function.start_line, function.start_column)?,
                        column(function.end_line, function.end_column)?,
                    )
                };
                target.functions.insert(key,json!({"file":path,"name":if function.demangled_name.is_empty(){&function.name}else{&function.demangled_name},"symbol":function.name,"start_line":function.start_line,"end_line":function.end_line,"start_column":start_column,"end_column":end_column,"native_columns":{"unit":"utf8-bytes","start":function.start_column,"end":function.end_column},"covered":previous||function.execution_count>0,"missing_lines":[]}));
            }
        }
    }
    if versions.len() != 1 || gcc_versions.len() != 1 {
        return Err(error("gcov report set mixes format or GCC versions"));
    }
    let missing: Vec<&String> = plan
        .context
        .source_files
        .iter()
        .filter(|source| !merged.contains_key(*source))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if !missing.is_empty() {
        if plan.context.options.legacy_gcov_source_wording {
            return Err(error(legacy_missing_sources(&missing)));
        }
        return Err(error("gcov evidence omits declared source files"));
    }
    let mut result = CoverageReport {
        files: BTreeMap::new(),
        counts: Counts::default(),
        function_rows: vec![],
        excluded_files: excluded.into_iter().collect(),
        limitations: vec![
            "compiler-generated exception-unwind branches are excluded from both branch counts"
                .into(),
        ],
    };
    let mut function_total = 0;
    let mut function_covered = 0;
    for (path, file) in merged {
        let executed = file
            .lines
            .iter()
            .filter(|(_, covered)| **covered)
            .map(|(line, _)| *line)
            .collect::<BTreeSet<_>>();
        let missing = file
            .lines
            .iter()
            .filter(|(_, covered)| !**covered)
            .map(|(line, _)| *line)
            .collect::<BTreeSet<_>>();
        let counts = Counts {
            covered_lines: executed.len() as u64,
            total_lines: file.lines.len() as u64,
            covered_branches: file.branches.values().filter(|covered| **covered).count() as u64,
            total_branches: file.branches.len() as u64,
            covered_functions: Some(
                file.functions
                    .values()
                    .filter(|function| function["covered"] == true)
                    .count() as u64,
            ),
            total_functions: Some(file.functions.len() as u64),
        };
        result.counts.covered_lines += counts.covered_lines;
        result.counts.total_lines += counts.total_lines;
        result.counts.covered_branches += counts.covered_branches;
        result.counts.total_branches += counts.total_branches;
        function_total += counts.total_functions.unwrap_or(0);
        function_covered += counts.covered_functions.unwrap_or(0);
        result.function_rows.extend(file.functions.into_values());
        result.files.insert(
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
    if result.counts.total_lines == 0 {
        return Err(error("gcov reports contain no executable line evidence"));
    }
    if result.function_rows.len() > 10000 {
        return Err(error(
            "gcov function report inventory exceeds 10000 targets",
        ));
    }
    result.counts.total_functions = Some(function_total);
    result.counts.covered_functions = Some(function_covered);
    if result
        .function_rows
        .iter()
        .any(|function| function["start_column"].is_null() || function["end_column"].is_null())
    {
        result.limitations.push(
            "function columns omitted where immutable UTF-8 source coordinates are unavailable"
                .into(),
        );
    }
    Ok(result)
}
/// Frozen `_GcovJsonTally.absorb` (`not line.block_ids`): a line whose `block_ids` key is
/// missing (format 1) or an empty `[]` (GCC writes one for a line without basic blocks).
pub fn line_without_block_ids(line: &GcovLine) -> bool {
    line.block_ids.as_ref().is_none_or(Vec::is_empty)
}

fn provenance(
    reports: &[GcovReport],
    plan: &ValidationPlan,
    report: &CoverageReport,
    compressed: usize,
) -> Result<Value, ValidationError> {
    let (
        mut files,
        mut functions,
        mut lines,
        mut branches,
        mut calls,
        mut without_blocks,
        mut ordered,
        mut matched,
        mut ignored,
    ) = (0, 0, 0, 0, 0, 0, 0, 0, 0);
    for document in reports {
        for file in &document.files {
            files += 1;
            functions += file.functions.len();
            lines += file.lines.len();
            if source_path(&file.file, &document.current_working_directory, plan)?.is_some() {
                matched += 1;
            } else {
                ignored += 1;
            }
            for line in &file.lines {
                branches += line.branches.len();
                calls += line.calls.len();
                without_blocks += usize::from(line_without_block_ids(line));
                ordered += line
                    .branches
                    .iter()
                    .filter(|branch| {
                        branch.source_block_id.is_none() || branch.destination_block_id.is_none()
                    })
                    .count();
            }
        }
    }
    Ok(
        json!({"format":"gcov-json","format_versions":reports.iter().map(|report|report.format_version.parse::<u8>().expect("validated format version")).collect::<BTreeSet<_>>(),"gcc_versions":reports.iter().map(|report|&report.gcc_version).collect::<BTreeSet<_>>(),"report_count":reports.len(),"compressed_bytes":compressed,"decompressed_bytes":reports.iter().map(|report|report.decompressed_bytes).sum::<usize>(),"file_records":files,"function_records":functions,"line_records":lines,"branch_records":branches,"call_records":calls,"lines_without_block_ids":without_blocks,"ordered_branch_records":ordered,"branch_identity":if ordered==0{"basic-block"}else{"basic-block-or-line-order"},"matched_file_records":matched,"ignored_file_records":ignored,"expected_sources":plan.context.source_files.len(),"covered_sources":report.files.len(),"empty_sources":report.files.values().filter(|file|file.counts.total_lines==0).map(|file|&file.path).collect::<Vec<_>>(),"function_geometry":"exact","source_mapping":"recorded-compilation-directory-or-project-root","throw_branches_excluded":true}),
    )
}
pub(crate) fn lexical(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    result
}
const AMBIGUOUS_SOURCE: &str = "gcov source path maps to multiple declared files";
pub(crate) fn source_path(
    name: &str,
    cwd: &str,
    plan: &ValidationPlan,
) -> Result<Option<String>, ValidationError> {
    let mut candidates = BTreeSet::new();
    let raw = Path::new(name);
    let mut roots = vec![plan.context.root.join(raw)];
    if raw.is_absolute() {
        roots.push(raw.into());
    } else if Path::new(cwd).is_absolute() {
        roots.push(Path::new(cwd).join(raw));
    }
    // Frozen `_gcov_json_source_path` resolves the candidate and the project root
    // (`Path.resolve()`, symbolic links followed; a link loop skips the candidate); native's
    // own mapping stays lexical.
    let legacy = plan.context.options.legacy_gcov_source_wording;
    let root = if legacy {
        crate::gcov_legacy_text::resolve(&plan.context.root)
            .unwrap_or_else(|| plan.context.root.clone())
    } else {
        plan.context.root.clone()
    };
    for path in roots {
        let path = if legacy {
            match crate::gcov_legacy_text::resolve(&path) {
                Some(path) => path,
                None => continue,
            }
        } else {
            lexical(&path)
        };
        if let Ok(relative) = path.strip_prefix(&root) {
            let relative = relative.to_string_lossy().replace('\\', "/");
            if plan.context.source_files.contains(&relative) {
                candidates.insert(relative);
            }
        }
    }
    if candidates.len() > 1 {
        return Err(error(AMBIGUOUS_SOURCE));
    }
    Ok(candidates.into_iter().next())
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if !plan.context.options.gcov_preexisting_data.is_empty() {
        return Err(error(
            "preexisting GCOV counters can contain accumulated coverage; prior data was preserved",
        ));
    }
    if plan.context.options.frozen_next_gcov {
        return crate::next_gcov::observe(plan, outcomes, artifacts, output);
    }
    let (format, version) = crate::gcov_format::selected(plan, outcomes)?
        .ok_or_else(|| error("GCOV capability/version evidence is absent"))?;
    let id = format!(
        "{}.{}",
        plan.context.task_id,
        if format == crate::GcovFormat::Json {
            "report"
        } else {
            "text-report"
        }
    );
    if plan.context.options.gcov_capability_probe
        && outcomes.contains_key(&format!(
            "{}.{}",
            plan.context.task_id,
            if format == crate::GcovFormat::Json {
                "text-report"
            } else {
                "report"
            }
        ))
    {
        return Err(error(
            "mutually exclusive GCOV report formats were both executed",
        ));
    }
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| error("gcov report step absent"))?;
    if plan.context.options.legacy_gcov_source_wording {
        // Frozen `_collect_cpp_coverage` / `collect_coverage`: "gcov output was incomplete" for
        // a timed-out or truncated run, else "gcov failed with exit code N" (a signal is
        // Python's negative returncode).
        if outcome.timed_out || outcome.truncated {
            return Err(error("gcov output was incomplete"));
        }
        if let Some(code) = outcome
            .exit_code
            .or(outcome.signal.map(|signal| -signal))
            .filter(|code| *code != 0)
        {
            return Err(error(format!("gcov failed with exit code {code}")));
        }
    }
    if !planning::completed(outcome) || outcome.exit_code != Some(0) {
        return Err(error("gcov report process did not complete successfully"));
    }
    // Frozen legacy TestEngine: a CTest case that did not run leaves its notes without counters,
    // and gcov reports them "not executed". Only notes with no data artifact at all qualify. The
    // reading without a suite ran nothing, so that is every note.
    let unexecuted: Vec<&String> =
        if plan.context.options.ctest_not_run_measured || plan.context.options.gcov_without_suite {
            plan.context
                .targets
                .iter()
                .filter(|target| {
                    !artifacts
                        .outputs
                        .contains_key(&format!("gcov-data:{target}"))
                })
                .collect()
        } else {
            vec![]
        };
    let stderr: String = String::from_utf8_lossy(&outcome.stderr)
        .lines()
        .filter(|line| {
            !(line.contains("cannot open data file") || line.contains("assuming not executed"))
                || unexecuted.iter().all(|target| {
                    let data = target.strip_suffix(".gcno").unwrap_or(target);
                    !line.contains(&format!("{data}.gcda"))
                })
        })
        .collect::<Vec<_>>()
        .join("\n");
    if !unexecuted.is_empty() {
        output.observation.limitations.push(format!(
            "{} coverage note file(s) had no run data because no executed test linked them",
            unexecuted.len()
        ));
    }
    // Frozen's text reader reads no gcov stderr at all.
    let legacy_text =
        plan.context.options.legacy_gcov_source_wording && format == crate::GcovFormat::Text;
    if !legacy_text
        && [
            "stamp mismatch",
            "version mismatch",
            "cannot open data file",
            "assuming not executed",
            "not a gcov data file",
        ]
        .iter()
        .any(|needle| stderr.contains(needle))
    {
        // Frozen reads no gcov stderr: a report set it rejects (a declared source the reports
        // omit, mixed versions, ...) is its result.
        if plan.context.options.legacy_gcov_source_wording
            && format == crate::GcovFormat::Json
            && let Err(rejected) = read_reports(plan, artifacts, &id, format)
            && rejected.0.starts_with("gcov JSON evidence rejected (")
        {
            return Err(rejected);
        }
        return Err(error(
            "gcov instrumentation is stale, mismatched or lacks run data",
        ));
    }
    // Frozen build-session `collect_coverage` (CMake/qmake/Make) refuses an empty report
    // directory itself; only the direct g++ path reaches the gcov JSON reader below.
    if plan.context.options.legacy_gcov_source_wording
        && plan.context.options.shared_cpp_test_sources.is_empty()
        && artifacts.errors.iter().any(|(key, failure)| {
            (key.starts_with("gcov-json") || key.starts_with("gcov-text"))
                && failure == crate::artifacts::NO_FRESH_REPORT
        })
    {
        return Err(error(format!(
            "gcov produced no {} reports",
            if format == crate::GcovFormat::Json {
                "JSON"
            } else {
                "text"
            }
        )));
    }
    // Frozen `_bounded_gcov_json_report_paths`: no report is `missing_data`. A report it cannot
    // read is refused in report order by `read_reports` (frozen `_read_compressed_file`).
    let legacy_json =
        plan.context.options.legacy_gcov_source_wording && format == crate::GcovFormat::Json;
    if legacy_json
        && artifacts.errors.iter().any(|(key, failure)| {
            key.starts_with("gcov-json") && failure == crate::artifacts::NO_FRESH_REPORT
        })
    {
        return Err(legacy_rejection(
            "missing_data",
            "no .gcov.json.gz reports were found",
        ));
    }
    // decisions.md "legacy gcov text 수집 한도": frozen reads a text report of any size; native
    // never drops one over its 64 MiB bound (that would measure the rest as complete), so the
    // reading fails closed like the 4096-report and 256 MiB aggregate bounds of the capture.
    if legacy_text
        && artifacts.errors.iter().any(|(key, failure)| {
            key.starts_with("gcov-text:") && failure == crate::artifacts::ARTIFACT_TOO_LARGE
        })
    {
        return Err(error(LEGACY_TEXT_TOO_LARGE));
    }
    // Frozen's text reader skips a report it cannot read and reads an empty directory as no rows.
    if artifacts.errors.keys().any(|key| {
        (key.starts_with("gcov-json") && !(legacy_json && key.starts_with("gcov-json:")))
            || (key.starts_with("gcov-text") && !legacy_text)
    }) {
        return Err(error("gcov report ownership/capture failed"));
    }
    // The stopped-compile reading ran no suite, so it has no test run and no counters to date.
    let tests = if plan.context.options.gcov_without_suite {
        (0, 0)
    } else {
        let producer = plan
            .context
            .options
            .shared_test_task_id
            .as_deref()
            .ok_or_else(|| error("gcov shared suite provenance absent"))?;
        let suite_ids = suite_ids(plan)?;
        let test_evidence = if plan.context.options.shared_cpp_test_sources.is_empty() {
            let suite = outcomes
                .get(producer)
                .ok_or_else(|| error("gcov shared suite outcome absent"))?;
            crate::cpp::test_evidence(plan, producer, suite)?
        } else {
            crate::cpp::collective_test_evidence(plan, outcomes)?
        };
        output.extra["gcov_test_batch"] = json!({"canonical_batch_id":producer,"physical_suite_ids":suite_ids,"freshness":"after earliest start of all completed physical suites in this owned batch"});
        if let Some(nodes) = test_evidence.extra.get("test_outcomes") {
            output.extra["test_outcomes"] = nodes.clone();
        }
        let tests = test_evidence
            .observation
            .measurements
            .iter()
            .find(|metric| metric.name.ends_with(".cases"))
            .and_then(|metric| metric.numerator.zip(metric.denominator))
            .ok_or_else(|| error("C++ test raw counts absent"))?;
        for target in plan
            .context
            .targets
            .iter()
            .filter(|target| !unexecuted.contains(target))
        {
            let key = format!("gcov-data:{target}");
            // Frozen's text reader reads whatever gcov wrote: notes whose object recorded no run
            // data (a source without functions) are no refusal there.
            if legacy_text && !artifacts.outputs.contains_key(&key) {
                continue;
            }
            if !artifacts.outputs.get(&key).is_some_and(|witness| {
                witness.fresh && witness.size > 0 && witness.producer_task_id == producer
            }) {
                return Err(error(
                    "gcov data was not freshly written by the declared suite",
                ));
            }
        }
        tests
    };
    let (report, info) = match read_reports(plan, artifacts, &id, format) {
        // Frozen `_consume_cpp_coverage` records the gcov-text provenance before it checks the
        // rows (test_coverage.py:229-246), so a reading without rows keeps it.
        Err(error) if legacy_text => {
            output.extra["gcov_text_info"] =
                crate::gcov_legacy_text::read(plan, artifacts, "gcov-text:").provenance;
            return Err(error);
        }
        reading => reading?,
    };
    if let Some(version) = version {
        output
            .observation
            .tool_versions
            .push(("gcov".into(), version.clone()));
        output.extra["gcov_capability"] = json!({"format":format,"version":version,"probe_task_id":format!("{}.capability",plan.context.task_id),"version_task_id":format!("{}.version",plan.context.task_id)});
    }
    output.extra[if format == crate::GcovFormat::Json {
        "gcov_json_info"
    } else {
        "gcov_text_info"
    }] = info;
    for (name, a, b) in [
        (
            "coverage.cpp.lines",
            Some(report.counts.covered_lines),
            Some(report.counts.total_lines),
        ),
        (
            "coverage.cpp.branches",
            Some(report.counts.covered_branches),
            Some(report.counts.total_branches),
        ),
        (
            "coverage.cpp.functions",
            report.counts.covered_functions,
            report.counts.total_functions,
        ),
    ] {
        if let Some((a, b)) = a.zip(b).filter(|(_, b)| *b > 0) {
            output.observation.measurements.push(pair(name, a, b));
        }
    }
    output.extra["coverage"] = json!(report);
    crate::coverage_baseline::attach(plan, &report, output)?;
    let inputs = crate::tem::TemInputs {
        passed: tests.0,
        total: tests.1,
        line_coverage: crate::tem::percentage(
            report.counts.covered_lines,
            report.counts.total_lines,
        ),
        branch_coverage: crate::tem::percentage(
            report.counts.covered_branches,
            report.counts.total_branches,
        ),
        function_coverage: report
            .counts
            .covered_functions
            .zip(report.counts.total_functions)
            .and_then(|(a, b)| crate::tem::percentage(a, b)),
        estimated_coverage: None,
    };
    let score = crate::tem::calculate(&inputs);
    // Frozen legacy TEM reads `_measure_coverage`'s function coverage (vacuous 100.0 without a
    // function row under `legacy_coverage_inputs`).
    let legacy_tem = crate::tem::calculate(&crate::tem::legacy_inputs(&crate::tem::TemInputs {
        function_coverage: crate::tem::coverage_inputs(
            &report.counts,
            plan.context.options.legacy_coverage_inputs,
        )
        .0,
        ..inputs.clone()
    }));
    output.extra["legacy_tem"] = json!(legacy_tem);
    output.score = score.score;
    output.max_score = Some(5.0);
    output.extra["tem"] = json!(score);
    output
        .observation
        .limitations
        .extend(report.limitations.iter().cloned());
    output.summary = format!(
        "{}/{} C++ executable lines covered",
        report.counts.covered_lines, report.counts.total_lines
    );
    crate::test_metrics::coverage(plan, &report, output);
    // Frozen `_measure_coverage`: rows that miss a declared source are measured, but coverage is
    // incomplete (`coverage_complete` false), so no policy target and an empty policy snapshot.
    if let Some(error) = output.extra["gcov_text_info"]["legacy_coverage_error"]
        .as_str()
        .map(str::to_owned)
    {
        output.extra["legacy_coverage_error"] = json!(error);
        output.extra["coverage_policy"] = Value::Null;
        return Ok(());
    }
    let suite_failed = output.extra["test_outcomes"]
        .as_object()
        .is_some_and(|nodes| {
            nodes
                .values()
                .any(|verdict| matches!(verdict.as_str(), Some("FAILED" | "ERROR" | "XPASS")))
        });
    crate::policy::evaluate_suite(plan, &report, legacy_tem.score, suite_failed, output)
}

fn read_reports(
    plan: &ValidationPlan,
    artifacts: &CapturedArtifacts,
    id: &str,
    format: crate::GcovFormat,
) -> Result<(CoverageReport, Value), ValidationError> {
    let prefix = if format == crate::GcovFormat::Json {
        "gcov-json:"
    } else {
        "gcov-text:"
    };
    let inactive = if format == crate::GcovFormat::Json {
        "gcov-text:"
    } else {
        "gcov-json:"
    };
    if artifacts
        .files
        .values()
        .any(|artifact| artifact.id.starts_with(inactive))
    {
        return Err(error("inactive GCOV report format has captured artifacts"));
    }
    if format == crate::GcovFormat::Text && plan.context.options.legacy_gcov_source_wording {
        return legacy_text_reports(plan, artifacts, id, prefix);
    }
    if format == crate::GcovFormat::Json && plan.context.options.legacy_gcov_source_wording {
        let (json_reports, bytes) = legacy_json_reports(plan, artifacts, id, prefix)?;
        let report = merge(&json_reports, plan)?;
        let info = provenance(&json_reports, plan, &report, bytes)?;
        return Ok((report, info));
    }
    let mut json_reports = vec![];
    let mut text_reports = vec![];
    let mut bytes = 0usize;
    let mut decompressed = 0usize;
    for artifact in artifacts
        .files
        .values()
        .filter(|artifact| artifact.id.starts_with(prefix))
    {
        if artifact.producer_task_id != id || artifact.digest != ici_domain::sha256(&artifact.bytes)
        {
            return Err(error("GCOV report provenance mismatch"));
        }
        let legacy =
            format == crate::GcovFormat::Json && plan.context.options.legacy_gcov_source_wording;
        // Frozen `_bounded_gcov_json_report_paths` / `_read_report_within_budget`.
        if legacy && json_reports.len() >= 4096 {
            return Err(legacy_rejection(
                "aggregate_limit",
                "gcov JSON report count exceeds 4096",
            ));
        }
        if legacy
            && let Some(rejected) = legacy_byte_budget(bytes, decompressed, artifact.bytes.len())
        {
            return Err(rejected);
        }
        bytes = bytes
            .checked_add(artifact.bytes.len())
            .ok_or_else(|| error("GCOV report byte count overflow"))?;
        if json_reports.len() + text_reports.len() >= 4096
            || bytes
                > if format == crate::GcovFormat::Json {
                    64 * 1024 * 1024
                } else {
                    256 * 1024 * 1024
                }
        {
            return Err(error("GCOV report set exceeds its count/byte bound"));
        }
        if format == crate::GcovFormat::Json {
            let budget = (256 * 1024 * 1024usize - decompressed).min(64 * 1024 * 1024);
            let report = parse_gzip(&artifact.bytes, budget).map_err(|rejected| {
                if legacy
                    && budget < 64 * 1024 * 1024
                    && rejected.0 == "gcov decompressed byte limit exceeded"
                {
                    legacy_rejection(
                        "aggregate_limit",
                        "cumulative gcov JSON byte budget was exceeded",
                    )
                } else {
                    rejected
                }
            })?;
            decompressed += report.decompressed_bytes;
            json_reports.push(report);
        } else {
            text_reports.push(crate::gcov_text::parse(&artifact.bytes)?);
        }
    }
    if format == crate::GcovFormat::Json {
        let report = merge(&json_reports, plan)?;
        let info = provenance(&json_reports, plan, &report, bytes)?;
        Ok((report, info))
    } else {
        let (report, mut info) = crate::gcov_text::merge(&text_reports, plan)?;
        info["report_bytes"] = json!(bytes);
        Ok((report, info))
    }
}

/// Frozen `_consume_cpp_coverage`'s `gcov-text` branch (test_coverage.py:221-246): no rows is
/// `legacy gcov text evidence is missing N source(s)` (then frozen's `C++ gcov coverage output
/// was missing or malformed`); rows that miss a declared source keep frozen's coverage error.
fn legacy_text_reports(
    plan: &ValidationPlan,
    artifacts: &CapturedArtifacts,
    id: &str,
    prefix: &str,
) -> Result<(CoverageReport, Value), ValidationError> {
    if artifacts.files.values().any(|artifact| {
        artifact.id.starts_with(prefix)
            && (artifact.producer_task_id != id
                || artifact.digest != ici_domain::sha256(&artifact.bytes))
    }) {
        return Err(error("GCOV report provenance mismatch"));
    }
    let reading = crate::gcov_legacy_text::read(plan, artifacts, prefix);
    if reading.report.files.is_empty() {
        return Err(error(
            reading
                .missing
                .unwrap_or_else(|| LEGACY_TEXT_NO_ROWS.to_owned()),
        ));
    }
    let mut provenance = reading.provenance;
    if let Some(missing) = reading.missing {
        provenance["legacy_coverage_error"] = json!(missing);
    }
    Ok((reading.report, provenance))
}
/// A legacy gcov text report over native's 64 MiB per-report bound (frozen has none).
pub const LEGACY_TEXT_TOO_LARGE: &str =
    "legacy gcov text report exceeds the 64 MiB per-report bound; C++ coverage was not measured";
/// Frozen test_coverage.py:181 / test.py:689.
pub(crate) const LEGACY_TEXT_NO_ROWS: &str = "C++ gcov coverage output was missing or malformed";

/// Frozen `parse_gcov_json_dir`'s per-report loop (`_read_report_within_budget` then the
/// tally, uniform-toolchain and source-path checks) over the captured reports and the reports
/// the capture refused, in report-name order.
fn legacy_json_reports(
    plan: &ValidationPlan,
    artifacts: &CapturedArtifacts,
    id: &str,
    prefix: &str,
) -> Result<(Vec<GcovReport>, usize), ValidationError> {
    use crate::gcov_legacy_json::{MAX_COMPRESSED_BYTES, MAX_DECOMPRESSED_BYTES, check_report};
    let mut entries: BTreeMap<&str, Result<&crate::artifacts::CapturedArtifact, &str>> =
        BTreeMap::new();
    for (key, artifact) in &artifacts.files {
        if artifact.id.starts_with(prefix) {
            entries.insert(key, Ok(artifact));
        }
    }
    for (key, failure) in &artifacts.errors {
        if key.starts_with(prefix) {
            entries.insert(key, Err(failure));
        }
    }
    if entries.len() > 4096 {
        return Err(legacy_rejection(
            "aggregate_limit",
            "gcov JSON report count exceeds 4096",
        ));
    }
    let mut reports = vec![];
    let mut bytes = 0usize;
    let mut decompressed = 0usize;
    for entry in entries.into_values() {
        let compressed_remaining = (64 * 1024 * 1024usize).saturating_sub(bytes);
        let decompressed_remaining = (256 * 1024 * 1024usize).saturating_sub(decompressed);
        if compressed_remaining == 0 || decompressed_remaining == 0 {
            return Err(legacy_rejection(
                "aggregate_limit",
                "cumulative gcov JSON byte budget is exhausted",
            ));
        }
        let max_compressed = MAX_COMPRESSED_BYTES.min(compressed_remaining);
        let max_decompressed = MAX_DECOMPRESSED_BYTES.min(decompressed_remaining);
        let checked = match entry {
            Ok(artifact) => {
                if artifact.producer_task_id != id
                    || artifact.digest != ici_domain::sha256(&artifact.bytes)
                {
                    return Err(error("GCOV report provenance mismatch"));
                }
                check_report(&artifact.bytes, max_compressed, max_decompressed).map(|()| artifact)
            }
            Err(failure) if failure == crate::artifacts::ARTIFACT_TOO_LARGE => Err((
                "compressed_limit",
                format!("compressed gcov JSON is larger than {max_compressed} bytes"),
            )),
            Err(failure) if failure == crate::artifacts::ARTIFACT_NOT_REGULAR => Err((
                "read_error",
                "gcov JSON evidence must be a regular non-symlink file".to_owned(),
            )),
            Err(failure) => Err((
                "read_error",
                format!("cannot read gcov JSON gzip file: {failure}"),
            )),
        };
        let artifact = match checked {
            Ok(artifact) => artifact,
            Err((code, message)) => {
                // The reports read before this one were merged first in frozen.
                if !reports.is_empty()
                    && let Err(rejected) = merge(&reports, plan)
                    && rejected.0.starts_with("gcov JSON evidence rejected (")
                    && !rejected.0.starts_with(LEGACY_MISSING_SOURCES)
                {
                    return Err(rejected);
                }
                return Err(
                    if matches!(code, "compressed_limit" | "decompressed_limit")
                        && (compressed_remaining < MAX_COMPRESSED_BYTES
                            || decompressed_remaining < MAX_DECOMPRESSED_BYTES)
                    {
                        legacy_rejection(
                            "aggregate_limit",
                            "cumulative gcov JSON byte budget was exceeded",
                        )
                    } else {
                        legacy_rejection(code, message)
                    },
                );
            }
        };
        let report = parse_gzip(&artifact.bytes, max_decompressed)?;
        bytes += artifact.bytes.len();
        decompressed += report.decompressed_bytes;
        reports.push(report);
    }
    Ok((reports, bytes))
}

mod strict_json {
    use super::*;
    use serde::de::{DeserializeSeed, Error, MapAccess, SeqAccess, Visitor};
    struct Seed {
        depth: usize,
    }
    impl<'de> DeserializeSeed<'de> for Seed {
        type Value = Value;
        fn deserialize<D: serde::Deserializer<'de>>(
            self,
            deserializer: D,
        ) -> Result<Value, D::Error> {
            if self.depth > 64 {
                return Err(D::Error::custom("gcov JSON depth exceeds 64"));
            }
            deserializer.deserialize_any(self)
        }
    }
    impl<'de> Visitor<'de> for Seed {
        type Value = Value;
        fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            f.write_str("bounded JSON evidence")
        }
        fn visit_bool<E: Error>(self, value: bool) -> Result<Value, E> {
            Ok(Value::Bool(value))
        }
        fn visit_unit<E: Error>(self) -> Result<Value, E> {
            Ok(Value::Null)
        }
        fn visit_i64<E: Error>(self, value: i64) -> Result<Value, E> {
            Ok(value.into())
        }
        fn visit_u64<E: Error>(self, value: u64) -> Result<Value, E> {
            Ok(value.into())
        }
        fn visit_f64<E: Error>(self, value: f64) -> Result<Value, E> {
            serde_json::Number::from_f64(value)
                .map(Value::Number)
                .ok_or_else(|| E::custom("nonfinite JSON number"))
        }
        fn visit_str<E: Error>(self, value: &str) -> Result<Value, E> {
            if value.len() > 1024 * 1024 || value.chars().any(|c| c < ' ') {
                return Err(E::custom("gcov JSON string size/control violation"));
            }
            Ok(Value::String(value.into()))
        }
        fn visit_seq<A: SeqAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
            let mut items = vec![];
            while let Some(item) = access.next_element_seed(Seed {
                depth: self.depth + 1,
            })? {
                if items.len() >= 2000000 {
                    return Err(A::Error::custom("gcov JSON array bound"));
                }
                items.push(item);
            }
            Ok(Value::Array(items))
        }
        fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
            let mut values = serde_json::Map::new();
            while let Some(key) = access.next_key::<String>()? {
                if values.len() >= 128
                    || values.contains_key(&key)
                    || key.len() > 1024 * 1024
                    || key.chars().any(|c| c < ' ')
                {
                    return Err(A::Error::custom("gcov JSON duplicate/oversize object"));
                }
                let value = access.next_value_seed(Seed {
                    depth: self.depth + 1,
                })?;
                values.insert(key, value);
            }
            Ok(Value::Object(values))
        }
    }
    pub(super) fn parse(bytes: &[u8]) -> Result<Value, ValidationError> {
        let mut deserializer = serde_json::Deserializer::from_slice(bytes);
        let value = Seed { depth: 0 }
            .deserialize(&mut deserializer)
            .map_err(|e| error(format!("gcov JSON: {e}")))?;
        deserializer
            .end()
            .map_err(|e| error(format!("gcov JSON: {e}")))?;
        Ok(value)
    }
}

#[cfg(test)]
mod legacy_wording_tests {
    /// Frozen `_require_complete_source_coverage` as `_consume_cpp_coverage` words it (cppval
    /// `test:direct-empty-source` replays the one-source case against the frozen capture).
    /// Frozen coverage_support.py:682 `GcovJsonError("cumulative gcov JSON byte budget is
    /// exhausted", code="aggregate_limit")`, :694 `"cumulative gcov JSON byte budget was
    /// exceeded"` (a per-file compressed/decompressed limit hit with less than the per-file
    /// budget left) and :610 `f"cannot enumerate gcov JSON evidence: {exc}", code="read_error"`.
    /// The read_error OS text is native's capture error, not CPython's OSError text.
    #[test]
    fn byte_budget_and_read_error_rejections_use_frozen_wording() {
        const MIB: usize = 1024 * 1024;
        let text = |rejected: Option<crate::ValidationError>| rejected.map(|error| error.0);
        let exhausted = "gcov JSON evidence rejected (aggregate_limit): aggregate_limit: cumulative gcov JSON byte budget is exhausted";
        let exceeded = "gcov JSON evidence rejected (aggregate_limit): aggregate_limit: cumulative gcov JSON byte budget was exceeded";
        assert_eq!(
            text(super::legacy_byte_budget(64 * MIB, 0, 1)).as_deref(),
            Some(exhausted)
        );
        assert_eq!(
            text(super::legacy_byte_budget(0, 256 * MIB, 1)).as_deref(),
            Some(exhausted)
        );
        assert_eq!(
            text(super::legacy_byte_budget(60 * MIB, 0, 5 * MIB)).as_deref(),
            Some(exceeded)
        );
        assert_eq!(text(super::legacy_byte_budget(60 * MIB, 0, 4 * MIB)), None);
        assert_eq!(text(super::legacy_byte_budget(0, 0, 9 * MIB)), None);
        assert_eq!(
            super::legacy_rejection("read_error", "cannot enumerate gcov JSON evidence: denied").0,
            "gcov JSON evidence rejected (read_error): read_error: cannot enumerate gcov JSON evidence: denied"
        );
    }
    #[test]
    fn missing_sources_preview_the_first_eight_in_order_and_count_the_rest() {
        let one = ["src/empty.cpp".to_owned()];
        assert_eq!(
            super::legacy_missing_sources(&one.iter().collect::<Vec<_>>()),
            "gcov JSON evidence rejected (incomplete_source_coverage): \
             incomplete_source_coverage: coverage evidence is missing 1 source(s): src/empty.cpp"
        );
        let ten: Vec<String> = (0..10).map(|index| format!("src/{index}.cpp")).collect();
        assert!(
            super::legacy_missing_sources(&ten.iter().collect::<Vec<_>>()).ends_with(
                "missing 10 source(s): src/0.cpp, src/1.cpp, src/2.cpp, src/3.cpp, src/4.cpp, \
                 src/5.cpp, src/6.cpp, src/7.cpp (+2 more)"
            )
        );
    }
}
