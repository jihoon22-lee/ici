#!/usr/bin/env python3
"""Synthetic large-report benchmark for the console, HTML, JSON and SARIF reporters.

The record this writes is a trend artifact, not a gate. Wall-clock on a shared
runner moves for reasons that have nothing to do with the reporters, so a hard
threshold here would fail on noise. Each measurement carries its budget and the
comparison only runs when ``--enforce`` is passed on purpose.

Budgets were measured on the reference workstation described in
``docs/ci-integration.md`` and then given headroom; see that section before
changing one.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from rich.console import Console  # noqa: E402

from ici import __version__ as ICI_VERSION  # noqa: E402
from ici.core.models import (  # noqa: E402
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.console import print_suite_dashboard  # noqa: E402
from ici.reporters.html import generate_html_report  # noqa: E402
from ici.reporters.issue_view import ConsoleOptions  # noqa: E402
from ici.reporters.json_rep import serialize_suite_result  # noqa: E402
from ici.reporters.sarif import serialize_sarif  # noqa: E402

BENCHMARK_SCHEMA = "ici.benchmark.report/v1"
DEFAULT_FINDINGS = 100_000
DEFAULT_ENGINES = 10
DEFAULT_FILES = 2_000

# Seconds, at the DEFAULT_FINDINGS inventory size, roughly three times the
# reference-workstation measurement recorded in docs/ci-integration.md.
#
# The 3x was first chosen on an assumption — that a shared runner is several
# times slower — and the first CI run disproved it: GitHub's ubuntu-latest came
# in at 1.05x to 1.36x, using 35-44% of these ceilings. So the multiple is not
# absorbing a slow machine. It is there because the regression worth catching
# is a reporter turning superlinear in the finding count, which overshoots by a
# factor, while ordinary run-to-run variation does not. A tighter ceiling would
# report that variation without adding signal.
#
# This is also why --enforce is opt-in; see the module docstring.
BUDGET_SECONDS: dict[str, float] = {
    "console-default": 35.0,
    "console-verbose": 155.0,
    "html": 50.0,
    "json": 45.0,
    "sarif": 40.0,
}

_SEVERITIES = (
    FindingSeverity.CRITICAL,
    FindingSeverity.HIGH,
    FindingSeverity.MEDIUM,
    FindingSeverity.LOW,
    FindingSeverity.INFO,
)
_CATEGORIES = (
    FindingCategory.CORRECTNESS,
    FindingCategory.MAINTAINABILITY,
    FindingCategory.SECURITY,
    FindingCategory.RESOURCE,
)
_CONFIDENCES = (
    FindingConfidence.EXACT,
    FindingConfidence.HIGH,
    FindingConfidence.MEDIUM,
    FindingConfidence.LOW,
)


def build_suite(findings: int, engines: int, files: int) -> VerificationSuiteResult:
    """Build a deterministic synthetic suite with the requested finding count.

    Findings are spread over engines, files, rules, severities and categories so
    the grouping, sorting and filtering paths all see real fan-out rather than
    one repeated row that a dictionary lookup would collapse.
    """

    if findings < 0 or engines < 1 or files < 1:
        raise ValueError("findings must be >= 0 and engines/files must be >= 1")

    results: list[EngineResult] = []
    for engine_index in range(engines):
        engine_name = f"synthetic{engine_index:02d}"
        share = findings // engines + (1 if engine_index < findings % engines else 0)
        engine_findings = [
            Finding(
                rule_id=f"ici.{engine_name}.rule{index % 17:02d}",
                category=_CATEGORIES[index % len(_CATEGORIES)],
                severity=_SEVERITIES[index % len(_SEVERITIES)],
                confidence=_CONFIDENCES[index % len(_CONFIDENCES)],
                fingerprint=f"{engine_name}-{index:08d}",
                primary_location=SourceLocation(
                    f"src/pkg{(index % files) % 32:02d}/module_{index % files:05d}.py",
                    (index % 900) + 1,
                    end_line=(index % 900) + 3,
                    start_column=1,
                ),
                message=(
                    f"{engine_name} finding {index}: synthetic condition "
                    f"observed in generated benchmark input"
                ),
            )
            for index in range(share)
        ]
        results.append(
            EngineResult(
                engine_name=engine_name,
                status=EngineStatus.WARN if engine_findings else EngineStatus.PASS,
                summary=f"{len(engine_findings)} synthetic findings",
                duration=0.5,
                findings=engine_findings,
            )
        )
    return VerificationSuiteResult(
        suite_status=EngineStatus.WARN,
        results=results,
        duration=float(engines),
    )


def _measure(stage: str, work: Callable[[], int]) -> dict[str, Any]:
    """Run one reporter and record its wall-clock seconds and output size."""

    start = time.perf_counter()
    size = work()
    elapsed = time.perf_counter() - start
    budget = BUDGET_SECONDS[stage]
    return {
        "stage": stage,
        "seconds": round(elapsed, 3),
        "output_bytes": size,
        "budget_seconds": budget,
        "within_budget": elapsed <= budget,
    }


def _console_bytes(suite: VerificationSuiteResult, base: Path, *, verbose: bool) -> int:
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=120, force_terminal=False, no_color=True, legacy_windows=False
    )
    options = ConsoleOptions(verbose=verbose)
    print_suite_dashboard(suite, base, options=options, output_console=console)
    return len(buffer.getvalue().encode("utf-8"))


def _commit() -> str:
    """Return the exact revision this record describes, or an empty string."""

    for name in ("GITHUB_SHA", "ICI_BENCHMARK_COMMIT"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        completed = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def run_benchmark(findings: int, engines: int, files: int) -> dict[str, Any]:
    """Measure every reporter against one synthetic suite and return the record."""

    suite = build_suite(findings, engines, files)
    with tempfile.TemporaryDirectory(prefix="ici-benchmark-") as raw_dir:
        base = Path(raw_dir)
        html_path = base / "benchmark.html"
        measurements = [
            _measure("console-default", lambda: _console_bytes(suite, base, verbose=False)),
            _measure("console-verbose", lambda: _console_bytes(suite, base, verbose=True)),
            _measure(
                "html",
                lambda: (
                    generate_html_report(suite, html_path, "benchmark", base),
                    html_path.stat().st_size,
                )[1],
            ),
            _measure(
                "json",
                lambda: len(
                    json.dumps(serialize_suite_result(suite, base), separators=(",", ":")).encode(
                        "utf-8"
                    )
                ),
            ),
            _measure(
                "sarif",
                lambda: len(
                    json.dumps(serialize_sarif(suite, base), separators=(",", ":")).encode("utf-8")
                ),
            ),
        ]
    return {
        "schema": BENCHMARK_SCHEMA,
        "ici_version": ICI_VERSION,
        "commit": _commit(),
        "python_version": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "finding_count": findings,
        "engine_count": engines,
        "file_count": files,
        "measurements": measurements,
        "within_budget": all(item["within_budget"] for item in measurements),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=int, default=DEFAULT_FINDINGS)
    parser.add_argument("--engines", type=int, default=DEFAULT_ENGINES)
    parser.add_argument("--files", type=int, default=DEFAULT_FILES)
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="exit non-zero when a stage exceeds its budget (off by default: this is a trend artifact)",
    )
    args = parser.parse_args(argv)

    record = run_benchmark(args.findings, args.engines, args.files)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(text, encoding="utf-8")
    sys.stdout.write(text)

    if args.enforce and not record["within_budget"]:
        over = [item["stage"] for item in record["measurements"] if not item["within_budget"]]
        sys.stderr.write(f"benchmark exceeded its budget: {', '.join(over)}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
