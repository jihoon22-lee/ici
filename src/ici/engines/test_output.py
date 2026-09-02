"""Bounded pytest and unittest output normalization for the test engine."""

from __future__ import annotations

import re

from ici.core.models import EngineStatus, InspectionTarget
from ici.core.runner import ProcessResult
from ici.engines.coverage_support import pytest_result_has_evidence


class TestOutputMixin:
    """Normalize supported Python test-runner states into the shared contract."""

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
