use crate::{
    CppError,
    analysis::{finding, measurement, option},
    source::{self, ParsedSource},
};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget};
use serde_json::json;
use std::sync::Arc;
use tree_sitter::Node;

pub(crate) fn lines(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[(&str, &str)],
    cancellation: Option<&std::sync::atomic::AtomicBool>,
) {
    let legacy = request.options["legacy_projection"]
        .as_bool()
        .unwrap_or(false);
    let extra_targets = request.options["extra_targets"].as_bool().unwrap_or(true);
    let selected = |key: &str, path: &str| {
        request.options[key]
            .as_array()
            .is_none_or(|paths| paths.iter().any(|item| item.as_str() == Some(path)))
    };
    let limit = |key: &str, default| {
        (!request
            .options
            .get(key)
            .is_some_and(serde_json::Value::is_null))
        .then(|| option(request, key, default))
    };
    let warn = limit("warn_limit", 500);
    let fail = limit("fail_limit", 1000);
    let (mut code, mut comments, mut blanks) = (0u64, 0u64, 0u64);
    let (mut all_code, mut all_comments, mut all_blanks, mut counted) = (0u64, 0u64, 0u64, 0u64);
    let mut rows = vec![];
    for (path, text) in files {
        // Frozen next `languages/python/lines.count`: a file whose bytes decode to nothing is
        // not an empty file, and counting it would report a file nobody looked at.
        if !legacy
            && text.is_empty()
            && request
                .inputs
                .iter()
                .any(|input| input.path == *path && !input.bytes.is_empty())
        {
            output
                .observation
                .limitations
                .push(format!("not readable: {path}"));
            continue;
        }
        let extension = path.rsplit('.').next().unwrap_or("");
        let cpp = ["cpp", "c", "h", "hpp", "cc", "cxx"].contains(&extension);
        let hash_comments = ["py", "pyi", "sh", "csh", "toml", "yml", "yaml"].contains(&extension);
        let language = match extension {
            "py" | "pyi" => "Python",
            "sh" | "csh" => "Shell",
            "toml" => "TOML",
            "md" => "Markdown",
            "yml" | "yaml" => "YAML",
            "json" => "JSON",
            _ => "C++",
        };
        let mut comment = false;
        let (mut fc, mut fm, mut fb) = (0u64, 0u64, 0u64);
        for (index, line) in source::physical_lines(text).enumerate() {
            if index % 4096 == 0 && crate::analysis::cancelled(output, cancellation) {
                return;
            }
            let stripped = line.trim_matches(crate::legacy_heuristic::py_space);
            if stripped.is_empty() {
                fb += 1;
                continue;
            }
            if cpp && comment {
                fm += 1;
                if stripped.contains("*/") {
                    comment = false;
                }
                continue;
            }
            if cpp && stripped.starts_with("/*") {
                fm += 1;
                comment = !stripped.contains("*/");
            } else if (cpp && stripped.starts_with("//"))
                || (hash_comments && stripped.starts_with('#'))
            {
                fm += 1;
            } else {
                fc += 1;
            }
        }
        all_code += fc;
        all_comments += fm;
        all_blanks += fb;
        let in_scope = !legacy || selected("source_paths", path);
        let gated = in_scope && (!legacy || selected("gated_paths", path));
        if in_scope {
            code += fc;
            comments += fm;
            blanks += fb;
            counted += 1;
        }
        let mut target = InspectionTarget::at(*path, 1);
        target.target_name = if legacy {
            language.to_owned()
        } else {
            (*path).to_owned()
        };
        target.end_line = Some((fc + fm + fb).max(1) as u32);
        // Frozen legacy line rows name their counts code/comment/blank/total only.
        if !legacy {
            target.metrics.insert("code_lines".into(), json!(fc));
            target.metrics.insert("comment_lines".into(), json!(fm));
            target.metrics.insert("blank_lines".into(), json!(fb));
        }
        if legacy {
            target.metrics.extend([
                ("code".into(), json!(fc)),
                ("comment".into(), json!(fm)),
                ("blank".into(), json!(fb)),
                ("total".into(), json!(fc + fm + fb)),
            ]);
        }
        let over_fail = gated && fail.is_some_and(|limit| fc > limit);
        let over_warn = gated && warn.is_some_and(|limit| fc > limit);
        target.status = if over_fail {
            "FAIL"
        } else if over_warn {
            "WARN"
        } else {
            "PASS"
        }
        .into();
        target.message = format!("{fc} code lines, {fm} comment lines, {fb} blank lines");
        if legacy {
            target.message = if over_fail {
                format!(
                    "Pure code lines ({fc}) exceed {} lines limit (Refactoring required)",
                    fail.unwrap()
                )
            } else if over_warn {
                format!(
                    "Pure code lines ({fc}) exceed {} lines threshold (Split recommended)",
                    warn.unwrap()
                )
            } else {
                format!("{fc} code lines")
            };
        }
        if over_warn || over_fail {
            finding(
                output,
                request,
                &target,
                "line.file-size",
                if over_fail { "high" } else { "medium" },
                false,
            );
        }
        rows.push(json!({"path":path,"lang":language,"code":fc,"comment":fm,"blank":fb,"total":fc+fm+fb,"status":target.status,"scope":if in_scope {"source"} else {"extra"}}));
        // Frozen LineCountEngine records pass-2 extras (ici.toml, docs) only in the all_* totals;
        // a frozen-schema request asks for that (`extra_targets: false`) like the Python line rule.
        if in_scope || extra_targets {
            output.targets.push(target);
        }
    }
    let total = code + comments + blanks;
    for (name, value) in [
        ("lines_total", total),
        ("lines_code", code),
        ("lines_comment", comments),
        ("lines_blank", blanks),
        ("files_counted", counted),
    ] {
        measurement(output, name, value, false);
        let metric = output.observation.measurements.last_mut().unwrap();
        let denominator = if name == "files_counted" {
            request.inputs.len() as u64
        } else {
            total
        };
        if name != "lines_total" && denominator > 0 {
            metric.numerator = Some(value);
            metric.denominator = Some(denominator);
        }
    }
    if legacy {
        rows.sort_by_key(|row| std::cmp::Reverse(row["code"].as_u64().unwrap_or(0)));
        let top: Vec<_> = rows
            .iter()
            .filter(|row| row["scope"] == "source")
            .take(5)
            .cloned()
            .collect();
        let top_all: Vec<_> = rows.iter().take(5).cloned().collect();
        output.extra = json!({"code":code,"comment":comments,"blank":blanks,"total":total,"files_data":rows,"top_files":top,"top_files_all":top_all,"all":{"code":all_code,"comment":all_comments,"blank":all_blanks,"total":all_code+all_comments+all_blanks,"files":files.len()},"metrics_summary":format!("{} code / {counted} files", crate::analysis::grouped(code))});
        // Frozen `LineEngine` source-pass directory order for the legacy report projection.
        if let Some(order) = request.options.get("line_scope_order") {
            output.extra["line_scope_order"] = order.clone();
        }
    }
}
#[derive(Default)]
struct Metric {
    complexity: u64,
    cognitive: u64,
    nesting: u64,
    control_nesting: u64,
    lambdas: u64,
    unbraced: u64,
    logical_sequences: u64,
    conditional: bool,
}
fn is_control(kind: &str) -> bool {
    matches!(
        kind,
        "if_statement"
            | "for_statement"
            | "for_range_loop"
            | "while_statement"
            | "do_statement"
            | "switch_statement"
            | "catch_clause"
    )
}
fn metric(file: &ParsedSource, body: Node<'_>) -> Result<Metric, CppError> {
    let mut metric = Metric {
        complexity: 1,
        ..Default::default()
    };
    let mut pending = vec![(body, 0u64, 0u64, false)];
    let mut logical = vec![];
    let mut count = 0;
    while let Some((node, block_depth, control_depth, else_if)) = pending.pop() {
        count += 1;
        if count > 1_000_000 || block_depth > 128 || control_depth > 128 {
            return Err(CppError::new(
                "metric-limit",
                "C++ function exceeds token/nesting bound",
            ));
        }
        let kind = node.kind();
        if matches!(kind, "lambda_expression" | "function_definition") {
            metric.lambdas += source::nodes(node)?
                .iter()
                .filter(|nested| nested.kind() == "lambda_expression")
                .count() as u64;
            continue;
        }
        if kind.starts_with("preproc_") {
            metric.conditional |= matches!(
                kind,
                "preproc_if" | "preproc_ifdef" | "preproc_elif" | "preproc_else"
            );
        }
        let control = is_control(kind);
        let compound = kind == "compound_statement";
        let next_block = block_depth + u64::from(compound && node.id() != body.id());
        metric.nesting = metric.nesting.max(next_block);
        if matches!(
            kind,
            "if_statement"
                | "for_statement"
                | "for_range_loop"
                | "while_statement"
                | "do_statement"
                | "catch_clause"
                | "conditional_expression"
        ) {
            metric.complexity += 1;
        }
        if kind == "case_statement" && node.child_by_field_name("value").is_some() {
            metric.complexity += 1;
        }
        if control && !else_if {
            metric.cognitive += 1 + control_depth;
        }
        if control
            && let Some(control_body) = node.child_by_field_name(if kind == "if_statement" {
                "consequence"
            } else {
                "body"
            })
        {
            if control_body.kind() != "compound_statement" {
                metric.unbraced += 1;
            } else {
                metric.control_nesting = metric.control_nesting.max(control_depth + 1);
            }
        }
        if matches!(
            kind,
            "break_statement" | "continue_statement" | "goto_statement" | "conditional_expression"
        ) {
            metric.cognitive += 1;
        }
        // Source order of logical tokens is needed to count same-operator sequences once.
        if node.child_count() == 0
            && matches!(
                file.text(node),
                "&&" | "||"
                    | "and"
                    | "or"
                    | ","
                    | ";"
                    | "{"
                    | "}"
                    | ":"
                    | "if"
                    | "for"
                    | "while"
                    | "switch"
                    | "do"
                    | "catch"
                    | "else"
                    | "?"
            )
        {
            logical.push((node.start_byte(), file.text(node).to_owned()));
        }
        if kind == "binary_expression"
            && let Some(operator) = node.child_by_field_name("operator")
            && matches!(file.text(operator), "&&" | "||")
        {
            metric.complexity += 1;
        }
        for i in (0..node.child_count()).rev() {
            let child = node.child(i).expect("child index");
            let mut child_depth = control_depth;
            let mut continuation = false;
            if control {
                let body_field = if kind == "if_statement" {
                    "consequence"
                } else {
                    "body"
                };
                if node
                    .child_by_field_name(body_field)
                    .is_some_and(|b| b.id() == child.id())
                {
                    child_depth += 1;
                }
                if kind == "if_statement"
                    && node
                        .child_by_field_name("alternative")
                        .is_some_and(|a| a.id() == child.id())
                {
                    metric.cognitive += 1 + control_depth;
                    // tree-sitter wraps `else` and its statement in an else_clause.
                    continuation = child.kind() == "if_statement" || child.kind() == "else_clause";
                    child_depth += u64::from(!continuation);
                }
            }
            if kind == "else_clause" {
                continuation = child.kind() == "if_statement";
                child_depth += u64::from(!continuation && child.is_named());
            }
            pending.push((child, next_block, child_depth, continuation));
        }
    }
    logical.sort_by_key(|(offset, _)| *offset);
    let mut previous = "";
    for (_, token) in &logical {
        match token.as_str() {
            "&&" | "and" => {
                if previous != "and" {
                    metric.cognitive += 1;
                    metric.logical_sequences += 1;
                }
                previous = "and";
            }
            "||" | "or" => {
                if previous != "or" {
                    metric.cognitive += 1;
                    metric.logical_sequences += 1;
                }
                previous = "or";
            }
            _ => previous = "",
        }
    }
    Ok(metric)
}
/// Frozen `project_cpp_sources()`: the legacy engines read `.cpp`/`.cc`/`.cxx`/`.c` only.
fn is_legacy_source_path(path: &str) -> bool {
    std::path::Path::new(path)
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension, "cpp" | "cc" | "cxx" | "c"))
}
pub(crate) fn metrics(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[Arc<ParsedSource>],
    kind: &str,
) -> Result<(), CppError> {
    let mut peak = 0;
    let mut functions = 0;
    let mut total_lambdas = 0;
    // Frozen engines.complexity.warn_cc/fail_cc (15/25) and engines.cognitive.warn/fail (30/60)
    // are the configured C++ thresholds; warn_limit/fail_limit remain explicit API overrides.
    let complexity = kind == "complexity";
    let threshold = |frozen: &str, generic: &str, default: u64| {
        request
            .options
            .get(frozen)
            .and_then(serde_json::Value::as_u64)
            .unwrap_or_else(|| option(request, generic, default))
    };
    let warn = if complexity {
        threshold("warn_cc", "warn_limit", 15)
    } else {
        threshold("warn", "warn_limit", 30)
    };
    let fail = if complexity {
        threshold("fail_cc", "fail_limit", 25)
    } else {
        threshold("fail", "fail_limit", 60)
    };
    let nesting_warn = option(request, "warn_nesting", 4);
    // Legacy projections never reach here: `legacy_metrics` reads the frozen brace heuristic.
    for file in files {
        let mut found = false;
        for function in source::nodes(file.tree.root_node())?
            .into_iter()
            .filter(|node| node.kind() == "function_definition")
        {
            // A function inside a lambda is excluded from the enclosing function, but named local-class methods still have their own row.
            let Some(body) = function.child_by_field_name("body") else {
                continue;
            };
            let metric = metric(file, body)?;
            let value = if kind == "complexity" {
                metric.complexity
            } else {
                metric.cognitive
            };
            peak = peak.max(value);
            functions += 1;
            total_lambdas += metric.lambdas;
            found = true;
            let name = source::function_name(file, function);
            let nesting = if kind == "complexity" {
                metric.nesting
            } else {
                metric.control_nesting
            };
            let over_fail = value > fail;
            let over_warn = value > warn || nesting > nesting_warn;
            let mut target = file.target(function, &name);
            target.status = if over_fail {
                "FAIL"
            } else if over_warn {
                "WARN"
            } else {
                "PASS"
            }
            .into();
            target.message =
                format!("{name}: {kind} {value} (nesting {nesting}, estimated C++ metric)");
            for (key, value) in [
                (kind, json!(value)),
                ("complexity", json!(metric.complexity)),
                ("cognitive", json!(metric.cognitive)),
                ("nesting", json!(nesting)),
                ("excluded_nested_lambdas", json!(metric.lambdas)),
                ("unbraced_controls", json!(metric.unbraced)),
                ("logical_sequences", json!(metric.logical_sequences)),
                ("preprocessor_conditional", json!(metric.conditional)),
                ("boundary_source", json!("concrete-source-syntax")),
                ("boundary_confidence", json!("medium")),
                (
                    "metric_confidence",
                    json!(if metric.conditional || metric.unbraced > 0 {
                        "low"
                    } else {
                        "medium"
                    }),
                ),
                ("function_origin", json!("source-spelled")),
                ("metric_source", json!("bounded-cpp-source")),
            ] {
                target.metrics.insert(key.into(), value);
            }
            if over_fail || over_warn {
                finding(
                    output,
                    request,
                    &target,
                    &format!("metrics.{kind}"),
                    if over_fail { "high" } else { "medium" },
                    true,
                );
            }
            output.targets.push(target);
        }
        if !found {
            let mut target = InspectionTarget::at(&file.path, 1);
            target.target_name = "NoFunctionDefinitions".into();
            target.message = "No source-spelled function definitions".into();
            output.targets.push(target);
        }
    }
    measurement(output, "functions_measured", functions, true);
    measurement(output, &format!("max_{kind}"), peak, true);
    output.observation.limitations.push("Source metrics remain estimated and do not model preprocessor execution; compiler boundary verification, when available, is recorded separately".into());
    output.extra = json!({"boundary_mode":"heuristic","estimated_boundaries":functions,"exact_boundaries":0,"lambdas_excluded":total_lambdas});
    Ok(())
}

fn exception_finding(
    output: &mut AnalysisOutput,
    request: &AnalysisRequest,
    target: InspectionTarget,
) {
    let rule = format!("exception.{}", target.target_name.to_ascii_lowercase());
    finding(output, request, &target, &rule, "high", false);
    let issue = output.observation.findings.last_mut().unwrap();
    issue.native_rule_id = target.target_name.clone();
    issue.confidence = "medium".into();
    issue.category = "correctness".into();
    // Frozen `languages/hygiene.py`: the next finding is the line-rule target's start line
    // (no columns or label) with the `hygiene-` provider identity.
    issue.primary_location = ici_domain::SourceSpan {
        path: target.file_path.clone(),
        start_line: target.start_line,
        end_line: None,
        start_column: None,
        end_column: None,
        label: String::new(),
    };
    issue.fingerprint = ici_analysis::sha1_short(
        "hygiene-",
        &format!(
            "{}:{}:{}:{}",
            target.file_path, target.start_line, target.target_name, target.message
        ),
    );
    output.targets.push(target);
}
pub(crate) fn exceptions(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    files: &[(&str, &str)],
) -> Result<(), CppError> {
    // Frozen `analyze_cpp_exceptions` serves both the engine and next `measure_hygiene`: one
    // start-line target per masked source line, every destructor throw line first, then every
    // empty catch-all, over decoded text with no syntax tree that could refuse a source.
    let legacy = request.options["legacy_projection"]
        .as_bool()
        .unwrap_or(false);
    let scope: Vec<&(&str, &str)> = files
        .iter()
        .filter(|(path, _)| !legacy || is_legacy_source_path(path))
        .collect();
    for &&(path, text) in &scope {
        let mut issues = 0;
        let masked = crate::legacy_exceptions::mask_literals(text);
        for (lines, name, message) in [
            (
                crate::legacy_exceptions::destructor_throw_lines(&masked),
                "DestructorThrow",
                "C++ destructor throws an exception and may call std::terminate",
            ),
            (
                crate::legacy_exceptions::empty_catch_all_lines(&masked),
                "CatchAllSwallowed",
                "Silent catch(...) block without logging or re-throw",
            ),
        ] {
            for line in lines {
                let mut target =
                    InspectionTarget::at(path, u32::try_from(line).unwrap_or(u32::MAX));
                target.target_name = name.into();
                target.status = "FAIL".into();
                target.message = message.into();
                exception_finding(output, request, target);
                issues += 1;
            }
        }
        let mut target = InspectionTarget::at(path, 1);
        target.target_name = "CppExceptionSafety".into();
        target.message = if issues == 0 {
            "C++ exception constructs were inspected"
        } else {
            "C++ exception constructs were inspected; see findings above"
        }
        .into();
        output.targets.push(target);
    }
    // Frozen `languages/hygiene.py` measurements.
    let checked = scope.len() as u64;
    let found = output.observation.findings.len() as u64;
    measurement(output, "files_checked", checked, false);
    measurement(output, "issues_found", found, false);
    Ok(())
}
