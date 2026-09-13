#!/usr/bin/env python3
"""Render a bundle smoke report as a GitHub step summary (#202 PR C).

Separate from the workflow so the formatting has a test. The part that matters
is the last paragraph: a run where cases were blocked has to say which claims it
did not support, because a table of eight passes reads exactly like a table of
ten unless something names the gap.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA = "ici.next.bundle-smoke/v1"


def render(report: dict[str, object]) -> str:
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        return "## Bundle smoke\n\nThe report lists no cases, so nothing was measured.\n"

    lines = [
        "## Bundle smoke",
        "",
        f"`{report.get('schema', 'unknown schema')}` — "
        f"{report.get('pass', 0)} pass, {report.get('fail', 0)} fail, "
        f"{report.get('blocked', 0)} blocked",
        "",
        "|case|status|detail|",
        "|---|---|---|",
    ]
    lines.extend(
        f"|{case['case']}|{case['status']}|{_escape(str(case['detail']))}|" for case in cases
    )

    blocked = [case["case"] for case in cases if case["status"] == "BLOCKED"]
    if blocked:
        lines += [
            "",
            f"**Not measured on this runner: {', '.join(blocked)}.** "
            "These are claims about the artifact that this run does not support.",
        ]
    return "\n".join(lines) + "\n"


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: summarize_smoke.py <report.json>\n")
        return 2
    path = Path(argv[1])
    if not path.is_file():
        sys.stdout.write(
            "## Bundle smoke\n\nThe smoke produced no report, so nothing was measured.\n"
        )
        return 0
    sys.stdout.write(render(json.loads(path.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
