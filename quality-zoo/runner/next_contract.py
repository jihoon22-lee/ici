"""Validate ``ici.next.run`` results against schema-3 known-answer expectations.

The stable corpus asserted an ``ici.result/v3`` report: a suite status and
engine-keyed findings. The next result is a different shape — a ``gate``
verdict on its own axis, flat ``findings`` carrying ``provider``/``rule_id``/
``primary_location``, plus ``limitations`` and ``metrics``. Scenario
expectations are therefore expressed against that shape, not translated from
the old one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from runner.common import ContractError, require_string

GATES = {"PASS", "FAIL", "INCOMPLETE"}
SEVERITIES = {"info", "low", "medium", "high", "critical"}
EVIDENCE = {"MEASURED", "ESTIMATED", "BLOCKED", "UNAVAILABLE"}

SUITE_SCOPE = ""


@dataclass(frozen=True)
class ContractFailure:
    check: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class ContractResult:
    scenario_id: str
    observed_gate: str
    producer_version: str
    matched_findings: int
    failures: tuple[ContractFailure, ...]


def _predicate(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise ContractError("expectation-schema", f"{label} must be a non-empty object")
    predicate = dict(raw)
    allowed = {
        "provider",
        "task_id",
        "rule_id",
        "severity",
        "evidence",
        "path",
        "path_regex",
        "line",
        "line_min",
        "line_max",
        "message_regex",
    }
    unknown = set(predicate) - allowed
    if unknown:
        raise ContractError(
            "expectation-schema", f"{label} uses unknown fields: {sorted(unknown)}"
        )
    if "severity" in predicate and predicate["severity"] not in SEVERITIES:
        raise ContractError("expectation-schema", f"{label} severity is invalid")
    if "evidence" in predicate and predicate["evidence"] not in EVIDENCE:
        raise ContractError("expectation-schema", f"{label} evidence is invalid")
    for key in ("path_regex", "message_regex"):
        if key in predicate:
            if not isinstance(predicate[key], str) or not predicate[key]:
                raise ContractError("expectation-schema", f"{label}.{key} is invalid")
            predicate[key] = re.compile(predicate[key])
    if "line" in predicate and (
        not isinstance(predicate["line"], int) or predicate["line"] < 1
    ):
        raise ContractError("expectation-schema", f"{label}.line is invalid")
    return predicate


def _matches(predicate: dict[str, Any], finding: dict[str, Any]) -> bool:
    if "provider" in predicate and finding.get("provider") != predicate["provider"]:
        return False
    if "task_id" in predicate and finding.get("task_id") != predicate["task_id"]:
        return False
    if "rule_id" in predicate and finding.get("rule_id") != predicate["rule_id"]:
        return False
    if "severity" in predicate and finding.get("severity") != predicate["severity"]:
        return False
    if "evidence" in predicate and finding.get("evidence") != predicate["evidence"]:
        return False
    location = finding.get("primary_location") or {}
    if not isinstance(location, dict):
        return False
    path = location.get("path") or ""
    if "path" in predicate and path != predicate["path"]:
        return False
    if "path_regex" in predicate and not predicate["path_regex"].search(path):
        return False
    line = location.get("start_line")
    if "line" in predicate and line != predicate["line"]:
        return False
    if "line_min" in predicate and not (
        isinstance(line, int) and line >= predicate["line_min"]
    ):
        return False
    if "line_max" in predicate and not (
        isinstance(line, int) and line <= predicate["line_max"]
    ):
        return False
    if "message_regex" in predicate and not predicate["message_regex"].search(
        finding.get("message") or ""
    ):
        return False
    return True


def _describe(predicate: dict[str, Any]) -> str:
    parts = []
    for key in ("provider", "task_id", "rule_id", "severity", "path"):
        if key in predicate:
            parts.append(f"{key}={predicate[key]}")
    for key in ("path_regex", "message_regex"):
        if key in predicate:
            parts.append(f"{key}=/{predicate[key].pattern}/")
    if "line" in predicate:
        parts.append(f"line={predicate['line']}")
    return "{" + ", ".join(parts) + "}"


def evaluate_next_contract(
    result: dict[str, Any], expectation: dict[str, Any]
) -> ContractResult:
    """Match an ``ici.next.run`` document against a schema-3 expectation."""

    scenario_id = require_string(expectation.get("scenario_id"), "scenario_id")
    if expectation.get("schema") != 3:
        raise ContractError(
            "expectation-schema", f"{scenario_id} expectation schema must be 3"
        )
    expected = expectation.get("expected")
    if not isinstance(expected, dict) or not expected:
        raise ContractError(
            "expectation-schema", f"{scenario_id} needs a non-empty 'expected'"
        )
    allowed = {
        "gate",
        "exit_code",
        "findings",
        "forbidden_findings",
        "limitations_regex",
        "metrics",
    }
    unknown = set(expected) - allowed
    if unknown:
        raise ContractError(
            "expectation-schema", f"{scenario_id} expected has unknown fields: {sorted(unknown)}"
        )
    if expected.get("gate") not in GATES:
        raise ContractError(
            "expectation-schema", f"{scenario_id} expected.gate must be one of {sorted(GATES)}"
        )

    if result.get("schema_id") != "ici.next.run":
        raise ContractError(
            "result-schema",
            f"{scenario_id} result is not ici.next.run "
            f"(schema_id={result.get('schema_id')!r}) — the next corpus does not "
            "accept legacy findings/v3 documents",
        )

    failures: list[ContractFailure] = []
    gate = result.get("gate") or {}
    observed_gate = gate.get("selected")
    if observed_gate != expected["gate"]:
        failures.append(
            ContractFailure(
                SUITE_SCOPE,
                "gate",
                f"gate {observed_gate!r} != expected {expected['gate']!r} "
                f"(reasons: {gate.get('reasons')})",
            )
        )

    findings = [f for f in result.get("findings") or [] if isinstance(f, dict)]
    matched = 0
    for raw in expected.get("findings") or []:
        predicate = _predicate(raw, "findings[]")
        check = predicate.get("provider") or predicate.get("task_id") or SUITE_SCOPE
        if any(_matches(predicate, finding) for finding in findings):
            matched += 1
        else:
            failures.append(
                ContractFailure(
                    check,
                    "missing-finding",
                    f"no finding matches {_describe(predicate)}",
                )
            )
    for raw in expected.get("forbidden_findings") or []:
        predicate = _predicate(raw, "forbidden_findings[]")
        check = predicate.get("provider") or predicate.get("task_id") or SUITE_SCOPE
        hits = [f for f in findings if _matches(predicate, f)]
        for finding in hits:
            failures.append(
                ContractFailure(
                    check,
                    "forbidden-finding",
                    f"finding {_describe(predicate)} matched forbidden predicate "
                    f"({finding.get('message', '')[:120]})",
                )
            )

    limitations = [str(item) for item in result.get("limitations") or []]
    for pattern in expected.get("limitations_regex") or []:
        if not isinstance(pattern, str) or not pattern:
            raise ContractError(
                "expectation-schema", "limitations_regex entries must be non-empty strings"
            )
        regex = re.compile(pattern)
        if not any(regex.search(limitation) for limitation in limitations):
            failures.append(
                ContractFailure(
                    SUITE_SCOPE,
                    "missing-limitation",
                    f"no limitation matches /{pattern}/",
                )
            )

    metric_bounds = expected.get("metrics")
    if metric_bounds is not None:
        if not isinstance(metric_bounds, dict):
            raise ContractError("expectation-schema", "expected.metrics must be an object")
        observed = {
            m["name"]: m
            for m in result.get("metrics") or []
            if isinstance(m, dict) and isinstance(m.get("name"), str)
        }
        for name, bounds in metric_bounds.items():
            if not isinstance(name, str) or not isinstance(bounds, dict):
                raise ContractError(
                    "expectation-schema", "expected.metrics entries must be name: {bounds}"
                )
            unknown = set(bounds) - {"min", "max", "numerator_min"}
            if unknown:
                raise ContractError(
                    "expectation-schema",
                    f"metric {name} has unknown bounds: {sorted(unknown)}",
                )
            entry = observed.get(name)
            value = entry.get("value") if entry else None
            if not isinstance(value, (int, float)):
                failures.append(
                    ContractFailure(SUITE_SCOPE, "metric-absent", f"metric {name} not measured")
                )
                continue
            if "min" in bounds and value < bounds["min"]:
                failures.append(
                    ContractFailure(
                        SUITE_SCOPE,
                        "metric-floor",
                        f"{name}={value} < {bounds['min']}",
                    )
                )
            if "max" in bounds and value > bounds["max"]:
                failures.append(
                    ContractFailure(
                        SUITE_SCOPE,
                        "metric-ceiling",
                        f"{name}={value} > {bounds['max']}",
                    )
                )
            numerator = entry.get("numerator")
            if "numerator_min" in bounds and not (
                isinstance(numerator, (int, float)) and numerator >= bounds["numerator_min"]
            ):
                failures.append(
                    ContractFailure(
                        SUITE_SCOPE,
                        "metric-numerator",
                        f"{name} numerator {numerator} < {bounds['numerator_min']}",
                    )
                )

    producer = result.get("producer") or {}
    producer_version = str(producer.get("ici_version") or producer.get("version") or "")
    return ContractResult(
        scenario_id=scenario_id,
        observed_gate=str(observed_gate),
        producer_version=producer_version,
        matched_findings=matched,
        failures=tuple(failures),
    )
