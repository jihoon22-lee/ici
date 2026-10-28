//! Lexical bindings shared by semantic validation, security, resources and duplicates.
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, Expr, ExprContext, Mod, Pattern, PythonVersion, Stmt};
use ruff_python_parser::semantic_errors::{
    LazyImportContext, SemanticSyntaxChecker, SemanticSyntaxContext, SemanticSyntaxError,
};
use ruff_text_size::{Ranged, TextRange};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};

type Key = (u32, u32);
fn key(value: &impl Ranged) -> Key {
    (value.range().start().to_u32(), value.range().end().to_u32())
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScopeKind {
    Module,
    Function { asynchronous: bool },
    Class,
    Lambda,
    Comprehension { asynchronous: bool, generator: bool },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Binding {
    Import(String),
    Parameter,
    Definition,
    Assigned,
}
#[derive(Clone, Debug)]
pub struct Scope {
    pub kind: ScopeKind,
    pub parent: Option<usize>,
    pub bindings: BTreeMap<String, Binding>,
    pub globals: BTreeMap<String, TextRange>,
    pub nonlocals: BTreeMap<String, TextRange>,
    pub parameters: BTreeSet<String>,
    pub wildcard: bool,
}
impl Scope {
    fn new(kind: ScopeKind, parent: Option<usize>) -> Self {
        Self {
            kind,
            parent,
            bindings: BTreeMap::new(),
            globals: BTreeMap::new(),
            nonlocals: BTreeMap::new(),
            parameters: BTreeSet::new(),
            wildcard: false,
        }
    }
}
#[derive(Clone, Copy, Debug, Default)]
struct Context {
    scope: usize,
    loops: usize,
    tries: usize,
}
enum NameLookup {
    Bound(usize),
    Unknown,
    Unbound,
}
#[derive(Clone, Debug)]
pub struct ScopeGraph {
    pub scopes: Vec<Scope>,
    expression_contexts: BTreeMap<Key, Context>,
    statement_contexts: BTreeMap<Key, Context>,
    pub child_scopes: BTreeMap<Key, usize>,
    future_annotations: bool,
}
impl ScopeGraph {
    pub fn build(module: &Mod) -> Self {
        let mut builder = Builder {
            graph: Self {
                scopes: vec![Scope::new(ScopeKind::Module, None)],
                expression_contexts: BTreeMap::new(),
                statement_contexts: BTreeMap::new(),
                child_scopes: BTreeMap::new(),
                future_annotations: false,
            },
            context: Context::default(),
        };
        if let Mod::Module(module) = module {
            builder.visit_body(&module.body);
        }
        builder.graph
    }
    pub fn scope_of(&self, expression: &Expr) -> usize {
        self.expression_contexts
            .get(&key(expression))
            .map_or(0, |ctx| ctx.scope)
    }
    pub fn scope_of_statement(&self, statement: &Stmt) -> usize {
        self.statement_contexts
            .get(&key(statement))
            .map_or(0, |ctx| ctx.scope)
    }
    pub fn child_scope(&self, node: &impl Ranged) -> Option<usize> {
        self.child_scopes.get(&key(node)).copied()
    }
    pub fn resolve(&self, expression: &Expr) -> Option<String> {
        match expression {
            Expr::Name(name) => self.resolve_name(self.scope_of(expression), name.id.as_str()),
            Expr::Attribute(attribute) => self
                .resolve(&attribute.value)
                .map(|owner| format!("{owner}.{}", attribute.attr)),
            _ => None,
        }
    }
    pub fn binding_scope(&self, scope: usize, name: &str) -> Option<usize> {
        match self.lookup(scope, name) {
            NameLookup::Bound(scope) => Some(scope),
            NameLookup::Unknown | NameLookup::Unbound => None,
        }
    }
    fn lookup(&self, mut scope: usize, name: &str) -> NameLookup {
        let original = scope;
        let mut visited = BTreeSet::new();
        while visited.insert(scope) {
            let current = &self.scopes[scope];
            if current.globals.contains_key(name) && scope != 0 {
                scope = 0;
                continue;
            }
            if current.nonlocals.contains_key(name) {
                let Some(owner) = self.nonlocal_scope(scope, name) else {
                    return NameLookup::Unknown;
                };
                scope = owner;
                continue;
            }
            if current.bindings.contains_key(name) {
                return NameLookup::Bound(scope);
            }
            if current.wildcard {
                return NameLookup::Unknown;
            }
            let Some(mut parent) = current.parent else {
                return NameLookup::Unbound;
            };
            // A class namespace is not a closure for its methods/comprehensions.
            if scope == original
                && matches!(
                    current.kind,
                    ScopeKind::Function { .. }
                        | ScopeKind::Lambda
                        | ScopeKind::Comprehension { .. }
                )
            {
                while self.scopes[parent].kind == ScopeKind::Class {
                    let Some(outer) = self.scopes[parent].parent else {
                        return NameLookup::Unknown;
                    };
                    parent = outer;
                }
            }
            scope = parent;
        }
        NameLookup::Unknown
    }
    pub fn resolve_name(&self, scope: usize, name: &str) -> Option<String> {
        match self.lookup(scope, name) {
            NameLookup::Bound(owner) => {
                return match &self.scopes[owner].bindings[name] {
                    Binding::Import(value) => Some(value.clone()),
                    _ => None,
                };
            }
            NameLookup::Unknown => return None,
            NameLookup::Unbound => {}
        }
        if matches!(
            name,
            "open"
                | "eval"
                | "exec"
                | "compile"
                | "__import__"
                | "list"
                | "dict"
                | "set"
                | "bytearray"
                | "getattr"
                | "setattr"
                | "hasattr"
                | "globals"
                | "locals"
                | "super"
                | "object"
                | "staticmethod"
                | "classmethod"
                | "property"
                | "Exception"
                | "BaseException"
                | "ValueError"
                | "RuntimeError"
        ) {
            Some(name.to_owned())
        } else {
            None
        }
    }
    fn nonlocal_scope(&self, scope: usize, name: &str) -> Option<usize> {
        let mut current = self.scopes[scope].parent;
        while let Some(index) = current {
            let item = &self.scopes[index];
            if item.kind == ScopeKind::Module {
                return None;
            }
            if !matches!(item.kind, ScopeKind::Class)
                && item.bindings.contains_key(name)
                && !item.globals.contains_key(name)
            {
                return Some(index);
            }
            current = item.parent;
        }
        None
    }
    pub fn semantic_errors(
        &self,
        module: &Mod,
        source: &str,
        version: PythonVersion,
    ) -> Vec<SemanticSyntaxError> {
        let errors = RefCell::new(Vec::new());
        let mut checker = SemanticVisitor {
            graph: self,
            source,
            version,
            checker: SemanticSyntaxChecker::new(),
            errors: &errors,
        };
        if let Mod::Module(module) = module {
            checker.visit_body(&module.body);
        }
        errors.into_inner()
    }
}
struct Builder {
    graph: ScopeGraph,
    context: Context,
}
impl Builder {
    fn bind_at(&mut self, scope: usize, name: &str, binding: Binding) {
        self.graph.scopes[scope]
            .bindings
            .entry(name.to_owned())
            .and_modify(|previous| {
                if *previous != binding {
                    *previous = Binding::Assigned;
                }
            })
            .or_insert(binding);
    }
    fn bind(&mut self, name: &str, binding: Binding) {
        self.bind_at(self.context.scope, name, binding);
    }
    fn enter(&mut self, node: &impl Ranged, kind: ScopeKind) -> Context {
        let previous = self.context;
        let index = self.graph.scopes.len();
        self.graph
            .scopes
            .push(Scope::new(kind, Some(previous.scope)));
        self.graph.child_scopes.insert(key(node), index);
        self.context = Context {
            scope: index,
            loops: 0,
            tries: 0,
        };
        previous
    }
    fn parameters(&mut self, parameters: &ast::Parameters) {
        for parameter in parameters.iter() {
            let name = parameter.name().as_str();
            self.graph.scopes[self.context.scope]
                .parameters
                .insert(name.to_owned());
            self.bind(name, Binding::Parameter);
        }
    }
    fn signature(&mut self, parameters: &ast::Parameters) {
        for parameter in parameters.iter_non_variadic_params() {
            if let Some(annotation) = &parameter.parameter.annotation {
                self.visit_expr(annotation);
            }
            if let Some(default) = &parameter.default {
                self.visit_expr(default);
            }
        }
        for parameter in [&parameters.vararg, &parameters.kwarg]
            .into_iter()
            .flatten()
        {
            if let Some(annotation) = &parameter.annotation {
                self.visit_expr(annotation);
            }
        }
    }
    fn comprehension(
        &mut self,
        expression: &Expr,
        generators: &[ast::Comprehension],
        results: &[&Expr],
        generator: bool,
    ) {
        if let Some(first) = generators.first() {
            self.visit_expr(&first.iter);
        }
        let previous = self.enter(
            expression,
            ScopeKind::Comprehension {
                asynchronous: generators.iter().any(|item| item.is_async),
                generator,
            },
        );
        // All iteration variables are local, even at earlier lexical positions.
        for item in generators {
            self.visit_expr(&item.target);
        }
        for (index, item) in generators.iter().enumerate() {
            if index != 0 {
                self.visit_expr(&item.iter);
            }
            for condition in &item.ifs {
                self.visit_expr(condition);
            }
        }
        for result in results {
            self.visit_expr(result);
        }
        self.context = previous;
    }
}
impl<'a> Visitor<'a> for Builder {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        self.graph
            .statement_contexts
            .insert(key(statement), self.context);
        match statement {
            Stmt::FunctionDef(function) => {
                self.bind(function.name.as_str(), Binding::Definition);
                for decorator in &function.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                self.signature(&function.parameters);
                if let Some(returns) = &function.returns {
                    self.visit_expr(returns);
                }
                if let Some(parameters) = &function.type_params {
                    self.visit_type_params(parameters);
                }
                let previous = self.enter(
                    function,
                    ScopeKind::Function {
                        asynchronous: function.is_async,
                    },
                );
                self.parameters(&function.parameters);
                self.visit_body(&function.body);
                self.context = previous;
            }
            Stmt::ClassDef(class) => {
                self.bind(class.name.as_str(), Binding::Definition);
                for decorator in &class.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                if let Some(arguments) = &class.arguments {
                    self.visit_arguments(arguments);
                }
                if let Some(parameters) = &class.type_params {
                    self.visit_type_params(parameters);
                }
                let previous = self.enter(class, ScopeKind::Class);
                self.visit_body(&class.body);
                self.context = previous;
            }
            Stmt::Import(import) => {
                for alias in &import.names {
                    let name = alias.name.as_str();
                    let local = alias.asname.as_ref().map_or_else(
                        || name.split('.').next().unwrap_or(name),
                        |value| value.as_str(),
                    );
                    self.bind(
                        local,
                        Binding::Import(if alias.asname.is_some() {
                            name.to_owned()
                        } else {
                            local.to_owned()
                        }),
                    );
                }
            }
            Stmt::ImportFrom(import) => {
                let prefix = import.module.as_ref().map_or("", |value| value.as_str());
                if prefix == "__future__"
                    && import
                        .names
                        .iter()
                        .any(|name| name.name.as_str() == "annotations")
                {
                    self.graph.future_annotations = true;
                }
                for alias in &import.names {
                    if alias.name.as_str() == "*" {
                        self.graph.scopes[self.context.scope].wildcard = true;
                        continue;
                    }
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if import.level == 0 && !prefix.is_empty() {
                        self.bind(local, Binding::Import(format!("{prefix}.{}", alias.name)));
                    } else {
                        self.bind(local, Binding::Assigned);
                    }
                }
            }
            Stmt::Global(global) => {
                for name in &global.names {
                    self.graph.scopes[self.context.scope]
                        .globals
                        .insert(name.to_string(), name.range());
                }
            }
            Stmt::Nonlocal(nonlocal) => {
                for name in &nonlocal.names {
                    self.graph.scopes[self.context.scope]
                        .nonlocals
                        .insert(name.to_string(), name.range());
                }
            }
            Stmt::For(node) => {
                self.visit_expr(&node.iter);
                self.visit_expr(&node.target);
                self.context.loops += 1;
                self.visit_body(&node.body);
                self.context.loops -= 1;
                self.visit_body(&node.orelse);
            }
            Stmt::While(node) => {
                self.visit_expr(&node.test);
                self.context.loops += 1;
                self.visit_body(&node.body);
                self.context.loops -= 1;
                self.visit_body(&node.orelse);
            }
            Stmt::Try(node) => {
                self.context.tries += 1;
                self.visit_body(&node.body);
                for handler in &node.handlers {
                    self.visit_except_handler(handler);
                }
                self.context.tries -= 1;
                self.visit_body(&node.orelse);
                self.visit_body(&node.finalbody);
            }
            _ => visitor::walk_stmt(self, statement),
        }
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        self.graph
            .expression_contexts
            .insert(key(expression), self.context);
        match expression {
            Expr::Name(name) if matches!(name.ctx, ExprContext::Store | ExprContext::Del) => {
                self.bind(name.id.as_str(), Binding::Assigned)
            }
            Expr::Lambda(lambda) => {
                if let Some(parameters) = &lambda.parameters {
                    self.signature(parameters);
                }
                let previous = self.enter(lambda, ScopeKind::Lambda);
                if let Some(parameters) = &lambda.parameters {
                    self.parameters(parameters);
                }
                self.visit_expr(&lambda.body);
                self.context = previous;
            }
            Expr::ListComp(node) => {
                self.comprehension(expression, &node.generators, &[&node.elt], false)
            }
            Expr::SetComp(node) => {
                self.comprehension(expression, &node.generators, &[&node.elt], false)
            }
            Expr::DictComp(node) => {
                let mut values = vec![node.value.as_ref()];
                if let Some(key) = &node.key {
                    values.insert(0, key);
                }
                self.comprehension(expression, &node.generators, &values, false);
            }
            Expr::Generator(node) => {
                self.comprehension(expression, &node.generators, &[&node.elt], true)
            }
            Expr::Named(node) => {
                self.visit_expr(&node.value);
                let previous = self.context;
                while matches!(
                    self.graph.scopes[self.context.scope].kind,
                    ScopeKind::Comprehension { .. }
                ) {
                    self.context.scope = self.graph.scopes[self.context.scope].parent.unwrap_or(0);
                }
                self.visit_expr(&node.target);
                self.context = previous;
            }
            _ => visitor::walk_expr(self, expression),
        }
    }
    fn visit_except_handler(&mut self, handler: &'a ast::ExceptHandler) {
        let ast::ExceptHandler::ExceptHandler(handler) = handler;
        if let Some(name) = &handler.name {
            self.bind(name.as_str(), Binding::Assigned);
        }
        if let Some(type_) = &handler.type_ {
            self.visit_expr(type_);
        }
        self.visit_body(&handler.body);
    }
    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        match pattern {
            Pattern::MatchAs(pattern) => {
                if let Some(name) = &pattern.name {
                    self.bind(name.as_str(), Binding::Assigned);
                }
            }
            Pattern::MatchStar(pattern) => {
                if let Some(name) = &pattern.name {
                    self.bind(name.as_str(), Binding::Assigned);
                }
            }
            Pattern::MatchMapping(pattern) => {
                if let Some(name) = &pattern.rest {
                    self.bind(name.as_str(), Binding::Assigned);
                }
            }
            _ => {}
        }
        visitor::walk_pattern(self, pattern);
    }
}

struct SemanticContext<'a> {
    graph: &'a ScopeGraph,
    source: &'a str,
    version: PythonVersion,
    context: Context,
    errors: &'a RefCell<Vec<SemanticSyntaxError>>,
}
impl SemanticContext<'_> {
    fn kinds(&self) -> Vec<ScopeKind> {
        let mut current = Some(self.context.scope);
        let mut result = Vec::new();
        while let Some(scope) = current {
            result.push(self.graph.scopes[scope].kind);
            current = self.graph.scopes[scope].parent;
        }
        result
    }
}
impl SemanticSyntaxContext for SemanticContext<'_> {
    fn future_annotations_or_stub(&self) -> bool {
        self.graph.future_annotations
    }
    fn lazy_import_context(&self) -> Option<LazyImportContext> {
        for kind in self.kinds() {
            match kind {
                ScopeKind::Function { .. } | ScopeKind::Lambda => {
                    return Some(LazyImportContext::Function);
                }
                ScopeKind::Class => return Some(LazyImportContext::Class),
                _ => {}
            }
        }
        (self.context.tries > 0).then_some(LazyImportContext::TryExceptBlocks)
    }
    fn python_version(&self) -> PythonVersion {
        self.version
    }
    fn source(&self) -> &str {
        self.source
    }
    fn global(&self, name: &str) -> Option<TextRange> {
        self.graph.scopes[self.context.scope]
            .globals
            .get(name)
            .copied()
    }
    fn has_nonlocal_binding(&self, name: &str) -> bool {
        self.graph
            .nonlocal_scope(self.context.scope, name)
            .is_some()
    }
    fn in_async_context(&self) -> bool {
        for kind in self.kinds() {
            match kind {
                ScopeKind::Function { asynchronous } => return asynchronous,
                ScopeKind::Class | ScopeKind::Lambda | ScopeKind::Module => return false,
                _ => {}
            }
        }
        false
    }
    fn in_await_allowed_context(&self) -> bool {
        self.kinds()
            .into_iter()
            .find_map(|kind| match kind {
                ScopeKind::Function { .. } | ScopeKind::Lambda => Some(true),
                ScopeKind::Class | ScopeKind::Module => Some(false),
                _ => None,
            })
            .unwrap_or(false)
    }
    fn in_yield_allowed_context(&self) -> bool {
        matches!(
            self.graph.scopes[self.context.scope].kind,
            ScopeKind::Function { .. } | ScopeKind::Lambda
        )
    }
    fn in_sync_comprehension(&self) -> bool {
        for kind in self.kinds() {
            match kind {
                ScopeKind::Comprehension {
                    asynchronous: false,
                    ..
                } => return true,
                ScopeKind::Function { .. }
                | ScopeKind::Class
                | ScopeKind::Lambda
                | ScopeKind::Module => return false,
                _ => {}
            }
        }
        false
    }
    fn in_class_body_comprehension(&self) -> bool {
        self.kinds()
            .into_iter()
            .find_map(|kind| match kind {
                ScopeKind::Class => Some(true),
                ScopeKind::Function { .. } | ScopeKind::Lambda | ScopeKind::Module => Some(false),
                _ => None,
            })
            .unwrap_or(false)
    }
    fn in_module_scope(&self) -> bool {
        self.graph.scopes[self.context.scope].kind == ScopeKind::Module
    }
    fn in_function_scope(&self) -> bool {
        matches!(
            self.graph.scopes[self.context.scope].kind,
            ScopeKind::Function { .. } | ScopeKind::Lambda
        )
    }
    fn in_generator_context(&self) -> bool {
        matches!(
            self.graph.scopes[self.context.scope].kind,
            ScopeKind::Comprehension {
                generator: true,
                ..
            }
        )
    }
    fn in_notebook(&self) -> bool {
        false
    }
    fn report_semantic_error(&self, error: SemanticSyntaxError) {
        self.errors.borrow_mut().push(error);
    }
    fn in_loop_context(&self) -> bool {
        self.context.loops > 0
    }
    fn is_bound_parameter(&self, name: &str) -> bool {
        self.graph.scopes[self.context.scope]
            .parameters
            .contains(name)
    }
}
struct SemanticVisitor<'a> {
    graph: &'a ScopeGraph,
    source: &'a str,
    version: PythonVersion,
    checker: SemanticSyntaxChecker,
    errors: &'a RefCell<Vec<SemanticSyntaxError>>,
}
impl<'a> Visitor<'a> for SemanticVisitor<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let context = self
            .graph
            .statement_contexts
            .get(&key(statement))
            .copied()
            .unwrap_or_default();
        self.checker.visit_stmt(
            statement,
            &SemanticContext {
                graph: self.graph,
                source: self.source,
                version: self.version,
                context,
                errors: self.errors,
            },
        );
        visitor::walk_stmt(self, statement);
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        let context = self
            .graph
            .expression_contexts
            .get(&key(expression))
            .copied()
            .unwrap_or_default();
        self.checker.visit_expr(
            expression,
            &SemanticContext {
                graph: self.graph,
                source: self.source,
                version: self.version,
                context,
                errors: self.errors,
            },
        );
        visitor::walk_expr(self, expression);
    }
}
