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
    measurement(
        &mut output,
        "compilation_units",
        context.units.len() as u64,
        false,
    );
    measurement(
        &mut output,
        "uncovered_sources",
        context.uncovered_sources.len() as u64,
        false,
    );
    output.extra = json!({"context":context});
    output.summary = format!(
        "{} compile configurations, {} uncovered sources",
        context.units.len(),
        context.uncovered_sources.len()
    );
    output
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
        if spelled.rsplit("::").next().unwrap_or(&spelled) != name {
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
    let selected: BTreeSet<_> = request
        .inputs
        .iter()
        .filter(|input| crate::is_translation_unit(&input.path))
        .filter(|input| source::is_excluded(&input.path).is_none())
        .map(|input| input.path.clone())
        .collect();
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
    output.extra = json!({"mode":"compiler-unused-internal","sources_checked":configurations.len(),"non_tu_diagnostics_excluded":excluded});
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
        if depth > 4096 || depth > stack.len() {
            return Err(CppError::new(
                "include-trace-depth",
                "Compiler include trace jumps over a parent or exceeds 4096 levels",
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
        let key = (
            target.file_path.clone(),
            target.start_line,
            target.end_line.unwrap_or(target.start_line),
            target.target_name.clone(),
        );
        if let Some(configurations) = confirmed.get(&key) {
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
    let mut output = AnalysisOutput::new(&request.task_id, "compiler.includes");
    output.observation.state = TaskState::Succeeded;
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
        .filter(|unit| known.contains(&unit.source))
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
            "Compiler include tracing completed for this translation-unit configuration".into();
        target.metrics = BTreeMap::from([
            ("configuration".into(), json!(observation.configuration)),
            ("resolved_edges".into(), json!(seen.len())),
            ("resolution".into(), json!("compiler_trace")),
        ]);
        output.targets.push(target);
    }
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
    for (members, configurations) in &cycles {
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
    }
    measurement(&mut output, "cycles_found", cycles.len() as u64, false);
    output.extra = json!({"resolution":"compiler_trace","configuration_graphs":graphs,"scope_counts":scope_counts,"resolved_edges":resolved,"configurations_checked":actual.len()});
    output
}
