"""Attribute shared observations before deduplication or policy evaluation.

A provider's native fingerprint identifies evidence in its own path space.
A run also needs the consumer's component, analysis unit and variant: two
components may both call a different file main.py, and a shared invocation
still has independent suppression/required policy for each consumer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from ici.application.plan import PlannedCheck
from ici.domain.finding import Finding
from ici.domain.observation import Observation
from ici.domain.result import FINGERPRINT_VERSION


def attribute(
    observation: Observation, planned: PlannedCheck, component: str | None
) -> Observation:
    task = planned.task.task if planned.task is not None else None
    unit = (
        task.analysis_unit_ids[0] if task is not None and len(task.analysis_unit_ids) == 1 else None
    )
    variant = task.variant if task is not None else None
    findings = []
    for item in observation.findings:
        owner = component or item.component_id
        analysis_unit = unit or item.analysis_unit_id
        analysis_variant = variant or item.variant
        if owner is None and analysis_unit is None and analysis_variant is None:
            findings.append(item)
            continue
        attributed = replace(
            item,
            component_id=owner,
            analysis_unit_id=analysis_unit,
            variant=analysis_variant,
            task_id=observation.task_id,
        )
        attributed = replace(attributed, fingerprint=_fingerprint(attributed))
        findings.append(attributed)
    return replace(observation, findings=tuple(findings))


def _fingerprint(finding: Finding) -> str:
    span = finding.primary_location
    payload = (
        FINGERPRINT_VERSION,
        finding.component_id,
        finding.analysis_unit_id,
        finding.variant,
        finding.provider,
        finding.rule_id,
        finding.fingerprint,
        finding.message,
        span.path,
        span.start_line,
        span.end_line,
        span.start_column,
        span.end_column,
    )
    return (
        "sha256:" + hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    )
