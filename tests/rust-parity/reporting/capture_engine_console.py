"""Development-only capture: frozen console of legacy single-engine commands.

Runs the frozen `__main__._print_engine_result`, `print_line_distribution_chart` and
`_save_single_report` printers (rich 15.0.0, non-terminal width 80) on fixed engine results.
Usage: PYTHONPATH=<oracle>/src python -I capture_engine_console.py <oracle> > engine-console.json
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console

import ici.__main__ as frozen_main
import ici.reporters.console as frozen_console
from ici.core.models import EngineResult, EngineStatus

LONG = (
    "Makefile at the project root has no configured adapter; CMake, qmake, and "
    "explicitly enabled Make are supported, and generic g++ was not invoked"
)
CASES = [
    ("build", "PASS", "Build Completed: pkg v2.3.4 into v2.3.4/x86_64", {}, None),
    ("build", "ERROR", LONG, {}, None),
    ("lint", "WARN", "x" * 95 + " tail", {}, None),
    ("dup", "FAIL", "short " + "y" * 170, {}, "dup_report.json"),
    (
        "type",
        "SKIP",
        "spaced   words    with  runs of   spaces that go past the eighty col",
        {},
        None,
    ),
    (
        "test",
        "PASS",
        "trailing spaces at the end of a long line that should be cut here     ",
        {},
        None,
    ),
    ("security", "ERROR", "한국어 요약은 두 칸 너비 문자로 줄을 나눕니다 " * 3, {}, None),
    ("cycle", "PASS", "a [bracket] b", {}, None),
    ("line", "PASS", "3 code / 1 files", {"code": 2, "comment": 0, "blank": 1, "total": 3}, None),
    (
        "line",
        "PASS",
        "12345 code / 9 files",
        {"code": 12345, "comment": 2345, "blank": 678, "total": 15368},
        "line_report.json",
    ),
    ("line", "SKIP", "No files", {}, None),
]


def capture(oracle: Path) -> dict[str, object]:
    buffer = io.StringIO()
    console = Console(file=buffer, width=80, force_terminal=False, color_system=None)
    frozen_main.console = console
    frozen_console.console = console
    records = []
    for engine, status, summary, extra, report in CASES:
        buffer.seek(0)
        buffer.truncate()
        result = EngineResult(
            engine_name=engine, status=EngineStatus(status), summary=summary, extra=extra
        )
        frozen_main._print_engine_result(result)
        if engine == "line":
            frozen_console.print_line_distribution_chart(
                extra.get("code", 0),
                extra.get("comment", 0),
                extra.get("blank", 0),
                extra.get("total", 0),
            )
        if report:
            # Frozen `_save_single_report` prints this line after writing the report.
            frozen_main.console.print(f"[dim]Report saved to: {report}[/dim]")
        records.append(
            {
                "engine": {
                    "engine_name": engine,
                    "status": status,
                    "summary": summary,
                    "extra": extra,
                },
                "report": report,
                "stdout": buffer.getvalue(),
            }
        )
    head = subprocess.run(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {"oracle": head, "rich": "15.0.0", "cases": records}


if __name__ == "__main__":
    json.dump(capture(Path(sys.argv[1])), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
