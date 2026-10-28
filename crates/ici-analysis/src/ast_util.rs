use crate::parser::ParsedFile;
use ruff_python_ast::visitor::source_order::{SourceOrderVisitor, TraversalSignal};
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, AnyNodeRef, Expr, ExprContext, Stmt};
use ruff_text_size::{Ranged, TextRange};
use std::collections::BTreeSet;

/// CPython function locations start at async/def, excluding decorators.
pub fn function_range(file: &ParsedFile, function: &ast::StmtFunctionDef) -> TextRange {
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
    TextRange::new(start, function.range().end())
}

pub fn functions(file: &ParsedFile) -> Vec<&ast::StmtFunctionDef> {
    struct Collector<'a>(Vec<&'a ast::StmtFunctionDef>);
    impl<'a> SourceOrderVisitor<'a> for Collector<'a> {
        fn enter_node(&mut self, node: AnyNodeRef<'a>) -> TraversalSignal {
            if let AnyNodeRef::StmtFunctionDef(function) = node {
                self.0.push(function);
            }
            TraversalSignal::Traverse
        }
    }
    let mut collector = Collector(vec![]);
    collector.visit_mod(file.parsed.syntax());
    collector.0
}
pub fn loaded_names(expression: &Expr) -> BTreeSet<String> {
    struct Names(BTreeSet<String>);
    impl<'a> Visitor<'a> for Names {
        fn visit_expr(&mut self, expression: &'a Expr) {
            if let Expr::Name(name) = expression
                && name.ctx == ExprContext::Load
            {
                self.0.insert(name.id.to_string());
            }
            visitor::walk_expr(self, expression);
        }
    }
    let mut names = Names(BTreeSet::new());
    names.visit_expr(expression);
    names.0
}
pub fn bound_names(expression: &Expr) -> Vec<String> {
    match expression {
        Expr::Name(name) => vec![name.id.to_string()],
        Expr::Tuple(tuple) => tuple.elts.iter().flat_map(bound_names).collect(),
        Expr::List(list) => list.elts.iter().flat_map(bound_names).collect(),
        _ => vec![],
    }
}
pub fn direct_call(expression: &Expr) -> Option<&ast::ExprCall> {
    match expression {
        Expr::Call(call) => Some(call),
        Expr::Await(await_) => await_.value.as_call_expr(),
        _ => None,
    }
}
/// Count the CPython AST projection rather than parser implementation detail nodes.
pub fn cpython_node_count(file: &ParsedFile) -> usize {
    struct Counter {
        count: usize,
        interpolated: bool,
    }
    impl<'a> Visitor<'a> for Counter {
        fn visit_stmt(&mut self, node: &'a Stmt) {
            self.count += 1;
            visitor::walk_stmt(self, node);
        }
        fn visit_expr(&mut self, node: &'a Expr) {
            self.count += 1;
            let old = self.interpolated;
            self.interpolated = matches!(node, Expr::FString(_) | Expr::TString(_));
            visitor::walk_expr(self, node);
            self.interpolated = old;
        }
        fn visit_expr_context(&mut self, _: &'a ExprContext) {
            self.count += 1;
        }
        fn visit_bool_op(&mut self, _: &'a ast::BoolOp) {
            self.count += 1;
        }
        fn visit_operator(&mut self, _: &'a ast::Operator) {
            self.count += 1;
        }
        fn visit_unary_op(&mut self, _: &'a ast::UnaryOp) {
            self.count += 1;
        }
        fn visit_cmp_op(&mut self, _: &'a ast::CmpOp) {
            self.count += 1;
        }
        fn visit_comprehension(&mut self, node: &'a ast::Comprehension) {
            self.count += 1;
            visitor::walk_comprehension(self, node);
        }
        fn visit_except_handler(&mut self, node: &'a ast::ExceptHandler) {
            self.count += 1;
            visitor::walk_except_handler(self, node);
        }
        fn visit_parameters(&mut self, node: &'a ast::Parameters) {
            self.count += 1;
            visitor::walk_parameters(self, node);
        }
        fn visit_parameter(&mut self, node: &'a ast::Parameter) {
            self.count += 1;
            visitor::walk_parameter(self, node);
        }
        fn visit_keyword(&mut self, node: &'a ast::Keyword) {
            self.count += 1;
            visitor::walk_keyword(self, node);
        }
        fn visit_alias(&mut self, _: &'a ast::Alias) {
            self.count += 1;
        }
        fn visit_with_item(&mut self, node: &'a ast::WithItem) {
            self.count += 1;
            visitor::walk_with_item(self, node);
        }
        fn visit_type_param(&mut self, node: &'a ast::TypeParam) {
            self.count += 1;
            visitor::walk_type_param(self, node);
        }
        fn visit_match_case(&mut self, node: &'a ast::MatchCase) {
            self.count += 1;
            visitor::walk_match_case(self, node);
        }
        fn visit_pattern(&mut self, node: &'a ast::Pattern) {
            self.count += 1;
            visitor::walk_pattern(self, node);
        }
        fn visit_elif_else_clause(&mut self, node: &'a ast::ElifElseClause) {
            if node.test.is_some() {
                self.count += 1;
            }
            visitor::walk_elif_else_clause(self, node);
        }
        fn visit_string_literal(&mut self, _: &'a ast::StringLiteral) {
            if self.interpolated {
                self.count += 1;
            }
        }
        fn visit_interpolated_string_element(&mut self, node: &'a ast::InterpolatedStringElement) {
            self.count += 1;
            if let ast::InterpolatedStringElement::Interpolation(item) = node
                && item.format_spec.is_some()
            {
                self.count += 1;
            }
            visitor::walk_interpolated_string_element(self, node);
        }
    }
    let mut counter = Counter {
        count: 1,
        interpolated: false,
    };
    counter.visit_body(file.body());
    counter.count
}
