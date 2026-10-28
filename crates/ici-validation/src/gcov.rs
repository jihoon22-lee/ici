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
        if tally
            .iter()
            .zip([16384, 500000, 4000000, 8000000, 8000000])
            .any(|(actual, maximum)| *actual > maximum)
        {
            return Err(error("aggregate gcov record budget exceeded"));
        }
        for file in &report.files {
            let Some(path) = source_path(&file.file, &report.current_working_directory, plan)?
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
                let start_column = column(function.start_line, function.start_column)?;
                let end_column = column(function.end_line, function.end_column)?;
                target.functions.insert(key,json!({"file":path,"name":if function.demangled_name.is_empty(){&function.name}else{&function.demangled_name},"symbol":function.name,"start_line":function.start_line,"end_line":function.end_line,"start_column":start_column,"end_column":end_column,"native_columns":{"unit":"utf8-bytes","start":function.start_column,"end":function.end_column},"covered":previous||function.execution_count>0,"missing_lines":[]}));
            }
        }
    }
    if versions.len() != 1 || gcc_versions.len() != 1 {
        return Err(error("gcov report set mixes format or GCC versions"));
    }
    if plan
        .context
        .source_files
        .iter()
        .any(|source| !merged.contains_key(source))
    {
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
                without_blocks += usize::from(line.block_ids.is_none());
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
fn lexical(path: &Path) -> PathBuf {
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
fn source_path(
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
    for path in roots {
        if let Ok(relative) = lexical(&path).strip_prefix(&plan.context.root) {
            let relative = relative.to_string_lossy().replace('\\', "/");
            if plan.context.source_files.contains(&relative) {
                candidates.insert(relative);
            }
        }
    }
    if candidates.len() > 1 {
        return Err(error("gcov source path maps to multiple declared files"));
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
    let id = format!("{}.report", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| error("gcov report step absent"))?;
    if !planning::completed(outcome) || outcome.exit_code != Some(0) {
        return Err(error("gcov report process did not complete successfully"));
    }
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    if [
        "stamp mismatch",
        "version mismatch",
        "cannot open data file",
        "assuming not executed",
        "not a gcov data file",
    ]
    .iter()
    .any(|needle| stderr.contains(needle))
    {
        return Err(error(
            "gcov instrumentation is stale, mismatched or lacks run data",
        ));
    }
    if artifacts
        .errors
        .keys()
        .any(|key| key.starts_with("gcov-json"))
    {
        return Err(error("gcov report ownership/capture failed"));
    }
    let producer = plan
        .context
        .options
        .shared_test_task_id
        .as_deref()
        .ok_or_else(|| error("gcov shared suite provenance absent"))?;
    let suite = outcomes
        .get(producer)
        .ok_or_else(|| error("gcov shared suite outcome absent"))?;
    let test_evidence = crate::cpp::test_evidence(plan, producer, suite)?;
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
    for target in &plan.context.targets {
        let key = format!("gcov-data:{target}");
        if !artifacts.outputs.get(&key).is_some_and(|witness| {
            witness.fresh && witness.size > 0 && witness.producer_task_id == producer
        }) {
            return Err(error(
                "gcov data was not freshly written by the declared suite",
            ));
        }
    }
    let mut reports = vec![];
    let mut decompressed = 0;
    for artifact in artifacts
        .files
        .values()
        .filter(|artifact| artifact.id.starts_with("gcov-json:"))
    {
        if artifact.producer_task_id != id || artifact.digest != ici_domain::sha256(&artifact.bytes)
        {
            return Err(error("gcov report provenance mismatch"));
        }
        let budget = (256 * 1024 * 1024usize - decompressed).min(64 * 1024 * 1024);
        let report = parse_gzip(&artifact.bytes, budget)?;
        decompressed += report.decompressed_bytes;
        reports.push(report);
    }
    let report = merge(&reports, plan)?;
    output.extra["gcov_json_info"] = provenance(
        &reports,
        plan,
        &report,
        artifacts
            .files
            .values()
            .filter(|artifact| artifact.id.starts_with("gcov-json:"))
            .map(|artifact| artifact.bytes.len())
            .sum(),
    )?;
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
    let score = crate::tem::calculate(&crate::tem::TemInputs {
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
    });
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
    crate::policy::evaluate(plan, &report, score.score, output)
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
