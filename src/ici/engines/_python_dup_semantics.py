"""Bounded AST semantic shapes for Python duplicate analysis.

This module deliberately does not claim behavioural equivalence.  It produces a
stable, source-linked representation of named Python functions, methods, and
classes that a caller may use as an exact semantic-shape key.  Near-clone edit
matching and integration with the duplicate engine are intentionally outside
this module's scope.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

SEMANTIC_SHAPE_VERSION = "semantic-shape-v1"
SEMANTIC_SHAPE_ALGORITHM = "sha256/semantic-shape-v1"

# These are intentionally conservative defaults.  The public limits object is
# the preferred injection point for tests and future callers; keeping named
# constants also makes the safety envelope easy to audit.
MAX_PYTHON_SEMANTIC_FILES = 256
MAX_PYTHON_SEMANTIC_REGIONS = 20_000
MAX_PYTHON_SEMANTIC_NODES = 500_000
MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS = 16 * 1024 * 1024

# Short aliases are useful to code which treats duplicate-analysis budgets as
# one family, while the long names document what each bound covers.
MAX_SEMANTIC_FILES = MAX_PYTHON_SEMANTIC_FILES
MAX_SEMANTIC_REGIONS = MAX_PYTHON_SEMANTIC_REGIONS
MAX_SEMANTIC_NODES = MAX_PYTHON_SEMANTIC_NODES
MAX_SEMANTIC_SERIALIZED_CHARS = MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS

_NAMED_SCOPE_TYPES = (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)
_COMPREHENSION_TYPES = (ast.DictComp, ast.GeneratorExp, ast.ListComp, ast.SetComp)
_LOCATION_FIELDS = frozenset({"lineno", "col_offset", "end_lineno", "end_col_offset"})
_VERSION_OPTIONAL_EMPTY_FIELDS = frozenset({"type_params"})
_BUILTIN_DYNAMIC_CALLS = frozenset({"eval", "exec"})
_BINDING_STRING_FIELDS = frozenset(
    {
        ("arg", "arg"),
        ("ExceptHandler", "name"),
        ("MatchAs", "name"),
        ("MatchStar", "name"),
        ("MatchMapping", "rest"),
    }
)

# The parser is pinned to Python 3.10 grammar.  All AST classes available in
# that runtime are accepted, while a foreign/custom AST class is unsupported
# and therefore excluded rather than guessed at.
_KNOWN_AST_NODE_NAMES = frozenset(
    name
    for name, value in vars(ast).items()
    if isinstance(value, type) and issubclass(value, ast.AST)
)


@dataclass(frozen=True, slots=True)
class SemanticLimits:
    """Resource limits for one bounded semantic-shape analysis."""

    max_files: int = MAX_PYTHON_SEMANTIC_FILES
    max_regions: int = MAX_PYTHON_SEMANTIC_REGIONS
    max_nodes: int = MAX_PYTHON_SEMANTIC_NODES
    max_serialized_chars: int = MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS

    def __post_init__(self) -> None:
        for field_name in (
            "max_files",
            "max_regions",
            "max_nodes",
            "max_serialized_chars",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class SemanticRegion:
    """One named source region and its exact canonical semantic shape."""

    file_path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    canonical_shape: str
    fingerprint: str
    node_count: int
    fingerprint_algorithm: str = SEMANTIC_SHAPE_ALGORITHM
    shape_version: str = SEMANTIC_SHAPE_VERSION

    @property
    def shape(self) -> str:
        """Compatibility spelling for callers that call the shape ``shape``."""

        return self.canonical_shape

    @property
    def source_name(self) -> str:
        """Return the source-spelled leaf name of this (possibly nested) region."""

        return self.name.rpartition(".")[2]

    @property
    def digest(self) -> str:
        """Return the shape fingerprint without introducing a second hash."""

        return self.fingerprint


@dataclass(frozen=True, slots=True)
class SemanticExclusion:
    """A source or named region omitted by a conservative safety policy."""

    file_path: str
    reason: str
    region_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    message: str = ""

    @property
    def scope_name(self) -> str | None:
        """Alias used by callers that refer to a region as a scope."""

        return self.region_name


@dataclass(frozen=True, slots=True)
class SemanticAnalysisOutcome:
    """Immutable result metadata for a bounded semantic-shape pass."""

    regions: tuple[SemanticRegion, ...] = ()
    exclusions: tuple[SemanticExclusion, ...] = ()
    files: tuple[str, ...] = ()
    status: str = "ok"
    node_count: int = 0
    serialized_chars: int = 0
    fingerprint_algorithm: str = SEMANTIC_SHAPE_ALGORITHM
    shape_version: str = SEMANTIC_SHAPE_VERSION

    @property
    def is_complete(self) -> bool:
        """Whether the source set was fully usable under the safety policy."""

        return not self.exclusions

    @property
    def complete(self) -> bool:
        """Short alias for :attr:`is_complete`."""

        return self.is_complete

    @property
    def fingerprints(self) -> tuple[str, ...]:
        """Return region fingerprints in deterministic region order."""

        return tuple(region.fingerprint for region in self.regions)


# ``SemanticOutcome`` is intentionally an alias, not a second mutable shape.
SemanticOutcome = SemanticAnalysisOutcome


@dataclass(frozen=True, slots=True)
class _RegionSpec:
    node: ast.AST
    name: str
    kind: str
    start_line: int
    end_line: int


class _BudgetExceeded(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _UnsupportedShape(ValueError):
    def __init__(self, node_name: str) -> None:
        super().__init__(node_name)
        self.node_name = node_name


_OMIT = object()


def _default_limits() -> SemanticLimits:
    """Build defaults at call time so tests can inject module constants."""

    return SemanticLimits(
        max_files=MAX_PYTHON_SEMANTIC_FILES,
        max_regions=MAX_PYTHON_SEMANTIC_REGIONS,
        max_nodes=MAX_PYTHON_SEMANTIC_NODES,
        max_serialized_chars=MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS,
    )


def _effective_limits(limits: SemanticLimits | None) -> SemanticLimits:
    if limits is None:
        return _default_limits()
    if not isinstance(limits, SemanticLimits):
        raise TypeError("limits must be a SemanticLimits instance or None")
    return limits


def semantic_shape_fingerprint(canonical_shape: str) -> str:
    """Hash one canonical shape using the versioned semantic-shape algorithm."""

    if not isinstance(canonical_shape, str):
        raise TypeError("canonical_shape must be a string")
    payload = f"{SEMANTIC_SHAPE_ALGORITHM}\0{canonical_shape}".encode()
    return hashlib.sha256(payload).hexdigest()


def _source_path(value: object) -> str:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if not isinstance(value, str):
        raise TypeError("source file path must be a string or path-like value")
    if not value:
        raise ValueError("source file path must not be empty")
    return value


def _normalise_sources(
    sources: Mapping[object, str] | Iterable[tuple[object, str]], limits: SemanticLimits
) -> tuple[tuple[tuple[str, str], ...], SemanticExclusion | None]:
    if isinstance(sources, (str, bytes, os.PathLike)):
        raise TypeError("sources must be a mapping or an iterable of (file_path, source) pairs")

    iterable = sources.items() if isinstance(sources, Mapping) else iter(sources)
    items: list[tuple[str, str]] = []
    try:
        for item in iterable:
            try:
                raw_path, text = item
            except (TypeError, ValueError) as error:
                raise TypeError("each source must be a (file_path, source) pair") from error
            path = _source_path(raw_path)
            if not isinstance(text, str):
                raise TypeError(f"source text for {path!r} must be a string")
            items.append((path, text))
            if len(items) > limits.max_files:
                return (
                    tuple(sorted(items, key=lambda pair: pair[0])),
                    SemanticExclusion(
                        file_path="<sources>",
                        reason="max-files",
                        message=(
                            "source set exceeds "
                            f"max_files={limits.max_files}; no semantic regions were emitted"
                        ),
                    ),
                )
    except TypeError:
        raise
    except Exception as error:
        raise TypeError("sources must be an iterable of (file_path, source) pairs") from error

    ordered = tuple(sorted(items, key=lambda pair: pair[0]))
    duplicate_paths = {
        path
        for index, (path, _text) in enumerate(ordered)
        if index and path == ordered[index - 1][0]
    }
    if duplicate_paths:
        duplicate_text = ", ".join(sorted(duplicate_paths))
        return (
            ordered,
            SemanticExclusion(
                file_path="<sources>",
                reason="duplicate-file-path",
                message=f"source paths are not unique: {duplicate_text}",
            ),
        )
    return ordered, None


def _parse_source(text: str, file_path: str) -> ast.Module:
    try:
        return ast.parse(
            text,
            filename=file_path,
            mode="exec",
            type_comments=True,
            feature_version=10,
        )
    except (IndentationError, MemoryError, RecursionError, SyntaxError, ValueError) as error:
        raise ValueError("malformed-ast") from error


def _count_nodes(root: ast.AST, limit: int) -> int:
    count = 0
    stack = [root]
    while stack:
        if count >= limit:
            raise _BudgetExceeded("max-nodes")
        node = stack.pop()
        count += 1
        try:
            children = tuple(ast.iter_child_nodes(node))
        except (AttributeError, TypeError, ValueError) as error:
            raise _UnsupportedShape(type(node).__name__) from error
        stack.extend(reversed(children))
    return count


def _line_for(node: ast.AST, field_name: str, fallback: int = 1) -> int:
    value = getattr(node, field_name, fallback)
    return value if type(value) is int and value > 0 else fallback


def _scope_start(node: ast.AST) -> int:
    starts = [_line_for(node, "lineno")]
    starts.extend(
        _line_for(decorator, "lineno")
        for decorator in getattr(node, "decorator_list", ())
        if isinstance(decorator, ast.AST)
    )
    return min(starts)


def _scope_end(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    if type(end) is int and end > 0:
        return end
    last = _line_for(node, "lineno")
    stack = [node]
    while stack:
        current = stack.pop()
        last = max(last, _line_for(current, "lineno"), _line_for(current, "end_lineno", last))
        stack.extend(ast.iter_child_nodes(current))
    return last


def _collect_regions(tree: ast.Module) -> list[_RegionSpec]:
    specs: list[_RegionSpec] = []

    def visit(node: ast.AST, parents: tuple[tuple[str, str], ...]) -> None:
        next_parents = parents
        if isinstance(node, _NAMED_SCOPE_TYPES):
            raw_name = getattr(node, "name", None)
            if not isinstance(raw_name, str) or not raw_name:
                # A parsed named scope always has a source name.  Treat a
                # malformed/custom AST as unsupported during serialization.
                raise _UnsupportedShape(type(node).__name__)
            qualified = ".".join((*tuple(name for _kind, name in parents), raw_name))
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif parents and parents[-1][0] == "class":
                kind = "method"
            else:
                kind = "function"
            specs.append(
                _RegionSpec(
                    node=node,
                    name=qualified,
                    kind=kind,
                    start_line=_scope_start(node),
                    end_line=_scope_end(node),
                )
            )
            next_parents = (*parents, (kind, raw_name))

        try:
            children = tuple(ast.iter_child_nodes(node))
        except (AttributeError, TypeError, ValueError) as error:
            raise _UnsupportedShape(type(node).__name__) from error
        for child in children:
            visit(child, next_parents)

    visit(tree, ())
    specs.sort(key=lambda spec: (spec.start_line, spec.end_line, spec.name, spec.kind))
    return specs


def _walk_scope(root: ast.AST) -> Iterable[ast.AST]:
    """Yield a scope while pruning nested named scope bodies."""

    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        try:
            children = tuple(ast.iter_child_nodes(node))
        except (AttributeError, TypeError, ValueError) as error:
            raise _UnsupportedShape(type(node).__name__) from error
        for child in reversed(children):
            if child is not root and isinstance(child, _NAMED_SCOPE_TYPES):
                continue
            stack.append(child)


def _unsafe_reason(root: ast.AST) -> tuple[str, str] | None:
    for node in _walk_scope(root):
        node_name = type(node).__name__
        if node_name not in _KNOWN_AST_NODE_NAMES or type(node).__module__ != "ast":
            return "unsupported-node", f"unsupported AST node {node_name}"
        if isinstance(node, ast.Lambda):
            return "unsupported-node", "lambda scopes are not named semantic regions"
        if isinstance(node, _COMPREHENSION_TYPES):
            return (
                "comprehension-scope",
                "comprehension bindings require a nested scope and are excluded conservatively",
            )
        if isinstance(node, ast.Global):
            return "global-statement", "global bindings are excluded conservatively"
        if isinstance(node, ast.Nonlocal):
            return "nonlocal-statement", "nonlocal bindings are excluded conservatively"
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return "star-import", "star imports do not expose a bounded API anchor"
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id in _BUILTIN_DYNAMIC_CALLS:
                return f"dynamic-{function.id}", f"dynamic {function.id} call is excluded"
            if isinstance(function, ast.Attribute) and function.attr in _BUILTIN_DYNAMIC_CALLS:
                return f"dynamic-{function.attr}", f"dynamic {function.attr} call is excluded"
            if (
                isinstance(function, ast.Name)
                and function.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _BUILTIN_DYNAMIC_CALLS
            ):
                dynamic_name = node.args[1].value
                return f"dynamic-{dynamic_name}", f"dynamic {dynamic_name} lookup is excluded"
    return None


def _collect_bindings(root: ast.AST) -> tuple[dict[str, int], frozenset[str]]:
    """Collect local binders in deterministic AST field order."""

    bound: dict[str, int] = {}
    imported: set[str] = set()

    def bind(name: object) -> None:
        if isinstance(name, str) and name and name not in bound:
            bound[name] = len(bound)

    def visit(node: ast.AST, *, is_root: bool = False) -> None:
        if not is_root and isinstance(node, _NAMED_SCOPE_TYPES):
            # The nested definition itself can be referenced by the enclosing
            # scope, but its body belongs to a separate semantic region.
            bind(getattr(node, "name", None))
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and is_root:
            # The enclosing/module binding is represented by the source-spelled
            # region name, not by a local alpha slot in its own shape.
            pass
        elif isinstance(node, ast.arg):
            bind(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bind(node.id)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            bind(node.name)
        elif isinstance(node, ast.MatchMapping):
            bind(node.rest)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported.add(alias.asname or alias.name)

        try:
            children = tuple(ast.iter_child_nodes(node))
        except (AttributeError, TypeError, ValueError) as error:
            raise _UnsupportedShape(type(node).__name__) from error
        for child in children:
            visit(child)

    visit(root, is_root=True)
    return bound, frozenset(imported)


def _literal_shape(value: object) -> list[object]:
    """Encode a Constant with its actual Python literal kind and value."""

    if value is None:
        return ["none", None]
    if value is Ellipsis:
        return ["ellipsis", "..."]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, complex):
        return ["complex", repr(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    raise _UnsupportedShape(f"Constant({type(value).__name__})")


class _Canonicalizer:
    def __init__(
        self,
        root: ast.AST,
        bindings: Mapping[str, int],
        imported: frozenset[str],
    ) -> None:
        self.root = root
        self.bindings = bindings
        self.imported = imported
        self.node_count = 0
        self.root_name = (
            root.name
            if isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else None
        )

    def _name_value(self, value: str) -> str:
        if value in self.bindings and value not in self.imported:
            return f"LOCAL_{self.bindings[value]}"
        if value == self.root_name and value not in self.imported:
            return "<self-name>"
        return value

    def _scalar(
        self,
        value: object,
        parent: ast.AST,
        field_name: str,
        *,
        alpha_names: bool,
    ) -> object:
        if isinstance(value, str):
            if alpha_names and isinstance(parent, ast.Name) and field_name == "id":
                return self._name_value(value)
            key = (type(parent).__name__, field_name)
            if alpha_names and key in _BINDING_STRING_FIELDS:
                return self._name_value(value)
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, (complex, bytes)):
            # These occur in manually built AST metadata rather than normal
            # fields; encode them explicitly instead of relying on JSON.
            return _literal_shape(value)
        raise _UnsupportedShape(f"{type(parent).__name__}.{field_name}")

    def _value(
        self,
        value: object,
        parent: ast.AST,
        field_name: str,
        *,
        alpha_names: bool,
    ) -> object:
        if isinstance(value, ast.AST):
            return self._node(value, alpha_names=alpha_names)
        if isinstance(value, list):
            result: list[object] = []
            for item in value:
                encoded = self._value(
                    item,
                    parent,
                    field_name,
                    alpha_names=alpha_names,
                )
                if encoded is not _OMIT:
                    result.append(encoded)
            return result
        if isinstance(value, tuple):
            return [
                self._value(item, parent, field_name, alpha_names=alpha_names) for item in value
            ]
        return self._scalar(value, parent, field_name, alpha_names=alpha_names)

    def _node(self, node: ast.AST, *, alpha_names: bool = True) -> object:
        if node is not self.root and isinstance(node, _NAMED_SCOPE_TYPES):
            return _OMIT
        node_name = type(node).__name__
        if node_name not in _KNOWN_AST_NODE_NAMES or type(node).__module__ != "ast":
            raise _UnsupportedShape(node_name)
        if isinstance(node, ast.Constant):
            self.node_count += 1
            fields = [["value", _literal_shape(node.value)]]
            if hasattr(node, "kind"):
                fields.append(["kind", node.kind])
            return [node_name, fields]

        self.node_count += 1
        fields: list[list[object]] = []
        try:
            ast_fields = ast.iter_fields(node)
            for field_name, value in ast_fields:
                if field_name in _LOCATION_FIELDS:
                    continue
                if field_name in _VERSION_OPTIONAL_EMPTY_FIELDS and value == []:
                    continue
                if (
                    field_name == "name"
                    and node is self.root
                    and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ):
                    encoded = "<function-name>"
                elif field_name == "name" and node is self.root and isinstance(node, ast.ClassDef):
                    encoded = "<class-name>"
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                    field_name == "type_comment" and value is None
                ):
                    # Keep explicit type comments, but omit the parser's
                    # absent-value noise from every version's shape.
                    continue
                else:
                    field_alpha_names = alpha_names
                    if node is self.root and isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        if field_name in {"decorator_list", "returns", "type_params"}:
                            field_alpha_names = False
                    elif node is self.root and isinstance(node, ast.ClassDef):
                        if field_name in {"bases", "decorator_list", "keywords", "type_params"}:
                            field_alpha_names = False
                    elif (
                        isinstance(node, ast.arguments)
                        and field_name in {"defaults", "kw_defaults"}
                    ) or (isinstance(node, ast.arg) and field_name == "annotation"):
                        field_alpha_names = False
                    encoded = self._value(
                        value,
                        node,
                        field_name,
                        alpha_names=field_alpha_names,
                    )
                if encoded is not _OMIT:
                    fields.append([field_name, encoded])
        except (AttributeError, TypeError, ValueError) as error:
            raise _UnsupportedShape(node_name) from error
        return [node_name, fields]

    def serialize(self, *, max_chars: int) -> tuple[str, int]:
        payload = self._node(self.root)
        if payload is _OMIT:
            raise _UnsupportedShape(type(self.root).__name__)
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (RecursionError, TypeError, ValueError) as error:
            raise _UnsupportedShape(type(self.root).__name__) from error
        if len(encoded) > max_chars:
            raise _BudgetExceeded("max-serialized-chars")
        return encoded, self.node_count


def canonical_python_shape(
    node: ast.AST,
    *,
    local_bindings: Mapping[str, int] | None = None,
    imported_names: Iterable[str] = (),
    max_serialized_chars: int = MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS,
) -> tuple[str, int]:
    """Serialize one AST scope as canonical shape JSON.

    This low-level helper is useful for callers that already selected a named
    AST node.  :func:`analyze_python_source` is preferred because it applies
    parsing, region pruning, safety checks, and all aggregate budgets.
    """

    if not isinstance(node, ast.AST):
        raise TypeError("node must be an ast.AST instance")
    if type(max_serialized_chars) is not int or max_serialized_chars <= 0:
        raise ValueError("max_serialized_chars must be a positive integer")
    if local_bindings is None:
        local_bindings, collected_imports = _collect_bindings(node)
        imported = frozenset(collected_imports).union(imported_names)
    else:
        imported = frozenset(imported_names)
    return _Canonicalizer(node, local_bindings, imported).serialize(max_chars=max_serialized_chars)


def _failure_outcome(
    files: tuple[str, ...],
    exclusions: Iterable[SemanticExclusion],
    *,
    node_count: int = 0,
    serialized_chars: int = 0,
) -> SemanticAnalysisOutcome:
    return SemanticAnalysisOutcome(
        regions=(),
        exclusions=tuple(exclusions),
        files=files,
        status="excluded",
        node_count=node_count,
        serialized_chars=serialized_chars,
    )


def _excluded_region(spec: _RegionSpec, reason: str, message: str) -> SemanticExclusion:
    return SemanticExclusion(
        file_path="",
        region_name=spec.name,
        start_line=spec.start_line,
        end_line=spec.end_line,
        reason=reason,
        message=message,
    )


def analyze_python_sources(
    sources: Mapping[object, str] | Iterable[tuple[object, str]],
    *,
    limits: SemanticLimits | None = None,
) -> SemanticAnalysisOutcome:
    """Analyze a bounded, deterministic collection of Python source strings."""

    effective_limits = _effective_limits(limits)
    ordered, collection_exclusion = _normalise_sources(sources, effective_limits)
    files = tuple(path for path, _text in ordered)
    if collection_exclusion is not None:
        return _failure_outcome(files, (collection_exclusion,))

    all_regions: list[SemanticRegion] = []
    all_exclusions: list[SemanticExclusion] = []
    total_nodes = 0
    total_serialized_chars = 0
    regions_seen = 0

    for file_path, source in ordered:
        try:
            tree = _parse_source(source, file_path)
        except ValueError:
            all_exclusions.append(
                SemanticExclusion(
                    file_path=file_path,
                    reason="malformed-ast",
                    message="Python source could not be parsed; no partial regions were emitted",
                )
            )
            continue

        remaining_nodes = effective_limits.max_nodes - total_nodes
        if remaining_nodes <= 0:
            return _failure_outcome(
                files,
                [
                    *all_exclusions,
                    SemanticExclusion(
                        file_path=file_path,
                        reason="max-nodes",
                        message="AST node budget was exhausted; no partial source result was emitted",
                    ),
                ],
                node_count=total_nodes,
                serialized_chars=total_serialized_chars,
            )
        try:
            source_node_count = _count_nodes(tree, remaining_nodes)
        except _BudgetExceeded as error:
            return _failure_outcome(
                files,
                [
                    *all_exclusions,
                    SemanticExclusion(
                        file_path=file_path,
                        reason=error.reason,
                        message="AST node budget was exhausted; no partial source result was emitted",
                    ),
                ],
                node_count=total_nodes,
                serialized_chars=total_serialized_chars,
            )
        except (RecursionError, _UnsupportedShape) as error:
            node_name = error.node_name if isinstance(error, _UnsupportedShape) else "<deep-tree>"
            all_exclusions.append(
                SemanticExclusion(
                    file_path=file_path,
                    reason="unsupported-node",
                    message=f"unsupported AST node {node_name}; source was excluded",
                )
            )
            continue
        total_nodes += source_node_count

        try:
            specs = _collect_regions(tree)
            module_unsafe = _unsafe_reason(tree)
        except (RecursionError, _UnsupportedShape) as error:
            node_name = error.node_name if isinstance(error, _UnsupportedShape) else "<deep-tree>"
            all_exclusions.append(
                SemanticExclusion(
                    file_path=file_path,
                    reason="unsupported-node",
                    message=f"unsupported AST node {node_name}; source was excluded",
                )
            )
            continue

        if regions_seen + len(specs) > effective_limits.max_regions:
            return _failure_outcome(
                files,
                [
                    *all_exclusions,
                    SemanticExclusion(
                        file_path=file_path,
                        reason="max-regions",
                        message=(
                            "named-region budget was exhausted; "
                            "no partial source result was emitted"
                        ),
                    ),
                ],
                node_count=total_nodes,
                serialized_chars=total_serialized_chars,
            )
        regions_seen += len(specs)

        if module_unsafe is not None:
            reason, message = module_unsafe
            all_exclusions.append(
                SemanticExclusion(file_path=file_path, reason=reason, message=message)
            )

        file_regions: list[SemanticRegion] = []
        file_exclusions: list[SemanticExclusion] = []
        try:
            for spec in specs:
                unsafe = _unsafe_reason(spec.node)
                if unsafe is not None:
                    reason, message = unsafe
                    exclusion = _excluded_region(spec, reason, message)
                    file_exclusions.append(
                        SemanticExclusion(
                            file_path=file_path,
                            reason=exclusion.reason,
                            region_name=exclusion.region_name,
                            start_line=exclusion.start_line,
                            end_line=exclusion.end_line,
                            message=exclusion.message,
                        )
                    )
                    continue
                bindings, imported = _collect_bindings(spec.node)
                remaining_chars = effective_limits.max_serialized_chars - total_serialized_chars
                if remaining_chars <= 0:
                    raise _BudgetExceeded("max-serialized-chars")
                shape, region_node_count = canonical_python_shape(
                    spec.node,
                    local_bindings=bindings,
                    imported_names=imported,
                    max_serialized_chars=remaining_chars,
                )
                fingerprint = semantic_shape_fingerprint(shape)
                file_regions.append(
                    SemanticRegion(
                        file_path=file_path,
                        name=spec.name,
                        kind=spec.kind,
                        start_line=spec.start_line,
                        end_line=spec.end_line,
                        canonical_shape=shape,
                        fingerprint=fingerprint,
                        node_count=region_node_count,
                    )
                )
                total_serialized_chars += len(shape)
        except _BudgetExceeded as error:
            return _failure_outcome(
                files,
                [
                    *all_exclusions,
                    *file_exclusions,
                    SemanticExclusion(
                        file_path=file_path,
                        reason=error.reason,
                        message=(
                            "serialized semantic-shape budget was exhausted; "
                            "no partial source result was emitted"
                        ),
                    ),
                ],
                node_count=total_nodes,
                serialized_chars=total_serialized_chars,
            )
        except (RecursionError, _UnsupportedShape) as error:
            node_name = error.node_name if isinstance(error, _UnsupportedShape) else "<deep-tree>"
            return _failure_outcome(
                files,
                [
                    *all_exclusions,
                    *file_exclusions,
                    SemanticExclusion(
                        file_path=file_path,
                        reason="unsupported-node",
                        region_name=None,
                        message=f"unsupported AST node {node_name}; no partial source result was emitted",
                    ),
                ],
                node_count=total_nodes,
                serialized_chars=total_serialized_chars,
            )
        all_regions.extend(file_regions)
        all_exclusions.extend(file_exclusions)

    all_regions.sort(
        key=lambda region: (region.file_path, region.start_line, region.end_line, region.name)
    )
    all_exclusions.sort(
        key=lambda exclusion: (
            exclusion.file_path,
            exclusion.start_line if exclusion.start_line is not None else 0,
            exclusion.end_line if exclusion.end_line is not None else 0,
            exclusion.region_name or "",
            exclusion.reason,
        )
    )
    return SemanticAnalysisOutcome(
        regions=tuple(all_regions),
        exclusions=tuple(all_exclusions),
        files=files,
        status="excluded" if all_exclusions else "ok",
        node_count=total_nodes,
        serialized_chars=total_serialized_chars,
    )


def analyze_python_source(
    source: str,
    file_path: str = "<memory>",
    *,
    limits: SemanticLimits | None = None,
) -> SemanticAnalysisOutcome:
    """Analyze one Python source string with source-linked region metadata."""

    return analyze_python_sources(((file_path, source),), limits=limits)


def python_semantic_regions(
    source: str,
    file_path: str = "<memory>",
    *,
    limits: SemanticLimits | None = None,
) -> tuple[SemanticRegion, ...]:
    """Return only usable semantic regions from one Python source string."""

    return analyze_python_source(source, file_path=file_path, limits=limits).regions


# Explicit aliases keep the standalone helper discoverable without coupling it
# to the existing lexical duplicate engine's naming.
extract_python_semantic_regions = python_semantic_regions
analyze_python_semantic_source = analyze_python_source
analyze_python_semantic_sources = analyze_python_sources


__all__ = [
    "MAX_PYTHON_SEMANTIC_FILES",
    "MAX_PYTHON_SEMANTIC_NODES",
    "MAX_PYTHON_SEMANTIC_REGIONS",
    "MAX_PYTHON_SEMANTIC_SERIALIZED_CHARS",
    "MAX_SEMANTIC_FILES",
    "MAX_SEMANTIC_NODES",
    "MAX_SEMANTIC_REGIONS",
    "MAX_SEMANTIC_SERIALIZED_CHARS",
    "SEMANTIC_SHAPE_ALGORITHM",
    "SEMANTIC_SHAPE_VERSION",
    "SemanticAnalysisOutcome",
    "SemanticExclusion",
    "SemanticLimits",
    "SemanticOutcome",
    "SemanticRegion",
    "analyze_python_semantic_source",
    "analyze_python_semantic_sources",
    "analyze_python_source",
    "analyze_python_sources",
    "canonical_python_shape",
    "extract_python_semantic_regions",
    "python_semantic_regions",
    "semantic_shape_fingerprint",
]
