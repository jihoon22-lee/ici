"""Development-only capture: frozen finding and baseline-delta redaction (core/redaction.py).

Feeds a finding and a delta carrying credentials in every string field to frozen
`_redact_finding` and `_redact_delta` and records the redacted fields. Never called by the Rust
product.

Run from a neutral directory: PYTHONPATH=<oracle>/src <venv python> -s
capture_finding_redaction.py <out.json> (not -I, which would ignore PYTHONPATH).
"""

import dataclasses
import enum
import json
import subprocess
import sys
from pathlib import Path

from ici.core import models
from ici.core.redaction import _redact_delta, _redact_finding

EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
TOKEN = "ghp_" + "A" * 36
BEARER = "Bearer abc.def-123"
ASSIGN = "password=hunter2secret"


def plain(value):
    if dataclasses.is_dataclass(value):
        return {item.name: plain(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def location(path: str) -> models.SourceLocation:
    return models.SourceLocation(path=path, start_line=3, label=f"label {ASSIGN}")


def main() -> None:
    oracle = Path(sys.modules["ici"].__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != EXPECTED:
        raise SystemExit(f"oracle must be {EXPECTED}, found {revision}")
    finding = models.Finding(
        rule_id="ici.lint.rule",
        category=models.FindingCategory.CORRECTNESS,
        severity=models.FindingSeverity.HIGH,
        confidence=models.FindingConfidence.HIGH,
        fingerprint="sha256:" + "0" * 64,
        primary_location=location(f"src/{TOKEN}.py"),
        related_locations=[location("src/other.py")],
        message=f"leaked {TOKEN} in message",
        explanation=f"Authorization: {BEARER}",
        remediation=f"rotate {ASSIGN}",
        tool_rule_id=f"rule-{TOKEN}",
        tool_name=f"tool {ASSIGN}",
        tool_version=f"1.0 {BEARER}",
        suppression=models.FindingSuppression(
            suppressed=True, kind=models.SuppressionKind.CONFIG, reason=f"because {ASSIGN}"
        ),
        metrics={
            f"metric {TOKEN}": models.FindingMetric(value=1, unit=f"unit {BEARER}"),
            "metric ghp_" + "B" * 36: models.FindingMetric(value=2, unit="plain"),
            "plain": models.FindingMetric(value=3.5, unit=""),
        },
        snippet=f"x = '{TOKEN}'",
        fixes=[
            models.FindingFix(
                description=f"replace {ASSIGN}",
                replacements=(
                    models.FindingFixReplacement(
                        path=f"src/{TOKEN}.py",
                        start_line=1,
                        start_column=1,
                        end_line=1,
                        end_column=4,
                        replacement=f"token = '{TOKEN}'",
                    ),
                ),
            )
        ],
    )
    delta = models.FindingDelta(
        state=models.DeltaState.NEW,
        engine_name=f"lint {ASSIGN}",
        fingerprint="sha256:" + "1" * 64,
        rule_id=f"ici.lint.{TOKEN}",
        message=f"new {BEARER}",
        current_location=location(f"src/{TOKEN}.py"),
        baseline_location=location("src/base.py"),
    )
    document = {
        "oracle": EXPECTED,
        "finding": {"input": plain(finding), "redacted": plain(_redact_finding(finding))},
        "delta": {"input": plain(delta), "redacted": plain(_redact_delta(delta))},
    }
    Path(sys.argv[1]).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
