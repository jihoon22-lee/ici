use crate::{
    CppError,
    source::{self, ParsedSource},
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    SourceSpan, TaskState,
};
use serde_json::json;
use std::{
    collections::BTreeMap,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

#[derive(Default)]
pub struct CppAnalysisSession {
    parsed: BTreeMap<String, Arc<ParsedSource>>,
    weight: usize,
    invocations: usize,
}
impl CppAnalysisSession {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn parser_invocations(&self) -> usize {
        self.invocations
    }
    pub fn analyze(&mut self, request: &AnalysisRequest) -> AnalysisOutput {
        self.inner(request, None, None)
    }
    pub fn analyze_cancellable(
        &mut self,
        request: &AnalysisRequest,
        cancellation: &AtomicBool,
    ) -> AnalysisOutput {
        self.inner(request, Some(cancellation), None)
    }
    pub fn analyze_with_compiler_context(
        &mut self,
        request: &AnalysisRequest,
        context: &crate::CompilationContext,
        evidence: &[crate::CompilerBoundaryEvidence],
    ) -> AnalysisOutput {
        self.inner(request, None, Some((context, evidence)))
    }
    pub fn analyze_with_compiler_context_cancellable(
        &mut self,
        request: &AnalysisRequest,
        context: &crate::CompilationContext,
        evidence: &[crate::CompilerBoundaryEvidence],
        cancellation: &AtomicBool,
    ) -> AnalysisOutput {
        self.inner(request, Some(cancellation), Some((context, evidence)))
    }
    fn inner(
        &mut self,
        request: &AnalysisRequest,
        cancellation: Option<&AtomicBool>,
        compiler: Option<(
            &crate::CompilationContext,
            &[crate::CompilerBoundaryEvidence],
        )>,
    ) -> AnalysisOutput {
        let kind = request.check_id.rsplit('.').next().unwrap_or("");
        let provider = match kind {
            "line" => "ici.line",
            "complexity" | "cognitive" => "ici.metrics",
            "cycle" => "ici.cycles",
            "dup" => "ici.dup",
            "exception" => "ici.hygiene",
            _ => "ici.cpp",
        };
        let mut output = AnalysisOutput::new(&request.task_id, provider);
        if request.language != "cpp" || request.inputs.is_empty() {
            fail(
                &mut output,
                ".",
                "invalid-scope",
                "C++ analysis requires selected C++ sources",
            );
            return output;
        }
        // Frozen DuplicateEngine reads its whole bounded inventory first
        // (`read_analysis_sources`) and reports only the first intake fault.
        let mut legacy_dup_inventory = None;
        if kind == "dup" && request.options["legacy_projection"].as_bool() == Some(true) {
            let inventory = match crate::legacy_source_inventory(request) {
                Ok(inventory) => inventory,
                Err(error) => {
                    let mut output = crate::legacy_intake_output(output, &error);
                    legacy_dup_evidence(&mut output, &crate::LegacyInventory::default());
                    return output;
                }
            };
            legacy_dup_evidence(&mut output, &inventory);
            let owned: Vec<_> = request
                .inputs
                .iter()
                .filter(|input| source::policy_exclusions(request, &input.path).is_empty())
                .collect();
            if owned.is_empty() {
                // Frozen dup.py:121: no owned source is one `DuplicateCode` SKIP at the root.
                let mut target = InspectionTarget::at(".", 1);
                target.target_name = "DuplicateCode".into();
                target.status = "SKIP".into();
                target.message =
                    "All selected sources were excluded by the generated/vendor policy".into();
                output.targets = vec![target];
                output.status = "SKIP".into();
                output.summary = "Duplicate analysis skipped: no owned source files".into();
                let mut measured = Measurement::measured("owned_sources", 0.0);
                measured.evidence = EvidenceLevel::NotApplicable;
                output.observation.measurements.push(measured);
                return output;
            }
            // Frozen dup.py:149 `_load_and_index_files`: the bounded lexical normalization of
            // every owned source in path order precedes any region or comparison work.
            legacy_dup_inventory = Some(inventory);
            if let Some((path, message)) = crate::duplicates::legacy_normalization_error(&owned) {
                return crate::legacy_intake::legacy_failure_output(
                    output,
                    &path,
                    "SourceTokenizationError",
                    &message,
                    &format!("Duplicate analysis could not normalize {path}"),
                );
            }
        } else if (kind != "line" && request.inputs.len() > 2048)
            || request.inputs.iter().map(|i| i.bytes.len()).sum::<usize>()
                > if kind == "line" {
                    512 * 1024 * 1024
                } else {
                    64 * 1024 * 1024
                }
        {
            fail(
                &mut output,
                ".",
                "inventory-limit",
                if kind == "line" {
                    "C++ line snapshot exceeds 512 MiB"
                } else {
                    "C++ rule inventory exceeds 2048 files or 64 MiB"
                },
            );
            return output;
        }
        let mut files = vec![];
        let mut line_files = vec![];
        let mut source_recovery = false;
        // Frozen legacy C++ complexity/cognitive engines read every source strictly as UTF-8
        // first (`read_cpp_source_text`); an unreadable source stops the metric before any
        // function is measured.
        let legacy_metric = matches!(kind, "complexity" | "cognitive")
            && request.options["legacy_projection"].as_bool() == Some(true);
        // Frozen next metrics (`languages/metrics.py::_scan_cpp`) scan every planned file with the
        // brace heuristic; only an explicit compiler-boundary run keeps the concrete syntax tree.
        let next_heuristic =
            matches!(kind, "complexity" | "cognitive") && !legacy_metric && compiler.is_none();
        let mut next_inputs: Vec<(&str, &[u8])> = vec![];
        let mut legacy_sources: Vec<crate::heuristic_metrics::LegacySource> = vec![];
        let mut legacy_errors = vec![];
        let mut legacy_failed = false;
        let mut excluded_counts = BTreeMap::<String, usize>::new();
        let mut paths = std::collections::BTreeSet::new();
        for input in &request.inputs {
            if cancelled(&mut output, cancellation) {
                return output;
            }
            if input.path.is_empty()
                || input.path.starts_with('/')
                || input.path.split('/').any(|p| matches!(p, ".." | "." | ""))
                || input.path.contains(['\\', '\0'])
                || !paths.insert(&input.path)
            {
                fail(
                    &mut output,
                    &input.path,
                    "invalid-source-path",
                    "Source paths must be unique project-relative paths",
                );
                continue;
            }
            if next_heuristic {
                next_inputs.push((input.path.as_str(), input.bytes.as_slice()));
                continue;
            }
            if legacy_metric {
                // Frozen `project_cpp_sources()` (`.cpp`/`.cc`/`.cxx`/`.c`) read strictly as
                // UTF-8 within the 8 MiB `read_cpp_source_text` bound; headers are never read.
                if is_legacy_metric_source(&input.path) {
                    let text = (input.bytes.len() <= crate::legacy_intake::LEGACY_MAX_FILE_BYTES)
                        .then(|| std::str::from_utf8(&input.bytes).ok().map(str::to_owned))
                        .flatten();
                    legacy_sources.push((input.path.clone(), text));
                }
                continue;
            }
            if kind == "line" {
                if input.bytes.len() > 16 * 1024 * 1024 {
                    fail(
                        &mut output,
                        &input.path,
                        "source-too-large",
                        "C++ source exceeds 16 MiB",
                    );
                    continue;
                }
                match std::str::from_utf8(&input.bytes) {
                    Ok(text) if !text.contains('\0') => {
                        line_files.push((input.path.as_str(), text))
                    }
                    _ => fail(
                        &mut output,
                        &input.path,
                        "source-encoding",
                        "C++ line source must be UTF-8 without null bytes",
                    ),
                }
                continue;
            }
            let legacy = request.options["legacy_projection"].as_bool() == Some(true);
            // Frozen next `measure_hygiene` reads the exception scope through
            // `read_analysis_sources`: its generated/vendor reasons, reported as limitations.
            let excluded = if kind == "dup" || (kind == "exception" && !legacy) {
                source::policy_exclusions(request, &input.path)
            } else if !matches!(kind, "line" | "complexity" | "cognitive" | "cycle") {
                source::is_excluded(&input.path).into_iter().collect()
            } else {
                vec![]
            };
            if !excluded.is_empty() {
                for reason in &excluded {
                    *excluded_counts.entry((*reason).to_owned()).or_default() += 1;
                }
                let reason = excluded.join(", ");
                output
                    .observation
                    .limitations
                    .push(format!("excluded {}: {reason}", input.path));
                // Frozen DuplicateEngine names no target for an excluded source.
                if !(kind == "dup" && request.options["legacy_projection"].as_bool() == Some(true))
                {
                    let mut target = InspectionTarget::at(&input.path, 1);
                    target.status = "SKIP".into();
                    target.target_name = "ExcludedAnalysisSource".into();
                    target.message = reason;
                    output.targets.push(target);
                }
                continue;
            }
            let key = ici_domain::sha256(
                [
                    source::PARSER_ID.as_bytes(),
                    input.path.as_bytes(),
                    &input.bytes,
                ]
                .concat(),
            );
            let file = if let Some(file) = self.parsed.get(&key) {
                Arc::clone(file)
            } else {
                match ParsedSource::parse(input, cancellation) {
                    Ok(file) => {
                        self.invocations += 1;
                        let file = Arc::new(file);
                        if self.weight + file.weight > 32 * 1024 * 1024 {
                            self.parsed.clear();
                            self.weight = 0;
                        }
                        if file.weight <= 32 * 1024 * 1024 {
                            self.parsed.insert(key, Arc::clone(&file));
                            self.weight += file.weight;
                        }
                        file
                    }
                    Err(error) => {
                        // Frozen ExceptionSafetyEngine reads each source with a strict UTF-8
                        // `read_text`: a decode failure is its `ReadError` with CPython's text.
                        if kind == "exception"
                            && request.options["legacy_projection"].as_bool() == Some(true)
                            && let Some(text) = ici_analysis::python_utf8_decode_error(&input.bytes)
                        {
                            fail(
                                &mut output,
                                &input.path,
                                "ReadError",
                                &format!("Could not read C++ source: {text}"),
                            );
                            continue;
                        }
                        fail(&mut output, &input.path, &error.code, &error.message);
                        continue;
                    }
                }
            };
            if !matches!(kind, "line" | "dup") && file.tree.root_node().has_error() {
                source_recovery = true;
                if compiler.is_some() && matches!(kind, "complexity" | "cognitive") {
                    output.observation.limitations.push(format!("{}: concrete source syntax has recovery regions; only compiler-confirmed source boundaries can establish complete geometry",file.path));
                } else {
                    fail(
                        &mut output,
                        &file.path,
                        "source-syntax",
                        "C++ source has concrete syntax recovery errors; any recovered source targets are partial evidence",
                    );
                }
            }
            if let Err(error) = source::nodes(file.tree.root_node()) {
                fail(&mut output, &file.path, &error.code, &error.message);
                continue;
            }
            files.push(file);
        }
        if cancelled(&mut output, cancellation) {
            return output;
        }
        if kind == "dup"
            && files.is_empty()
            && !excluded_counts.is_empty()
            && output.observation.state == TaskState::Succeeded
        {
            output.status = "SKIP".into();
            output.summary = "Duplicate analysis skipped: no owned source files".into();
            let mut measured = Measurement::measured("owned_sources", 0.0);
            measured.evidence = EvidenceLevel::NotApplicable;
            output.observation.measurements.push(measured);
            output.extra = json!({"excluded_source_counts":excluded_counts,"evidence":"NOT_APPLICABLE","parser_identity":source::PARSER_ID});
            return output;
        }
        let mut duplicate_status = None;
        let result = match kind {
            "line" => {
                crate::source_rules::lines(request, &mut output, &line_files, cancellation);
                Ok(())
            }
            "complexity" | "cognitive" if legacy_metric => {
                let outcome =
                    crate::heuristic_metrics::metrics(request, &mut output, &legacy_sources, kind);
                legacy_errors = outcome.errors;
                legacy_failed = outcome.failed;
                Ok(())
            }
            "complexity" | "cognitive" if next_heuristic => {
                crate::heuristic_metrics::next_metrics(request, &mut output, &next_inputs, kind);
                Ok(())
            }
            "complexity" | "cognitive" => {
                crate::source_rules::metrics(request, &mut output, &files, kind)
            }
            "exception" => crate::source_rules::exceptions(request, &mut output, &files),
            "cycle" => crate::cycles::analyze(request, &mut output, &files),
            "dup" => {
                let data = files
                    .iter()
                    .map(|file| crate::duplicates::file_data(file, cancellation))
                    .collect::<Result<Vec<_>, _>>();
                match data {
                    Ok(data) => {
                        let duplicate_output = if let Some(cancellation) = cancellation {
                            ici_analysis::duplicates::analyze_type2_cancellable(
                                request,
                                &data,
                                cancellation,
                            )
                        } else {
                            ici_analysis::duplicates::analyze_type2(request, &data)
                        };
                        output
                            .observation
                            .findings
                            .extend(duplicate_output.observation.findings);
                        let legacy = request.options["legacy_projection"]
                            .as_bool()
                            .unwrap_or(false);
                        // Frozen DuplicateEngine reports a completed scan as ESTIMATED evidence.
                        output.observation.measurements.extend(
                            duplicate_output.observation.measurements.into_iter().map(
                                |mut measurement| {
                                    if legacy {
                                        measurement.evidence = EvidenceLevel::Estimated;
                                    }
                                    measurement
                                },
                            ),
                        );
                        output
                            .observation
                            .limitations
                            .extend(duplicate_output.observation.limitations);
                        output.targets.extend(duplicate_output.targets);
                        output.extra = duplicate_output.extra;
                        if duplicate_output.observation.state != TaskState::Succeeded {
                            output.observation.state = duplicate_output.observation.state;
                            output.status = duplicate_output.status;
                            // Frozen dup.py:207: a bounded comparison stop is the
                            // `DuplicateComparisonLimit` ERROR with its fixed summary.
                            if legacy
                                && let [target] = output.targets.as_mut_slice()
                                && target.target_name == "DuplicateAnalysisError"
                                && target.message.contains("exceeds MAX_DUPLICATE_")
                            {
                                target.target_name = "DuplicateComparisonLimit".into();
                            }
                        } else {
                            // Frozen DuplicateEngine: the verdict is the duplication rate
                            // against warn_pct/fail_pct under the engine mode, not the
                            // severity of individual clone findings.
                            duplicate_status = Some(duplicate_output.status);
                        }
                        Ok(())
                    }
                    Err(error) => Err(error),
                }
            }
            _ => Err(CppError::new(
                "implementation-pending",
                format!("Native {} analysis is not implemented", request.check_id),
            )),
        };
        if cancelled(&mut output, cancellation) {
            return output;
        }
        if !legacy_errors.is_empty() {
            return legacy_metric_error(kind, request, output, &legacy_errors);
        }
        if matches!(kind, "complexity" | "cognitive") {
            let policy = request.options["cpp_boundaries"].as_str().unwrap_or("auto");
            let result = if let Some((context, evidence)) = compiler {
                crate::compiler::apply_compiler_boundaries(request, &mut output, context, evidence)
            } else if legacy_metric {
                // Frozen legacy engines: the rows above already took the verify run's compiler
                // boundaries (`legacy_boundaries`) and every required-boundary error.
                Ok(())
            } else if policy == "required" {
                Err(CppError::new(
                    "compiler-boundaries-required",
                    "Compiler boundaries were required but no compiler observations were supplied",
                ))
            } else if !["auto", "off"].contains(&policy) {
                Err(CppError::new(
                    "invalid-boundary-policy",
                    "cpp_boundaries must be auto, off or required",
                ))
            } else {
                Ok(())
            };
            if source_recovery && compiler.is_some() && output.extra["boundary_mode"] != "exact" {
                fail(
                    &mut output,
                    ".",
                    "source-recovery-unconfirmed",
                    "Recovered C++ source geometry was not fully confirmed by compiler observations",
                );
            }
            if source_recovery {
                output.extra["source_syntax_recovery"] = json!(true);
            }
            if let Err(error) = result {
                fail(&mut output, ".", &error.code, &error.message);
            }
        }
        if let Err(error) = result {
            fail(&mut output, ".", &error.code, &error.message);
        }
        if kind == "dup" {
            output.extra["excluded_source_counts"] = json!(excluded_counts);
            // Each clone occurrence is the frozen `CloneGroup#N` target; the historical view
            // keeps only that target's finding.
            ici_analysis::decorate_finding_details(&mut output);
        }
        if output.observation.state == TaskState::Succeeded {
            output.observation.state = TaskState::Succeeded;
            output.status = if output
                .observation
                .findings
                .iter()
                .any(|f| f.severity == "high")
            {
                "FAIL"
            } else if output.observation.findings.is_empty() {
                "PASS"
            } else {
                "WARN"
            }
            .into();
            if let Some(status) = duplicate_status.take() {
                output.status = status;
            }
        }
        if let Some(inventory) = &legacy_dup_inventory {
            legacy_dup_evidence(&mut output, inventory);
        }
        output.summary = if output.observation.state == TaskState::Succeeded {
            frozen_summary(kind, request, &mut output)
        } else {
            None
        }
        .or_else(|| {
            // Frozen ExceptionSafetyEngine: analysis errors name the summary (first three).
            let errors: Vec<&str> = output
                .targets
                .iter()
                .filter(|target| kind == "exception" && target.target_name == "ReadError")
                .take(3)
                .map(|target| target.message.as_str())
                .collect();
            (!errors.is_empty()).then(|| errors.join("; "))
        })
        .or_else(|| {
            // Frozen dup.py:207 `DuplicateComparisonLimit` keeps its fixed summary.
            (kind == "dup"
                && output
                    .targets
                    .iter()
                    .any(|target| target.target_name == "DuplicateComparisonLimit"))
            .then(|| "Duplicate analysis exceeded a bounded comparison limit".to_owned())
        })
        .unwrap_or_else(|| {
            format!(
                "{}: {} inspected targets, {} findings",
                request.check_id,
                output.targets.len(),
                output.observation.findings.len()
            )
        });
        if legacy_failed {
            // Frozen engine summaries of a run that recorded its boundary or range errors.
            output.summary = if kind == "cognitive" {
                "Cognitive complexity analysis did not complete".into()
            } else {
                frozen_summary(kind, request, &mut output).unwrap_or_default()
            };
        }
        output.extra["parser_identity"] = json!(if legacy_metric || next_heuristic {
            crate::legacy_heuristic::PARSER_ID
        } else {
            source::PARSER_ID
        });
        output
    }
}
/// Frozen `DuplicateEngine._source_evidence`: the source inventory counters of every legacy dup
/// result (zeros when the intake failed).
fn legacy_dup_evidence(output: &mut AnalysisOutput, inventory: &crate::LegacyInventory) {
    if !output.extra.is_object() {
        output.extra = json!({});
    }
    output.extra["analysis_provenance"] = json!("language-lexical-region-heuristic");
    output.extra["source_files_analyzed"] = json!(inventory.files);
    output.extra["source_bytes_analyzed"] = json!(inventory.bytes);
    output.extra["source_files_excluded"] = json!(inventory.files_excluded);
    output.extra["source_exclusion_counts"] = json!(inventory.exclusion_counts);
}

/// Frozen `_cpp_source_rows` / `_load_cpp_sources` / `_append_estimated_span` errors (an
/// unreadable source, an invalid inventory scope, an unterminated or invalid cognitive range):
/// cognitive keeps its ERROR targets ("Cognitive complexity analysis did not complete");
/// complexity drops every row for one `CppComplexityAnalysisError` at the project root joining
/// the first ten errors (`_finish_cpp_analysis`).
fn legacy_metric_error(
    kind: &str,
    request: &AnalysisRequest,
    mut output: AnalysisOutput,
    errors: &[String],
) -> AnalysisOutput {
    if kind == "cognitive" {
        output.summary = "Cognitive complexity analysis did not complete".into();
    } else {
        output.targets.clear();
        output.observation.findings.clear();
        let mut target = InspectionTarget::at(".", 1);
        target.target_name = "CppComplexityAnalysisError".into();
        target.status = "ERROR".into();
        target.message = errors
            .iter()
            .take(10)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
        target
            .metrics
            .insert("boundary_source".into(), json!("compiler-tool-error"));
        output.targets.push(target);
        output.summary = frozen_summary(kind, request, &mut output).unwrap_or_default();
    }
    output.status = "ERROR".into();
    output.observation.state = TaskState::Failed;
    output
        .observation
        .limitations
        .extend(errors.iter().cloned());
    // Frozen `_cpp_boundary_evidence` of the stopped heuristic pass: no exact configuration or
    // source was checked and every error is a boundary error.
    for (key, value) in [
        ("cpp_boundary_configurations_checked", json!(0)),
        ("cpp_boundary_sources_checked", json!(0)),
        ("cpp_boundary_errors", json!(errors)),
        ("cpp_boundary_warnings", json!([])),
    ] {
        output.extra[key] = value;
    }
    output.extra["parser_identity"] = json!(crate::legacy_heuristic::PARSER_ID);
    output
}
/// Frozen `get_all_cpp_sources`: the legacy metric engines read `.cpp`, `.cc`, `.cxx` and `.c`.
pub(crate) fn is_legacy_metric_source(path: &str) -> bool {
    std::path::Path::new(path)
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension, "cpp" | "cc" | "cxx" | "c"))
}
/// Python `{value:,}` grouping for the frozen line summary.
pub(crate) fn grouped(value: u64) -> String {
    let digits = value.to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    out
}
fn measured(output: &AnalysisOutput, name: &str) -> u64 {
    output
        .observation
        .measurements
        .iter()
        .find(|item| item.name == name)
        .map_or(0, |item| item.value as u64)
}
/// The frozen engine summary (and score) of a completed C++ source check, as the historical
/// LineCount/Complexity/Cognitive/Cycle/Duplicate/ExceptionSafety engines word it for a C++
/// scope.
fn frozen_summary(
    kind: &str,
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
) -> Option<String> {
    let count = |status: &str| {
        output
            .targets
            .iter()
            .filter(|target| target.status == status)
            .count()
    };
    let (fails, warns) = (count("FAIL"), count("WARN"));
    match kind {
        "line" => Some(format!(
            "Total {} lines ({} code, {} comment, {} blank) across {} files",
            grouped(measured(output, "lines_total")),
            grouped(measured(output, "lines_code")),
            grouped(measured(output, "lines_comment")),
            grouped(measured(output, "lines_blank")),
            output.targets.len()
        )),
        "complexity" | "cognitive" => {
            let rows: Vec<u64> = output
                .targets
                .iter()
                .filter_map(|target| target.metrics.get(kind).and_then(|v| v.as_u64()))
                .collect();
            let peak = rows.iter().copied().max().unwrap_or(0);
            if kind == "complexity" {
                output.score = Some(peak as f64);
                let warn = request
                    .options
                    .get("warn_cc")
                    .and_then(|value| value.as_u64())
                    .unwrap_or_else(|| option(request, "warn_limit", 15));
                Some(format!(
                    "Max Cyclomatic Complexity: {peak} (limit {warn}) across {} functions ({} issues)",
                    rows.len(),
                    // Frozen counts every non-PASS target, including a required-boundary ERROR.
                    fails + warns + count("ERROR")
                ))
            } else {
                let threshold = |frozen: &str, default| {
                    request
                        .options
                        .get(frozen)
                        .and_then(|value| value.as_u64())
                        .unwrap_or(default)
                };
                let base = format!(
                    "Max cognitive complexity: {peak} across {} functions",
                    rows.len()
                );
                Some(if fails > 0 {
                    format!(
                        "{base}; fail threshold {} exceeded",
                        threshold("fail", option(request, "fail_limit", 60))
                    )
                } else if warns > 0 {
                    format!(
                        "{base}; warn threshold {} or nesting threshold {} reached",
                        threshold("warn", option(request, "warn_limit", 30)),
                        option(request, "warn_nesting", 4)
                    )
                } else {
                    base
                })
            }
        }
        "cycle" => {
            let cycles = output.extra["cycles"].as_array().map_or(0, Vec::len);
            let mut summary = if cycles > 0 {
                format!("Dependency cycles: {cycles} found")
            } else {
                "No cyclic dependencies detected".into()
            };
            let ambiguous = output.extra["ambiguous_cpp_includes"].as_u64().unwrap_or(0);
            let unresolved = output.extra["unresolved_cpp_includes"]
                .as_u64()
                .unwrap_or(0);
            if ambiguous + unresolved > 0 {
                summary.push_str(&format!(
                    "; C++ include graph incomplete: {ambiguous} ambiguous, {unresolved} unresolved"
                ));
            }
            Some(summary)
        }
        "dup" => {
            let total = output.extra["total_code_lines"].as_u64().unwrap_or(0);
            let duplicated = output.extra["duplicated_lines"].as_u64().unwrap_or(0);
            let percent = if total > 0 {
                duplicated as f64 / total as f64 * 100.0
            } else {
                0.0
            };
            output.score = Some(percent);
            Some(format!(
                "Code Duplication Rate: {percent:.1}% ({} distinct clone groups found, 0 from Python AST shapes)",
                measured(output, "clone_groups")
            ))
        }
        "exception" => Some(if fails + warns == 0 {
            "Exception Handling Safety Clean".into()
        } else {
            format!("{fails} Critical Exception Violations, {warns} Warnings")
        }),
        _ => None,
    }
}
pub(crate) fn cancelled(output: &mut AnalysisOutput, cancellation: Option<&AtomicBool>) -> bool {
    if cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
        output.observation.state = TaskState::Cancelled;
        output.status = "ERROR".into();
        output
            .observation
            .limitations
            .push("C++ source analysis cancelled".into());
        true
    } else {
        false
    }
}
pub(crate) fn fail(output: &mut AnalysisOutput, path: &str, code: &str, message: &str) {
    output.observation.state = TaskState::Failed;
    output.status = "ERROR".into();
    output
        .observation
        .limitations
        .push(format!("{path}: {code}: {message}"));
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = code.into();
    target.status = "ERROR".into();
    target.message = message.into();
    output.targets.push(target);
}
pub(crate) fn finding(
    output: &mut AnalysisOutput,
    request: &AnalysisRequest,
    target: &InspectionTarget,
    rule: &str,
    severity: &str,
    estimated: bool,
) {
    let span = SourceSpan {
        path: target.file_path.clone(),
        start_line: target.start_line,
        end_line: target.end_line,
        start_column: target.start_column,
        end_column: target.end_column,
        label: target.target_name.clone(),
    };
    let mut finding = Finding::new(rule, &target.message, span, &output.observation.provider);
    finding.fingerprint = ici_domain::sha256(format!(
        "{}:{}:{}:{}",
        rule, target.file_path, target.start_line, target.target_name
    ));
    finding.severity = severity.into();
    finding.confidence = if estimated { "medium" } else { "high" }.into();
    finding.evidence = if estimated {
        EvidenceLevel::Estimated
    } else {
        EvidenceLevel::Measured
    };
    finding.component_id = Some(request.component_id.clone());
    finding.task_id = Some(request.task_id.clone());
    if estimated {
        finding.limitations.push(
            "Bounded C++ source analysis without preprocessing or compiler semantic verification"
                .into(),
        );
    }
    output.observation.findings.push(finding);
}
pub(crate) fn measurement(output: &mut AnalysisOutput, name: &str, value: u64, estimated: bool) {
    let mut measurement = Measurement::measured(name, value as f64);
    // Frozen next measurement units (languages/{lines,metrics,cycles,hygiene}.py).
    measurement.unit = match name {
        "lines_total" | "lines_code" | "lines_comment" | "lines_blank" => "lines",
        "files_counted" | "files_checked" => "files",
        "functions_measured" => "functions",
        "max_complexity" => "complexity",
        "max_cognitive" => "cognitive",
        "cycles_found" => "cycles",
        "issues_found" => "issues",
        _ => "",
    }
    .into();
    if estimated {
        measurement.evidence = EvidenceLevel::Estimated;
    }
    output.observation.measurements.push(measurement);
}
pub(crate) fn option(request: &AnalysisRequest, name: &str, default: u64) -> u64 {
    request
        .options
        .get(name)
        .and_then(|v| v.as_u64())
        .unwrap_or(default)
}
