//! Bounded intraprocedural ownership flow, preserving lexical close/transfer evidence.
use crate::ast_util::{bound_names, cpython_node_count, direct_call, functions, loaded_names};
use crate::{failure, finding, finish_status, parser::ParsedFile, source::SourceError};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, Measurement};
use ruff_python_ast::{self as ast, Expr, Stmt};
use ruff_text_size::{Ranged, TextRange};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

const OPEN: u8 = 1;
const CLOSED: u8 = 2;
const TRANSFERRED: u8 = 4;
#[derive(Clone, Default)]
struct State {
    resources: BTreeMap<usize, u8>,
    names: BTreeMap<String, BTreeSet<usize>>,
    managers: BTreeSet<String>,
}
#[derive(Clone, Copy)]
enum Exit {
    Return,
    Raise,
    Break,
    Continue,
}
struct Flow {
    live: Option<State>,
    exits: Vec<(Exit, State)>,
}
impl Flow {
    fn live(state: State) -> Self {
        Self {
            live: Some(state),
            exits: vec![],
        }
    }
}
struct Acquisition {
    range: TextRange,
    factory: String,
    variable: String,
}
fn merge(states: Vec<State>) -> Option<State> {
    if states.is_empty() {
        return None;
    }
    let mut merged = State::default();
    for state in states {
        for (id, outcome) in state.resources {
            *merged.resources.entry(id).or_default() |= outcome;
        }
        for (name, ids) in state.names {
            merged.names.entry(name).or_default().extend(ids);
        }
        merged.managers.extend(state.managers);
    }
    Some(merged)
}
fn outcome(state: &mut State, ids: &BTreeSet<usize>, outcome: u8) {
    for id in ids {
        if let Some(value) = state.resources.get_mut(id) {
            if *value & OPEN != 0 {
                *value = (*value & !OPEN) | outcome;
            }
        }
    }
}
fn expression_ids(state: &State, expression: Option<&Expr>) -> BTreeSet<usize> {
    expression
        .map(loaded_names)
        .unwrap_or_default()
        .into_iter()
        .flat_map(|name| state.names.get(&name).cloned().unwrap_or_default())
        .collect()
}
fn transfer(state: &mut State, expression: Option<&Expr>) {
    let ids = expression_ids(state, expression);
    outcome(state, &ids, TRANSFERRED);
}
struct FlowAnalyzer<'a> {
    file: &'a ParsedFile,
    cancellation: Option<&'a std::sync::atomic::AtomicBool>,
    interrupted: bool,
    acquisitions: BTreeMap<usize, Acquisition>,
    next_id: usize,
}
impl FlowAnalyzer<'_> {
    fn qualified(&self, expression: &Expr) -> Option<String> {
        self.file.scopes.resolve(expression).map(|name| {
            if matches!(
                name.as_str(),
                "open" | "list" | "dict" | "set" | "bytearray"
            ) {
                format!("builtins.{name}")
            } else {
                name
            }
        })
    }
    fn factory(&self, call: &ast::ExprCall) -> Option<String> {
        if let Some(name) = self.qualified(&call.func) {
            if matches!(
                name.as_str(),
                "builtins.open"
                    | "io.open"
                    | "socket.socket"
                    | "tempfile.NamedTemporaryFile"
                    | "tempfile.SpooledTemporaryFile"
                    | "tempfile.TemporaryFile"
            ) {
                return Some(name);
            }
        }
        if let Expr::Attribute(attribute) = call.func.as_ref() {
            if attribute.attr.as_str() == "open" {
                if let Expr::Call(owner) = attribute.value.as_ref() {
                    if self.qualified(&owner.func).as_deref() == Some("pathlib.Path") {
                        return Some("pathlib.Path.open".into());
                    }
                }
            }
        }
        None
    }
    fn new_resource(
        &mut self,
        state: &mut State,
        expression: &Expr,
        factory: String,
        names: &[String],
    ) {
        let id = self.next_id;
        self.next_id += 1;
        self.acquisitions.insert(
            id,
            Acquisition {
                range: expression.range(),
                factory,
                variable: names.first().cloned().unwrap_or_default(),
            },
        );
        state.resources.insert(id, OPEN);
        for name in names {
            state.names.insert(name.clone(), BTreeSet::from([id]));
        }
    }
    fn process_call(&self, state: &mut State, expression: Option<&Expr>) {
        let Some(call) = expression.and_then(direct_call) else {
            return;
        };
        let Expr::Attribute(attribute) = call.func.as_ref() else {
            return;
        };
        let Expr::Name(owner) = attribute.value.as_ref() else {
            return;
        };
        if matches!(attribute.attr.as_str(), "close" | "aclose") {
            let ids = state
                .names
                .get(owner.id.as_str())
                .cloned()
                .unwrap_or_default();
            outcome(state, &ids, CLOSED);
        } else if attribute.attr.as_str() == "enter_context"
            && state.managers.contains(owner.id.as_str())
        {
            transfer(state, call.arguments.args.first());
        }
    }
    fn assign(&mut self, state: &mut State, targets: &[&Expr], value: &Expr) {
        let names: Vec<String> = targets
            .iter()
            .flat_map(|target| bound_names(target))
            .collect();
        if let Some(factory) = direct_call(value).and_then(|call| self.factory(call)) {
            if !names.is_empty() {
                self.new_resource(state, value, factory, &names);
                if targets.iter().any(|target| bound_names(target).is_empty()) {
                    let ids = state.names.get(&names[0]).cloned().unwrap_or_default();
                    outcome(state, &ids, TRANSFERRED);
                }
            }
            return;
        }
        let aliases = if let Expr::Name(name) = value {
            state.names.get(name.id.as_str()).cloned()
        } else {
            None
        };
        for name in names {
            if let Some(ids) = &aliases {
                state.names.insert(name, ids.clone());
            } else {
                state.names.remove(&name);
            }
        }
        if targets.iter().any(|target| bound_names(target).is_empty()) {
            transfer(state, Some(value));
        }
        self.process_call(state, Some(value));
    }
    fn suite(&mut self, statements: &[Stmt], initial: State) -> Flow {
        let mut live = Some(initial);
        let mut exits = vec![];
        for statement in statements {
            if crate::cancellation_requested(self.cancellation) {
                self.interrupted = true;
                break;
            }
            let Some(state) = live else {
                break;
            };
            let flow = self.statement(statement, state);
            live = flow.live;
            exits.extend(flow.exits);
        }
        Flow { live, exits }
    }
    fn branch(&mut self, suites: Vec<&[Stmt]>, state: State) -> Flow {
        let mut live = vec![];
        let mut exits = vec![];
        for suite in suites {
            let flow = self.suite(suite, state.clone());
            if let Some(state) = flow.live {
                live.push(state);
            }
            exits.extend(flow.exits);
        }
        Flow {
            live: merge(live),
            exits,
        }
    }
    fn with(&mut self, node: &ast::StmtWith, state: State) -> Flow {
        let mut managed = BTreeSet::new();
        let mut managers = BTreeSet::new();
        for item in &node.items {
            managed.extend(expression_ids(&state, Some(&item.context_expr)));
            let name = direct_call(&item.context_expr).and_then(|call| self.qualified(&call.func));
            if matches!(
                name.as_deref(),
                Some("contextlib.ExitStack" | "contextlib.AsyncExitStack")
            ) {
                if let Some(expression) = &item.optional_vars {
                    managers.extend(bound_names(expression));
                }
            }
        }
        let mut body = state;
        body.managers.extend(managers.clone());
        let mut flow = self.suite(&node.body, body);
        for state in flow
            .live
            .iter_mut()
            .chain(flow.exits.iter_mut().map(|(_, state)| state))
        {
            outcome(state, &managed, CLOSED);
            state.managers.retain(|name| !managers.contains(name));
        }
        flow
    }
    fn finally(&mut self, flow: Flow, statements: &[Stmt]) -> Flow {
        let live = flow
            .live
            .map(|state| self.suite(statements, state))
            .unwrap_or(Flow {
                live: None,
                exits: vec![],
            });
        let mut exits = live.exits;
        for (kind, state) in flow.exits {
            let flow = self.suite(statements, state);
            exits.extend(flow.exits);
            if let Some(state) = flow.live {
                exits.push((kind, state));
            }
        }
        Flow {
            live: live.live,
            exits,
        }
    }
    fn try_(&mut self, node: &ast::StmtTry, state: State) -> Flow {
        let mut body = self.suite(&node.body, state.clone());
        let normal = body
            .live
            .take()
            .map(|state| self.suite(&node.orelse, state));
        let mut lives = vec![];
        let mut exits = body.exits;
        if let Some(flow) = normal {
            if let Some(state) = flow.live {
                lives.push(state);
            }
            exits.extend(flow.exits);
        }
        for handler in &node.handlers {
            let ast::ExceptHandler::ExceptHandler(handler) = handler;
            let flow = self.suite(&handler.body, state.clone());
            if let Some(state) = flow.live {
                lives.push(state);
            }
            exits.extend(flow.exits);
        }
        let flow = Flow {
            live: merge(lives),
            exits,
        };
        if node.finalbody.is_empty() {
            flow
        } else {
            self.finally(flow, &node.finalbody)
        }
    }
    fn loop_(&mut self, body: &[Stmt], orelse: &[Stmt], state: State) -> Flow {
        let flow = self.suite(body, state.clone());
        let mut continuing = vec![state];
        let mut breaks = vec![];
        let mut exits = vec![];
        if let Some(state) = flow.live {
            continuing.push(state);
        }
        for (kind, state) in flow.exits {
            match kind {
                Exit::Continue => continuing.push(state),
                Exit::Break => breaks.push(state),
                _ => exits.push((kind, state)),
            }
        }
        let normal = self.suite(orelse, merge(continuing).expect("zero iteration state"));
        if let Some(state) = normal.live {
            breaks.push(state);
        }
        exits.extend(normal.exits);
        // Loop else does not execute on a break path; preserve that open ownership path.
        Flow {
            live: merge(breaks),
            exits,
        }
    }
    fn statement(&mut self, node: &Stmt, mut state: State) -> Flow {
        match node {
            Stmt::Assign(node) => self.assign(
                &mut state,
                &node.targets.iter().collect::<Vec<_>>(),
                &node.value,
            ),
            Stmt::AnnAssign(node) => {
                if let Some(value) = &node.value {
                    self.assign(&mut state, &[&node.target], value)
                }
            }
            Stmt::Expr(node) => {
                if let Some(factory) = direct_call(&node.value).and_then(|call| self.factory(call))
                {
                    self.new_resource(&mut state, &node.value, factory, &[]);
                } else {
                    self.process_call(&mut state, Some(&node.value));
                }
            }
            Stmt::Delete(node) => {
                for target in &node.targets {
                    for name in bound_names(target) {
                        state.names.remove(&name);
                    }
                }
            }
            Stmt::Return(node) => {
                self.process_call(&mut state, node.value.as_deref());
                transfer(&mut state, node.value.as_deref());
                return Flow {
                    live: None,
                    exits: vec![(Exit::Return, state)],
                };
            }
            Stmt::Raise(_) => {
                return Flow {
                    live: None,
                    exits: vec![(Exit::Raise, state)],
                };
            }
            Stmt::Break(_) => {
                return Flow {
                    live: None,
                    exits: vec![(Exit::Break, state)],
                };
            }
            Stmt::Continue(_) => {
                return Flow {
                    live: None,
                    exits: vec![(Exit::Continue, state)],
                };
            }
            Stmt::If(node) => {
                let mut suites = vec![node.body.as_slice()];
                let mut has_else = false;
                for clause in &node.elif_else_clauses {
                    suites.push(clause.body.as_slice());
                    has_else |= clause.test.is_none();
                }
                if !has_else {
                    suites.push(&[]);
                }
                return self.branch(suites, state);
            }
            Stmt::For(node) => return self.loop_(&node.body, &node.orelse, state),
            Stmt::While(node) => return self.loop_(&node.body, &node.orelse, state),
            Stmt::With(node) => return self.with(node, state),
            Stmt::Try(node) => return self.try_(node, state),
            Stmt::Match(node) => {
                let mut suites: Vec<&[Stmt]> =
                    node.cases.iter().map(|case| case.body.as_slice()).collect();
                suites.push(&[]);
                return self.branch(suites, state);
            }
            _ => {}
        }
        Flow::live(state)
    }
    fn analyze(&mut self, body: &[Stmt]) -> Vec<InspectionTarget> {
        let flow = self.suite(body, State::default());
        if self.interrupted {
            return Vec::new();
        }
        let mut states: Vec<State> = flow.live.into_iter().collect();
        states.extend(flow.exits.into_iter().map(|(_, state)| state));
        let final_ = merge(states).unwrap_or_default();
        let mut issues = vec![];
        for (id, acquisition) in &self.acquisitions {
            let outcomes = final_.resources.get(id).copied().unwrap_or(OPEN);
            if outcomes & OPEN == 0 {
                continue;
            }
            let variable = if acquisition.variable.is_empty() {
                "<unbound>"
            } else {
                &acquisition.variable
            };
            let mut target = self
                .file
                .source
                .target(acquisition.range, "Resource:OpenWithoutWith");
            target.status = "WARN".into();
            target.message = format!(
                "{variable} from {} may remain open on an exit path",
                acquisition.factory
            );
            target.snippet = if acquisition.variable.is_empty() {
                format!("{}(...)", acquisition.factory)
            } else {
                format!("{variable} = {}(...)", acquisition.factory)
            };
            target.metrics.extend([
                ("open_path".into(), json!(1)),
                (
                    "closed_path".into(),
                    json!(u32::from(outcomes & CLOSED != 0)),
                ),
                (
                    "transferred_path".into(),
                    json!(u32::from(outcomes & TRANSFERRED != 0)),
                ),
            ]);
            issues.push(target);
        }
        issues
    }
}
const LEAK_EXPLANATION: &str = "A bounded intraprocedural flow analysis found at least one lexical exit where this standard-library resource is neither closed nor transferred.";
const LEAK_REMEDIATION: &str = "Use a context manager or ensure every return/raise/fallthrough path closes or deliberately returns/transfers the resource.";
const MUTABLE_EXPLANATION: &str = "Python evaluates a default value once when the function is defined, so mutations are retained by later calls.";
const MUTABLE_REMEDIATION: &str =
    "Use None as the default and construct a fresh value inside the function.";

pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&std::sync::atomic::AtomicBool>,
) {
    let (mut acquisitions, mut defaults, mut ast_nodes, mut checked) =
        (0usize, 0usize, 0usize, 0usize);
    let mut all_issues = vec![];
    let mut legacy_issues = vec![];
    for file in files {
        if crate::cancelled(output, cancellation) {
            break;
        }
        let nodes = cpython_node_count(file);
        if nodes > 100_000 {
            failure(
                output,
                &file.source.path,
                &SourceError::new(
                    "resource-ast-limit",
                    "Python resource AST exceeds the bounded limit (100000 nodes)",
                ),
            );
            continue;
        }
        ast_nodes += nodes;
        let function_nodes = functions(file);
        let mut bodies = vec![file.body()];
        bodies.extend(
            function_nodes
                .iter()
                .map(|function| function.body.as_slice()),
        );
        let mut targets = vec![];
        for body in bodies {
            if crate::cancelled(output, cancellation) {
                break;
            }
            let mut flow = FlowAnalyzer {
                file,
                cancellation,
                interrupted: false,
                acquisitions: BTreeMap::new(),
                next_id: acquisitions,
            };
            targets.extend(flow.analyze(body));
            acquisitions = flow.next_id;
        }
        for function in function_nodes {
            if crate::cancelled(output, cancellation) {
                break;
            }
            for parameter in function.parameters.iter_non_variadic_params() {
                let Some(default) = &parameter.default else {
                    continue;
                };
                defaults += 1;
                let mutable = matches!(
                    default.as_ref(),
                    Expr::List(_) | Expr::Dict(_) | Expr::Set(_)
                ) || direct_call(default)
                    .and_then(|call| file.scopes.resolve(&call.func))
                    .is_some_and(|name| {
                        matches!(
                            name.as_str(),
                            "list"
                                | "dict"
                                | "set"
                                | "bytearray"
                                | "builtins.list"
                                | "builtins.dict"
                                | "builtins.set"
                                | "builtins.bytearray"
                                | "collections.defaultdict"
                                | "collections.deque"
                        )
                    });
                if !mutable {
                    continue;
                }
                let mut target = file
                    .source
                    .target(default.range(), "Correctness:MutableDefault");
                target.status = "WARN".into();
                target.message = format!(
                    "Mutable default argument in {}() is shared across calls",
                    function.name
                );
                target.snippet = "parameter=<mutable value>".into();
                targets.push(target);
            }
        }
        targets.sort_by(|a, b| {
            (&a.start_line, &a.start_column, &a.target_name).cmp(&(
                &b.start_line,
                &b.start_column,
                &b.target_name,
            ))
        });
        for target in targets {
            let mutable = target.target_name == "Correctness:MutableDefault";
            let (category, confidence, explanation, remediation) = if mutable {
                (
                    "correctness",
                    "exact",
                    MUTABLE_EXPLANATION,
                    MUTABLE_REMEDIATION,
                )
            } else {
                ("resource", "medium", LEAK_EXPLANATION, LEAK_REMEDIATION)
            };
            finding::hygiene(
                output, request, &target, "resource", "medium", category, confidence,
            );
            legacy_issues.push(json!({"target":target,"category":category,"confidence":confidence,"explanation":explanation,"remediation":remediation}));
            all_issues.push(target);
        }
        if crate::cancelled(output, cancellation) {
            break;
        }
        checked += 1;
        let mut target = InspectionTarget::at(&file.source.path, 1);
        target.target_name = "Resource:ASTFlow".into();
        target.message = "Bounded intraprocedural resource-flow analysis completed".into();
        output.targets.push(target);
    }
    let count = all_issues.len();
    output.targets.extend(all_issues);
    output.observation.measurements.extend([
        Measurement {
            unit: "files".into(),
            ..Measurement::measured("files_checked", checked as f64)
        },
        Measurement {
            unit: "issues".into(),
            ..Measurement::measured("issues_found", count as f64)
        },
    ]);
    output.extra = json!({"analysis_mode":"python-intraprocedural-resource-flow-v1","files_checked":checked,"acquisitions_checked":acquisitions,"mutable_defaults_checked":defaults,"ast_nodes":ast_nodes,"resource_issues":legacy_issues,"limitations":["Flow is intraprocedural and does not infer arbitrary user-defined factories","A direct close is lexical evidence, not proof that close itself cannot raise","Returned resources and assignments to attributes or subscripts transfer ownership"]});
    output.summary = if count > 0 {
        format!("Resource/correctness analysis: {count} finding(s)")
    } else {
        format!("Resource/correctness analysis clean across {checked} file(s)")
    };
    crate::cancelled(output, cancellation);
    finish_status(output, request);
}
