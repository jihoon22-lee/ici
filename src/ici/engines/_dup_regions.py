"""Bounded source-region projection for lexical duplicate matching."""

from __future__ import annotations

import ast
import bisect
import heapq
from collections.abc import Iterable

from ici.engines._cpp_dup_tokenization import cpp_directive_lines
from ici.engines.complexity import _cpp_function_spans

RegionKey = tuple[int, int]


def _segment_for(line: int, barriers: tuple[int, ...]) -> int:
    return bisect.bisect_right(barriers, line)


def _project_regions(
    lines: Iterable[int],
    intervals: list[tuple[int, int, int]],
    barriers: set[int],
) -> tuple[RegionKey, ...]:
    ordered_barriers = tuple(sorted(barriers))
    ordered_intervals = sorted(intervals)
    active: list[tuple[int, int, int]] = []
    next_interval = 0
    projected: list[RegionKey] = []
    for line in lines:
        while (
            next_interval < len(ordered_intervals) and ordered_intervals[next_interval][0] <= line
        ):
            start, end, candidate_scope = ordered_intervals[next_interval]
            heapq.heappush(active, (end - start, end, candidate_scope))
            next_interval += 1
        while active and active[0][1] < line:
            heapq.heappop(active)
        scope = active[0][2] if active else 0
        projected.append((scope, _segment_for(line, ordered_barriers)))
    return tuple(projected)


def _python_intervals_and_barriers(text: str) -> tuple[list[tuple[int, int, int]], set[int]]:
    try:
        tree = ast.parse(text)
    except (IndentationError, RecursionError, SyntaxError, ValueError) as error:
        raise ValueError(
            "Python syntax could not be parsed for duplicate-region boundaries"
        ) from error

    intervals: list[tuple[int, int, int]] = []
    barriers: set[int] = set()
    scoped = (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)
    scope = 0
    for node in ast.walk(tree):
        if isinstance(node, scoped):
            scope += 1
            decorators = getattr(node, "decorator_list", ())
            start = min(
                [node.lineno, *(item.lineno for item in decorators if hasattr(item, "lineno"))]
            )
            end = getattr(node, "end_lineno", node.lineno)
            intervals.append((start, end, scope))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            barriers.update(range(start, end + 1))
    return intervals, barriers


def python_duplicate_regions(text: str, lines: Iterable[int]) -> tuple[RegionKey, ...]:
    """Map Python code lines to their narrowest AST scope and import segment."""

    intervals, barriers = _python_intervals_and_barriers(text)
    return _project_regions(lines, intervals, barriers)


def _cpp_directive_lines(text: str) -> set[int]:
    return set(cpp_directive_lines(text))


def cpp_duplicate_regions(text: str, lines: Iterable[int]) -> tuple[RegionKey, ...]:
    """Map C++ code lines to fallback function scopes and directive segments."""

    source_lines = text.splitlines()
    try:
        spans = _cpp_function_spans(source_lines)
    except (RuntimeError, ValueError):
        spans = []
    intervals = [
        (span.start_line, span.end_line, index)
        for index, span in enumerate(spans, 1)
        if span.end_line >= span.start_line
    ]
    return _project_regions(lines, intervals, _cpp_directive_lines(text))
