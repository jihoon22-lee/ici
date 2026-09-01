"""Shared Python AST traversal boundaries for per-function metrics."""

import ast
from collections.abc import Iterator

_NESTED_SCOPE_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


def iter_metric_children(node: ast.AST, *, root: ast.AST) -> Iterator[ast.AST]:
    """Yield children evaluated in ``root`` without entering nested scope bodies.

    A nested function, class, or lambda owns its executable body and is measured
    independently when applicable. Definition-time expressions remain part of
    the enclosing metric: decorators, defaults, annotations, class bases, and
    class keywords are evaluated while the nested scope is created.
    """

    for field_name, value in ast.iter_fields(node):
        if node is not root and isinstance(node, _NESTED_SCOPE_TYPES) and field_name == "body":
            continue
        if isinstance(value, ast.AST):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield item


def walk_metric_scope(root: ast.AST) -> Iterator[ast.AST]:
    """Walk one metric scope deterministically, pruning nested executable bodies."""

    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        children = tuple(iter_metric_children(node, root=root))
        stack.extend(reversed(children))
