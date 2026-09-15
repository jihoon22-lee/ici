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


def cyclomatic_complexity(node: ast.AST) -> int:
    """Cyclomatic complexity of one function body: 1 + branching points."""

    complexity = 1
    for child in walk_metric_scope(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.While,
                ast.For,
                ast.AsyncFor,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
            ),
        ):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            complexity += 1
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
        elif isinstance(child, ast.Match):
            complexity += 1 + sum(1 for case in child.cases if case.guard is not None)
    return complexity


def max_nesting(node: ast.AST) -> int:
    """Maximum block nesting depth inside one function body."""

    def _get_depth(curr: ast.AST, depth: int) -> int:
        max_d = depth
        is_block = isinstance(curr, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.Try, ast.With))
        new_depth = depth + (1 if is_block else 0)
        max_d = max(max_d, new_depth)
        for child in iter_metric_children(curr, root=node):
            max_d = max(max_d, _get_depth(child, new_depth))
        return max_d

    return _get_depth(node, 0)


def cognitive_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, int]:
    """Return (cognitive_complexity, max_nesting) for one function.

    Rules (Sonar-inspired, pure-Python):
    - +1 for if/elif/else, for, while, except, with, assert, comprehension
    - +1 per boolean operator chain (and/or), plus nesting
    - +nesting_level for each nesting increment (if/for/while/except/with)
    - an ``elif`` continues the decision chain: +1 without a nesting increment
    - break/continue inside a loop count as +1
    """
    cognitive = 0
    nesting_peak = 0

    def _is_elif(parent: ast.AST, child: ast.AST) -> bool:
        """Report whether ``child`` is the ``elif`` continuing ``parent``.

        Python has no ``elif`` node: the parser nests it as the only statement
        of the preceding ``If``'s ``orelse``. Reading that shape literally would
        score a flat chain of mutually exclusive branches as if each one were
        indented inside the previous, which is neither what the source looks
        like nor what S3776 specifies. The C++ path already treats an else-if as
        a continuation of the same decision chain; this keeps both languages on
        one rule.
        """
        return (
            isinstance(parent, ast.If)
            and isinstance(child, ast.If)
            and len(parent.orelse) == 1
            and parent.orelse[0] is child
        )

    def walk(n, nesting: int, in_loop: bool = False):
        nonlocal cognitive, nesting_peak
        nesting_peak = max(nesting_peak, nesting)
        for child in iter_metric_children(n, root=node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.ExceptHandler,
                    ast.With,
                    ast.AsyncWith,
                ),
            ):
                # Each branch/loop/handler/with is +1, weighted by its nesting
                # depth. An elif continues the chain instead of deepening it, so
                # it takes a flat +1 and keeps the current nesting level.
                if _is_elif(n, child):
                    cognitive += 1
                    walk(child, nesting, in_loop)
                    continue
                cognitive += 1 + nesting
                walk(
                    child,
                    nesting + 1,
                    in_loop or isinstance(child, (ast.For, ast.AsyncFor, ast.While)),
                )
            elif isinstance(child, ast.BoolOp):
                if isinstance(child.op, (ast.And, ast.Or)):
                    cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            elif isinstance(child, ast.comprehension):
                cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            elif isinstance(child, (ast.Break, ast.Continue)):
                if in_loop:
                    cognitive += 1
            elif isinstance(child, ast.Assert):
                cognitive += 1 + nesting
                walk(child, nesting, in_loop)
            else:
                walk(child, nesting, in_loop)

    walk(node, 0)
    return cognitive, nesting_peak
