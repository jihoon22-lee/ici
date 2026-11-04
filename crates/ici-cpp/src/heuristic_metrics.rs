//! Frozen legacy C++ complexity/cognitive rows over the brace-heuristic function inventory.
//!
//! `ComplexityEngine._cpp_source_rows` / `_append_estimated_cpp_targets` /
//! `_finish_cpp_analysis` (engines/complexity.py) and `_load_cpp_sources` /
//! `_append_estimated_span` / `_target` (analysis/_cpp_cognitive.py) without compiler
//! boundaries: every function comes from `legacy_heuristic::function_inventory`, never from a
//! syntax tree, so malformed sources keep frozen's spans, unterminated ranges and errors.
//!
//! The next path (`next_metrics`) is frozen `languages/metrics.py::_scan_cpp` / `measure`: the
//! same inventory over every planned file read with `errors="replace"`, one row per terminated
//! span carrying both measures, frozen finding messages and fingerprints, ESTIMATED evidence.
use crate::analysis::{finding, measurement, option};
use crate::legacy_heuristic::{self, HeuristicSpan};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    SourceSpan, TaskState,
};
use serde_json::{Value, json};

/// One frozen legacy metric source: project-relative path and its strict UTF-8 text, `None`
/// when frozen `read_cpp_source_text` rejected it (not UTF-8 or over 8 MiB).
pub(crate) type LegacySource = (String, Option<String>);

/// What the legacy rows established: the frozen errors (empty when analysis completed).
pub(crate) struct LegacyMetricOutcome {
    pub errors: Vec<String>,
}

fn threshold(request: &AnalysisRequest, frozen: &str, generic: &str, default: u64) -> u64 {
    request
        .options
        .get(frozen)
        .and_then(Value::as_u64)
        .unwrap_or_else(|| option(request, generic, default))
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn located(
    path: &str,
    start_line: usize,
    end_line: usize,
    start_column: Option<usize>,
    end_column: Option<usize>,
) -> InspectionTarget {
    let mut target = InspectionTarget::at(path, start_line as u32);
    target.end_line = Some(end_line as u32);
    target.start_column = start_column.map(|column| column as u32);
    target.end_column = end_column.map(|column| column as u32);
    target
}

fn insert_all(target: &mut InspectionTarget, fields: Value) {
    target.metrics = fields
        .as_object()
        .expect("metric fields")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
}

/// Run the frozen legacy rows for `kind` over `sources` (input order, translation units only).
pub(crate) fn metrics(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    sources: &[LegacySource],
    kind: &str,
) -> LegacyMetricOutcome {
    if kind == "complexity" {
        complexity(request, output, sources)
    } else {
        cognitive(request, output, sources)
    }
}

fn complexity(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    sources: &[LegacySource],
) -> LegacyMetricOutcome {
    let warn = threshold(request, "warn_cc", "warn_limit", 15) as i64;
    let fail = threshold(request, "fail_cc", "fail_limit", 25) as i64;
    let warn_nesting = option(request, "warn_nesting", 4) as i64;
    let mut errors = vec![];
    let mut rows: Vec<(&str, Vec<HeuristicSpan>)> = vec![];
    let mut lambdas = 0usize;
    for (path, text) in sources {
        let Some(text) = text else {
            errors.push(format!(
                "C++ complexity source is not a bounded project file: {}",
                file_name(path)
            ));
            continue;
        };
        let scanned = legacy_heuristic::function_inventory(&crate::py_splitlines(text))
            .and_then(|(spans, _)| legacy_heuristic::lambda_body_count(text).map(|n| (spans, n)));
        match scanned {
            Ok((spans, count)) => {
                rows.push((path, spans));
                lambdas += count;
            }
            Err(error) => {
                // Frozen returns `{}`: no source row survives the invalid scope.
                errors.push(format!(
                    "C++ complexity source scope is invalid: {path}: {error}"
                ));
                rows.clear();
                break;
            }
        }
    }
    let mut estimated = 0u64;
    let mut peak = 0i64;
    if errors.is_empty() {
        for (path, spans) in &rows {
            for span in spans {
                let (cc, nesting) = (span.complexity, span.max_nesting);
                let over_fail = cc > fail;
                let over_warn = cc > warn || nesting >= warn_nesting;
                let mut target = located(
                    path,
                    span.start_line,
                    span.end_line,
                    Some(span.start_column),
                    span.end_column,
                );
                target.target_name = if span.name.ends_with("()") {
                    span.name.clone()
                } else {
                    format!("{}()", span.name)
                };
                target.status = if over_fail {
                    "FAIL"
                } else if over_warn {
                    "WARN"
                } else {
                    "PASS"
                }
                .into();
                target.message = if over_fail {
                    format!("Critical complexity: {cc} > {fail} (Immediate refactoring required)")
                } else if over_warn {
                    format!(
                        "High complexity: {cc} (limit {warn}), Max Nesting: {nesting} (limit {warn_nesting})"
                    )
                } else {
                    format!("Complexity: {cc}, Nesting: {nesting}")
                };
                insert_all(
                    &mut target,
                    json!({"complexity": cc, "nesting": nesting, "boundary_source": "heuristic",
                        "boundary_confidence": "medium",
                        "metric_confidence": if span.preprocessor_conditional { "low" } else { "medium" },
                        "configurations": [], "function_kind": span.function_kind,
                        "function_template": span.is_template, "function_origin": "source-scanner",
                        "metric_variant": span.preprocessor_conditional,
                        "preprocessor_conditional": span.preprocessor_conditional,
                        "excluded_nested_lambdas": span.excluded_lambdas}),
                );
                if over_fail || over_warn {
                    let severity = if over_fail { "high" } else { "medium" };
                    finding(
                        output,
                        request,
                        &target,
                        "metrics.complexity",
                        severity,
                        true,
                    );
                }
                output.targets.push(target);
                estimated += 1;
                peak = peak.max(cc);
            }
        }
    }
    finish(output, "complexity", estimated, peak, &errors, lambdas);
    LegacyMetricOutcome { errors }
}

fn cognitive(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    sources: &[LegacySource],
) -> LegacyMetricOutcome {
    let warn = threshold(request, "warn", "warn_limit", 30) as i64;
    let fail = threshold(request, "fail", "fail_limit", 60) as i64;
    let warn_nesting = option(request, "warn_nesting", 4) as i64;
    let mut errors = vec![];
    let mut rows: Vec<(&str, &str, Vec<HeuristicSpan>)> = vec![];
    for (path, text) in sources {
        let scanned = text.as_deref().map(|text| {
            legacy_heuristic::function_inventory(&crate::py_splitlines(text))
                .map(|(spans, _)| (text, spans))
        });
        match scanned {
            Some(Ok((text, spans))) => rows.push((path, text, spans)),
            // Frozen `_append_source_error`: an unreadable source and an invalid inventory are
            // both the `ValueError` of one source.
            _ => {
                let message = format!(
                    "C++ cognitive source is not a bounded project file: {path} (ValueError)"
                );
                let mut target = InspectionTarget::at(path, 1);
                target.target_name = "CppCognitiveSourceError".into();
                target.status = "ERROR".into();
                target.message = message.clone();
                target
                    .metrics
                    .insert("boundary_source".into(), json!("source-intake"));
                output.targets.push(target);
                errors.push(message);
            }
        }
    }
    let mut estimated = 0u64;
    let mut peak = 0i64;
    if errors.is_empty() {
        for (path, text, spans) in &rows {
            for span in spans {
                let range_error = |message: String, end_column: Option<usize>| {
                    let mut target = located(
                        path,
                        span.start_line,
                        span.end_line,
                        Some(span.start_column),
                        end_column,
                    );
                    target.target_name = span.name.clone();
                    target.status = "ERROR".into();
                    target.message = message;
                    target
                        .metrics
                        .insert("boundary_source".into(), json!("source-scanner"));
                    target
                };
                let Some(end_column) = span.end_column else {
                    let message = format!(
                        "C++ cognitive function range is unterminated: {path}:{}",
                        span.start_line
                    );
                    output.targets.push(range_error(message.clone(), None));
                    errors.push(message);
                    continue;
                };
                let metric = legacy_heuristic::source_slice(
                    text,
                    span.body_start_line,
                    span.body_start_column,
                    span.end_line,
                    end_column,
                )
                .and_then(|body| legacy_heuristic::cognitive_metric(&body));
                let metric = match metric {
                    Ok(metric) => metric,
                    Err(error) => {
                        let message =
                            format!("C++ cognitive function range is invalid: {path}: {error}");
                        output
                            .targets
                            .push(range_error(message.clone(), Some(end_column)));
                        errors.push(message);
                        continue;
                    }
                };
                let value = metric.cognitive;
                let over_fail = value >= fail;
                let over_warn = value >= warn || metric.max_nesting >= warn_nesting;
                let mut target = located(
                    path,
                    span.start_line,
                    span.end_line,
                    Some(span.start_column),
                    Some(end_column),
                );
                target.target_name = span.name.clone();
                target.status = if over_fail {
                    "FAIL"
                } else if over_warn {
                    "WARN"
                } else {
                    "PASS"
                }
                .into();
                target.message = format!(
                    "Cognitive {value} (control nesting {}, estimated C++ metric)",
                    metric.max_nesting
                );
                let low = metric.preprocessor_conditional || metric.unbraced_controls > 0;
                insert_all(
                    &mut target,
                    json!({"cognitive": value, "nesting": metric.max_nesting,
                        "metric_source": "bounded-cpp-tokens",
                        "metric_confidence": if low { "low" } else { "medium" },
                        "boundary_source": "source-scanner", "configurations": [],
                        "logical_sequences": metric.logical_sequences,
                        "unbraced_controls": metric.unbraced_controls,
                        "excluded_nested_lambdas": metric.excluded_lambdas,
                        "preprocessor_conditional": metric.preprocessor_conditional}),
                );
                if over_fail || over_warn {
                    let severity = if over_fail { "high" } else { "medium" };
                    finding(
                        output,
                        request,
                        &target,
                        "metrics.cognitive",
                        severity,
                        true,
                    );
                }
                output.targets.push(target);
                estimated += 1;
                peak = peak.max(value);
            }
        }
    }
    finish(output, "cognitive", estimated, peak, &errors, 0);
    LegacyMetricOutcome { errors }
}

/// Frozen boundary bookkeeping without compiler boundaries: `heuristic` (or `error`), every
/// measured span an estimated boundary; complexity counts its sources' lambda bodies, cognitive
/// takes the unavailable boundary scan's zero counts; no macro-generated function is excluded.
fn finish(
    output: &mut AnalysisOutput,
    kind: &str,
    estimated: u64,
    peak: i64,
    errors: &[String],
    lambdas: usize,
) {
    measurement(output, "functions_measured", estimated, true);
    measurement(output, &format!("max_{kind}"), peak.max(0) as u64, true);
    output.observation.limitations.push("Source metrics remain estimated and do not model preprocessor execution; compiler boundary verification, when available, is recorded separately".into());
    output.extra = json!({
        "boundary_mode": if errors.is_empty() { "heuristic" } else { "error" },
        "estimated_boundaries": estimated,
        "exact_boundaries": 0,
        "lambdas_excluded": lambdas,
        "cpp_scope_exclusions": {"lambda": lambdas, "macro_generated_function": 0},
    });
}

/// One frozen next `FunctionMetric` of a C++ file (always `heuristic=True`).
struct NextRow {
    name: String,
    start_line: usize,
    end_line: usize,
    start_column: usize,
    end_column: usize,
    cyclomatic: i64,
    cognitive: i64,
    nesting: i64,
    /// Lambda bodies excluded by the scanner and, when it read the body, the cognitive parser.
    span_lambdas: usize,
    cognitive_lambdas: Option<usize>,
}

/// Frozen `_scan_cpp`: `Err` is the `ValueError` of the function inventory.
fn scan_next(text: &str) -> Result<Vec<NextRow>, String> {
    let (spans, metric_lines) = legacy_heuristic::function_inventory(&crate::py_splitlines(text))?;
    Ok(spans
        .into_iter()
        .filter_map(|span| {
            let end_column = span.end_column?;
            // Frozen reads the cognitive body from the literal-masked metric lines; a body the
            // cognitive parser rejects keeps its row with cognitive 0.
            let cognitive = legacy_heuristic::slice_body(
                &metric_lines,
                span.body_start_line,
                span.body_start_column,
                span.end_line,
                end_column,
            )
            .and_then(|body| legacy_heuristic::cognitive_metric(&body).ok());
            Some(NextRow {
                start_line: span.start_line,
                end_line: span.end_line,
                start_column: span.start_column,
                end_column,
                cyclomatic: span.complexity,
                cognitive: cognitive.map_or(0, |metric| metric.cognitive),
                nesting: span
                    .max_nesting
                    .max(cognitive.map_or(0, |metric| metric.max_nesting)),
                span_lambdas: span.excluded_lambdas,
                cognitive_lambdas: cognitive.map(|metric| metric.excluded_lambdas),
                name: span.name,
            })
        })
        .collect())
}

/// Frozen next `measure` over C++ `inputs` (path and bytes, plan order).
pub(crate) fn next_metrics(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    inputs: &[(&str, &[u8])],
    kind: &str,
) {
    let complexity = kind == "complexity";
    // Frozen next ships the stable engines' policy (15/25/4, 30/60/4); explicit per-check
    // options override it like the Python metric checks.
    let warn = if complexity {
        threshold(request, "warn_cc", "warn_limit", 15)
    } else {
        threshold(request, "warn", "warn_limit", 30)
    } as i64;
    let fail = if complexity {
        threshold(request, "fail_cc", "fail_limit", 25)
    } else {
        threshold(request, "fail", "fail_limit", 60)
    } as i64;
    let warn_nesting = option(request, "warn_nesting", 4) as i64;
    let mut limitations = vec![];
    let (mut functions, mut measured, mut peak) = (0u64, 0usize, 0i64);
    for (path, bytes) in inputs {
        let rows = match scan_next(&String::from_utf8_lossy(bytes)) {
            Ok(rows) => rows,
            Err(_) => {
                limitations.push(format!("{path}: ValueError"));
                continue;
            }
        };
        measured += 1;
        if rows.is_empty() {
            let mut target = InspectionTarget::at(*path, 1);
            target.target_name = "NoFunctionDefinitions".into();
            target.message = "No source-scanned function definitions".into();
            output.targets.push(target);
        }
        for row in rows {
            functions += 1;
            let value = if complexity {
                row.cyclomatic
            } else {
                row.cognitive
            };
            peak = peak.max(value);
            let over_fail = value > fail;
            let over_warn = value > warn || row.nesting > warn_nesting;
            let mut target = located(
                path,
                row.start_line,
                row.end_line,
                Some(row.start_column),
                Some(row.end_column),
            );
            target.target_name = row.name.clone();
            target.status = if over_fail {
                "FAIL"
            } else if over_warn {
                "WARN"
            } else {
                "PASS"
            }
            .into();
            target.message = format!(
                "{}: {kind} {value} (nesting {}, estimated C++ metric)",
                row.name, row.nesting
            );
            insert_all(
                &mut target,
                json!({kind: value, "complexity": row.cyclomatic, "cognitive": row.cognitive,
                    "nesting": row.nesting,
                    "excluded_nested_lambdas": if complexity {
                        row.span_lambdas
                    } else {
                        row.cognitive_lambdas.unwrap_or(row.span_lambdas)
                    },
                    "boundary_source": "source-scanner",
                    "boundary_confidence": "medium", "function_origin": "source-scanner",
                    "metric_source": "bounded-cpp-tokens"}),
            );
            if over_fail || over_warn {
                let message = if over_fail {
                    format!("{}: {kind} {value} exceeds fail limit {fail}", row.name)
                } else {
                    let mut parts = vec![];
                    if value > warn {
                        parts.push(format!("{kind} {value} exceeds warn limit {warn}"));
                    }
                    if row.nesting > warn_nesting {
                        parts.push(format!(
                            "nesting depth {} exceeds {warn_nesting}",
                            row.nesting
                        ));
                    }
                    format!("{}: {}", row.name, parts.join("; "))
                };
                let location = SourceSpan {
                    path: (*path).to_owned(),
                    start_line: row.start_line as u32,
                    end_line: Some(row.end_line as u32),
                    start_column: None,
                    end_column: None,
                    label: String::new(),
                };
                let mut item =
                    Finding::new(format!("metrics.{kind}"), message, location, "ici.metrics");
                item.fingerprint =
                    ici_analysis::sha1_short("metrics-", &format!("{path}:{}:{kind}", row.name));
                item.severity = if over_fail { "high" } else { "medium" }.into();
                item.confidence = "low".into();
                item.evidence = EvidenceLevel::Estimated;
                item.component_id = Some(request.component_id.clone());
                item.task_id = Some(request.task_id.clone());
                output.observation.findings.push(item);
            }
            output.targets.push(target);
        }
    }
    let evidence = if functions > 0 {
        EvidenceLevel::Estimated
    } else {
        EvidenceLevel::Measured
    };
    for (name, value, unit) in [
        ("functions_measured".to_owned(), functions, "functions"),
        (format!("max_{kind}"), peak.max(0) as u64, kind),
    ] {
        let mut item = Measurement::measured(name, value as f64);
        item.unit = unit.into();
        item.evidence = evidence;
        output.observation.measurements.push(item);
    }
    output.observation.limitations.push(
        "function boundaries from the heuristic scanner — preprocessed or macro-built functions may be missed or mis-bounded"
            .into(),
    );
    output.observation.limitations.extend(limitations);
    output.extra = json!({"boundary_mode": "heuristic", "estimated_boundaries": functions,
        "exact_boundaries": 0});
    if measured != inputs.len() {
        output.observation.state = TaskState::Failed;
        output.status = "ERROR".into();
    }
}
