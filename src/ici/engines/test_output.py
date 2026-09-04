"""Bounded pytest and unittest output normalization for the test engine."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ici.core.models import EngineStatus, InspectionTarget
from ici.core.runner import ProcessResult
from ici.engines.coverage_support import pytest_result_has_evidence

_PYTEST_VERDICTS = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")
_PYTEST_DURATION_RE = re.compile(
    r"^\s*(?P<seconds>(?:\d+(?:\.\d+)?|\.\d+))s\s+"
    r"(?P<phase>[A-Za-z][A-Za-z0-9_-]*)\s+(?P<nodeid>\S.*?)\s*$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MAX_DURATION_TOKEN_CHARS = 32


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _node_location(nodeid: str, project_root: Path) -> tuple[str, int]:
    """Resolve a pytest node id to a contained project path and line.

    Pytest's duration report normally contains a node id without a source line,
    but plugins and custom reporters sometimes emit ``file.py:42::test``.  The
    line is kept when present.  A malformed or escaping path is intentionally
    reduced to the stable ``tests`` scope rather than allowing report data to
    point outside the project.
    """

    source = nodeid.split("::", 1)[0]
    line = 1
    line_match = re.match(r"^(?P<path>.*):(?P<line>[1-9]\d*)$", source)
    if line_match is not None:
        source = line_match.group("path")
        line = int(line_match.group("line"))

    try:
        root = project_root.resolve(strict=False)
        candidate = Path(source)
        resolved = (
            candidate.resolve(strict=False)
            if candidate.is_absolute()
            else (root / candidate).resolve(strict=False)
        )
        relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError, TypeError):
        return "tests", 1
    if not relative or relative == "." or relative.startswith("../"):
        return "tests", 1
    return relative, line


def parse_pytest_outcomes(output: str) -> dict[str, str]:
    """Return deterministic per-node pytest verdicts from verbose output.

    Summary-only output cannot identify individual nodes and therefore yields
    an empty mapping.  When a plugin repeats a report line, the last verdict is
    authoritative, matching pytest's final terminal state.
    """

    outcomes: dict[str, str] = {}
    for line in _strip_ansi(output).splitlines():
        if "::" not in line:
            continue
        # Match from the line rather than splitting on whitespace: pytest
        # parameter ids may legitimately contain spaces (``test[x y]``).
        verdict_matches = list(
            re.finditer(r"\b(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", line)
        )
        if not verdict_matches:
            continue
        match = verdict_matches[-1]
        nodeid = line[: match.start()].strip()
        if nodeid:
            outcomes[nodeid] = match.group(0)
    return {key: outcomes[key] for key in sorted(outcomes)}


def pytest_node_location(nodeid: str, project_root: Path) -> tuple[str, int]:
    """Expose the contained source location mapping used by quality findings."""

    return _node_location(nodeid, project_root)


def _pytest_duration_summary(
    output: str,
    project_root: Path,
    *,
    threshold: float = 0.0,
    max_items: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Parse pytest duration rows and retain a bounded, source-aware inventory.

    Pytest prints the slowest rows in a presentation-oriented table.  Parsing
    and sorting here gives callers stable ordering across pytest versions and
    keeps duplicate plugin rows from inflating the observed count.
    """

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold < 0
    ):
        raise ValueError("duration threshold must be a finite non-negative number")
    if type(max_items) is not int or max_items < 1:
        raise ValueError("max_items must be positive")

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in _strip_ansi(output).splitlines():
        match = _PYTEST_DURATION_RE.match(line)
        if match is None:
            continue
        seconds_text = match.group("seconds")
        if len(seconds_text) > _MAX_DURATION_TOKEN_CHARS:
            continue
        seconds = float(seconds_text)
        if not math.isfinite(seconds):
            continue
        if seconds < threshold:
            continue
        phase = match.group("phase")
        nodeid = match.group("nodeid").strip()
        if "::" not in nodeid:
            continue
        file_path, start_line = _node_location(nodeid, project_root)
        key = (nodeid, phase)
        candidate = {
            "nodeid": nodeid,
            "phase": phase,
            "duration": seconds,
            "file_path": file_path,
            "start_line": start_line,
        }
        current = rows.get(key)
        if current is None or seconds > float(current["duration"]):
            rows[key] = candidate

    ordered = sorted(
        rows.values(),
        key=lambda row: (
            -float(row["duration"]),
            str(row["file_path"]),
            int(row["start_line"]),
            str(row["nodeid"]),
            str(row["phase"]),
        ),
    )
    return ordered[:max_items], len(ordered)


def parse_pytest_durations(
    output: str,
    project_root: Path,
    *,
    threshold: float = 0.0,
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """Return the bounded retained portion of a pytest duration report."""

    rows, _observed = _pytest_duration_summary(
        output,
        project_root,
        threshold=threshold,
        max_items=max_items,
    )
    return rows


class TestOutputMixin:
    """Normalize supported Python test-runner states into the shared contract."""

    @staticmethod
    def _parse_pytest_outcomes(output: str) -> dict[str, str]:
        """Compatibility wrapper for callers that keep parser access on the engine."""

        return parse_pytest_outcomes(output)

    @staticmethod
    def _parse_pytest_durations(
        output: str,
        project_root: Path,
        *,
        threshold: float = 0.0,
        max_items: int = 50,
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for the deterministic duration parser."""

        return parse_pytest_durations(
            output,
            project_root,
            threshold=threshold,
            max_items=max_items,
        )

    @staticmethod
    def _parse_pytest_duration_summary(
        output: str,
        project_root: Path,
        *,
        threshold: float = 0.0,
        max_items: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        return _pytest_duration_summary(
            output,
            project_root,
            threshold=threshold,
            max_items=max_items,
        )

    def _parse_pytest_result(
        self, result: ProcessResult, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        passed, total, has_failure = self._parse_pytest_stdout(
            result.stdout + ("\n" + result.stderr if result.stderr else ""), targets
        )
        output = result.stdout + "\n" + result.stderr
        collected = re.search(r"\bcollected\s+(\d+)\s+items?\b", output)
        if total == 0 and collected is not None:
            total = int(collected.group(1))
        skipped = sum(
            int(target.metrics.get("test_cases", 1))
            for target in targets
            if target.status == EngineStatus.SKIP
        )
        if result.returncode == 5 or (total == 0 and result.returncode == 0):
            has_failure = True
            if not any(target.target_name == "[Python] Tests" for target in targets):
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name="[Python] Tests",
                        status=EngineStatus.FAIL,
                        message="No tests collected",
                    )
                )
        elif (
            result.returncode == 0
            and not pytest_result_has_evidence(output)
            and not (total > 0 and skipped >= total)
        ):
            self._record_tool_error(  # type: ignore[attr-defined]
                "Pytest returned success without parseable test results"
            )
        elif result.returncode not in (0, 1):
            self._record_tool_error(  # type: ignore[attr-defined]
                f"Pytest failed with exit code {result.returncode}"
            )
        elif result.returncode == 1 and not has_failure:
            self._record_tool_error(  # type: ignore[attr-defined]
                "Pytest returned failure without parseable diagnostics"
            )
        elif total == 0:
            has_failure = True
        return passed, total, has_failure

    @staticmethod
    def _parse_unittest_stdout(
        result: ProcessResult, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        passed = 0
        total = 0
        has_failure = False
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if " ... " not in line:
                continue
            name, verdict = line.split(" ... ", 1)
            verdict = verdict.strip()
            if verdict == "ok":
                status = EngineStatus.PASS
                message = "Unittest passed"
                passed += 1
            elif verdict.startswith("expected failure"):
                status = EngineStatus.PASS
                message = "Expected failure was exercised"
                passed += 1
            elif verdict.startswith("skipped"):
                status = EngineStatus.SKIP
                message = verdict
            elif verdict.startswith(("FAIL", "ERROR", "unexpected success")):
                status = EngineStatus.FAIL
                message = (
                    "Unexpected success violated the expected-failure contract"
                    if verdict.startswith("unexpected success")
                    else "Unittest assertion failure"
                )
                has_failure = True
            else:
                continue
            total += 1
            targets.append(
                InspectionTarget(
                    file_path="tests",
                    start_line=1,
                    target_name=name.strip(),
                    status=status,
                    message=message,
                )
            )
        return passed, total, has_failure

    def _parse_pytest_stdout(
        self, out: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        passed = 0
        total = 0
        has_failure = False
        verdicts = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")
        for line in out.splitlines():
            if "::" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            verdict = next((item for item in parts[1:] if item in verdicts), None)
            if verdict is None:
                continue
            total += 1
            target_name = parts[0]
            test_file = target_name.split("::")[0] if "::" in target_name else "tests"
            if verdict in ("PASSED", "XFAIL"):
                passed += 1
                status = EngineStatus.PASS
                message = (
                    "Expected failure was exercised"
                    if verdict == "XFAIL"
                    else "Test passed successfully"
                )
            elif verdict == "SKIPPED":
                status = EngineStatus.SKIP
                message = "Test was collected but not executed"
            else:
                has_failure = True
                status = EngineStatus.FAIL
                message = (
                    "Unexpected pass violated the expected-failure contract"
                    if verdict == "XPASS"
                    else "Test assertion failed"
                )
            targets.append(
                InspectionTarget(
                    file_path=test_file,
                    start_line=1,
                    target_name=target_name,
                    status=status,
                    message=message,
                )
            )
        if total == 0:

            def last_count(label: str) -> int:
                matches = re.findall(rf"\b(\d+)\s+{label}\b", out)
                return int(matches[-1]) if matches else 0

            ordinary_passed = last_count("passed")
            failed = last_count("failed") + last_count("errors?")
            skipped = last_count("skipped")
            xfailed = last_count("xfailed")
            xpassed = last_count("xpassed")
            passed = ordinary_passed + xfailed
            total = passed + failed + skipped + xpassed
            has_failure = failed > 0 or xpassed > 0
            for label, count, status, message in (
                ("Passed", ordinary_passed, EngineStatus.PASS, "Pytest summary: passed"),
                ("XFail", xfailed, EngineStatus.PASS, "Pytest summary: expected failure"),
                ("Skipped", skipped, EngineStatus.SKIP, "Pytest summary: not executed"),
                ("Failed", failed, EngineStatus.FAIL, "Pytest summary: failed"),
                ("XPass", xpassed, EngineStatus.FAIL, "Pytest summary: unexpected pass"),
            ):
                if count:
                    targets.append(
                        InspectionTarget(
                            file_path="tests",
                            start_line=1,
                            target_name=f"[Python] {label} ({count})",
                            status=status,
                            message=message,
                            metrics={"test_cases": count},
                        )
                    )
        return passed, total, has_failure
