//! Source locations shared with native coverage and pytest consumers.
use crate::{cancelled, parser::ParsedFile};
use ici_domain::AnalysisOutput;
use ruff_python_ast::{
    Stmt,
    visitor::{self, Visitor},
};
use ruff_text_size::Ranged;
use serde::{Deserialize, Serialize};
use std::sync::{Arc, atomic::AtomicBool};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FunctionFact {
    pub path: String,
    pub name: String,
    pub qualified_name: String,
    pub class_names: Vec<String>,
    pub start_line: u32,
    /// Code-object entry location includes decorators, independently of def.
    pub entry_line: u32,
    pub end_line: u32,
    /// Historical coverage policy: every integer line from the first body
    /// statement's start through the function's end, including blank lines.
    pub body_start_line: u32,
    pub body_end_line: u32,
    /// The `def` statement's CPython `ast` depth (its ancestors, the module included): frozen
    /// `compute_python_function_coverage` lists functions in `ast.walk` breadth-first order,
    /// which is (depth, source position).
    #[serde(default)]
    pub ast_depth: u32,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ClassFact {
    pub path: String,
    pub name: String,
    pub qualified_name: String,
    pub start_line: u32,
    pub end_line: u32,
}
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceFacts {
    pub functions: Vec<FunctionFact>,
    pub classes: Vec<ClassFact>,
}
struct Collector<'a> {
    file: &'a ParsedFile,
    facts: SourceFacts,
    qualifier: Vec<String>,
    classes: Vec<String>,
    cancellation: Option<&'a AtomicBool>,
    /// CPython `ast` depth of the statements being visited (module body: 1).
    depth: u32,
}
impl Collector<'_> {
    fn nested(&mut self, walk: impl FnOnce(&mut Self)) {
        self.depth += 1;
        walk(self);
        self.depth -= 1;
    }
}
impl<'a> Visitor<'a> for Collector<'a> {
    // CPython wraps an `except` body in an `ExceptHandler` and a `case` body in a `match_case`.
    fn visit_except_handler(&mut self, handler: &'a ruff_python_ast::ExceptHandler) {
        self.nested(|this| visitor::walk_except_handler(this, handler));
    }
    fn visit_match_case(&mut self, case: &'a ruff_python_ast::MatchCase) {
        self.nested(|this| visitor::walk_match_case(this, case));
    }
    fn visit_stmt(&mut self, node: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        match node {
            Stmt::If(statement) => {
                // CPython nests every `elif` as an `If` in its predecessor's `orelse`: the k-th
                // `elif` body is k + 2 levels below this `if`, a final `else` k + 1 levels.
                let depth = self.depth;
                self.depth = depth + 1;
                self.visit_body(&statement.body);
                for (index, clause) in (0u32..).zip(&statement.elif_else_clauses) {
                    self.depth = depth + index + if clause.test.is_some() { 2 } else { 1 };
                    self.visit_body(&clause.body);
                }
                self.depth = depth;
            }
            Stmt::FunctionDef(function) => {
                self.qualifier.push(function.name.to_string());
                let span = self
                    .file
                    .source
                    .span(crate::ast_util::function_range(self.file, function));
                let body_start_line = function.body.first().map_or(span.start_line, |body| {
                    self.file
                        .source
                        .location(
                            crate::ast_util::statement_range(self.file, body)
                                .start()
                                .to_usize(),
                        )
                        .0
                });
                self.facts.functions.push(FunctionFact {
                    path: self.file.source.path.clone(),
                    name: function.name.to_string(),
                    qualified_name: self.qualifier.join("."),
                    class_names: self.classes.clone(),
                    start_line: span.start_line,
                    entry_line: function.decorator_list.first().map_or(
                        span.start_line,
                        |decorator| {
                            self.file
                                .source
                                .location(decorator.range().start().to_usize())
                                .0
                        },
                    ),
                    end_line: span.end_line.unwrap_or(span.start_line),
                    body_start_line,
                    body_end_line: span.end_line.unwrap_or(span.start_line),
                    ast_depth: self.depth,
                });
                self.nested(|this| visitor::walk_stmt(this, node));
                self.qualifier.pop();
            }
            Stmt::ClassDef(class) => {
                self.qualifier.push(class.name.to_string());
                self.classes.push(class.name.to_string());
                let start = self
                    .file
                    .source
                    .location(
                        crate::ast_util::statement_range(self.file, node)
                            .start()
                            .to_usize(),
                    )
                    .0;
                let span = self.file.source.span(class.range());
                self.facts.classes.push(ClassFact {
                    path: self.file.source.path.clone(),
                    name: class.name.to_string(),
                    qualified_name: self.qualifier.join("."),
                    start_line: start,
                    end_line: span.end_line.unwrap_or(start),
                });
                self.nested(|this| visitor::walk_stmt(this, node));
                self.classes.pop();
                self.qualifier.pop();
            }
            _ => self.nested(|this| visitor::walk_stmt(this, node)),
        }
    }
}
pub(crate) fn analyze(
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    let mut facts = SourceFacts::default();
    for file in files {
        if cancelled(output, cancellation) {
            break;
        }
        let mut collector = Collector {
            file,
            facts: SourceFacts::default(),
            qualifier: vec![],
            classes: vec![],
            cancellation,
            depth: 1,
        };
        collector.visit_body(file.body());
        facts.functions.extend(collector.facts.functions);
        facts.classes.extend(collector.facts.classes);
    }
    output.extra = serde_json::to_value(facts).expect("source facts serialize");
}
