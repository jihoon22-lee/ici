"""Coverage parsers and aggregation helpers used by :mod:`ici.engines.test`."""

import ast
import contextlib
import json
from pathlib import Path
from typing import Any

from ici.core.project import get_all_python_sources

_COVERAGE_KEYS = (
    "covered_lines",
    "num_statements",
    "missing_lines",
    "num_branches",
    "covered_branches",
)


def _load_coverage_json(json_path: Path) -> dict | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_coverage_counts(values: object) -> dict[str, int] | None:
    if not isinstance(values, dict):
        return None
    parsed: dict[str, int] = {}
    for key in _COVERAGE_KEYS:
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        parsed[key] = value
    if parsed["covered_lines"] > parsed["num_statements"]:
        return None
    if parsed["missing_lines"] > parsed["num_statements"]:
        return None
    if parsed["covered_lines"] + parsed["missing_lines"] != parsed["num_statements"]:
        return None
    if parsed["covered_branches"] > parsed["num_branches"]:
        return None
    return parsed


def _parse_line_numbers(value: object, expected: int) -> list[int] | None:
    if not isinstance(value, list) or len(value) != expected:
        return None
    parsed: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            return None
        parsed.append(item)
    if len(set(parsed)) != len(parsed):
        return None
    return parsed


def _relative_coverage_path(fname: str, project_root: Path) -> str:
    relative = fname
    with contextlib.suppress(ValueError):
        relative = str(Path(fname).relative_to(project_root))
    return relative


def _parse_coverage_file(
    fname: object, finfo: object, project_root: Path
) -> tuple[str, dict] | None:
    if not isinstance(fname, str) or not isinstance(finfo, dict):
        return None
    executed_lines = finfo.get("executed_lines")
    missing_lines = finfo.get("missing_lines")
    summary = finfo.get("summary")
    if (
        not isinstance(executed_lines, list)
        or not isinstance(missing_lines, list)
        or not isinstance(summary, dict)
    ):
        return None
    parsed_summary = _parse_coverage_counts(summary)
    if parsed_summary is None:
        return None
    parsed_executed = _parse_line_numbers(executed_lines, parsed_summary["covered_lines"])
    parsed_missing = _parse_line_numbers(missing_lines, parsed_summary["missing_lines"])
    if parsed_executed is None or parsed_missing is None:
        return None
    if set(parsed_executed).intersection(parsed_missing):
        return None
    return _relative_coverage_path(fname, project_root), {
        "executed_lines": parsed_executed,
        "missing_lines": parsed_missing,
        "summary": parsed_summary,
    }


def _parse_coverage_files(files: object, project_root: Path) -> dict[str, dict] | None:
    if not isinstance(files, dict) or not files:
        return None
    parsed_files: dict[str, dict] = {}
    for fname, finfo in files.items():
        parsed_file = _parse_coverage_file(fname, finfo, project_root)
        if parsed_file is None:
            return None
        relative, file_data = parsed_file
        if relative in parsed_files:
            return None
        parsed_files[relative] = file_data
    return parsed_files


def _sum_coverage_counts(file_data: dict[str, dict]) -> dict[str, int]:
    totals = {key: 0 for key in _COVERAGE_KEYS}
    for finfo in file_data.values():
        summary = finfo["summary"]
        for key in _COVERAGE_KEYS:
            totals[key] += summary[key]
    return totals


def _coverage_percent(covered: int, total: int) -> float | None:
    return round(covered / total * 100.0, 1) if total else None


def _build_coverage_result(file_data: dict[str, dict], totals: dict[str, int]) -> dict:
    line_cov = _coverage_percent(totals["covered_lines"], totals["num_statements"])
    branch_cov = _coverage_percent(totals["covered_branches"], totals["num_branches"])
    return {
        "files": file_data,
        "branch_cov": branch_cov,
        "line_cov": line_cov,
        "totals": {
            "stmts": totals["num_statements"],
            "miss": totals["missing_lines"],
            "cover": line_cov,
            "branch_cover": branch_cov,
        },
    }


def parse_coverage_json(json_path: Path, project_root: Path) -> dict | None:
    """Parse coverage.py JSON into strict per-file line and branch data."""

    data = _load_coverage_json(json_path)
    if data is None:
        return None
    totals = _parse_coverage_counts(data.get("totals"))
    file_data = _parse_coverage_files(data.get("files"), project_root)
    if totals is None or file_data is None:
        return None
    if totals["num_statements"] == 0 or _sum_coverage_counts(file_data) != totals:
        return None
    return _build_coverage_result(file_data, totals)


def _gcov_source_path(gcov_file: Path, source_files: set[str], project_root: Path) -> str | None:
    candidate = gcov_file.name[:-5].replace("#", "/")
    if candidate in source_files:
        return candidate
    try:
        path = Path(candidate)
        absolute = path if path.is_absolute() else project_root / path
        relative = str(absolute.resolve().relative_to(project_root))
    except ValueError:
        return None
    return relative if relative in source_files else None


def parse_gcov_dir(cov_dir: Path, source_files: set[str], project_root: Path) -> list[dict]:
    """Parse gcov branch and line output into per-module coverage rows."""

    rows: list[dict] = []
    for gcov_file in cov_dir.glob("*.gcov"):
        relative = _gcov_source_path(gcov_file, source_files, project_root)
        if relative is None:
            continue

        covered = 0
        miss = 0
        branches = 0
        covered_branches = 0
        missing: list[int] = []
        try:
            content = gcov_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in content.splitlines():
            if line.startswith("branch"):
                parts = line.split()
                branches += 1
                if "taken" in parts:
                    index = parts.index("taken")
                    value = parts[index + 1] if index + 1 < len(parts) else "0"
                    try:
                        if int(value.rstrip("%")) > 0:
                            covered_branches += 1
                    except ValueError as error:
                        _ = error
                continue
            if ":" not in line:
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            count, line_number, _ = parts
            count = count.strip()
            line_number = line_number.strip()
            if count.startswith("-") or not line_number.isdigit():
                continue
            number = int(line_number)
            if count.startswith("#"):
                miss += 1
                missing.append(number)
            else:
                digits = count.rstrip("*")
                if digits.isdigit() and int(digits) > 0:
                    covered += 1

        statements = covered + miss
        rows.append(
            {
                "file": relative,
                "stmts": statements,
                "covered": covered,
                "miss": miss,
                "cover": round(covered / statements * 100.0, 1) if statements else 100.0,
                "branch_cover": round(covered_branches / branches * 100.0, 1) if branches else None,
                "nb": branches,
                "cb": covered_branches,
                "missing_lines": missing[:30],
            }
        )
    return rows


def compute_python_function_coverage(
    cov_data: dict, project_root: Path, config: dict[str, Any] | None
) -> list[dict]:
    """Mark a Python function covered when one body line executed."""

    rows: list[dict] = []
    file_map = cov_data.get("files", {})
    for py_file in get_all_python_sources(project_root, config):
        relative = str(py_file.relative_to(project_root))
        file_info = file_map.get(relative)
        if not file_info:
            continue
        executed = set(file_info.get("executed_lines") or [])
        missing = set(file_info.get("missing_lines") or [])
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_start = node.body[0].lineno if node.body else node.lineno
            body_end = node.end_lineno or body_start
            body_lines = set(range(body_start, body_end + 1))
            rows.append(
                {
                    "file": relative,
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": body_end,
                    "covered": bool(body_lines & executed),
                    "missing_lines": sorted(body_lines & missing)[:30],
                }
            )
    return rows


def parse_gcov_functions(cov_dir: Path, source_files: set[str], project_root: Path) -> list[dict]:
    """Parse gcov ``function ... called N`` records."""

    rows: list[dict] = []
    for gcov_file in cov_dir.glob("*.gcov"):
        relative = _gcov_source_path(gcov_file, source_files, project_root)
        if relative is None:
            continue
        try:
            content = gcov_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            if not line.startswith("function "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            called = 0
            if "called" in parts:
                index = parts.index("called")
                try:
                    called = int(parts[index + 1])
                except (ValueError, IndexError):
                    called = 0
            rows.append(
                {
                    "file": relative,
                    "name": parts[1],
                    "start_line": 1,
                    "end_line": 1,
                    "covered": called > 0,
                    "missing_lines": [],
                }
            )
    return rows


def build_coverage_summary(
    coverage_data: dict | None, cpp_rows: list[dict]
) -> tuple[list[dict], dict | None, str]:
    """Combine Python and C++ module rows and return files, totals, and source label."""

    python_rows: list[dict] = []
    if coverage_data:
        for relative, file_info in coverage_data.get("files", {}).items():
            summary = file_info.get("summary") or {}
            statements = int(summary.get("num_statements", 0))
            covered = int(summary.get("covered_lines", 0))
            missing = int(summary.get("missing_lines", 0))
            branches = int(summary.get("num_branches", 0))
            covered_branches = int(summary.get("covered_branches", 0))
            python_rows.append(
                {
                    "file": relative,
                    "stmts": statements,
                    "covered": covered,
                    "miss": missing,
                    "cover": round(covered / statements * 100.0, 1) if statements else 100.0,
                    "branch_cover": round(covered_branches / branches * 100.0, 1)
                    if branches
                    else None,
                    "nb": branches,
                    "cb": covered_branches,
                    "missing_lines": file_info.get("missing_lines", [])[:30],
                }
            )

    files = [*python_rows, *cpp_rows]
    files.sort(key=lambda row: (row["cover"], row["file"]))
    if not files:
        totals = coverage_data.get("totals") if coverage_data else None
        source = "coverage.py" if coverage_data else "estimated"
        return files, totals, source

    statements = sum(row["stmts"] for row in files)
    covered = sum(row["covered"] for row in files)
    missing = sum(row["miss"] for row in files)
    branches = sum(row.get("nb", 0) for row in files)
    covered_branches = sum(row.get("cb", 0) for row in files)
    totals = {
        "stmts": statements,
        "miss": missing,
        "cover": round(covered / statements * 100.0, 1) if statements else None,
        "branch_cover": round(covered_branches / branches * 100.0, 1) if branches else None,
    }
    sources = []
    if coverage_data:
        sources.append("coverage.py")
    if cpp_rows:
        sources.append("gcov")
    return files, totals, "/".join(sources)


def calculate_tem(
    branch_cov: float,
    func_cov: float,
    passed_tests: int,
    total_tests: int,
    coverage_totals: dict | None,
) -> dict[str, Any]:
    """Calculate the TEM score from measured or estimated coverage inputs."""

    pass_rate = (passed_tests / total_tests) if total_tests > 0 else 0.0
    line_cov = coverage_totals.get("cover") if coverage_totals else None
    real_branch = coverage_totals.get("branch_cover") if coverage_totals else None
    if line_cov is not None:
        cov_factor, cov_label, cov_shown = min(80.0, line_cov) / 80.0, "Line", line_cov
    elif real_branch is not None:
        cov_factor = min(80.0, real_branch * 1.25) / 80.0
        cov_label, cov_shown = "Branch", real_branch
    else:
        cov_factor, cov_label, cov_shown = min(80.0, branch_cov) / 80.0, "Line", branch_cov
    tem_score = round(cov_factor * (func_cov / 100.0) * pass_rate * 5.0, 2)
    return {
        "tem_score": max(0.0, min(5.0, tem_score)),
        "cov_label": cov_label,
        "cov_shown": cov_shown,
        "line_coverage": line_cov,
        "pass_rate": round(pass_rate, 4),
        "cov_suffix": " (est)" if line_cov is None and real_branch is None else "",
    }
