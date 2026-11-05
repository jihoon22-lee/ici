//! Compiler fact adapters over explicit replay outcomes. No process is run by these functions.
use crate::{
    CompilationContext, CompilationUnit, CppError, ReplayPurpose,
    analysis::{fail, finding, measurement},
    input::{read_contained, scoped, strict_json},
    source::{self, ParsedSource},
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, Finding, InspectionTarget, SourceInput, TaskSpec, TaskState,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub fn analyze_compile_context(
    request: &AnalysisRequest,
    context: &CompilationContext,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "ici.compile");
    output.observation.state = TaskState::Succeeded;
    let production = context
        .selected_sources
        .iter()
        .filter(|source| crate::is_translation_unit(source))
        .cloned()
        .collect::<BTreeSet<_>>();
    if production.is_empty()
        && request.options["legacy_projection"]
            .as_bool()
            .unwrap_or(false)
    {
        for input in &request.inputs {
            let mut target = InspectionTarget::at(&input.path, 1);
            target.target_name = "CompilationDatabase:NotApplicable".into();
            target.status = "SKIP".into();
            target.message = "No production C/C++ translation units are in scope".into();
            output.targets.push(target);
        }
        let mut applicable = ici_domain::Measurement::measured("compilation_units", 0.0);
        applicable.evidence = ici_domain::EvidenceLevel::NotApplicable;
        output.observation.measurements.push(applicable);
        output.status = "SKIP".into();
        output.summary = "No production C/C++ translation units are in scope".into();
        return output;
    }
    if context.database_path.is_none()
        && request.options["legacy_projection"]
            .as_bool()
            .unwrap_or(false)
    {
        let required = request.options["database_required"]
            .as_bool()
            .unwrap_or(false);
        // Frozen `CompileDatabaseEngine.run` without a database: the context diagnostics (a
        // failed canonical CMake/qmake preparation) first, at the first production source.
        let fallback = production.first().map(String::as_str).unwrap_or(".");
        output.targets.extend(
            context
                .diagnostics
                .iter()
                .map(|item| legacy_diagnostic_target(item, fallback)),
        );
        for source in &production {
            // Frozen CompileDatabaseEngine `_coverage_target`: line 1 to 1, sentence message.
            let mut target = InspectionTarget::at(source, 1);
            target.end_line = Some(1);
            target.target_name = "ici.compile-db.coverage".into();
            target.status = if required { "FAIL" } else { "WARN" }.into();
            target.message =
                "No compilation database is available for this production translation unit.".into();
            crate::analysis::finding(
                &mut output,
                request,
                &target,
                "ici.compile-db.coverage",
                if required { "high" } else { "medium" },
                true,
            );
            if required {
                // The absence violates an explicit metadata policy. Compile coverage itself is
                // still estimated and never becomes measured syntax evidence.
                output
                    .observation
                    .findings
                    .last_mut()
                    .expect("missing database finding")
                    .evidence = ici_domain::EvidenceLevel::Measured;
            }
            output.targets.push(target);
        }
        for (name, value) in [
            ("compilation_units", 0),
            ("uncovered_sources", production.len() as u64),
        ] {
            let mut item = ici_domain::Measurement::measured(name, value as f64);
            item.evidence = ici_domain::EvidenceLevel::Estimated;
            output.observation.measurements.push(item);
        }
        // Frozen `CompileDbEngine._result` wording and coverage score for a missing database.
        let count = |status: &str| {
            output
                .targets
                .iter()
                .filter(|target| target.status == status)
                .count()
        };
        let (failures, warnings) = (count("FAIL"), count("WARN"));
        output.status = if failures > 0 {
            "FAIL"
        } else if warnings > 0 {
            "WARN"
        } else {
            "PASS"
        }
        .into();
        output.summary = format!(
            "Compilation DB: 0/{} production units covered, 0 configurations, {failures} failures, {warnings} warnings.",
            production.len(),
        );
        output.score = Some(0.0);
        output.max_score = Some(100.0);
        output.extra = json!({"context":context,"database_path":null,"production_units":production.len(),"covered_units":0,"configurations":0,"coverage_percent":0.0});
        return output;
    }
    let required_flags = string_options(&request.options, "required_flags");
    let forbidden_flags = string_options(&request.options, "forbidden_flags");
    // Frozen `CompileDatabaseEngine` reports uncovered production sources, unit diagnostics
    // (a gone directory, a stale source) and refused database rows (`_RowError`, e.g. an entry
    // that is not an object) as its own FAIL rows; only a database-level error stops it. An
    // incomplete qmake capture (frozen `_with_capture_coverage`) is reported beside the rows.
    if context.database_path.is_some()
        && (context.complete()
            || !context.diagnostics.iter().any(|diagnostic| {
                diagnostic.level == "error"
                    && diagnostic.entry_index.is_none()
                    && diagnostic.code != "qmake-capture-incomplete"
            }))
        && request.options["legacy_projection"]
            .as_bool()
            .unwrap_or(false)
    {
        return legacy_database_output(
            output,
            request,
            context,
            &production,
            &required_flags,
            &forbidden_flags,
        );
    }
    for unit in &context.units {
        let mut target = InspectionTarget::at(&unit.source, 1);
        target.target_name = "CompilationInput".into();
        target.message = format!(
            "Recorded {} invocation, standard {}, variant {}",
            unit.language, unit.standard, unit.variant
        );
        target.metrics = BTreeMap::from([
            ("configuration".into(), json!(unit.configuration)),
            ("output".into(), json!(unit.output)),
            ("target".into(), json!(unit.target)),
        ]);
        if unit
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.level == "error")
        {
            target.status = "ERROR".into();
        }
        output.targets.push(target);
        if !unit
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.level == "error")
        {
            for (rule, flags, present, message) in [
                (
                    "required-flag",
                    &required_flags,
                    false,
                    "A required compiler flag is absent from this configuration",
                ),
                (
                    "forbidden-flag",
                    &forbidden_flags,
                    true,
                    "A forbidden compiler flag is present in this configuration",
                ),
            ] {
                for flag in flags
                    .iter()
                    .filter(|flag| unit.argv.contains(flag) == present)
                {
                    let mut target = InspectionTarget::at(&unit.source, 1);
                    target.target_name = format!("ici.compile-db.{rule}");
                    target.status = "FAIL".into();
                    target.message = message.into();
                    target
                        .metrics
                        .insert("configuration".into(), json!(unit.configuration));
                    target
                        .metrics
                        .insert("flag_digest".into(), json!(ici_domain::sha256(flag)));
                    crate::analysis::finding(
                        &mut output,
                        request,
                        &target,
                        &format!("ici.compile-db.{rule}"),
                        "high",
                        false,
                    );
                    let finding = output
                        .observation
                        .findings
                        .last_mut()
                        .expect("new policy finding");
                    finding.analysis_unit_id = Some(format!(
                        "{}.cpp:{}",
                        request.component_id, unit.configuration
                    ));
                    finding.variant = Some(unit.variant.clone());
                    finding.fingerprint = ici_domain::sha256(format!(
                        "compile-policy:{}:{rule}:{}",
                        unit.configuration,
                        ici_domain::sha256(flag)
                    ));
                    output.targets.push(target);
                }
            }
        }
    }
    for source in &context.selected_sources {
        if context.units.iter().any(|unit| &unit.source == source) {
            continue;
        }
        if let Some(configurations) = context.source_coverage.get(source) {
            let mut target = InspectionTarget::at(source, 1);
            target.target_name = "AggregateCompilationInput".into();
            target.message="Compiler include traces confirmed this original source in recorded aggregate translation units".into();
            target
                .metrics
                .insert("configurations".into(), json!(configurations));
            output.targets.push(target);
        }
    }
    for source in &context.uncovered_sources {
        let mut target = InspectionTarget::at(source, 1);
        target.target_name = "UncoveredTranslationUnit".into();
        target.status = "ERROR".into();
        target.message =
            "Selected translation unit has no recorded compilation configuration".into();
        output.targets.push(target);
    }
    for diagnostic in &context.diagnostics {
        output
            .observation
            .limitations
            .push(format!("{}: {}", diagnostic.code, diagnostic.message));
    }
    if !context.complete() {
        fail(
            &mut output,
            context.database_path.as_deref().unwrap_or("."),
            "incomplete-compilation-context",
            "Compilation evidence is absent, ambiguous, malformed or does not cover every selected source",
        );
    }
    let (covered, expected, generated) = unit_coverage(request, context);
    if !request.options["legacy_projection"]
        .as_bool()
        .unwrap_or(false)
    {
        // Frozen `internal_checks._compile_coverage`: the uncovered translation units lead the
        // limitations (the gate reason is the first one).
        let recorded: BTreeSet<&String> = context.units.iter().map(|unit| &unit.source).collect();
        let missing: Vec<&String> = context
            .selected_sources
            .iter()
            .filter(|source| crate::is_translation_unit(source) && !recorded.contains(source))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        if !missing.is_empty() {
            let shown = missing
                .iter()
                .take(8)
                .map(|source| source.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            let more = if missing.len() > 8 {
                format!(" (+{} more)", missing.len() - 8)
            } else {
                String::new()
            };
            output.observation.limitations.insert(
                0,
                format!(
                    "no compile invocation for {}/{expected} translation units: {shown}{more}",
                    missing.len()
                ),
            );
        }
        // Frozen `_compile_coverage`: a build-generated input that is not on disk is an
        // incomplete capture (FAILED, never a full pass).
        if request.options["incomplete_capture"]
            .as_bool()
            .unwrap_or(false)
        {
            output.observation.state = TaskState::Failed;
            output.status = "ERROR".into();
        }
        // The linked build's project-file reach warnings and missing generated inputs
        // (`options.reach_limitations`).
        output.observation.limitations.extend(
            request.options["reach_limitations"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_owned),
        );
    }
    // Frozen `internal_checks._compile_coverage`: the covered translation units over the
    // expected ones in scope, and the build-generated units (moc/uic/rcc output under a linked
    // build directory, outside the component's own sources).
    let mut units = ici_domain::Measurement::measured("units.covered", covered as f64);
    units.unit = "translation units".into();
    units.numerator = Some(covered as u64);
    units.denominator = Some(expected as u64);
    output.observation.measurements.push(units);
    let mut units = ici_domain::Measurement::measured("units.generated", generated as f64);
    units.unit = "translation units".into();
    output.observation.measurements.push(units);
    if output.observation.state == TaskState::Succeeded && !output.observation.findings.is_empty() {
        output.status = "FAIL".into();
    }
    output.extra = json!({"context":context});
    output.summary = format!(
        "{} compile configurations, {} uncovered sources",
        context.units.len(),
        context.uncovered_sources.len()
    );
    output
}
/// Frozen `compile_units.load_compile_inputs` counts: (covered, expected, generated). Expected
/// are the selected translation units, covered those a recorded unit compiles; a recorded unit
/// outside the selected sources whose source lies under a linked build directory
/// (`options.build_directories`, workspace-relative) is generated.
fn unit_coverage(request: &AnalysisRequest, context: &CompilationContext) -> (usize, usize, usize) {
    let expected: BTreeSet<&String> = context
        .selected_sources
        .iter()
        .filter(|source| crate::is_translation_unit(source))
        .collect();
    let recorded: BTreeSet<&String> = context.units.iter().map(|unit| &unit.source).collect();
    let covered = expected.intersection(&recorded).count();
    let directories: Vec<String> = request.options["build_directories"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|directory| directory.trim_end_matches('/').to_owned())
        .collect();
    let selected: BTreeSet<&String> = context.selected_sources.iter().collect();
    let generated = recorded
        .iter()
        .filter(|source| !selected.contains(*source))
        .filter(|source| {
            directories.iter().any(|directory| directory == ".")
                || directories.iter().any(|directory| {
                    !directory.is_empty()
                        && (source.as_str() == directory
                            || source.starts_with(&format!("{directory}/")))
                })
        })
        .count();
    (covered, expected.len(), generated)
}
/// Frozen `compile_db._diagnostic_target`: an `ici.compile-db.<code>` row at the diagnostic's
/// source (else `fallback`), FAIL for an error or a `_FAIL_DIAGNOSTICS` code, WARN for a warning.
fn legacy_diagnostic_target(
    item: &crate::model::ContextDiagnostic,
    fallback: &str,
) -> InspectionTarget {
    let status = if item.level == "error" || LEGACY_FAIL_DIAGNOSTICS.contains(&item.code.as_str()) {
        "FAIL"
    } else if item.level == "warning" {
        "WARN"
    } else {
        "PASS"
    };
    let path = if item.source.is_empty() {
        fallback
    } else {
        &item.source
    };
    let mut target = InspectionTarget::at(path, 1);
    target.end_line = Some(1);
    target.target_name = format!("ici.compile-db.{}", item.code);
    target.status = status.into();
    target.message = legacy_diagnostic_message(&item.code, &item.message).into();
    if let Some(index) = item.entry_index {
        target.metrics.insert("entry_index".into(), json!(index));
    }
    target
}
/// Frozen `CompileDatabaseEngine.run` with a selected database: one coverage target per
/// production translation unit, the context diagnostics, then per recorded configuration an
/// inspected target, its diagnostics and the required/forbidden flag violations.
fn legacy_database_output(
    mut output: AnalysisOutput,
    request: &AnalysisRequest,
    context: &CompilationContext,
    production: &BTreeSet<String>,
    required_flags: &[String],
    forbidden_flags: &[String],
) -> AnalysisOutput {
    fn target(path: &str, rule: &str, status: &str, message: &str) -> InspectionTarget {
        let mut target = InspectionTarget::at(path, 1);
        target.end_line = Some(1);
        target.target_name = format!("ici.compile-db.{rule}");
        target.status = status.into();
        target.message = message.into();
        target
    }
    let diagnostic = legacy_diagnostic_target;
    let configurations = |source: &str| {
        context
            .units
            .iter()
            .filter(|unit| unit.source == source)
            .count()
    };
    for source in production {
        let count = configurations(source);
        let mut item = if count > 0 {
            target(
                source,
                "coverage",
                "PASS",
                "Production translation unit is covered by the compilation database.",
            )
        } else {
            target(
                source,
                "coverage",
                "FAIL",
                "Production translation unit is missing from the compilation database.",
            )
        };
        item.metrics.insert("configurations".into(), json!(count));
        output.targets.push(item);
    }
    let fallback = context.database_path.as_deref().unwrap_or(".");
    output.targets.extend(
        context
            .diagnostics
            .iter()
            .map(|item| diagnostic(item, fallback)),
    );
    // Frozen `load_compilation_context` orders units by (source, directory, configuration
    // digest of the directory, argv and output).
    let mut units: Vec<_> = context.units.iter().collect();
    units.sort_by_cached_key(|unit| {
        (
            unit.source.clone(),
            unit.directory.clone(),
            ici_domain::canonical_json(
                &json!({"directory":unit.directory,"argv":unit.argv,"output":unit.output}),
            )
            .map(ici_domain::sha256)
            .unwrap_or_default(),
        )
    });
    for unit in units {
        output.targets.push(target(
            &unit.source,
            "configuration",
            "PASS",
            "Compilation configuration was inspected.",
        ));
        output.targets.extend(
            unit.diagnostics
                .iter()
                .map(|item| diagnostic(item, &unit.source)),
        );
        for (rule, flags, present, message) in [
            (
                "required-flag",
                required_flags,
                false,
                "A required compiler flag is absent from this configuration.",
            ),
            (
                "forbidden-flag",
                forbidden_flags,
                true,
                "A forbidden compiler flag is present in this configuration.",
            ),
        ] {
            for flag in flags
                .iter()
                .filter(|flag| unit.argv.contains(flag) == present)
            {
                let item = target(&unit.source, rule, "FAIL", message);
                // The same policy finding identity as the native projection keeps the gate.
                finding(
                    &mut output,
                    request,
                    &item,
                    &format!("ici.compile-db.{rule}"),
                    "high",
                    false,
                );
                let policy = output
                    .observation
                    .findings
                    .last_mut()
                    .expect("new policy finding");
                policy.analysis_unit_id = Some(format!(
                    "{}.cpp:{}",
                    request.component_id, unit.configuration
                ));
                policy.variant = Some(unit.variant.clone());
                policy.fingerprint = ici_domain::sha256(format!(
                    "compile-policy:{}:{rule}:{}",
                    unit.configuration,
                    ici_domain::sha256(flag)
                ));
                output.targets.push(item);
            }
        }
    }
    let count = |status: &str| {
        output
            .targets
            .iter()
            .filter(|item| item.status == status)
            .count()
    };
    let (failures, warnings) = (count("FAIL"), count("WARN"));
    let covered = production
        .iter()
        .filter(|source| configurations(source) > 0)
        .count();
    let coverage = if production.is_empty() {
        100.0
    } else {
        100.0 * covered as f64 / production.len() as f64
    };
    output.status = if failures > 0 {
        "FAIL"
    } else if warnings > 0 {
        "WARN"
    } else {
        "PASS"
    }
    .into();
    output.summary = format!(
        "Compilation DB: {covered}/{} production units covered, {} configurations, {failures} failures, {warnings} warnings.",
        production.len(),
        context.units.len()
    );
    output.score = Some(coverage);
    output.max_score = Some(100.0);
    measurement(
        &mut output,
        "compilation_units",
        context.units.len() as u64,
        false,
    );
    measurement(
        &mut output,
        "uncovered_sources",
        (production.len() - covered) as u64,
        false,
    );
    output.extra = json!({"context":context,"database_path":context.database_path,"production_units":production.len(),"covered_units":covered,"configurations":context.units.len(),"issues_count":failures + warnings,"coverage_percent":coverage});
    output
}
/// Frozen `compile_db` text of a native context diagnostic: the row-shape and unit codes have a
/// frozen sentence (an absent `directory` field and a gone working directory share
/// `missing-directory` but keep distinct native wording); other codes keep the native message.
pub fn legacy_diagnostic_message<'a>(code: &str, message: &'a str) -> &'a str {
    match code {
        "invalid-entry" => "A compilation database entry is not an object.",
        "missing-file" => "A compilation database entry has no file field.",
        "missing-directory" if message.contains("field") => {
            "A compilation database entry has no directory field."
        }
        "missing-directory" => "The compilation working directory does not exist.",
        "invalid-file" => "The compilation file field is not a string.",
        "invalid-directory" => "The compilation directory field is not a string.",
        "directory-outside-project" => "The compilation working directory is outside the project.",
        "source-outside-project" => "The compilation source resolves outside the project.",
        "invalid-source-path" => "The compilation source does not identify a file path.",
        "stale-source" => "The compilation source no longer exists.",
        _ => message,
    }
}
/// Frozen `compile_db._FAIL_DIAGNOSTICS`: context codes that fail regardless of their level.
const LEGACY_FAIL_DIAGNOSTICS: [&str; 37] = [
    "database-changed",
    "database-malformed",
    "database-missing",
    "database-not-array",
    "database-not-file",
    "database-outside-project",
    "database-too-large",
    "database-too-many-entries",
    "database-unreadable",
    "directory-outside-project",
    "foreign-path-syntax",
    "invalid-arguments",
    "invalid-command",
    "invalid-database-setting",
    "invalid-entry",
    "invalid-entry-value",
    "invalid-file",
    "invalid-path",
    "invalid-source-path",
    "missing-command",
    "missing-directory",
    "missing-file",
    "missing-flag-value",
    "missing-include-dir",
    "response-file-changed",
    "response-file-cycle",
    "response-file-depth",
    "response-file-invalid",
    "response-file-malformed",
    "response-file-missing",
    "response-file-not-file",
    "response-file-outside-project",
    "response-file-too-large",
    "response-file-unreadable",
    "source-mismatch",
    "source-outside-project",
    "stale-source",
];
fn string_options(options: &serde_json::Value, key: &str) -> Vec<String> {
    options[key]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(serde_json::Value::as_str)
        .map(str::to_owned)
        .collect()
}
/// The executable must have been resolved and approved by the application/tool resolver.
pub fn compiler_probe_task(
    root: &Path,
    unit: &CompilationUnit,
    compiler: &Path,
    purpose: ReplayPurpose,
    task_id: &str,
) -> Result<TaskSpec, CppError> {
    let replay = crate::replay_command(root, unit, compiler, purpose)?;
    let mut task = TaskSpec::command(
        task_id,
        "compiler",
        replay.argv,
        replay.cwd.display().to_string(),
    );
    task.env_overlay = crate::replay_environment();
    task.timeout_seconds = Some(120.0);
    task.output_limit_bytes = Some(if purpose == ReplayPurpose::AstJson {
        32 * 1024 * 1024
    } else {
        8 * 1024 * 1024
    });
    task.variant = Some(unit.variant.clone());
    task.input_refs = vec![unit.source.clone()];
    task.input_refs
        .extend(unit.response_files.iter().map(|input| input.path.clone()));
    task.tool_digest = Some(
        ici_execution::process::executable_digest(compiler)
            .map_err(|error| CppError::new("compiler-identity", error.to_string()))?,
    );
    Ok(task)
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CompilerFunctionBoundary {
    pub path: String,
    pub name: String,
    pub start_line: u32,
    pub end_line: u32,
    pub start_column: u32,
    pub end_column: u32,
    pub body_start_line: u32,
    pub body_start_column: u32,
    pub begin_byte: usize,
    pub end_byte: usize,
    pub body_begin_byte: usize,
    pub configuration: String,
    pub source_digest: String,
    pub function_kind: String,
    pub is_template: bool,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CompilerBoundaryEvidence {
    pub source: String,
    pub boundaries: Vec<CompilerFunctionBoundary>,
    pub macro_definitions_excluded: usize,
    pub lambda_definitions_excluded: usize,
    pub non_source_definitions_excluded: usize,
    pub source_digest: String,
    pub configuration: String,
}
fn literal_location(value: &Value) -> bool {
    value.is_object()
        && !value["spellingLoc"].is_object()
        && !value["expansionLoc"].is_object()
        && !value["includedFrom"].is_object()
}
fn offset(value: &Value) -> Option<usize> {
    value["offset"]
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
}
/// Consume only source-spelled compiler definitions whose byte geometry agrees with the exact
/// immutable source snapshot. Macro expansions, lambda call operators and implicit AST nodes stay out.
pub fn parse_clang_function_boundaries(
    transcript: &str,
    input: &SourceInput,
    absolute_source: &Path,
    configuration: &str,
) -> Result<CompilerBoundaryEvidence, CppError> {
    if transcript.len() > 32 * 1024 * 1024 || transcript.contains('\0') {
        return Err(CppError::new(
            "ast-output-bounds",
            "Compiler AST exceeds 32 MiB or contains null bytes",
        ));
    }
    let ast = strict_json(transcript.as_bytes())?;
    if ast["kind"] != "TranslationUnitDecl" {
        return Err(CppError::new(
            "ast-root",
            "Expected a Clang TranslationUnitDecl",
        ));
    }
    let file = ParsedSource::parse(input, None)?;
    let digest = ici_domain::sha256(&input.bytes);
    let nodes = source::nodes(file.tree.root_node())?;
    let mapped: BTreeMap<_, _> = nodes
        .iter()
        .filter(|node| node.kind() == "function_definition")
        .filter_map(|node| {
            node.child_by_field_name("body")
                .map(|body| ((body.start_byte(), body.end_byte()), (*node, body)))
        })
        .collect();
    let lambda_bodies: BTreeSet<_> = nodes
        .iter()
        .filter(|node| node.kind() == "lambda_expression")
        .filter_map(|node| {
            node.child_by_field_name("body")
                .map(|body| (body.start_byte(), body.end_byte()))
        })
        .collect();
    let mut output = CompilerBoundaryEvidence {
        source: input.path.clone(),
        source_digest: digest.clone(),
        configuration: configuration.into(),
        ..Default::default()
    };
    let mut pending = vec![(&ast, false, 0u32)];
    let mut count = 0;
    let mut seen = BTreeSet::new();
    while let Some((node, is_template, depth)) = pending.pop() {
        count += 1;
        if count > 1_000_000 || depth > 128 {
            return Err(CppError::new(
                "ast-shape-bounds",
                "Compiler AST exceeds node or nesting bound",
            ));
        }
        let kind = node["kind"].as_str().unwrap_or("");
        let is_template = is_template || kind == "FunctionTemplateDecl";
        if let Some(children) = node["inner"].as_array() {
            pending.extend(
                children
                    .iter()
                    .rev()
                    .map(|child| (child, is_template, depth + 1)),
            );
        }
        if !matches!(
            kind,
            "FunctionDecl"
                | "CXXMethodDecl"
                | "CXXConstructorDecl"
                | "CXXDestructorDecl"
                | "CXXConversionDecl"
        ) || node["isImplicit"] == true
        {
            continue;
        }
        let Some(body) = node["inner"].as_array().and_then(|children| {
            children
                .iter()
                .find(|child| matches!(child["kind"].as_str(), Some("CompoundStmt" | "CXXTryStmt")))
        }) else {
            continue;
        };
        let locations = [
            &node["loc"],
            &node["range"]["begin"],
            &node["range"]["end"],
            &body["range"]["begin"],
            &body["range"]["end"],
        ];
        if locations
            .iter()
            .any(|value| value["spellingLoc"].is_object() || value["expansionLoc"].is_object())
        {
            output.macro_definitions_excluded += 1;
            continue;
        }
        if locations.iter().any(|value| {
            !literal_location(value)
                || value["file"]
                    .as_str()
                    .is_some_and(|path| Path::new(path) != absolute_source)
        }) {
            output.non_source_definitions_excluded += 1;
            continue;
        }
        let Some(begin) = offset(&body["range"]["begin"]) else {
            return Err(CppError::new(
                "ast-body-location",
                "Compiler body lacks a byte offset",
            ));
        };
        let Some(end) = offset(&body["range"]["end"]).and_then(|offset| {
            offset.checked_add(body["range"]["end"]["tokLen"].as_u64().unwrap_or(1) as usize)
        }) else {
            return Err(CppError::new(
                "ast-body-location",
                "Compiler body end is invalid",
            ));
        };
        if lambda_bodies.contains(&(begin, end)) {
            output.lambda_definitions_excluded += 1;
            continue;
        }
        let Some((function, body)) = mapped.get(&(begin, end)) else {
            output.non_source_definitions_excluded += 1;
            continue;
        };
        let name = node["name"]
            .as_str()
            .ok_or_else(|| CppError::new("ast-function-name", "Compiler function lacks a name"))?;
        let spelled = source::function_name(&file, *function);
        if source::unqualified_name(&spelled) != name {
            return Err(CppError::new(
                "ast-source-mismatch",
                "Compiler name does not agree with immutable source declarator",
            ));
        }
        if !seen.insert((begin, end, name.to_owned())) {
            continue;
        }
        let target = file.target(*function, name);
        let body_target = file.target(*body, "");
        output.boundaries.push(CompilerFunctionBoundary {
            path: input.path.clone(),
            name: spelled,
            start_line: target.start_line,
            end_line: target.end_line.unwrap(),
            start_column: target.start_column.unwrap(),
            end_column: target.end_column.unwrap(),
            body_start_line: body_target.start_line,
            body_start_column: body_target.start_column.unwrap(),
            begin_byte: function.start_byte(),
            end_byte: function.end_byte(),
            body_begin_byte: begin,
            configuration: configuration.into(),
            source_digest: digest.clone(),
            function_kind: kind.into(),
            is_template,
        });
    }
    output.boundaries.sort_by_key(|boundary| {
        (
            boundary.begin_byte,
            boundary.end_byte,
            boundary.name.clone(),
        )
    });
    Ok(output)
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CompilerConfigurationObservation {
    pub source: String,
    pub configuration: String,
    pub complete: bool,
    pub findings: Vec<Finding>,
}
/// Internal-linkage unused diagnostics must agree across every captured configuration for a source.
/// This fact has no whole-program or linker reachability implication.
pub fn unused_function_consensus(
    request: &AnalysisRequest,
    context: &CompilationContext,
    observations: &[CompilerConfigurationObservation],
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "compiler.unused-function");
    output.observation.state = TaskState::Succeeded;
    if !context.complete() {
        fail(
            &mut output,
            ".",
            "incomplete-compilation-context",
            "Unused-function consensus requires complete compile evidence",
        );
        return output;
    }
    if context.unity_build {
        fail(
            &mut output,
            ".",
            "unity-unused-unsupported",
            "Internal unused-function ownership is not established through a unity build; use target-local GNU ELF evidence or separate source translation units",
        );
        return output;
    }
    let selected: BTreeSet<_> = request
        .inputs
        .iter()
        .filter(|input| crate::is_translation_unit(&input.path))
        .filter(|input| source::policy_exclusions(request, &input.path).is_empty())
        .map(|input| input.path.clone())
        .collect();
    let excluded_sources = source::record_policy_exclusions(request, &mut output);
    let mut configurations: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for unit in &context.units {
        if selected.contains(&unit.source) {
            configurations
                .entry(unit.source.clone())
                .or_default()
                .insert(unit.configuration.clone());
        }
    }
    for path in &selected {
        if !configurations.contains_key(path) {
            fail(
                &mut output,
                path,
                "source-configuration-missing",
                "Selected source lacks a captured configuration",
            );
        }
    }
    if selected.is_empty() {
        if !excluded_sources.is_empty() {
            output.status = "SKIP".into();
            output.summary =
                "All selected translation units were excluded by source ownership policy".into();
            output.extra = json!({"mode":"compiler-unused-internal","sources_checked":0,"excluded_source_counts":excluded_sources,"evidence":"NOT_APPLICABLE"});
            let mut metric = ici_domain::Measurement::measured("sources_checked", 0.0);
            metric.evidence = ici_domain::EvidenceLevel::NotApplicable;
            output.observation.measurements.push(metric);
            return output;
        }
        fail(
            &mut output,
            ".",
            "empty-production-scope",
            "No selected owned production translation units",
        );
        return output;
    }
    let mut excluded = 0;
    for (path, expected) in &configurations {
        let actual: Vec<_> = observations
            .iter()
            .filter(|observation| &observation.source == path)
            .collect();
        let observed: BTreeSet<_> = actual
            .iter()
            .map(|observation| observation.configuration.clone())
            .collect();
        if actual.len() != expected.len()
            || &observed != expected
            || actual.iter().any(|observation| !observation.complete)
        {
            fail(
                &mut output,
                path,
                "incomplete-unused-probe",
                "Every selected compile configuration must finish once before unused diagnostics can be compared",
            );
            continue;
        }
        type Key = (u32, Option<u32>, Option<u32>, Option<u32>);
        let mut intersection: Option<BTreeMap<Key, Finding>> = None;
        for observation in actual {
            let rows: BTreeMap<_, _> = observation
                .findings
                .iter()
                .filter_map(|item| {
                    if item.primary_location.path != *path {
                        excluded += 1;
                        return None;
                    }
                    if item.native_rule_id != "-Wunused-function"
                        && !item.rule_id.ends_with("unused-function")
                    {
                        return None;
                    }
                    let span = &item.primary_location;
                    Some((
                        (
                            span.start_line,
                            span.start_column,
                            span.end_line,
                            span.end_column,
                        ),
                        item.clone(),
                    ))
                })
                .collect();
            if let Some(previous) = intersection.as_mut() {
                previous.retain(|key, _| rows.contains_key(key));
            } else {
                intersection = Some(rows);
            }
        }
        let rows = intersection.unwrap_or_default();
        if rows.is_empty() {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "UnusedInternalFunctions".into();
            target.message = format!(
                "No unused internal function diagnostic agreed across {} configurations",
                expected.len()
            );
            output.targets.push(target);
        }
        for (_, item) in rows {
            let mut target = InspectionTarget::at(path, item.primary_location.start_line);
            target.start_column = item.primary_location.start_column;
            target.end_line = item.primary_location.end_line;
            target.end_column = item.primary_location.end_column;
            target.target_name = "UnusedInternalFunction".into();
            target.status = "WARN".into();
            target.message = item.message;
            target
                .metrics
                .insert("configurations".into(), json!(expected));
            finding(
                &mut output,
                request,
                &target,
                "dead.cpp-unused-internal-function",
                "medium",
                false,
            );
            output
                .observation
                .findings
                .last_mut()
                .unwrap()
                .native_rule_id = "-Wunused-function".into();
            output.targets.push(target);
        }
    }
    if output.observation.state == TaskState::Succeeded {
        output.status = if output.observation.findings.is_empty() {
            "PASS"
        } else {
            "WARN"
        }
        .into();
    }
    measurement(
        &mut output,
        "configurations_checked",
        observations.len() as u64,
        false,
    );
    output.extra = json!({"mode":"compiler-unused-internal","sources_checked":configurations.len(),"non_tu_diagnostics_excluded":excluded,"excluded_source_counts":excluded_sources});
    output.observation.limitations.push("Compiler unused-function diagnostics establish internal-linkage source facts; they do not establish whole-program reachability".into());
    output
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IncludeEdge {
    pub from: String,
    pub to: String,
    pub from_scope: String,
    pub to_scope: String,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct IncludeTrace {
    pub edges: Vec<IncludeEdge>,
    pub unexpected: Vec<String>,
}
impl IncludeTrace {
    pub fn complete(&self) -> bool {
        self.unexpected.is_empty()
    }
}
pub fn parse_include_trace(
    root: &Path,
    cwd: &Path,
    source: &Path,
    stderr: &str,
) -> Result<IncludeTrace, CppError> {
    if stderr.len() > 8 * 1024 * 1024 || stderr.contains('\0') {
        return Err(CppError::new(
            "include-trace-bounds",
            "Compiler include trace exceeds bounded text contract",
        ));
    }
    let root = root
        .canonicalize()
        .map_err(|error| CppError::new("include-root", error.to_string()))?;
    let (_, _, source) = scoped(&root, cwd, &source.display().to_string())?;
    let mut stack: Vec<Option<PathBuf>> = vec![Some(source)];
    let mut trace = IncludeTrace::default();
    let mut trailer = false;
    let mut count = 0;
    for raw in stderr.lines() {
        let line = raw.trim_end_matches('\r');
        if line.is_empty() {
            continue;
        }
        if line == "Multiple include guards may be useful for:" {
            if trailer {
                trace.unexpected.push(line.into());
            }
            trailer = true;
            continue;
        }
        count += 1;
        if count > 200_000 {
            return Err(CppError::new(
                "include-trace-limit",
                "Compiler include trace exceeds 200000 entries",
            ));
        }
        if trailer {
            let (_, _, physical) = scoped(&root, cwd, line)?;
            if !physical.is_file() {
                trace.unexpected.push(line.into());
            }
            continue;
        }
        let depth = line.bytes().take_while(|byte| *byte == b'.').count();
        if depth == 0 || line.as_bytes().get(depth) != Some(&b' ') {
            trace.unexpected.push(line.into());
            continue;
        }
        if depth > 4096 {
            return Err(CppError::new(
                "include-trace-limit",
                "Compiler include trace exceeds 4096 levels",
            ));
        }
        if depth > stack.len() {
            return Err(CppError::new(
                "include-trace-depth",
                "Compiler include trace jumps over a parent depth",
            ));
        }
        let raw = &line[depth + 1..];
        if raw.starts_with('<') && raw.ends_with('>') {
            stack.truncate(depth);
            stack.push(None);
            continue;
        }
        let (child, child_scope, physical) = scoped(&root, cwd, raw)?;
        if !physical.is_file() {
            return Err(CppError::new(
                "include-trace-stale",
                "Compiler include trace references a missing regular file",
            ));
        }
        if child_scope == "project" {
            read_contained(&root, &physical, 16 * 1024 * 1024)?;
        }
        if let Some(parent) = &stack[depth - 1] {
            let (parent, parent_scope, _) = scoped(&root, cwd, &parent.display().to_string())?;
            trace.edges.push(IncludeEdge {
                from: parent,
                to: child,
                from_scope: parent_scope,
                to_scope: child_scope,
            });
        }
        stack.truncate(depth);
        stack.push(Some(physical));
    }
    Ok(trace)
}

/// Annotate source metrics with independently verified compiler boundaries. The lexical metric
/// remains estimated even when boundary geometry is compiler-confirmed.
pub fn apply_compiler_boundaries(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    context: &CompilationContext,
    evidence: &[CompilerBoundaryEvidence],
) -> Result<(), CppError> {
    let policy = request.options["cpp_boundaries"].as_str().unwrap_or("auto");
    if !["auto", "off", "required"].contains(&policy) {
        return Err(CppError::new(
            "invalid-boundary-policy",
            "cpp_boundaries must be auto, off or required",
        ));
    }
    if policy == "off" {
        return Ok(());
    }
    let inputs: BTreeMap<_, _> = request
        .inputs
        .iter()
        .map(|input| (input.path.as_str(), input))
        .collect();
    let expected: BTreeSet<_> = context
        .units
        .iter()
        .filter(|unit| inputs.contains_key(unit.source.as_str()))
        .map(|unit| (unit.source.clone(), unit.configuration.clone()))
        .collect();
    let mut observed = BTreeSet::new();
    let mut confirmed: BTreeMap<(String, u32, u32, String), BTreeSet<String>> = BTreeMap::new();
    for fact in evidence {
        let input = inputs.get(fact.source.as_str()).ok_or_else(|| {
            CppError::new(
                "boundary-source-unselected",
                "Compiler boundaries reference an unselected source",
            )
        })?;
        if ici_domain::sha256(&input.bytes) != fact.source_digest {
            return Err(CppError::new(
                "boundary-source-drift",
                "Compiler boundaries do not match immutable source identity",
            ));
        }
        let key = (fact.source.clone(), fact.configuration.clone());
        if !expected.contains(&key) || !observed.insert(key) {
            return Err(CppError::new(
                "boundary-configuration-mismatch",
                "Compiler boundary configurations are duplicated or not captured",
            ));
        }
        for boundary in &fact.boundaries {
            if boundary.path != fact.source
                || boundary.configuration != fact.configuration
                || boundary.source_digest != fact.source_digest
            {
                return Err(CppError::new(
                    "boundary-evidence-mismatch",
                    "Compiler boundary does not agree with its source/configuration envelope",
                ));
            }
            confirmed
                .entry((
                    boundary.path.clone(),
                    boundary.start_line,
                    boundary.end_line,
                    boundary.name.clone(),
                ))
                .or_default()
                .insert(boundary.configuration.clone());
        }
    }
    let mut exact = 0;
    let mut estimated = 0;
    for target in &mut output.targets {
        if !target.metrics.contains_key("boundary_source") {
            continue;
        }
        let key = |name: &str| {
            (
                target.file_path.clone(),
                target.start_line,
                target.end_line.unwrap_or(target.start_line),
                name.to_owned(),
            )
        };
        // Legacy complexity labels append the frozen "()" display suffix to the source name.
        let found = confirmed.get(&key(&target.target_name)).or_else(|| {
            target
                .target_name
                .strip_suffix("()")
                .and_then(|name| confirmed.get(&key(name)))
        });
        if let Some(configurations) = found {
            let required: BTreeSet<_> = expected
                .iter()
                .filter(|(source, _)| source == &target.file_path)
                .map(|(_, configuration)| configuration.clone())
                .collect();
            let consistent = !required.is_empty() && configurations == &required;
            target.metrics.insert(
                "boundary_source".into(),
                json!(if consistent {
                    "compiler-ast"
                } else {
                    "compiler-ast-partial"
                }),
            );
            target.metrics.insert(
                "boundary_confidence".into(),
                json!(if consistent { "exact" } else { "medium" }),
            );
            target
                .metrics
                .insert("configurations".into(), json!(configurations));
            if consistent {
                exact += 1;
            } else {
                estimated += 1;
                target
                    .metrics
                    .insert("configuration_dependent".into(), json!(true));
                output.observation.limitations.push(format!(
                    "{}:{}: configuration-dependent function boundary was not promoted",
                    target.file_path, target.start_line
                ));
            }
        } else {
            estimated += 1;
        }
    }
    let complete = context.complete() && observed == expected && !expected.is_empty();
    let mode = if !complete {
        "partial"
    } else if exact > 0 && estimated > 0 {
        "mixed"
    } else if exact > 0 {
        "exact"
    } else {
        "heuristic"
    };
    output.extra["boundary_mode"] = json!(mode);
    output.extra["cpp_boundary_mode"] = json!(mode);
    output.extra["exact_boundaries"] = json!(exact);
    output.extra["estimated_boundaries"] = json!(estimated);
    output.extra["configurations_checked"] = json!(observed.len());
    if policy == "required" && (!complete || estimated > 0) {
        return Err(CppError::new(
            "compiler-boundaries-required",
            "Every selected source function requires complete compiler-backed configuration coverage",
        ));
    }
    if !complete {
        output
            .observation
            .limitations
            .push("Compiler boundary evidence is partial across selected configurations".into());
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CompilerIncludeObservation {
    pub source: String,
    pub configuration: String,
    pub complete: bool,
    pub trace: IncludeTrace,
}
/// Analyze each observed configuration graph separately. Combining incompatible variants can
/// invent cycles that no compiler invocation observed.
pub fn analyze_compiler_include_cycles(
    request: &AnalysisRequest,
    context: &CompilationContext,
    observations: &[CompilerIncludeObservation],
) -> AnalysisOutput {
    analyze_compiler_include_cycles_with_unresolved(request, context, observations, &[])
}

/// A translation-unit configuration whose include trace stopped at located missing headers:
/// frozen `_evaluate_trace` keeps it as checked with no dependency edge and its
/// `CppIncludeUnresolved` targets (legacy `ici verify`/`ici cycle` only).
pub struct UnresolvedIncludeTrace {
    pub source: String,
    pub configuration: String,
    pub targets: Vec<InspectionTarget>,
}

pub fn analyze_compiler_include_cycles_with_unresolved(
    request: &AnalysisRequest,
    context: &CompilationContext,
    observations: &[CompilerIncludeObservation],
    unresolved: &[UnresolvedIncludeTrace],
) -> AnalysisOutput {
    let legacy = request.options["legacy_projection"].as_bool() == Some(true);
    let mut output = AnalysisOutput::new(&request.task_id, "compiler.includes");
    output.observation.state = TaskState::Succeeded;
    let limit = match crate::cycles::report_limit(request) {
        Ok(limit) => limit,
        Err(error) => {
            fail(&mut output, ".", &error.code, &error.message);
            return output;
        }
    };
    if !context.complete() {
        fail(
            &mut output,
            ".",
            "include-context-incomplete",
            "Compiler include cycles require complete compile evidence",
        );
        return output;
    }
    let known: BTreeSet<_> = request
        .inputs
        .iter()
        .map(|input| input.path.clone())
        .collect();
    let expected: BTreeSet<_> = context
        .units
        .iter()
        .filter(|unit| context.unity_build || known.contains(&unit.source))
        .map(|unit| (unit.source.clone(), unit.configuration.clone()))
        .collect();
    let mut actual = BTreeSet::new();
    let mut graphs: BTreeMap<String, BTreeMap<String, BTreeSet<String>>> = BTreeMap::new();
    let mut scope_counts: BTreeMap<&str, u64> = BTreeMap::new();
    let mut cycles: BTreeMap<Vec<String>, BTreeSet<String>> = BTreeMap::new();
    let mut resolved = 0;
    for observation in observations {
        if !actual.insert((
            observation.source.clone(),
            observation.configuration.clone(),
        )) || !expected.contains(&(
            observation.source.clone(),
            observation.configuration.clone(),
        )) {
            fail(
                &mut output,
                &observation.source,
                "include-configuration-mismatch",
                "Compiler include observation is duplicated or not captured",
            );
            continue;
        }
        if !observation.complete || !observation.trace.complete() {
            fail(
                &mut output,
                &observation.source,
                "include-trace-incomplete",
                "Compiler include trace has unexpected output or did not finish",
            );
            continue;
        }
        let unit = context
            .units
            .iter()
            .find(|unit| {
                unit.source == observation.source && unit.configuration == observation.configuration
            })
            .unwrap();
        let graph = graphs.entry(observation.configuration.clone()).or_default();
        for path in &known {
            graph.entry(path.clone()).or_default();
        }
        let mut seen = BTreeSet::new();
        for edge in &observation.trace.edges {
            if edge.from == edge.to || !seen.insert((&edge.from, &edge.to)) {
                continue;
            }
            resolved += 1;
            let scope =
                if edge.to_scope == "project" {
                    if source::is_excluded(&edge.to).is_some() {
                        "third_party"
                    } else if known.contains(&edge.to) {
                        "project"
                    } else {
                        "generated"
                    }
                } else if unit.include_paths.iter().any(|path| {
                    path.kind != "system" && Path::new(&edge.to).starts_with(&path.path)
                }) {
                    "third_party"
                } else {
                    "system"
                };
            *scope_counts.entry(scope).or_default() += 1;
            if known.contains(&edge.from) && known.contains(&edge.to) {
                graph
                    .entry(edge.from.clone())
                    .or_default()
                    .insert(edge.to.clone());
            }
        }
        let mut target = InspectionTarget::at(&observation.source, 1);
        target.target_name = "CppIncludeTrace".into();
        target.message =
            "Compiler include tracing completed for this translation-unit configuration.".into();
        target.metrics = BTreeMap::from([
            ("configuration".into(), json!(observation.configuration)),
            ("resolved_edges".into(), json!(seen.len())),
            ("resolution".into(), json!("compiler_trace")),
        ]);
        output.targets.push(target);
    }
    let mut unresolved_targets = vec![];
    for trace in unresolved {
        if !actual.insert((trace.source.clone(), trace.configuration.clone()))
            || !expected.contains(&(trace.source.clone(), trace.configuration.clone()))
        {
            fail(
                &mut output,
                &trace.source,
                "include-configuration-mismatch",
                "Compiler include observation is duplicated or not captured",
            );
            continue;
        }
        unresolved_targets.extend(trace.targets.iter().cloned());
    }
    let unresolved_count = unresolved_targets.len();
    unresolved_targets.truncate(limit);
    output.targets.extend(unresolved_targets);
    if actual != expected || expected.is_empty() {
        fail(
            &mut output,
            ".",
            "include-configuration-missing",
            "Every selected translation-unit configuration requires an observed include trace",
        );
    }
    for (configuration, graph) in &graphs {
        for cycle in crate::cycles::components(graph) {
            cycles
                .entry(cycle)
                .or_default()
                .insert(configuration.clone());
        }
    }
    for (members, configurations) in cycles.iter().take(limit) {
        if legacy {
            // Frozen `_append_cpp_cycle_targets`: the actual cycle path from the smallest
            // member in the first configuration graph that has it, named by file names.
            let graph = configurations
                .iter()
                .next()
                .and_then(|configuration| graphs.get(configuration))
                .expect("cycle configuration graph");
            let path = crate::cycles::actual_cycle_path(&members[0], members, graph);
            let mut names: Vec<_> = path
                .iter()
                .map(|path| path.rsplit('/').next().unwrap_or(path))
                .collect();
            names.push(names[0]);
            let mut target = InspectionTarget::at(&path[0], 1);
            target.target_name = format!("CppCycle:{}", path.len());
            target.status = "WARN".into();
            target.message = format!(
                "C++ include cycle ({} files): {}",
                path.len(),
                names.join(" -> ")
            );
            target.metrics.insert("files".into(), json!(path));
            target
                .metrics
                .insert("configurations".into(), json!(configurations));
            finding(
                &mut output,
                request,
                &target,
                "cycle.include",
                "medium",
                false,
            );
            output.targets.push(target);
            continue;
        }
        let mut target = InspectionTarget::at(&members[0], 1);
        target.target_name = "IncludeCycle".into();
        target.status = "WARN".into();
        target.message = format!(
            "compiler-observed include cycle: {} -> {}",
            members.join(" -> "),
            members[0]
        );
        target
            .metrics
            .insert("configurations".into(), json!(configurations));
        finding(
            &mut output,
            request,
            &target,
            "cycle.include",
            "medium",
            false,
        );
        output
            .observation
            .findings
            .last_mut()
            .unwrap()
            .related_locations = members
            .iter()
            .skip(1)
            .map(|path| ici_domain::SourceSpan::line(path, 1))
            .collect();
        output.targets.push(target);
    }
    if output.observation.state == TaskState::Succeeded {
        output.status = if cycles.is_empty() { "PASS" } else { "WARN" }.into();
        // Frozen `CycleEngine` wording for a completed compiler-traced include graph.
        output.summary = if cycles.is_empty() {
            "No cyclic dependencies detected".into()
        } else {
            format!("Dependency cycles: {} found", cycles.len())
        };
        if unresolved_count > 0 {
            output.status = "WARN".into();
            output.summary.push_str(&format!(
                "; C++ include graph incomplete: 0 ambiguous, {unresolved_count} unresolved"
            ));
        }
    }
    measurement(&mut output, "cycles_found", cycles.len() as u64, false);
    output.extra = json!({"resolution":"compiler_trace","configuration_graphs":graphs,"scope_counts":scope_counts,"resolved_edges":resolved,"configurations_checked":actual.len(),"cycles_reported":cycles.len().min(limit),"cycles_truncated":cycles.len().saturating_sub(limit),"unresolved_cpp_includes":unresolved_count,"include_diagnostics_truncated":unresolved_count.saturating_sub(limit)});
    if legacy {
        // Frozen `CycleEngine` names each trace process "compiler include trace"; adapted legacy
        // findings take their tool name from the first one. Only the observed compiler path is
        // recorded: the trace argv and return code are not carried by the observation.
        let tools: Vec<_> = context
            .units
            .iter()
            .filter(|unit| actual.contains(&(unit.source.clone(), unit.configuration.clone())))
            .map(|unit| json!({"name":"compiler include trace","path":unit.compiler}))
            .collect();
        output.extra["tool_evidence"] = json!(tools);
    }
    output
}

/// Close aggregate/unity source coverage only after every recorded TU/configuration has an
/// observed complete compiler trace. A filename containing `unity_` is never coverage proof.
pub fn confirm_compilation_coverage(
    context: &CompilationContext,
    observations: &[CompilerIncludeObservation],
) -> Result<CompilationContext, CppError> {
    let expected: BTreeSet<_> = context
        .units
        .iter()
        .map(|unit| (unit.source.clone(), unit.configuration.clone()))
        .collect();
    let actual: BTreeSet<_> = observations
        .iter()
        .map(|observation| {
            (
                observation.source.clone(),
                observation.configuration.clone(),
            )
        })
        .collect();
    if actual != expected
        || observations.len() != expected.len()
        || observations
            .iter()
            .any(|observation| !observation.complete || !observation.trace.complete())
    {
        return Err(CppError::new(
            "coverage-probes-incomplete",
            "Every recorded translation-unit configuration requires a complete include trace before aggregate source coverage is confirmed",
        ));
    }
    let mut result = context.clone();
    for observation in observations {
        for path in std::iter::once(&observation.source).chain(
            observation
                .trace
                .edges
                .iter()
                .filter(|edge| edge.to_scope == "project")
                .map(|edge| &edge.to),
        ) {
            if crate::is_translation_unit(path) {
                result
                    .source_coverage
                    .entry(path.clone())
                    .or_default()
                    .push(observation.configuration.clone());
            }
        }
    }
    for configurations in result.source_coverage.values_mut() {
        configurations.sort();
        configurations.dedup();
    }
    result
        .uncovered_sources
        .retain(|source| !result.source_coverage.contains_key(source));
    Ok(result)
}
