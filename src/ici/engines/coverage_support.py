"""Coverage parsers and aggregation helpers used by :mod:`ici.engines.test`."""

import ast
import contextlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.project import get_all_python_sources
from ici.engines.gcov_json import (
    MAX_COMPRESSED_BYTES,
    MAX_DECOMPRESSED_BYTES,
    GcovJsonError,
    GcovReport,
    parse_gcov_json_gz,
)

_COVERAGE_KEYS = (
    "covered_lines",
    "num_statements",
    "missing_lines",
    "num_branches",
    "covered_branches",
)

_MAX_GCOV_JSON_REPORTS = 4_096
_MAX_GCOV_JSON_COMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_GCOV_JSON_DECOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_GCOV_JSON_FILE_RECORDS = 16_384
_MAX_GCOV_JSON_FUNCTION_RECORDS = 500_000
_MAX_GCOV_JSON_LINE_RECORDS = 4_000_000
_MAX_GCOV_JSON_BRANCH_RECORDS = 8_000_000
_MAX_GCOV_JSON_CALL_RECORDS = 8_000_000

# gcc emits an extra arm marked "(throw)" around nearly every call that may
# raise — which, with exceptions enabled, is essentially every STL allocation.
# It represents the exception-unwind edge, not a branch anyone wrote, and no
# test can take it without inducing a real throw (bad_alloc and friends).
# Counting it made C++ branch coverage read ~20 points lower than it is: a file
# whose every branch point was reached still scored 73% purely from unwind
# arms. lcov 2.x filters the same edges for the same reason. Excluded from both
# numerator and denominator so the remaining figure still measures real,
# author-written branches.
_EXCEPTION_UNWIND_ARM = "(throw)"


def module_unavailable(result, module: str) -> bool:
    """Accept only a minimal interpreter-level missing-module diagnostic."""

    if result.returncode <= 0 or result.timed_out or result.truncated:
        return False
    lines = [
        line.strip() for line in f"{result.stdout}\n{result.stderr}".splitlines() if line.strip()
    ]
    if len(lines) != 1:
        return False
    missing = rf"No module named ['\"]?{re.escape(module)}['\"]?"
    prefix = (
        rf"(?:python(?:3(?:\.\d+)?)?(?:\.exe)?|"
        rf"(?:[A-Za-z]:[\\/]|/)[^\n]*python(?:3(?:\.\d+)?)?(?:\.exe)?)"
        rf"\s*:\s*{missing}"
    )
    return bool(re.fullmatch(rf"(?:{missing}|{prefix})", lines[0]))


def pytest_result_has_evidence(output: str) -> bool:
    """Return whether pytest output reports a parseable test result."""

    return bool(
        re.search(r"::[^\r\n]*\s+(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", output)
        or re.search(r"\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b", output)
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


def _coverage_paths_match(
    file_data: dict[str, dict], expected_files: set[str] | None, project_root: Path
) -> bool:
    if expected_files is None:
        return True
    expected = {_relative_coverage_path(path, project_root) for path in expected_files}
    return set(file_data) == expected


def parse_coverage_json(
    json_path: Path,
    project_root: Path,
    expected_files: set[str] | None = None,
) -> dict | None:
    """Parse coverage.py JSON into strict per-file line and branch data."""

    data = _load_coverage_json(json_path)
    if data is None:
        return None
    totals = _parse_coverage_counts(data.get("totals"))
    file_data = _parse_coverage_files(data.get("files"), project_root)
    if totals is None or file_data is None:
        return None
    if not _coverage_paths_match(file_data, expected_files, project_root):
        return None
    if totals["num_statements"] == 0 or _sum_coverage_counts(file_data) != totals:
        return None
    return _build_coverage_result(file_data, totals)


def _match_source_suffix(candidate: str, source_files: set[str]) -> str | None:
    """Find the project-relative path a gcov `Source:` value refers to.

    gcov records the path the compiler saw. CMake compiles with absolute paths,
    but qmake compiles from inside the shadow tree with relative ones, so the
    value arrives as `../../../src/format.cpp` and there is no reliable base to
    resolve it against — the base is the object directory, which the .gcov file
    does not name. Dropping the leading components until the remainder is a
    known source recovers it. The set of sources is the project's own, so a
    match is not a coincidence.
    """

    parts = PurePosixPath(candidate).parts
    for start in range(len(parts)):
        suffix = "/".join(parts[start:])
        if suffix in source_files:
            return suffix
    return None


def _gcov_declared_source(gcov_file: Path) -> str | None:
    """Read the `Source:` header gcov writes as line 0 of its output."""

    try:
        with gcov_file.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(4):
                line = handle.readline()
                if not line:
                    return None
                marker = line.find("Source:")
                if marker != -1:
                    return line[marker + len("Source:") :].strip()
    except OSError:
        return None
    return None


def _in_scope_relative(candidate: str, source_files: set[str], project_root: Path) -> str | None:
    """Project-relative form of a path, but only if it is a measured source.

    Membership decides, not resolvability. A test source sits inside the project
    and resolves cleanly, and counting it inflates the coverage denominator of
    every project on the generic g++ path.
    """

    path = Path(candidate)
    absolute = (path if path.is_absolute() else project_root / path).resolve()
    if not absolute.is_relative_to(project_root):
        return None
    relative = str(absolute.relative_to(project_root))
    return relative if relative in source_files else None


def _gcov_source_path(gcov_file: Path, source_files: set[str], project_root: Path) -> str | None:
    candidate = gcov_file.name[:-5].replace("#", "/")
    if candidate in source_files:
        return candidate
    resolved = _in_scope_relative(candidate, source_files, project_root)
    if resolved is not None:
        return resolved

    # The filename is a mangled form of the path — gcov -p encodes "/" as "#"
    # and ".." as "^" — so it only round-trips for absolute paths. The header
    # inside the file is the unmangled original.
    declared = _gcov_declared_source(gcov_file)
    if declared is None:
        return None
    if Path(declared).is_absolute():
        return _in_scope_relative(declared, source_files, project_root)
    return _match_source_suffix(declared, source_files)


class _GcovCounts:
    """Mutable per-file tallies collected while scanning one ``.gcov`` text."""

    __slots__ = ("branches", "covered", "covered_branches", "covered_lines", "miss", "missing")

    def __init__(self) -> None:
        self.covered = 0
        self.miss = 0
        self.branches = 0
        self.covered_branches = 0
        self.missing: list[int] = []
        self.covered_lines: list[int] = []


def _count_gcov_branch(line: str, counts: _GcovCounts) -> None:
    """Tally one ``branch`` record, ignoring exception-unwind arms."""

    if _EXCEPTION_UNWIND_ARM in line:
        return
    parts = line.split()
    counts.branches += 1
    if "taken" not in parts:
        return
    index = parts.index("taken")
    value = parts[index + 1] if index + 1 < len(parts) else "0"
    try:
        if int(value.rstrip("%")) > 0:
            counts.covered_branches += 1
    except ValueError as error:
        _ = error


def _count_gcov_source(line: str, counts: _GcovCounts) -> None:
    """Tally one ``count:line:source`` record; non-executable lines are ignored."""

    parts = line.split(":", 2)
    if len(parts) < 3:
        return
    count, line_number, _ = parts
    count = count.strip()
    line_number = line_number.strip()
    if count.startswith("-") or not line_number.isdigit():
        return
    number = int(line_number)
    if count.startswith("#"):
        counts.miss += 1
        counts.missing.append(number)
        return
    digits = count.rstrip("*")
    if digits.isdigit() and int(digits) > 0:
        counts.covered += 1
        counts.covered_lines.append(number)


def _count_gcov_text(content: str) -> _GcovCounts:
    counts = _GcovCounts()
    for line in content.splitlines():
        if line.startswith("branch"):
            _count_gcov_branch(line, counts)
        elif ":" in line:
            _count_gcov_source(line, counts)
    return counts


def _gcov_row(relative: str, counts: _GcovCounts) -> dict:
    statements = counts.covered + counts.miss
    return {
        "file": relative,
        "stmts": statements,
        "covered": counts.covered,
        "miss": counts.miss,
        "cover": round(counts.covered / statements * 100.0, 1) if statements else 100.0,
        "branch_cover": (
            round(counts.covered_branches / counts.branches * 100.0, 1) if counts.branches else None
        ),
        "nb": counts.branches,
        "cb": counts.covered_branches,
        "missing_lines": counts.missing[:30],
        # Internal policy evidence. ``build_coverage_summary`` strips these
        # complete lists from public report rows after changed-line policy has
        # consumed them.
        "executable_lines": sorted({*counts.covered_lines, *counts.missing}),
        "covered_lines": sorted(set(counts.covered_lines)),
    }


def parse_gcov_dir(cov_dir: Path, source_files: set[str], project_root: Path) -> list[dict]:
    """Parse gcov branch and line output into per-module coverage rows."""

    rows: list[dict] = []
    for gcov_file in cov_dir.glob("*.gcov"):
        relative = _gcov_source_path(gcov_file, source_files, project_root)
        if relative is None:
            continue
        try:
            content = gcov_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts = _count_gcov_text(content)
        if counts.covered + counts.miss == 0:
            continue
        rows.append(_gcov_row(relative, counts))
    return rows


def compute_python_function_coverage(
    cov_data: dict,
    project_root: Path,
    config: dict[str, Any] | None,
    python_sources: list[Path] | None = None,
) -> list[dict]:
    """Mark a Python function covered when one body line executed."""

    rows: list[dict] = []
    file_map = cov_data.get("files", {})
    sources = (
        python_sources
        if python_sources is not None
        else get_all_python_sources(project_root, config)
    )
    for py_file in sources:
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


def _gcov_json_source_path(
    declared: str,
    compilation_directory: str,
    source_files: set[str],
    project_root: Path,
) -> str | None:
    """Map one gcov JSON source through its recorded compilation directory."""

    root = project_root.resolve()
    declared_path = Path(declared)
    candidates: list[Path] = []
    if declared_path.is_absolute():
        candidates.append(declared_path)
    else:
        cwd = Path(compilation_directory)
        if cwd.is_absolute():
            candidates.append(cwd / declared_path)
        candidates.append(root / declared_path)

    matches: list[str] = []
    for candidate in candidates:
        try:
            relative = str(candidate.resolve().relative_to(root))
        except (OSError, RuntimeError, ValueError):
            continue
        if relative in source_files and relative not in matches:
            matches.append(relative)
    if len(matches) > 1:
        raise GcovJsonError(
            f"source path {declared!r} maps to multiple project files",
            code="ambiguous_source_path",
        )
    return matches[0] if matches else None


def _merge_gcov_json_report(
    report: GcovReport,
    source_files: set[str],
    project_root: Path,
    lines_by_file: dict[str, dict[int, bool]],
    branches_by_file: dict[str, dict[tuple[int, str, int | None, int | None, bool, int], bool]],
    functions_by_file: dict[str, dict[tuple[str, int, int, int, int], dict]],
) -> tuple[int, int]:
    matched = 0
    ignored = 0
    for source in report.files:
        relative = _gcov_json_source_path(
            source.file,
            report.current_working_directory,
            source_files,
            project_root,
        )
        if relative is None:
            ignored += 1
            continue
        matched += 1
        lines = lines_by_file.setdefault(relative, {})
        branches = branches_by_file.setdefault(relative, {})
        functions = functions_by_file.setdefault(relative, {})
        for line in source.lines:
            lines[line.line_number] = lines.get(line.line_number, False) or line.count > 0
            for branch_index, branch in enumerate(line.branches):
                if branch.throw:
                    continue
                ordered_v1_index = (
                    branch_index
                    if branch.source_block_id is None or branch.destination_block_id is None
                    else -1
                )
                branch_identity = (
                    line.line_number,
                    line.function_name,
                    branch.source_block_id,
                    branch.destination_block_id,
                    branch.fallthrough,
                    ordered_v1_index,
                )
                branches[branch_identity] = branches.get(branch_identity, False) or branch.count > 0
        for function in source.functions:
            name = function.demangled_name or function.name or "<unnamed>"
            function_identity = (
                function.name,
                function.start_line,
                function.start_column,
                function.end_line,
                function.end_column,
            )
            previous = functions.get(function_identity)
            functions[function_identity] = {
                "file": relative,
                "name": name,
                "symbol": function.name,
                "start_line": function.start_line,
                "start_column": function.start_column,
                "end_line": function.end_line,
                "end_column": function.end_column,
                "covered": function.execution_count > 0 or bool(previous and previous["covered"]),
                "missing_lines": [],
            }
    return matched, ignored


def _bounded_gcov_json_report_paths(cov_dir: Path) -> list[Path]:
    """Return a deterministic, count-bounded snapshot of report paths."""

    reports: list[Path] = []
    try:
        for candidate in cov_dir.iterdir():
            if not candidate.name.endswith(".gcov.json.gz"):
                continue
            reports.append(candidate)
            if len(reports) > _MAX_GCOV_JSON_REPORTS:
                raise GcovJsonError(
                    f"gcov JSON report count exceeds {_MAX_GCOV_JSON_REPORTS}",
                    code="aggregate_limit",
                )
    except GcovJsonError:
        raise
    except OSError as exc:
        raise GcovJsonError(
            f"cannot enumerate gcov JSON evidence: {exc}", code="read_error"
        ) from exc
    reports.sort(key=lambda path: path.name)
    if not reports:
        raise GcovJsonError("no .gcov.json.gz reports were found", code="missing_data")
    return reports


def parse_gcov_json_dir(
    cov_dir: Path,
    source_files: set[str],
    project_root: Path,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Parse and aggregate bounded gcov JSON reports with exact source geometry.

    Every expected production translation unit must be present.  A malformed
    JSON report or incomplete source set raises :class:`GcovJsonError`; callers
    must not retry the same evidence through the lossy text parser.
    """

    reports = _bounded_gcov_json_report_paths(cov_dir)

    lines_by_file: dict[str, dict[int, bool]] = {}
    branches_by_file: dict[str, dict[tuple[int, str, int | None, int | None, bool, int], bool]] = {}
    functions_by_file: dict[str, dict[tuple[str, int, int, int, int], dict]] = {}
    versions: set[int] = set()
    gcc_versions: set[str] = set()
    matched_records = 0
    ignored_records = 0
    compressed_bytes = 0
    decompressed_bytes = 0
    file_records = 0
    function_records = 0
    line_records = 0
    branch_records = 0
    call_records = 0
    lines_without_block_ids = 0
    ordered_branch_records = 0
    for report_path in reports:
        compressed_remaining = _MAX_GCOV_JSON_COMPRESSED_BYTES - compressed_bytes
        decompressed_remaining = _MAX_GCOV_JSON_DECOMPRESSED_BYTES - decompressed_bytes
        if compressed_remaining <= 0 or decompressed_remaining <= 0:
            raise GcovJsonError(
                "cumulative gcov JSON byte budget is exhausted", code="aggregate_limit"
            )
        try:
            report = parse_gcov_json_gz(
                report_path,
                max_compressed_bytes=min(MAX_COMPRESSED_BYTES, compressed_remaining),
                max_decompressed_bytes=min(MAX_DECOMPRESSED_BYTES, decompressed_remaining),
            )
        except GcovJsonError as exc:
            if exc.code in {"compressed_limit", "decompressed_limit"} and (
                compressed_remaining < MAX_COMPRESSED_BYTES
                or decompressed_remaining < MAX_DECOMPRESSED_BYTES
            ):
                raise GcovJsonError(
                    "cumulative gcov JSON byte budget was exceeded", code="aggregate_limit"
                ) from exc
            raise
        compressed_bytes += report.compressed_bytes
        decompressed_bytes += report.decompressed_bytes
        file_records += len(report.files)
        function_records += sum(len(source.functions) for source in report.files)
        line_records += sum(len(source.lines) for source in report.files)
        branch_records += sum(
            len(line.branches) for source in report.files for line in source.lines
        )
        call_records += sum(len(line.calls) for source in report.files for line in source.lines)
        lines_without_block_ids += sum(
            not line.block_ids for source in report.files for line in source.lines
        )
        ordered_branch_records += sum(
            branch.source_block_id is None or branch.destination_block_id is None
            for source in report.files
            for line in source.lines
            for branch in line.branches
        )
        limits = (
            (file_records, _MAX_GCOV_JSON_FILE_RECORDS, "file"),
            (function_records, _MAX_GCOV_JSON_FUNCTION_RECORDS, "function"),
            (line_records, _MAX_GCOV_JSON_LINE_RECORDS, "line"),
            (branch_records, _MAX_GCOV_JSON_BRANCH_RECORDS, "branch"),
            (call_records, _MAX_GCOV_JSON_CALL_RECORDS, "call"),
        )
        for observed_count, maximum, label in limits:
            if observed_count > maximum:
                raise GcovJsonError(
                    f"cumulative gcov JSON {label} records exceed {maximum}",
                    code="aggregate_limit",
                )
        if versions and report.format_version not in versions:
            raise GcovJsonError(
                "gcov JSON reports contain mixed format versions",
                code="inconsistent_report_set",
            )
        if gcc_versions and report.gcc_version not in gcc_versions:
            raise GcovJsonError(
                "gcov JSON reports contain mixed GCC versions",
                code="inconsistent_report_set",
            )
        versions.add(report.format_version)
        gcc_versions.add(report.gcc_version)
        matched, ignored = _merge_gcov_json_report(
            report,
            source_files,
            project_root,
            lines_by_file,
            branches_by_file,
            functions_by_file,
        )
        matched_records += matched
        ignored_records += ignored

    observed = set(lines_by_file) | set(functions_by_file)
    missing_sources = sorted(source_files - observed)
    if missing_sources:
        preview = ", ".join(missing_sources[:8])
        suffix = f" (+{len(missing_sources) - 8} more)" if len(missing_sources) > 8 else ""
        raise GcovJsonError(
            f"coverage evidence is missing {len(missing_sources)} source(s): {preview}{suffix}",
            code="incomplete_source_coverage",
        )

    rows: list[dict] = []
    function_rows: list[dict] = []
    empty_sources: list[str] = []
    for relative in sorted(source_files):
        lines = lines_by_file.get(relative, {})
        branches = branches_by_file.get(relative, {})
        covered = sum(lines.values())
        statements = len(lines)
        missing_lines = sorted(number for number, executed in lines.items() if not executed)
        covered_branches = sum(branches.values())
        if statements == 0:
            empty_sources.append(relative)
        rows.append(
            {
                "file": relative,
                "stmts": statements,
                "covered": covered,
                "miss": statements - covered,
                "cover": round(covered / statements * 100.0, 1) if statements else 100.0,
                "branch_cover": (
                    round(covered_branches / len(branches) * 100.0, 1) if branches else None
                ),
                "nb": len(branches),
                "cb": covered_branches,
                "missing_lines": missing_lines[:30],
                "executable_lines": sorted(lines),
                "covered_lines": sorted(number for number, executed in lines.items() if executed),
            }
        )
        function_rows.extend(functions_by_file.get(relative, {}).values())

    function_rows.sort(
        key=lambda row: (
            row["file"],
            row["start_line"],
            row["start_column"],
            row["name"],
        )
    )
    provenance: dict[str, Any] = {
        "format": "gcov-json",
        "format_versions": sorted(versions),
        "gcc_versions": sorted(gcc_versions),
        "report_count": len(reports),
        "compressed_bytes": compressed_bytes,
        "decompressed_bytes": decompressed_bytes,
        "file_records": file_records,
        "function_records": function_records,
        "line_records": line_records,
        "branch_records": branch_records,
        "call_records": call_records,
        "lines_without_block_ids": lines_without_block_ids,
        "ordered_branch_records": ordered_branch_records,
        "branch_identity": (
            "basic-block" if ordered_branch_records == 0 else "basic-block-or-line-order"
        ),
        "matched_file_records": matched_records,
        "ignored_file_records": ignored_records,
        "expected_sources": len(source_files),
        "covered_sources": len(observed),
        "empty_sources": empty_sources,
        "function_geometry": "exact",
        "source_mapping": "recorded-compilation-directory-or-project-root",
        "throw_branches_excluded": True,
    }
    return rows, function_rows, provenance


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

    public_keys = (
        "file",
        "stmts",
        "covered",
        "miss",
        "cover",
        "branch_cover",
        "nb",
        "cb",
        "missing_lines",
    )
    public_cpp_rows = [{key: row.get(key) for key in public_keys} for row in cpp_rows]
    files = [*python_rows, *public_cpp_rows]
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
