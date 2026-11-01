"""Capture the frozen attributed fingerprint and unit attribution for a multi-unit task.

    PYTHONPATH=<oracle>/src python capture_multi_unit_fingerprint.py > multi-unit-fingerprint.json

Frozen ``application/finding_identity.attribute`` over one observation of a task declaring two
analysis units (declared out of order): a finding in a declared unit keeps it, a finding in a
foreign unit loses it, and every digest appends the sorted unit tuple.
"""

import json
import sys
from types import SimpleNamespace

from ici.application.finding_identity import attribute
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Observation, TaskState

UNITS = ("app.python.b", "app.python.a")
CASES = ("app.python.a", "elsewhere.unit", None)


def main():
    findings = tuple(
        Finding(
            fingerprint=f"native-{index}",
            rule_id="python.line.limit",
            message="line limit exceeded é",
            severity="warning",
            confidence="high",
            primary_location=SourceSpan("pkg/main.py", 3, 3, 1, 9),
            provider="ici",
            analysis_unit_id=unit,
        )
        for index, unit in enumerate(CASES)
    )
    task = SimpleNamespace(analysis_unit_ids=UNITS, variant=None)
    planned = SimpleNamespace(task=SimpleNamespace(task=task))
    observation = Observation(
        task_id="app.python.lint", provider="ici", state=TaskState.SUCCEEDED, findings=findings
    )
    attributed = attribute(observation, planned, "app")
    json.dump(
        {
            "units": list(UNITS),
            "findings": [
                {
                    "input_unit": unit,
                    "native_fingerprint": f"native-{index}",
                    "analysis_unit_id": item.analysis_unit_id,
                    "fingerprint": item.fingerprint,
                }
                for index, (unit, item) in enumerate(zip(CASES, attributed.findings, strict=True))
            ],
        },
        sys.stdout,
        indent=1,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
