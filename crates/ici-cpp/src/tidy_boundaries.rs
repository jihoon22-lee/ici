//! Dedicated clang-tidy function-size evidence, independent of project lint configuration.
use crate::{
    CompilationUnit, CompilerBoundaryEvidence, CompilerFunctionBoundary, CppError,
    GccStdlibProjection, ReplayPurpose,
    source::{self, ParsedSource},
};
use ici_domain::{AnalysisOutput, SourceInput, TaskSpec};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    os::unix::fs::PermissionsExt,
    path::Path,
    sync::LazyLock,
};
static DIAGNOSTIC: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(.+):([1-9][0-9]*):([1-9][0-9]*): (warning|note|error|fatal error): (.+)$")
        .unwrap()
});
static PARENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^function '(.+)' exceeds recommended size/complexity thresholds \[readability-function-size\]$").unwrap()
});
static NOTE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^([0-9]+) (lines including whitespace and comments|statements|parameters) \(threshold 0\)$").unwrap()
});
static SUMMARY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[0-9]+ warnings? generated\.$").unwrap());
static EXTERNAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^Suppressed ([1-9][0-9]*) warnings? \(([1-9][0-9]*) in non-user code\)\.$")
        .unwrap()
});
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct FunctionSizeMetric {
    pub path: String,
    pub name: String,
    pub start_line: u32,
    pub end_line: u32,
    pub configuration: String,
    pub lines: u32,
    pub statements: u32,
    pub parameters: u32,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ClangTidyBoundaryEvidence {
    pub compiler: CompilerBoundaryEvidence,
    pub metrics: Vec<FunctionSizeMetric>,
}
/// Pure command planning. GCC implicit C++ roots must come from the selected compiler probes.
pub fn clang_tidy_boundary_task(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
    tidy: &Path,
    projection: &GccStdlibProjection,
) -> Result<TaskSpec, CppError> {
    let replay = crate::replay_command(root, unit, compiler, ReplayPurpose::Boundaries)?;
    let physical = tidy
        .canonicalize()
        .map_err(|error| CppError::new("tidy-unavailable", error.to_string()))?;
    let metadata = physical
        .metadata()
        .map_err(|error| CppError::new("tidy-unavailable", error.to_string()))?;
    if !tidy.is_absolute()
        || physical.starts_with(root)
        || !metadata.is_file()
        || metadata.permissions().mode() & 0o111 == 0
    {
        return Err(CppError::new(
            "tidy-unapproved",
            "Boundary tool must be an approved absolute executable outside the project",
        ));
    }
    if !projection.arguments.is_empty() {
        if projection.arguments[0] != "-nostdinc++" || projection.arguments.len() % 2 != 1 {
            return Err(CppError::new(
                "stdlib-projection-shape",
                "Invalid GCC library projection",
            ));
        }
        for pair in projection.arguments[1..].as_chunks::<2>().0.iter() {
            if pair[0] != "-isystem"
                || !Path::new(&pair[1]).is_absolute()
                || !Path::new(&pair[1]).is_dir()
            {
                return Err(CppError::new(
                    "stdlib-projection-shape",
                    "Invalid GCC library search root",
                ));
            }
        }
    }
    let config = json!({"CheckOptions":{"readability-function-size.CountMemberInitAsStmt":"true","readability-function-size.IgnoreMacros":"true","readability-function-size.LineThreshold":"0","readability-function-size.ParameterThreshold":"0","readability-function-size.StatementThreshold":"0"}});
    let mut argv = vec![
        tidy.display().to_string(),
        "--use-color=false".into(),
        "--checks=-*,readability-function-size".into(),
        format!("--config={}", ici_domain::canonical_json(&config).unwrap()),
        format!(
            "--header-filter=^{}$",
            regex::escape(&replay.source.display().to_string())
        ),
        replay.source.display().to_string(),
        "--".into(),
    ];
    let mut index = 1;
    while index < replay.argv.len() {
        let argument = &replay.argv[index];
        if crate::replay_policy::PRESERVE_VALUE.contains(&argument.as_str()) {
            argv.extend([argument.clone(), replay.argv[index + 1].clone()]);
            index += 2;
            continue;
        }
        if argument != &replay.source.display().to_string() {
            argv.push(argument.clone());
        }
        index += 1;
    }
    if !matches!(unit.language.as_str(), "c" | "c++") {
        return Err(CppError::new(
            "boundary-language",
            "Clang boundary analysis supports C and C++ translation units",
        ));
    }
    argv.extend(["-x".into(), unit.language.clone()]);
    argv.extend(projection.arguments.clone());
    let mut task = TaskSpec::command(
        format!("cpp-boundaries:{}", unit.configuration),
        "clang-tidy.boundaries",
        argv,
        replay.cwd.display().to_string(),
    );
    task.env_overlay = crate::replay_environment();
    task.input_refs = vec![unit.source.clone()];
    task.input_refs
        .extend(unit.response_files.iter().map(|input| input.path.clone()));
    task.tool_digest = Some(
        ici_execution::process::executable_digest(tidy)
            .map_err(|error| CppError::new("tidy-identity", error.to_string()))?,
    );
    task.timeout_seconds = Some(120.0);
    task.output_limit_bytes = Some(1024 * 1024);
    task.variant = Some(unit.variant.clone());
    Ok(task)
}
struct Pending {
    path: String,
    line: u32,
    column: u32,
    name: String,
    values: BTreeMap<&'static str, u32>,
}
fn finish(
    output: &mut ClangTidyBoundaryEvidence,
    pending: Pending,
    file: &ParsedSource,
    absolute_source: &Path,
) -> Result<(), CppError> {
    if Path::new(&pending.path) != absolute_source {
        output.compiler.non_source_definitions_excluded += 1;
        return Ok(());
    }
    let Some(point) = file.byte_at(pending.line, pending.column) else {
        return Err(CppError::new(
            "tidy-source-location",
            "Function-size location is outside its immutable source",
        ));
    };
    let nodes = source::nodes(file.tree.root_node())?;
    let Some(function) = nodes.iter().find(|node| {
        node.kind() == "function_definition"
            && node.start_byte() <= point
            && node
                .child_by_field_name("body")
                .is_some_and(|body| point < body.start_byte())
            && source::unqualified_name(&source::function_name(file, **node))
                == source::unqualified_name(&pending.name)
    }) else {
        if nodes.iter().any(|node| {
            node.kind() == "lambda_expression"
                && node.start_byte() <= point
                && point < node.end_byte()
        }) {
            output.compiler.lambda_definitions_excluded += 1;
        } else {
            output.compiler.macro_definitions_excluded += 1;
        }
        return Ok(());
    };
    let body = function.child_by_field_name("body").unwrap();
    let target = file.target(*function, &pending.name);
    let body_target = file.target(body, "");
    let is_template = function
        .parent()
        .is_some_and(|parent| parent.kind() == "template_declaration");
    let boundary = CompilerFunctionBoundary {
        path: file.path.clone(),
        name: source::function_name(file, *function),
        start_line: target.start_line,
        end_line: target.end_line.unwrap(),
        start_column: target.start_column.unwrap(),
        end_column: target.end_column.unwrap(),
        body_start_line: body_target.start_line,
        body_start_column: body_target.start_column.unwrap(),
        begin_byte: function.start_byte(),
        end_byte: function.end_byte(),
        body_begin_byte: body.start_byte(),
        configuration: output.compiler.configuration.clone(),
        source_digest: output.compiler.source_digest.clone(),
        function_kind: if pending.name.starts_with("operator") {
            "operator"
        } else {
            "function"
        }
        .into(),
        is_template,
    };
    let metric = FunctionSizeMetric {
        path: file.path.clone(),
        name: boundary.name.clone(),
        start_line: boundary.start_line,
        end_line: boundary.end_line,
        configuration: boundary.configuration.clone(),
        lines: *pending.values.get("lines").unwrap_or(&0),
        statements: *pending.values.get("statements").unwrap_or(&0),
        parameters: *pending.values.get("parameters").unwrap_or(&0),
    };
    if let Some(previous) = output.metrics.iter().find(|previous| {
        previous.path == metric.path
            && previous.name == metric.name
            && previous.start_line == metric.start_line
            && previous.end_line == metric.end_line
    }) {
        if previous != &metric {
            return Err(CppError::new(
                "tidy-metric-conflict",
                "Function-size notes conflict within one configuration",
            ));
        }
        return Ok(());
    }
    output.metrics.push(metric);
    output.compiler.boundaries.push(boundary);
    Ok(())
}
pub fn parse_clang_tidy_boundaries(
    stdout: &str,
    stderr: &str,
    input: &SourceInput,
    absolute_source: &Path,
    configuration: &str,
) -> Result<ClangTidyBoundaryEvidence, CppError> {
    if stdout.len() + stderr.len() > 1024 * 1024 || stdout.contains('\0') || stderr.contains('\0') {
        return Err(CppError::new(
            "tidy-output-bounds",
            "Function-size output exceeds bounded text contract",
        ));
    }
    let file = ParsedSource::parse(input, None)?;
    let mut output = ClangTidyBoundaryEvidence {
        compiler: CompilerBoundaryEvidence {
            source: input.path.clone(),
            source_digest: ici_domain::sha256(&input.bytes),
            configuration: configuration.into(),
            ..Default::default()
        },
        metrics: vec![],
    };
    let mut pending: Option<Pending> = None;
    let mut count = 0;
    let mut external = false;
    for line in stdout
        .lines()
        .chain(stderr.lines())
        .filter(|line| !line.trim().is_empty())
    {
        let trimmed = line.trim();
        if SUMMARY.is_match(trimmed) {
            continue;
        }
        if trimmed.starts_with("Suppressed ") {
            let capture = EXTERNAL
                .captures(trimmed)
                .filter(|capture| capture[1] == capture[2])
                .ok_or_else(|| {
                    CppError::new(
                        "tidy-diagnostics-suppressed",
                        "Function-size diagnostics were suppressed beyond non-user code",
                    )
                })?;
            external = !capture[1].is_empty();
            continue;
        }
        if trimmed.starts_with("Use -header-filter=") {
            continue;
        }
        let Some(capture) = DIAGNOSTIC.captures(line) else {
            if line.starts_with(char::is_whitespace)
                && (line.contains('|') || trimmed.starts_with(['^', '~']))
            {
                continue;
            }
            return Err(CppError::new(
                "tidy-output-unrecognized",
                "Function-size output contains an unrecognized line",
            ));
        };
        count += 1;
        if count > 400_000 {
            return Err(CppError::new(
                "tidy-diagnostic-count",
                "Function-size diagnostics exceed bounded count",
            ));
        }
        if &capture[4] == "warning" {
            let name = PARENT.captures(&capture[5]).ok_or_else(|| {
                CppError::new(
                    "tidy-unexpected-check",
                    "Dedicated function-size output contains another check",
                )
            })?[1]
                .to_owned();
            if name.is_empty() || name.len() > 2048 {
                return Err(CppError::new(
                    "tidy-name-bounds",
                    "Function-size name exceeds bounded contract",
                ));
            }
            if let Some(previous) = pending.take() {
                finish(&mut output, previous, &file, absolute_source)?;
            }
            pending = Some(Pending {
                path: capture[1].into(),
                line: capture[2]
                    .parse()
                    .map_err(|_| CppError::new("tidy-line-bounds", "Invalid diagnostic line"))?,
                column: capture[3].parse().map_err(|_| {
                    CppError::new("tidy-column-bounds", "Invalid diagnostic column")
                })?,
                name,
                values: BTreeMap::new(),
            });
        } else if &capture[4] == "note" {
            let note = NOTE.captures(&capture[5]).ok_or_else(|| {
                CppError::new("tidy-note-shape", "Unrecognized function-size note")
            })?;
            let item = pending.as_mut().ok_or_else(|| {
                CppError::new(
                    "tidy-orphan-note",
                    "Function-size note has no preceding warning",
                )
            })?;
            if item.path != capture[1]
                || item.line.to_string() != capture[2]
                || item.column.to_string() != capture[3]
            {
                return Err(CppError::new(
                    "tidy-note-location",
                    "Function-size note does not match its warning location",
                ));
            }
            let key = match &note[2] {
                "statements" => "statements",
                "parameters" => "parameters",
                _ => "lines",
            };
            let value = note[1].parse().map_err(|_| {
                CppError::new(
                    "tidy-note-bounds",
                    "Function-size count exceeds bounded shape",
                )
            })?;
            if item.values.insert(key, value).is_some() {
                return Err(CppError::new(
                    "tidy-duplicate-note",
                    "Function-size note was duplicated",
                ));
            }
        } else {
            return Err(CppError::new(
                "tidy-compiler-error",
                "Compiler error prevents function-size boundary evidence",
            ));
        }
    }
    if let Some(previous) = pending {
        finish(&mut output, previous, &file, absolute_source)?;
    }
    if external && output.compiler.boundaries.is_empty() {
        return Err(CppError::new(
            "tidy-empty-suppression",
            "Function-size suppression produced no visible source diagnostics",
        ));
    }
    output.compiler.boundaries.sort_by_key(|boundary| {
        (
            boundary.begin_byte,
            boundary.end_byte,
            boundary.name.clone(),
        )
    });
    Ok(output)
}
pub fn apply_function_size_metrics(
    output: &mut AnalysisOutput,
    evidence: &[ClangTidyBoundaryEvidence],
) {
    for target in &mut output.targets {
        let mut rows: Vec<_> = evidence
            .iter()
            .flat_map(|item| &item.metrics)
            .filter(|metric| {
                metric.path == target.file_path
                    && metric.name == target.target_name
                    && metric.start_line == target.start_line
                    && Some(metric.end_line) == target.end_line
            })
            .collect();
        if rows.is_empty() {
            continue;
        }
        rows.sort_by_key(|row| {
            (
                &row.configuration,
                row.lines,
                row.statements,
                row.parameters,
            )
        });
        target.metrics.insert(
            "tool_lines".into(),
            json!(rows.iter().map(|row| row.lines).max().unwrap()),
        );
        target.metrics.insert(
            "tool_statements".into(),
            json!(rows.iter().map(|row| row.statements).max().unwrap()),
        );
        target.metrics.insert(
            "tool_parameters".into(),
            json!(rows.iter().map(|row| row.parameters).max().unwrap()),
        );
        target
            .metrics
            .insert("configuration_metrics".into(), json!(rows));
        let variants: BTreeSet<_> = rows
            .iter()
            .map(|row| (row.lines, row.statements, row.parameters))
            .collect();
        if variants.len() > 1 {
            target.metrics.insert("metric_variant".into(), json!(true));
        }
    }
}
