//! Ordered, conditional exception aliases and handler-local traceback rules.
use crate::{
    failure, finding, finish_status, parser::ParsedFile, scope::ScopeKind, source::SourceError,
};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, Measurement};
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, Expr, ExprContext, Pattern, Stmt};
use ruff_text_size::{Ranged, TextRange};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

const EXCEPTION: u8 = 1;
const BUILTINS: u8 = 2;
const SHADOW: u8 = 4;
const DELETE: u8 = 8;
const MATCH_DEFINITE: u8 = 16;
const MATCH: u8 = 32;
const UNBOUND: u8 = 64;
#[derive(Clone)]
struct Event {
    offset: u32,
    sequence: usize,
    name: String,
    kind: u8,
    conditional: bool,
}
struct Handler<'a> {
    node: &'a ast::ExceptHandlerExceptHandler,
    scope: usize,
    depth: usize,
    parents: Vec<(usize, String, u32)>,
}
struct Events<'a> {
    file: &'a ParsedFile,
    scopes: Vec<Vec<Event>>,
    scope_starts: Vec<u32>,
    handlers: Vec<Handler<'a>>,
    parents: Vec<(usize, String, u32)>,
    scope: usize,
    depth: usize,
    conditional: usize,
    sequence: usize,
}
impl<'a> Events<'a> {
    fn collect(file: &'a ParsedFile) -> Self {
        let mut result = Self {
            file,
            scopes: vec![vec![]; file.scopes.scopes.len()],
            scope_starts: vec![0; file.scopes.scopes.len()],
            handlers: vec![],
            parents: vec![],
            scope: 0,
            depth: 0,
            conditional: 0,
            sequence: 1,
        };
        result.visit_body(file.body());
        result
    }
    fn event(&mut self, scope: usize, name: &str, kind: u8, range: TextRange) {
        self.scopes[scope].push(Event {
            offset: range.start().to_u32(),
            sequence: self.sequence,
            name: name.into(),
            kind,
            conditional: self.conditional > 0,
        });
        self.sequence += 1;
    }
    fn conditional(&mut self, run: impl FnOnce(&mut Self)) {
        self.conditional += 1;
        run(self);
        self.conditional -= 1;
    }
    fn scope_body(&mut self, scope: usize, range: TextRange, body: &'a [Stmt]) {
        let previous = (self.scope, self.conditional);
        self.scope = scope;
        self.conditional = 0;
        self.scope_starts[scope] = range.start().to_u32();
        self.visit_body(body);
        (self.scope, self.conditional) = previous;
    }
    fn active_scopes(&self, handler: &Handler<'_>) -> Vec<usize> {
        let mut current = Some(handler.scope);
        let mut result = vec![];
        let mut function_seen = false;
        while let Some(index) = current {
            let scope = &self.file.scopes.scopes[index];
            if matches!(scope.kind, ScopeKind::Function { .. } | ScopeKind::Lambda) {
                function_seen = true;
            }
            if !(scope.kind == ScopeKind::Class && function_seen) {
                result.push(index);
            }
            current = scope.parent;
        }
        result.reverse();
        result
    }
    fn aliases(&self, handler: &Handler<'_>) -> Vec<Aliases> {
        let scopes = self.active_scopes(handler);
        scopes
            .iter()
            .enumerate()
            .map(|(index, scope)| {
                let current = index + 1 == scopes.len();
                let cutoff = if current {
                    handler.node.range().start().to_u32()
                } else {
                    self.scope_starts[scopes[index + 1]]
                };
                let future = !current
                    && matches!(
                        self.file.scopes.scopes[scopes[index + 1]].kind,
                        ScopeKind::Function { .. }
                    );
                let mut events = self.scopes[*scope].clone();
                for (owner, name, offset) in &handler.parents {
                    if owner == scope {
                        events.push(Event {
                            offset: *offset,
                            sequence: 0,
                            name: name.clone(),
                            kind: SHADOW,
                            conditional: false,
                        });
                    }
                }
                events.sort_by_key(|event| (event.offset, event.sequence));
                let mut bindings: BTreeMap<String, u8> = BTreeMap::new();
                for event in events.iter().filter(|event| event.offset < cutoff) {
                    if event.conditional {
                        *bindings.entry(event.name.clone()).or_insert(UNBOUND) |= event.kind;
                    } else {
                        bindings.insert(event.name.clone(), event.kind);
                    }
                }
                let mut result = Aliases::default();
                for (name, kinds) in &bindings {
                    if kinds & EXCEPTION != 0 {
                        result.exception.insert(name.clone());
                    }
                    if kinds & BUILTINS != 0 {
                        result.builtins.insert(name.clone());
                    }
                }
                if future {
                    for event in events.iter().filter(|event| event.offset >= cutoff) {
                        if event.kind == EXCEPTION {
                            result.exception.insert(event.name.clone());
                        }
                        if event.kind == BUILTINS {
                            result.builtins.insert(event.name.clone());
                        }
                    }
                }
                for (name, kinds) in bindings {
                    if !result.exception.contains(&name)
                        && !result.builtins.contains(&name)
                        && ((kinds & SHADOW != 0
                            && kinds & UNBOUND == 0
                            && !(kinds & DELETE != 0 && name == "BaseException"))
                            || kinds & MATCH_DEFINITE != 0
                            || (kinds & DELETE != 0 && name != "BaseException"))
                    {
                        result.shadowed.insert(name);
                    }
                }
                let scope_data = &self.file.scopes.scopes[*scope];
                if current && matches!(scope_data.kind, ScopeKind::Function { .. }) {
                    for event in &events {
                        if !scope_data.globals.contains_key(&event.name)
                            && !scope_data.nonlocals.contains_key(&event.name)
                            && !(event.offset == cutoff
                                && handler
                                    .node
                                    .name
                                    .as_ref()
                                    .is_some_and(|name| name.as_str() == event.name))
                        {
                            result.bound.insert(event.name.clone());
                        }
                    }
                }
                result
            })
            .collect()
    }
}
impl<'a> Visitor<'a> for Events<'a> {
    fn visit_stmt(&mut self, node: &'a Stmt) {
        let previous_scope = self.scope;
        self.scope = self.file.scopes.scope_of_statement(node);
        self.depth += 1;
        match node {
            Stmt::FunctionDef(function) => {
                self.event(self.scope, function.name.as_str(), SHADOW, node.range());
                if let Some(child) = self.file.scopes.child_scope(function) {
                    let old = self.conditional;
                    self.conditional = 0;
                    for parameter in function.parameters.iter() {
                        self.event(
                            child,
                            parameter.name().as_str(),
                            SHADOW,
                            parameter.as_parameter().range(),
                        );
                    }
                    self.conditional = old;
                    self.scope_body(child, node.range(), &function.body);
                }
            }
            Stmt::ClassDef(class) => {
                self.event(self.scope, class.name.as_str(), SHADOW, node.range());
                if let Some(child) = self.file.scopes.child_scope(class) {
                    self.scope_body(child, node.range(), &class.body);
                }
            }
            Stmt::Import(import) => {
                for alias in &import.names {
                    let name = alias.asname.as_ref().map_or_else(
                        || {
                            alias
                                .name
                                .as_str()
                                .split('.')
                                .next()
                                .unwrap_or(alias.name.as_str())
                        },
                        |name| name.as_str(),
                    );
                    self.event(
                        self.scope,
                        name,
                        if alias.name.as_str() == "builtins" {
                            BUILTINS
                        } else {
                            SHADOW
                        },
                        node.range(),
                    );
                }
            }
            Stmt::ImportFrom(import) => {
                for alias in &import.names {
                    if alias.name.as_str() != "*" {
                        let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                        let kind = if import.level == 0
                            && import
                                .module
                                .as_ref()
                                .is_some_and(|module| module.as_str() == "builtins")
                            && alias.name.as_str() == "BaseException"
                        {
                            EXCEPTION
                        } else {
                            SHADOW
                        };
                        self.event(self.scope, name, kind, node.range());
                    }
                }
            }
            Stmt::If(node) => {
                self.visit_expr(&node.test);
                self.conditional(|visitor| visitor.visit_body(&node.body));
                let base = self.depth;
                let mut branch_depth = 0;
                for clause in &node.elif_else_clauses {
                    if clause.test.is_some() {
                        branch_depth += 1;
                    }
                    self.depth = base + branch_depth;
                    self.conditional(|visitor| {
                        if let Some(test) = &clause.test {
                            visitor.visit_expr(test);
                        }
                        visitor.visit_body(&clause.body);
                    });
                }
                self.depth = base;
            }
            Stmt::For(node) => {
                self.visit_expr(&node.iter);
                self.conditional(|visitor| {
                    visitor.visit_expr(&node.target);
                    visitor.visit_body(&node.body);
                    visitor.visit_body(&node.orelse);
                });
            }
            Stmt::While(node) => {
                self.visit_expr(&node.test);
                self.conditional(|visitor| {
                    visitor.visit_body(&node.body);
                    visitor.visit_body(&node.orelse);
                });
            }
            Stmt::With(node) => {
                for (index, item) in node.items.iter().enumerate() {
                    if index == 0 {
                        self.visit_expr(&item.context_expr);
                    } else {
                        self.conditional(|visitor| visitor.visit_expr(&item.context_expr));
                    }
                    if let Some(target) = &item.optional_vars {
                        self.conditional(|visitor| visitor.visit_expr(target));
                    }
                }
                self.conditional(|visitor| visitor.visit_body(&node.body));
            }
            Stmt::Try(_) => self.conditional(|visitor| visitor::walk_stmt(visitor, node)),
            Stmt::Match(node) => {
                self.visit_expr(&node.subject);
                let definite = node.cases.len() == 1
                    && node.cases[0].guard.is_none()
                    && matches!(&node.cases[0].pattern,Pattern::MatchAs(pattern)if pattern.pattern.is_none()&&pattern.name.is_some());
                for case in &node.cases {
                    self.conditional(|visitor| {
                        let mut captures = CaptureNames(vec![]);
                        captures.visit_pattern(&case.pattern);
                        for (name, range) in captures.0 {
                            visitor.event(
                                visitor.scope,
                                &name,
                                if definite { MATCH_DEFINITE } else { MATCH },
                                range,
                            );
                        }
                        if let Some(guard) = &case.guard {
                            visitor.visit_expr(guard);
                        }
                        visitor.visit_body(&case.body);
                    });
                }
            }
            _ => visitor::walk_stmt(self, node),
        }
        self.depth -= 1;
        self.scope = previous_scope;
    }
    fn visit_expr(&mut self, node: &'a Expr) {
        match node {
            Expr::Name(name) if matches!(name.ctx, ExprContext::Store | ExprContext::Del) => self
                .event(
                    self.file.scopes.scope_of(node),
                    name.id.as_str(),
                    if name.ctx == ExprContext::Del {
                        DELETE
                    } else {
                        SHADOW
                    },
                    node.range(),
                ),
            Expr::BoolOp(node) => {
                if let Some((first, rest)) = node.values.split_first() {
                    self.visit_expr(first);
                    self.conditional(|visitor| {
                        for value in rest {
                            visitor.visit_expr(value);
                        }
                    });
                }
            }
            Expr::If(node) => {
                self.visit_expr(&node.test);
                self.conditional(|visitor| {
                    visitor.visit_expr(&node.body);
                    visitor.visit_expr(&node.orelse);
                });
            }
            Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_) => {}
            _ => visitor::walk_expr(self, node),
        }
    }
    fn visit_except_handler(&mut self, node: &'a ast::ExceptHandler) {
        let ast::ExceptHandler::ExceptHandler(handler) = node;
        self.depth += 1;
        self.handlers.push(Handler {
            node: handler,
            scope: self.scope,
            depth: self.depth,
            parents: self.parents.clone(),
        });
        if let Some(type_) = &handler.type_ {
            self.visit_expr(type_);
        }
        if let Some(name) = &handler.name {
            self.parents.push((
                self.scope,
                name.to_string(),
                handler.range().start().to_u32(),
            ));
        }
        self.conditional(|visitor| visitor.visit_body(&handler.body));
        if handler.name.is_some() {
            self.parents.pop();
        }
        self.depth -= 1;
    }
}
struct CaptureNames(Vec<(String, TextRange)>);
impl<'a> Visitor<'a> for CaptureNames {
    fn visit_pattern(&mut self, node: &'a Pattern) {
        match node {
            Pattern::MatchAs(node) => {
                if let Some(name) = &node.name {
                    self.0.push((name.to_string(), node.range()));
                }
            }
            Pattern::MatchStar(node) => {
                if let Some(name) = &node.name {
                    self.0.push((name.to_string(), node.range()));
                }
            }
            Pattern::MatchMapping(node) => {
                if let Some(name) = &node.rest {
                    self.0.push((name.to_string(), node.range()));
                }
            }
            _ => {}
        }
        visitor::walk_pattern(self, node);
    }
}
#[derive(Default)]
struct Aliases {
    exception: BTreeSet<String>,
    builtins: BTreeSet<String>,
    shadowed: BTreeSet<String>,
    bound: BTreeSet<String>,
}
fn resolves(aliases: &[Aliases], name: &str, kind: u8) -> bool {
    for scope in aliases.iter().rev() {
        let matches = if kind == EXCEPTION {
            &scope.exception
        } else {
            &scope.builtins
        };
        if matches.contains(name) {
            return true;
        }
        if scope.shadowed.contains(name) || scope.bound.contains(name) {
            return false;
        }
    }
    kind == EXCEPTION && name == "BaseException"
}
fn base_exception(expression: &Expr, aliases: &[Aliases]) -> bool {
    match expression {
        Expr::Name(name) => resolves(aliases, name.id.as_str(), EXCEPTION),
        Expr::Attribute(attribute) => {
            attribute.attr.as_str() == "BaseException"
                && matches!(attribute.value.as_ref(),Expr::Name(name)if resolves(aliases,name.id.as_str(),BUILTINS))
        }
        Expr::Tuple(node) => node.elts.iter().any(|item| base_exception(item, aliases)),
        Expr::List(node) => node.elts.iter().any(|item| base_exception(item, aliases)),
        _ => false,
    }
}
struct Raises<'a> {
    alias: &'a str,
    ranges: Vec<TextRange>,
}
impl<'a> Visitor<'a> for Raises<'_> {
    fn visit_stmt(&mut self, node: &'a Stmt) {
        match node {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => return,
            Stmt::Raise(raise)
                if raise.cause.is_none()
                    && matches!(raise.exc.as_deref(),Some(Expr::Name(name))if name.id.as_str()==self.alias) =>
            {
                self.ranges.push(node.range())
            }
            _ => {}
        }
        visitor::walk_stmt(self, node);
    }
    fn visit_except_handler(&mut self, node: &'a ast::ExceptHandler) {
        let ast::ExceptHandler::ExceptHandler(handler) = node;
        if handler
            .name
            .as_ref()
            .is_some_and(|name| name.as_str() == self.alias)
        {
            return;
        }
        visitor::walk_except_handler(self, node);
    }
}
fn target(
    file: &ParsedFile,
    range: TextRange,
    name: &str,
    status: &str,
    message: String,
) -> InspectionTarget {
    let mut target = file.source.target(range, name);
    target.status = status.into();
    target.message = message;
    target
}
pub fn analyze(request: &AnalysisRequest, files: &[Arc<ParsedFile>], output: &mut AnalysisOutput) {
    let mut checked = 0usize;
    for file in files {
        let events = Events::collect(file);
        let event_count: usize = events.scopes.iter().map(Vec::len).sum();
        if event_count.saturating_mul(events.handlers.len()) > 2_000_000 {
            failure(
                output,
                &file.source.path,
                &SourceError::new(
                    "exception-alias-limit",
                    "Exception alias queries exceed the bounded analysis limit",
                ),
            );
            continue;
        }
        checked += 1;
        let mut handlers: Vec<_> = events.handlers.iter().collect();
        handlers.sort_by_key(|handler| (handler.depth, handler.node.range().start()));
        let start = output.targets.len();
        for handler in handlers {
            let node = handler.node;
            if let Some(type_) = &node.type_ {
                if base_exception(type_, &events.aliases(handler)) {
                    output.targets.push(target(file,type_.range(),"BaseException","FAIL","Catching BaseException also intercepts system-exit and keyboard interrupt signals; catch a narrower exception type".into()));
                }
            } else {
                output.targets.push(target(
                    file,
                    node.range(),
                    "BareExcept",
                    "FAIL",
                    "Dangerous bare 'except:' clause catches all exceptions".into(),
                ));
            }
            if node.body.len() == 1
                && (matches!(&node.body[0], Stmt::Pass(_))
                    || matches!(&node.body[0],Stmt::Expr(ast::StmtExpr{value,..})if matches!(value.as_ref(),Expr::EllipsisLiteral(_))))
            {
                output.targets.push(target(
                    file,
                    node.range(),
                    "ErrorSwallowing",
                    "FAIL",
                    "Silent exception swallowing: handler only contains pass".into(),
                ));
            }
            if let Some(name) = &node.name {
                let mut visitor = Raises {
                    alias: name.as_str(),
                    ranges: vec![],
                };
                visitor.visit_body(&node.body);
                for range in visitor.ranges {
                    output.targets.push(target(file,range,"LostTraceback","WARN",format!("raise {name} resets the traceback; use bare 'raise' to re-raise the caught exception")));
                }
            }
        }
        let found = output.targets.len() > start;
        for target in output.targets[start..].to_vec() {
            finding::hygiene(
                output,
                request,
                &target,
                "exception",
                if target.status == "FAIL" {
                    "high"
                } else {
                    "medium"
                },
                "correctness",
                "high",
            );
        }
        let mut pass = InspectionTarget::at(&file.source.path, 1);
        pass.target_name = "PythonExceptionSafety".into();
        pass.message = if found {
            "Python exception handlers were inspected; see findings above"
        } else {
            "Python exception handlers were inspected"
        }
        .into();
        output.targets.push(pass);
    }
    let fail = output
        .targets
        .iter()
        .filter(|target| target.status == "FAIL")
        .count();
    let warn = output
        .targets
        .iter()
        .filter(|target| target.status == "WARN")
        .count();
    output.extra =
        json!({"violations_count":fail+warn,"metrics_summary":format!("{fail} exc errors")});
    output.observation.measurements.extend([
        Measurement {
            unit: "files".into(),
            ..Measurement::measured("files_checked", checked as f64)
        },
        Measurement {
            unit: "issues".into(),
            ..Measurement::measured("issues_found", (fail + warn) as f64)
        },
    ]);
    output.summary = if fail + warn == 0 {
        "Exception Handling Safety Clean".into()
    } else {
        format!("{fail} Critical Exception Violations, {warn} Warnings")
    };
    finish_status(output, request);
    if request.options.get("mode").is_none() && output.status == "WARN" {
        output.status = "FAIL".into();
    }
}
