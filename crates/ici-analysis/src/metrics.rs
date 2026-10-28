//! Existing Python cyclomatic/cognitive formulas, with nested executable scopes pruned.
use crate::parser::ParsedFile;
use crate::{finding, finish_status, number_option};
use ici_domain::{AnalysisOutput, AnalysisRequest, Finding, Measurement};
use ruff_python_ast::visitor::source_order::{SourceOrderVisitor, TraversalSignal};
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, AnyNodeRef, Expr, Stmt};
use ruff_text_size::Ranged;
use serde_json::json;
use std::sync::Arc;

#[derive(Clone, Debug)]
pub struct FunctionMetric {
    pub name: String,
    pub range: ruff_text_size::TextRange,
    pub complexity: u32,
    pub cognitive: u32,
    pub nesting: u32,
    pub cognitive_nesting: u32,
}
#[derive(Default)]
struct FunctionCollector<'a> {
    functions: Vec<&'a ast::StmtFunctionDef>,
}
impl<'a> SourceOrderVisitor<'a> for FunctionCollector<'a> {
    fn enter_node(&mut self, node: AnyNodeRef<'a>) -> TraversalSignal {
        if let AnyNodeRef::StmtFunctionDef(function) = node {
            self.functions.push(function);
        }
        TraversalSignal::Traverse
    }
}
pub fn scan(file: &ParsedFile) -> Vec<FunctionMetric> {
    let mut collector = FunctionCollector::default();
    collector.visit_mod(file.parsed.syntax());
    collector
        .functions
        .into_iter()
        .map(|function| {
            let mut metric = MetricVisitor {
                complexity: 1,
                ..Default::default()
            };
            metric.signature(function);
            metric.visit_body(&function.body);
            let after_decorator = function
                .decorator_list
                .last()
                .map_or(function.range().start(), |decorator| {
                    decorator.range().end()
                });
            let start = file
                .parsed
                .tokens()
                .iter()
                .find(|token| {
                    token.range().start() >= after_decorator
                        && token.range().start() <= function.name.range().start()
                        && matches!(
                            token.kind(),
                            ast::token::TokenKind::Def | ast::token::TokenKind::Async
                        )
                })
                .map_or(function.range().start(), |token| token.range().start());
            let range = ruff_text_size::TextRange::new(start, function.range().end());
            FunctionMetric {
                name: function.name.to_string(),
                range,
                complexity: metric.complexity,
                cognitive: metric.cognitive,
                nesting: metric.max_depth,
                cognitive_nesting: metric.cognitive_peak,
            }
        })
        .collect()
}
#[derive(Default)]
struct MetricVisitor {
    complexity: u32,
    cognitive: u32,
    block_depth: u32,
    max_depth: u32,
    cognitive_depth: u32,
    cognitive_peak: u32,
    in_loop: bool,
    continuation_if: bool,
}
impl MetricVisitor {
    fn signature(&mut self, function: &ast::StmtFunctionDef) {
        for decorator in &function.decorator_list {
            self.visit_decorator(decorator);
        }
        self.visit_parameters(&function.parameters);
        if let Some(returns) = &function.returns {
            self.visit_expr(returns);
        }
        if let Some(parameters) = &function.type_params {
            self.visit_type_params(parameters);
        }
    }
    fn block(&mut self, block: u32, cognitive: u32, in_loop: bool, run: impl FnOnce(&mut Self)) {
        let previous = (self.block_depth, self.cognitive_depth, self.in_loop);
        self.block_depth += block;
        self.cognitive_depth += cognitive;
        self.in_loop |= in_loop;
        self.max_depth = self.max_depth.max(self.block_depth);
        self.cognitive_peak = self.cognitive_peak.max(self.cognitive_depth);
        run(self);
        (self.block_depth, self.cognitive_depth, self.in_loop) = previous;
    }
}
impl<'a> Visitor<'a> for MetricVisitor {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(function) => self.signature(function),
            Stmt::ClassDef(class) => {
                for decorator in &class.decorator_list {
                    self.visit_decorator(decorator);
                }
                if let Some(arguments) = &class.arguments {
                    self.visit_arguments(arguments);
                }
                if let Some(parameters) = &class.type_params {
                    self.visit_type_params(parameters);
                }
            }
            Stmt::If(node) => {
                self.complexity += 1;
                let continuation = std::mem::take(&mut self.continuation_if);
                self.cognitive += if continuation {
                    1
                } else {
                    1 + self.cognitive_depth
                };
                self.block(1, u32::from(!continuation), false, |visitor| {
                    visitor.visit_expr(&node.test);
                    visitor.visit_body(&node.body);
                    let mut added_depth = 0;
                    for clause in &node.elif_else_clauses {
                        if let Some(test) = &clause.test {
                            visitor.complexity += 1;
                            visitor.cognitive += 1;
                            added_depth += 1;
                            visitor.block(added_depth, 0, false, |visitor| {
                                visitor.visit_expr(test);
                                visitor.visit_body(&clause.body);
                            });
                        } else {
                            visitor.block(added_depth, 0, false, |visitor| {
                                if clause.body.len() == 1 && matches!(clause.body[0], Stmt::If(_)) {
                                    // CPython AST represents both elif and a sole `else: if` identically.
                                    visitor.continuation_if = true;
                                }
                                visitor.visit_body(&clause.body);
                            });
                        }
                    }
                });
            }
            Stmt::For(_) | Stmt::While(_) => {
                self.complexity += 1;
                self.cognitive += 1 + self.cognitive_depth;
                self.block(1, 1, true, |visitor| visitor::walk_stmt(visitor, statement));
            }
            Stmt::With(node) => {
                self.complexity += 1;
                self.cognitive += 1 + self.cognitive_depth;
                self.block(u32::from(!node.is_async), 1, false, |visitor| {
                    visitor::walk_stmt(visitor, statement)
                });
            }
            Stmt::Try(node) => self.block(u32::from(!node.is_star), 0, false, |visitor| {
                visitor::walk_stmt(visitor, statement)
            }),
            Stmt::Match(node) => {
                self.complexity += 1 + node
                    .cases
                    .iter()
                    .filter(|case| case.guard.is_some())
                    .count() as u32;
                visitor::walk_stmt(self, statement);
            }
            Stmt::Assert(_) => {
                self.cognitive += 1 + self.cognitive_depth;
                visitor::walk_stmt(self, statement);
            }
            Stmt::Break(_) | Stmt::Continue(_) => {
                if self.in_loop {
                    self.cognitive += 1;
                }
            }
            _ => visitor::walk_stmt(self, statement),
        }
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::BoolOp(node) => {
                self.complexity += node.values.len().saturating_sub(1) as u32;
                self.cognitive += 1 + self.cognitive_depth;
            }
            Expr::If(_) => self.complexity += 1,
            Expr::Lambda(node) => {
                if let Some(parameters) = &node.parameters {
                    self.visit_parameters(parameters);
                }
                return;
            }
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
    fn visit_comprehension(&mut self, comprehension: &'a ast::Comprehension) {
        self.complexity += comprehension.ifs.len() as u32;
        self.cognitive += 1 + self.cognitive_depth;
        visitor::walk_comprehension(self, comprehension);
    }
    fn visit_except_handler(&mut self, handler: &'a ast::ExceptHandler) {
        self.complexity += 1;
        self.cognitive += 1 + self.cognitive_depth;
        self.block(0, 1, false, |visitor| {
            visitor::walk_except_handler(visitor, handler)
        });
    }
}

pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&std::sync::atomic::AtomicBool>,
) {
    let cognitive = request.check_id.ends_with("cognitive");
    let kind = if cognitive { "cognitive" } else { "complexity" };
    let warn = number_option(
        request,
        if cognitive { "warn" } else { "warn_cc" },
        if cognitive { 30 } else { 15 },
    );
    let fail = number_option(
        request,
        if cognitive { "fail" } else { "fail_cc" },
        if cognitive { 60 } else { 25 },
    );
    let warn_nesting = number_option(request, "warn_nesting", 4);
    let mut peak = 0;
    let mut count = 0;
    for file in files {
        if crate::cancelled(output, cancellation) {
            break;
        }
        let rows = file.metrics.get_or_init(|| scan(file));
        if rows.is_empty() {
            let mut target = ici_domain::InspectionTarget::at(&file.source.path, 1);
            target.target_name = "Python source".into();
            target.message = "No function definitions in this checked file".into();
            output.targets.push(target);
        }
        for row in rows {
            if crate::cancelled(output, cancellation) {
                break;
            }
            count += 1;
            let value = if cognitive {
                row.cognitive
            } else {
                row.complexity
            };
            let legacy_nesting = if cognitive {
                row.cognitive_nesting
            } else {
                row.nesting
            };
            let shared_nesting = row.nesting.max(row.cognitive_nesting);
            peak = peak.max(value);
            let mut target = file.source.target(
                row.range,
                if cognitive {
                    row.name.clone()
                } else {
                    format!("{}()", row.name)
                },
            );
            target.snippet = file
                .source
                .snippet(row.range, if cognitive { 20 } else { usize::MAX });
            target.metrics.insert(kind.into(), json!(value));
            target
                .metrics
                .insert("nesting".into(), json!(legacy_nesting));
            let legacy_fail = if cognitive {
                value >= fail
            } else {
                value > fail
            };
            let legacy_warn = if cognitive {
                value >= warn
            } else {
                value > warn
            } || legacy_nesting >= warn_nesting;
            target.status = if legacy_fail {
                "FAIL"
            } else if legacy_warn {
                "WARN"
            } else {
                "PASS"
            }
            .into();
            target.message = if cognitive {
                target
                    .metrics
                    .insert("metric_source".into(), json!("python-ast"));
                target
                    .metrics
                    .insert("metric_confidence".into(), json!("high"));
                format!("Cognitive {value} (nesting {legacy_nesting})")
            } else if legacy_fail {
                format!(
                    "Critical complexity: {value} > {fail} (Immediate refactoring required, Nesting: {legacy_nesting})"
                )
            } else if legacy_warn {
                format!(
                    "High complexity: {value} (limit {warn}), Max Nesting: {legacy_nesting} (limit {warn_nesting})"
                )
            } else {
                format!("Complexity: {value}, Nesting: {legacy_nesting}")
            };
            let over_fail = value > fail;
            if over_fail || value > warn || shared_nesting > warn_nesting {
                let message = if over_fail {
                    format!("{}: {kind} {value} exceeds fail limit {fail}", row.name)
                } else {
                    let mut parts = Vec::new();
                    if value > warn {
                        parts.push(format!("{kind} {value} exceeds warn limit {warn}"));
                    }
                    if shared_nesting > warn_nesting {
                        parts.push(format!(
                            "nesting depth {shared_nesting} exceeds {warn_nesting}"
                        ));
                    }
                    format!("{}: {}", row.name, parts.join("; "))
                };
                let mut item = Finding::new(
                    format!("metrics.{kind}"),
                    message,
                    file.source.span(row.range),
                    "ici.metrics",
                );
                item.fingerprint = finding::sha1_short(
                    "metrics-",
                    &format!("{}:{}:{kind}", file.source.path, row.name),
                );
                item.severity = if over_fail { "high" } else { "medium" }.into();
                item.component_id = Some(request.component_id.clone());
                item.task_id = Some(request.task_id.clone());
                output.observation.findings.push(item);
            }
            output.targets.push(target);
        }
    }
    output.observation.measurements.extend([
        Measurement {
            unit: "functions".into(),
            ..Measurement::measured("functions_measured", f64::from(count))
        },
        Measurement {
            unit: kind.into(),
            ..Measurement::measured(format!("max_{kind}"), f64::from(peak))
        },
    ]);
    let issues = output
        .targets
        .iter()
        .filter(|target| matches!(target.status.as_str(), "WARN" | "FAIL" | "ERROR"))
        .count();
    output.extra = if cognitive {
        json!({"max_cognitive":peak,"total_functions":count,"python_functions":count,"cpp_functions":0})
    } else {
        json!({"max_complexity":peak,"total_functions":count,"issues_count":issues,"metrics_summary":format!("Max CC: {peak} ({issues} issues / {count} funcs)")})
    };
    if !cognitive {
        output.score = Some(f64::from(peak));
    }
    output.summary = if cognitive {
        format!("Max cognitive complexity: {peak} across {count} functions")
    } else {
        format!(
            "Max Cyclomatic Complexity: {peak} (limit {warn}) across {count} functions ({issues} issues)"
        )
    };
    crate::cancelled(output, cancellation);
    finish_status(output, request);
}
