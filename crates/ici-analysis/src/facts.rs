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
    pub end_line: u32,
    /// Historical coverage policy: every integer line from the first body
    /// statement's start through the function's end, including blank lines.
    pub body_start_line: u32,
    pub body_end_line: u32,
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
}
impl<'a> Visitor<'a> for Collector<'a> {
    fn visit_stmt(&mut self, node: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        match node {
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
                    end_line: span.end_line.unwrap_or(span.start_line),
                    body_start_line,
                    body_end_line: span.end_line.unwrap_or(span.start_line),
                });
                visitor::walk_stmt(self, node);
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
                visitor::walk_stmt(self, node);
                self.classes.pop();
                self.qualifier.pop();
            }
            _ => visitor::walk_stmt(self, node),
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
        };
        collector.visit_body(file.body());
        facts.functions.extend(collector.facts.functions);
        facts.classes.extend(collector.facts.classes);
    }
    output.extra = serde_json::to_value(facts).expect("source facts serialize");
}
