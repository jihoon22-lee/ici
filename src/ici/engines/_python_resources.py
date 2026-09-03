"""Bounded flow-sensitive Python resource and mutable-default analysis."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ici.core.models import EngineStatus, FindingCategory, FindingConfidence, InspectionTarget
from ici.engines._python_resource_scopes import collect_scope_bindings, collect_scope_imports

MAX_RESOURCE_AST_NODES = 100_000
_OPEN = "open"
_CLOSED = "closed"
_TRANSFERRED = "transferred"
_RESOURCE_FACTORIES = frozenset(
    {
        "builtins.open",
        "io.open",
        "socket.socket",
        "tempfile.NamedTemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryFile",
    }
)
_MUTABLE_FACTORIES = frozenset(
    {
        "builtins.bytearray",
        "builtins.dict",
        "builtins.list",
        "builtins.set",
        "collections.defaultdict",
        "collections.deque",
    }
)


class ResourceAnalysisLimit(ValueError):
    """Raised when a source AST exceeds the documented per-file bound."""


@dataclass(frozen=True)
class ResourceIssue:
    target: InspectionTarget
    category: FindingCategory
    confidence: FindingConfidence
    explanation: str
    remediation: str


@dataclass(frozen=True)
class PythonResourceAnalysis:
    issues: tuple[ResourceIssue, ...]
    acquisitions_checked: int
    mutable_defaults_checked: int
    ast_nodes: int


@dataclass(frozen=True)
class _Acquisition:
    node: ast.AST
    factory: str
    variable: str


@dataclass
class _State:
    resources: dict[int, set[str]] = field(default_factory=dict)
    names: dict[str, set[int]] = field(default_factory=dict)
    managers: set[str] = field(default_factory=set)

    def clone(self) -> _State:
        return _State(
            resources={key: set(value) for key, value in self.resources.items()},
            names={key: set(value) for key, value in self.names.items()},
            managers=set(self.managers),
        )


@dataclass
class _Flow:
    live: _State | None
    exits: list[tuple[str, _State]] = field(default_factory=list)


def _node_range(node: ast.AST) -> tuple[int, int | None, int | None, int | None]:
    start_column = getattr(node, "col_offset", None)
    end_column = getattr(node, "end_col_offset", None)
    return (
        max(1, getattr(node, "lineno", 1)),
        getattr(node, "end_lineno", None),
        start_column + 1 if isinstance(start_column, int) else None,
        end_column if isinstance(end_column, int) and end_column > 0 else None,
    )


def _target(
    file_path: str,
    node: ast.AST,
    name: str,
    message: str,
    snippet: str = "",
    **metrics: int | str,
) -> InspectionTarget:
    start_line, end_line, start_column, end_column = _node_range(node)
    return InspectionTarget(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
        target_name=name,
        status=EngineStatus.WARN,
        message=message,
        snippet=snippet,
        metrics=metrics,
    )


def _merge_states(states: list[_State]) -> _State | None:
    if not states:
        return None
    merged = _State()
    for state in states:
        for resource_id, outcomes in state.resources.items():
            merged.resources.setdefault(resource_id, set()).update(outcomes)
        for name, resource_ids in state.names.items():
            merged.names.setdefault(name, set()).update(resource_ids)
        merged.managers.update(state.managers)
    return merged


def _imports(scope: ast.AST) -> dict[str, str]:
    return collect_scope_imports(scope)


def _shadowed_names(scope: ast.AST) -> frozenset[str]:
    return collect_scope_bindings(scope)


def _qualified_name(node: ast.AST, aliases: dict[str, str], shadowed: frozenset[str]) -> str:
    if isinstance(node, ast.Name):
        if node.id in shadowed and node.id in {"open", "list", "dict", "set", "bytearray"}:
            return node.id
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, aliases, shadowed)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _factory_name(call: ast.Call, aliases: dict[str, str], shadowed: frozenset[str]) -> str:
    qualified = _qualified_name(call.func, aliases, shadowed)
    if qualified in _RESOURCE_FACTORIES:
        return qualified
    if isinstance(call.func, ast.Attribute) and call.func.attr == "open":
        owner = call.func.value
        if isinstance(owner, ast.Call):
            owner_name = _qualified_name(owner.func, aliases, shadowed)
            if owner_name == "pathlib.Path":
                return "pathlib.Path.open"
    return ""


def _expression_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def _bound_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _bound_names(item)]
    return []


def _direct_call(node: ast.AST | None) -> ast.Call | None:
    if isinstance(node, ast.Call):
        return node
    if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
        return node.value
    return None


class _ScopeFlowAnalyzer:
    def __init__(
        self,
        file_path: str,
        scope: ast.AST,
        aliases: dict[str, str],
        first_resource_id: int,
    ) -> None:
        self.file_path = file_path
        self.aliases = aliases
        self.shadowed = _shadowed_names(scope)
        self.next_resource_id = first_resource_id
        self.acquisitions: dict[int, _Acquisition] = {}

    def analyze(self, statements: list[ast.stmt]) -> tuple[list[ResourceIssue], int]:
        flow = self._suite(statements, _State())
        states = ([flow.live] if flow.live is not None else []) + [state for _, state in flow.exits]
        final = _merge_states(states) or _State()
        issues: list[ResourceIssue] = []
        for resource_id, acquisition in sorted(self.acquisitions.items()):
            outcomes = final.resources.get(resource_id, {_OPEN})
            if _OPEN not in outcomes:
                continue
            variable = acquisition.variable or "<unbound>"
            issues.append(
                ResourceIssue(
                    target=_target(
                        self.file_path,
                        acquisition.node,
                        "Resource:OpenWithoutWith",
                        f"{variable} from {acquisition.factory} may remain open on an exit path",
                        f"{variable} = {acquisition.factory}(...)"
                        if acquisition.variable
                        else f"{acquisition.factory}(...)",
                        open_path=1,
                        closed_path=int(_CLOSED in outcomes),
                        transferred_path=int(_TRANSFERRED in outcomes),
                    ),
                    category=FindingCategory.RESOURCE,
                    confidence=FindingConfidence.MEDIUM,
                    explanation=(
                        "A bounded intraprocedural flow analysis found at least one lexical exit "
                        "where this standard-library resource is neither closed nor transferred."
                    ),
                    remediation=(
                        "Use a context manager or ensure every return/raise/fallthrough path closes "
                        "or deliberately returns/transfers the resource."
                    ),
                )
            )
        return issues, self.next_resource_id

    def _new_resource(self, state: _State, node: ast.AST, factory: str, names: list[str]) -> None:
        resource_id = self.next_resource_id
        self.next_resource_id += 1
        variable = names[0] if names else ""
        self.acquisitions[resource_id] = _Acquisition(node, factory, variable)
        state.resources[resource_id] = {_OPEN}
        for name in names:
            state.names[name] = {resource_id}

    @staticmethod
    def _set_outcome(state: _State, resource_ids: set[int], outcome: str) -> None:
        for resource_id in resource_ids:
            current = state.resources.get(resource_id, set())
            if _OPEN in current:
                state.resources[resource_id] = (current - {_OPEN}) | {outcome}

    def _transfer_expression(self, state: _State, expression: ast.AST | None) -> None:
        resource_ids: set[int] = set()
        for name in _expression_names(expression):
            resource_ids.update(state.names.get(name, set()))
        self._set_outcome(state, resource_ids, _TRANSFERRED)

    def _process_direct_call(self, state: _State, expression: ast.AST | None) -> None:
        call = _direct_call(expression)
        if call is None or not isinstance(call.func, ast.Attribute):
            return
        owner = call.func.value
        if call.func.attr in {"close", "aclose"} and isinstance(owner, ast.Name):
            self._set_outcome(state, state.names.get(owner.id, set()), _CLOSED)
            return
        if (
            call.func.attr == "enter_context"
            and isinstance(owner, ast.Name)
            and owner.id in state.managers
            and call.args
        ):
            self._transfer_expression(state, call.args[0])

    def _assign(self, state: _State, targets: list[ast.AST], value: ast.AST) -> None:
        call = _direct_call(value)
        factory = _factory_name(call, self.aliases, self.shadowed) if call is not None else ""
        names = [name for target in targets for name in _bound_names(target)]
        if factory:
            if names:
                self._new_resource(state, value, factory, names)
                if any(not _bound_names(target) for target in targets):
                    self._transfer_expression(state, ast.Name(id=names[0], ctx=ast.Load()))
            return
        if isinstance(value, ast.Name) and value.id in state.names:
            for name in names:
                state.names[name] = set(state.names[value.id])
        else:
            for name in names:
                state.names.pop(name, None)
        if any(not _bound_names(target) for target in targets):
            self._transfer_expression(state, value)
        self._process_direct_call(state, value)

    def _suite(self, statements: list[ast.stmt], initial: _State) -> _Flow:
        live: _State | None = initial
        exits: list[tuple[str, _State]] = []
        for statement in statements:
            if live is None:
                break
            flow = self._statement(statement, live)
            live = flow.live
            exits.extend(flow.exits)
        return _Flow(live, exits)

    def _branch(self, suites: list[list[ast.stmt]], state: _State) -> _Flow:
        flows = [self._suite(suite, state.clone()) for suite in suites]
        live = _merge_states([flow.live for flow in flows if flow.live is not None])
        return _Flow(live, [item for flow in flows for item in flow.exits])

    def _with_statement(self, node: ast.With | ast.AsyncWith, state: _State) -> _Flow:
        managed_resource_ids: set[int] = set()
        manager_names: set[str] = set()
        for item in node.items:
            managed_resource_ids.update(
                resource_id
                for name in _expression_names(item.context_expr)
                for resource_id in state.names.get(name, set())
            )
            call = _direct_call(item.context_expr)
            qualified = (
                _qualified_name(call.func, self.aliases, self.shadowed) if call is not None else ""
            )
            if qualified in {"contextlib.ExitStack", "contextlib.AsyncExitStack"}:
                manager_names.update(_bound_names(item.optional_vars) if item.optional_vars else [])
        body_state = state.clone()
        body_state.managers.update(manager_names)
        flow = self._suite(node.body, body_state)
        states = ([flow.live] if flow.live is not None else []) + [item[1] for item in flow.exits]
        for output in states:
            self._set_outcome(output, managed_resource_ids, _CLOSED)
            output.managers.difference_update(manager_names)
        return flow

    def _try_statement(self, node: ast.Try, state: _State) -> _Flow:
        body = self._suite(node.body, state.clone())
        normal = body.live
        if normal is not None and node.orelse:
            else_flow = self._suite(node.orelse, normal)
            normal = else_flow.live
            body.exits.extend(else_flow.exits)
        handler_flows = [self._suite(handler.body, state.clone()) for handler in node.handlers]
        combined = _Flow(
            _merge_states(
                ([normal] if normal is not None else [])
                + [flow.live for flow in handler_flows if flow.live is not None]
            ),
            body.exits + [item for flow in handler_flows for item in flow.exits],
        )
        if not node.finalbody:
            return combined
        return self._apply_finally(combined, node.finalbody)

    def _apply_finally(self, flow: _Flow, statements: list[ast.stmt]) -> _Flow:
        live_flow = self._suite(statements, flow.live) if flow.live is not None else _Flow(None)
        exits = list(live_flow.exits)
        for kind, state in flow.exits:
            final_flow = self._suite(statements, state)
            exits.extend(final_flow.exits)
            if final_flow.live is not None:
                exits.append((kind, final_flow.live))
        return _Flow(live_flow.live, exits)

    def _loop(self, body: list[ast.stmt], orelse: list[ast.stmt], state: _State) -> _Flow:
        body_flow = self._suite(body, state.clone())
        continuing = [state.clone()]
        if body_flow.live is not None:
            continuing.append(body_flow.live)
        loop_exits: list[tuple[str, _State]] = []
        for kind, output in body_flow.exits:
            if kind in {"break", "continue"}:
                continuing.append(output)
            else:
                loop_exits.append((kind, output))
        live = _merge_states(continuing)
        else_flow = self._suite(orelse, live) if live is not None and orelse else _Flow(live)
        return _Flow(else_flow.live, loop_exits + else_flow.exits)

    def _simple_statement(self, node: ast.stmt, state: _State) -> bool:
        if isinstance(node, ast.Assign):
            self._assign(state, list(node.targets), node.value)
            return True
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            self._assign(state, [node.target], node.value)
            return True
        if isinstance(node, ast.Expr):
            call = _direct_call(node.value)
            factory = _factory_name(call, self.aliases, self.shadowed) if call else ""
            if factory:
                self._new_resource(state, node.value, factory, [])
            else:
                self._process_direct_call(state, node.value)
            return True
        if isinstance(node, ast.Delete):
            for target in node.targets:
                for name in _bound_names(target):
                    state.names.pop(name, None)
            return True
        return False

    def _exit_statement(self, node: ast.stmt, state: _State) -> _Flow | None:
        if isinstance(node, ast.Return):
            self._process_direct_call(state, node.value)
            self._transfer_expression(state, node.value)
            return _Flow(None, [("return", state)])
        if isinstance(node, ast.Raise):
            return _Flow(None, [("raise", state)])
        if isinstance(node, ast.Break):
            return _Flow(None, [("break", state)])
        if isinstance(node, ast.Continue):
            return _Flow(None, [("continue", state)])
        return None

    def _compound_statement(self, node: ast.stmt, state: _State) -> _Flow | None:
        if isinstance(node, ast.If):
            return self._branch([node.body, node.orelse], state)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return self._loop(node.body, node.orelse, state)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return self._with_statement(node, state)
        if isinstance(node, ast.Try):
            return self._try_statement(node, state)
        if isinstance(node, ast.Match):
            suites = [case.body for case in node.cases]
            suites.append([])
            return self._branch(suites, state)
        return None

    def _statement(self, node: ast.stmt, state: _State) -> _Flow:
        if self._simple_statement(node, state):
            return _Flow(state)
        exit_flow = self._exit_statement(node, state)
        if exit_flow is not None:
            return exit_flow
        compound_flow = self._compound_statement(node, state)
        if compound_flow is not None:
            return compound_flow
        return _Flow(state)


def _scope_statements(scope: ast.AST) -> list[ast.stmt]:
    body = getattr(scope, "body", [])
    return list(body) if isinstance(body, list) else []


def _function_scopes(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _mutable_default_issues(
    file_path: str,
    tree: ast.AST,
    aliases: dict[str, str],
) -> tuple[list[ResourceIssue], int]:
    issues: list[ResourceIssue] = []
    checked = 0
    for function in _function_scopes(tree):
        shadowed = _shadowed_names(function)
        defaults = [*function.args.defaults, *function.args.kw_defaults]
        for default in defaults:
            if default is None:
                continue
            checked += 1
            is_mutable = isinstance(default, (ast.List, ast.Dict, ast.Set))
            call = _direct_call(default)
            if call is not None:
                is_mutable = _qualified_name(call.func, aliases, shadowed) in _MUTABLE_FACTORIES
            if not is_mutable:
                continue
            issues.append(
                ResourceIssue(
                    target=_target(
                        file_path,
                        default,
                        "Correctness:MutableDefault",
                        f"Mutable default argument in {function.name}() is shared across calls",
                        "parameter=<mutable value>",
                    ),
                    category=FindingCategory.CORRECTNESS,
                    confidence=FindingConfidence.EXACT,
                    explanation=(
                        "Python evaluates a default value once when the function is defined, so "
                        "mutations are retained by later calls."
                    ),
                    remediation="Use None as the default and construct a fresh value inside the function.",
                )
            )
    return issues, checked


def analyze_python_resources(file_path: str, text: str) -> PythonResourceAnalysis:
    """Analyze one already bounded UTF-8 Python source snapshot."""

    tree = ast.parse(text, filename=file_path)
    ast_nodes = sum(1 for _ in ast.walk(tree))
    if ast_nodes > MAX_RESOURCE_AST_NODES:
        raise ResourceAnalysisLimit(
            f"Python resource AST exceeds the bounded limit ({MAX_RESOURCE_AST_NODES} nodes)"
        )
    module_aliases = _imports(tree)
    issues: list[ResourceIssue] = []
    next_resource_id = 0
    scopes: list[ast.AST] = [tree, *_function_scopes(tree)]
    for scope in scopes:
        aliases = dict(module_aliases)
        if scope is not tree:
            aliases.update(_imports(scope))
        analyzer = _ScopeFlowAnalyzer(file_path, scope, aliases, next_resource_id)
        scope_issues, next_resource_id = analyzer.analyze(_scope_statements(scope))
        issues.extend(scope_issues)
    default_issues, defaults_checked = _mutable_default_issues(file_path, tree, module_aliases)
    issues.extend(default_issues)
    issues.sort(
        key=lambda item: (
            item.target.start_line,
            item.target.start_column or 0,
            item.target.target_name,
        )
    )
    return PythonResourceAnalysis(
        issues=tuple(issues),
        acquisitions_checked=next_resource_id,
        mutable_defaults_checked=defaults_checked,
        ast_nodes=ast_nodes,
    )
