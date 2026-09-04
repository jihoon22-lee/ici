"""Deterministic coverage-scope parsing and source-located policy targets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ici.core.findings import canonical_project_path
from ici.core.models import EngineStatus, InspectionTarget
from ici.core.path_utils import resolve_project_path

_LINE_RANGE_RE = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
_MAX_CHANGED_LINES = 10_000
_MAX_COVERAGE_ROWS = 10_000


def parse_changed_lines(
    project_root: Path,
    specs: object,
    *,
    max_lines: int = _MAX_CHANGED_LINES,
) -> dict[str, set[int]]:
    """Parse bounded ``path:line[-line]`` entries for changed-line coverage.

    Paths must already use canonical project-relative POSIX form and identify a
    regular project file. Overlapping entries are rejected so a line can never
    be counted twice by accident.
    """

    if type(max_lines) is not int or not 1 <= max_lines <= _MAX_COVERAGE_ROWS:
        raise ValueError("changed-line limit must be between 1 and 100000")
    if not isinstance(specs, list):
        raise ValueError("engines.test.changed_lines must be a list of strings")

    parsed: dict[str, set[int]] = {}
    total = 0
    root = project_root.resolve()
    for index, value in enumerate(specs):
        if not isinstance(value, str) or not value:
            raise ValueError(f"changed_lines[{index}] must be a non-empty string")
        raw_path, separator, raw_range = value.rpartition(":")
        match = _LINE_RANGE_RE.fullmatch(raw_range) if separator else None
        if not raw_path or match is None:
            raise ValueError(
                f"changed_lines[{index}] must use canonical path:line or path:start-end form"
            )
        canonical = canonical_project_path(raw_path)
        if canonical in {"", "."} or canonical != raw_path:
            raise ValueError(f"changed_lines[{index}] path must already be canonical")
        candidate = resolve_project_path(root, canonical)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"changed_lines[{index}] path is not a regular project file")

        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            raise ValueError(f"changed_lines[{index}] range is reversed")
        count = end - start + 1
        if count > max_lines - total:
            raise ValueError(f"changed lines exceed the bounded {max_lines}-line limit")
        lines = parsed.setdefault(canonical, set())
        incoming = set(range(start, end + 1))
        if lines.intersection(incoming):
            raise ValueError(f"changed_lines[{index}] duplicates or overlaps an earlier entry")
        lines.update(incoming)
        total += count
    return {path: parsed[path] for path in sorted(parsed)}


def build_changed_line_status(
    coverage_data: dict[str, Any] | None,
    cpp_rows: list[dict[str, Any]],
) -> dict[str, dict[int, bool]]:
    """Build an internal executable-line map without enlarging report rows."""

    status: dict[str, dict[int, bool]] = {}
    if coverage_data:
        files = coverage_data.get("files")
        if isinstance(files, dict):
            for raw_path, raw_info in files.items():
                if not isinstance(raw_path, str) or not isinstance(raw_info, dict):
                    continue
                lines = status.setdefault(raw_path, {})
                for line in raw_info.get("executed_lines", []):
                    if type(line) is int and line > 0:
                        lines[line] = True
                for line in raw_info.get("missing_lines", []):
                    if type(line) is int and line > 0:
                        lines.setdefault(line, False)
    for row in cpp_rows:
        raw_path = row.get("file")
        if not isinstance(raw_path, str):
            continue
        lines = status.setdefault(raw_path, {})
        for line in row.get("executable_lines", []):
            if type(line) is int and line > 0:
                lines.setdefault(line, False)
        for line in row.get("covered_lines", []):
            if type(line) is int and line > 0:
                lines[line] = True
    return {
        path: {line: status[path][line] for line in sorted(status[path])} for path in sorted(status)
    }


def _number(cfg: dict[str, Any], key: str, default: float) -> float:
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"engines.test.{key} must be a number")
    return float(value)


def _threshold_target(
    *,
    path: str,
    name: str,
    actual: float,
    threshold: float,
    message_label: str,
    start_line: int = 1,
    end_line: int | None = None,
    metrics: dict[str, Any] | None = None,
) -> InspectionTarget:
    passed = actual >= threshold
    values = {
        "actual": round(actual, 1),
        "threshold": threshold,
        "gated": True,
        **(metrics or {}),
    }
    return InspectionTarget(
        file_path=path,
        start_line=start_line,
        end_line=end_line,
        target_name=name,
        status=EngineStatus.PASS if passed else EngineStatus.WARN,
        message=(
            f"{message_label} {actual:.1f}% "
            f"{'meets' if passed else 'is below'} the configured minimum {threshold:.1f}%"
        ),
        metrics=values,
    )


def evaluate_coverage_policy(
    cfg: dict[str, Any],
    coverage_files: list[dict[str, Any]],
    function_rows: list[dict[str, Any]],
    changed_line_status: dict[str, dict[int, bool]],
    *,
    changed_lines: dict[str, set[int]] | None = None,
) -> list[InspectionTarget]:
    """Return complete PASS/WARN/ERROR inventory for measured coverage policy."""

    if len(coverage_files) > _MAX_COVERAGE_ROWS or len(function_rows) > _MAX_COVERAGE_ROWS:
        raise ValueError("coverage inventory exceeds the bounded 10000-row limit")
    targets: list[InspectionTarget] = []
    rows = sorted(coverage_files, key=lambda row: str(row.get("file", "")))
    if rows:
        statements = sum(int(row.get("stmts", 0)) for row in rows)
        covered = sum(int(row.get("covered", 0)) for row in rows)
        overall = covered / statements * 100.0 if statements else 100.0
        targets.append(
            _threshold_target(
                path=".",
                name="Coverage:Overall line",
                actual=overall,
                threshold=_number(cfg, "min_line_cov", 80.0),
                message_label="Overall line coverage",
                metrics={"covered_lines": covered, "executable_lines": statements},
            )
        )

        minimum_statements = cfg.get("min_file_statements", 5)
        if type(minimum_statements) is not int or minimum_statements < 1:
            raise ValueError("engines.test.min_file_statements must be a positive integer")
        file_threshold = _number(cfg, "min_file_cov", 80.0)
        for row in rows:
            row_statements = int(row.get("stmts", 0))
            if row_statements < minimum_statements:
                continue
            path = str(row.get("file", "."))
            targets.append(
                _threshold_target(
                    path=path,
                    name="Coverage:File",
                    actual=float(row.get("cover", 0.0)),
                    threshold=file_threshold,
                    message_label="File line coverage",
                    metrics={
                        "covered_lines": int(row.get("covered", 0)),
                        "executable_lines": row_statements,
                        "missed_lines": int(row.get("miss", 0)),
                    },
                )
            )

    ordered_functions = sorted(
        function_rows,
        key=lambda row: (
            str(row.get("file", "")),
            int(row.get("start_line", 1)),
            int(row.get("start_column") or 0),
            str(row.get("name", "")),
        ),
    )
    covered_functions = sum(bool(row.get("covered")) for row in ordered_functions)
    function_percent = (
        covered_functions / len(ordered_functions) * 100.0 if ordered_functions else 100.0
    )
    if rows or ordered_functions:
        targets.append(
            _threshold_target(
                path=".",
                name="Coverage:Functions",
                actual=function_percent,
                threshold=_number(cfg, "min_func_cov", 90.0),
                message_label="Aggregate function coverage",
                metrics={
                    "covered_functions": covered_functions,
                    "functions": len(ordered_functions),
                },
            )
        )
    for row in ordered_functions:
        covered_function = bool(row.get("covered"))
        path = str(row.get("file", "."))
        name = str(row.get("name", "<unnamed>"))
        targets.append(
            InspectionTarget(
                file_path=path,
                start_line=int(row.get("start_line", 1)),
                end_line=int(row.get("end_line") or row.get("start_line", 1)),
                start_column=(
                    int(row["start_column"]) if type(row.get("start_column")) is int else None
                ),
                end_column=(int(row["end_column"]) if type(row.get("end_column")) is int else None),
                target_name=f"Coverage:Function:{name}",
                status=EngineStatus.PASS if covered_function else EngineStatus.WARN,
                message=(
                    f"Function {name} was exercised by the aggregate project test suite"
                    if covered_function
                    else f"Function {name} was not exercised by the aggregate project test suite"
                ),
                metrics={
                    "covered": covered_function,
                    "gated": False,
                    "test_scope": "aggregate-project-suite",
                    "symbol": str(row.get("symbol") or name),
                },
            )
        )

    selected_lines = changed_lines or {}
    if selected_lines:
        threshold = _number(cfg, "min_changed_line_cov", 100.0)
        for path in sorted(selected_lines):
            measured = changed_line_status.get(path, {})
            requested = sorted(selected_lines[path])
            executable = [line for line in requested if line in measured]
            if not executable:
                targets.append(
                    InspectionTarget(
                        file_path=path,
                        start_line=requested[0],
                        end_line=requested[-1],
                        target_name="Coverage:Changed lines",
                        status=EngineStatus.ERROR,
                        message="Changed-line coverage has no executable measured lines",
                        metrics={
                            "requested_lines": len(requested),
                            "executable_lines": 0,
                            "threshold": threshold,
                            "gated": True,
                        },
                    )
                )
                continue
            covered_changed = sum(bool(measured[line]) for line in executable)
            actual = covered_changed / len(executable) * 100.0
            targets.append(
                _threshold_target(
                    path=path,
                    start_line=requested[0],
                    end_line=requested[-1],
                    name="Coverage:Changed lines",
                    actual=actual,
                    threshold=threshold,
                    message_label="Changed executable-line coverage",
                    metrics={
                        "requested_lines": len(requested),
                        "covered_lines": covered_changed,
                        "executable_lines": len(executable),
                        "non_executable_lines": len(requested) - len(executable),
                    },
                )
            )
    return targets


__all__ = ["build_changed_line_status", "evaluate_coverage_policy", "parse_changed_lines"]
