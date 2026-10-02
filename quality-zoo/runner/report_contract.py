"""Validate ici.result/v3 reports against stable known-answer expectations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runner.common import (
    WINDOWS_DRIVE_RE,
    ContractError,
    require_bool,
    require_int,
    require_string,
)

STATUSES = {"PASS", "WARN", "FAIL", "ERROR", "SKIP"}
EVIDENCE = {"MEASURED", "ESTIMATED", "NOT_RUN", "NOT_APPLICABLE"}
SEVERITIES = {"info", "low", "medium", "high", "critical"}
CONFIDENCES = {"exact", "high", "medium", "low"}
RULE_RE = re.compile(r"^ici\.[a-z0-9][a-z0-9.-]*$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


SUITE_SCOPE = ""


@dataclass(frozen=True)
class ContractFailure:
    """One contract violation, attributed to the engine that owns it.

    ``engine`` is ``SUITE_SCOPE`` for violations no single engine can be blamed
    for — a suite status, a producer version, a missing capability. Everything
    else names the engine so a failing run can be read as "which engine
    regressed" instead of a list of sentences.
    """

    engine: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"engine": self.engine, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class ContractResult:
    scenario_id: str
    verdict: str
    observed_suite_status: str
    producer_version: str
    matched_findings: int
    errors: tuple[str, ...]
    failures: tuple[ContractFailure, ...] = ()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("report-schema", f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError("report-schema", f"{label} must be an array")
    return value


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ContractError("report-schema", f"{label} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ContractError("report-schema", f"{label} must be >= {minimum}")
    return result


def _canonical_report_path(value: Any, label: str) -> str:
    path = require_string(value, label)
    if "\\" in path or WINDOWS_DRIVE_RE.match(path):
        raise ContractError("report-schema", f"{label} uses a backslash")
    parsed = Path(path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ContractError("report-schema", f"{label} is not project-relative")
    if parsed.as_posix() != path or path.startswith("./"):
        raise ContractError("report-schema", f"{label} is not canonical")
    return path


def _validate_location(value: Any, label: str) -> dict[str, Any]:
    location = _object(value, label)
    required = {
        "path",
        "start_line",
        "end_line",
        "start_column",
        "end_column",
        "label",
    }
    if not required <= set(location):
        raise ContractError("report-schema", f"{label} lacks required location fields")
    _canonical_report_path(location["path"], f"{label}.path")
    start_line = require_int(location["start_line"], f"{label}.start_line", minimum=1)
    for name in ("end_line", "start_column", "end_column"):
        item = location[name]
        if item is not None:
            require_int(item, f"{label}.{name}", minimum=1)
    if location["end_line"] is not None and location["end_line"] < start_line:
        raise ContractError("report-schema", f"{label}.end_line precedes start_line")
    require_string(location["label"], f"{label}.label", nonempty=False)
    return location


def _validate_finding(value: Any, label: str) -> dict[str, Any]:
    finding = _object(value, label)
    required = {
        "rule_id",
        "category",
        "severity",
        "confidence",
        "fingerprint",
        "primary_location",
        "related_locations",
        "message",
        "explanation",
        "remediation",
        "tool_rule_id",
        "tool_name",
        "tool_version",
        "suppression",
        "metrics",
        "snippet",
    }
    if not required <= set(finding):
        missing = sorted(required - set(finding))
        raise ContractError("report-schema", f"{label} lacks {missing!r}")
    if not RULE_RE.fullmatch(require_string(finding["rule_id"], f"{label}.rule_id")):
        raise ContractError("report-schema", f"{label}.rule_id is invalid")
    if require_string(finding["severity"], f"{label}.severity") not in SEVERITIES:
        raise ContractError("report-schema", f"{label}.severity is invalid")
    if require_string(finding["confidence"], f"{label}.confidence") not in CONFIDENCES:
        raise ContractError("report-schema", f"{label}.confidence is invalid")
    if not FINGERPRINT_RE.fullmatch(
        require_string(finding["fingerprint"], f"{label}.fingerprint")
    ):
        raise ContractError("report-schema", f"{label}.fingerprint is invalid")
    _validate_location(finding["primary_location"], f"{label}.primary_location")
    for index, related in enumerate(
        _array(finding["related_locations"], f"{label}.related")
    ):
        _validate_location(related, f"{label}.related_locations[{index}]")
    for name in (
        "message",
        "explanation",
        "remediation",
        "tool_rule_id",
        "tool_name",
        "tool_version",
        "snippet",
    ):
        require_string(finding[name], f"{label}.{name}", nonempty=False)
    suppression = _object(finding["suppression"], f"{label}.suppression")
    require_bool(suppression.get("suppressed"), f"{label}.suppression.suppressed")
    if suppression.get("kind") not in {"none", "inline", "config", "baseline"}:
        raise ContractError("report-schema", f"{label}.suppression.kind is invalid")
    require_string(
        suppression.get("reason"), f"{label}.suppression.reason", nonempty=False
    )
    metrics = _object(finding["metrics"], f"{label}.metrics")
    for name, raw_metric in metrics.items():
        require_string(name, f"{label}.metric name")
        metric = _object(raw_metric, f"{label}.metrics.{name}")
        _number(metric.get("value"), f"{label}.metrics.{name}.value")
        require_string(
            metric.get("unit"), f"{label}.metrics.{name}.unit", nonempty=False
        )
    return finding


def validate_report(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Perform dependency-free structural and relational v3 validation."""

    if payload.get("schema_version") != "ici.result/v3":
        raise ContractError("report-schema-version", "expected ici.result/v3 suite")
    status = require_string(payload.get("suite_status"), "suite_status")
    if status not in STATUSES:
        raise ContractError("report-schema", "suite_status is invalid")
    _number(payload.get("duration"), "duration", minimum=0)
    count_fields = (
        "passed_count",
        "warned_count",
        "failed_count",
        "error_count",
        "skipped_count",
        "total_count",
    )
    for name in count_fields:
        require_int(payload.get(name), name, minimum=0)
    results = _array(payload.get("results"), "results")
    if payload["total_count"] != len(results):
        raise ContractError("report-count", "total_count differs from results length")
    actual_counts = {
        "passed_count": sum(
            item.get("status") == "PASS" for item in results if isinstance(item, dict)
        ),
        "warned_count": sum(
            item.get("status") == "WARN" for item in results if isinstance(item, dict)
        ),
        "error_count": sum(
            item.get("status") == "ERROR" for item in results if isinstance(item, dict)
        ),
        "skipped_count": sum(
            item.get("status") == "SKIP" for item in results if isinstance(item, dict)
        ),
    }
    # ici retains historical FAIL+ERROR semantics in failed_count.
    actual_counts["failed_count"] = sum(
        item.get("status") in {"FAIL", "ERROR"}
        for item in results
        if isinstance(item, dict)
    )
    for name, actual in actual_counts.items():
        if payload[name] != actual:
            raise ContractError(
                "report-count", f"{name} is {payload[name]}, expected {actual}"
            )
    metadata = _object(payload.get("analysis_metadata"), "analysis_metadata")
    require_string(metadata.get("producer_version"), "producer_version")
    engines: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw_engine in enumerate(results):
        engine = _object(raw_engine, f"results[{index}]")
        if engine.get("schema_version") != "ici.result/v3":
            raise ContractError("report-schema", f"results[{index}] is not v3")
        name = require_string(
            engine.get("engine_name"), f"results[{index}].engine_name"
        )
        if name in seen_names:
            raise ContractError("report-schema", f"duplicate engine {name!r}")
        seen_names.add(name)
        if (
            engine.get("status") not in STATUSES
            or engine.get("evidence") not in EVIDENCE
        ):
            raise ContractError(
                "report-schema", f"engine {name} has invalid status/evidence"
            )
        require_bool(engine.get("required"), f"engine {name}.required")
        require_bool(engine.get("cache_hit"), f"engine {name}.cache_hit")
        findings = _array(engine.get("findings"), f"engine {name}.findings")
        for finding_index, finding in enumerate(findings):
            _validate_finding(finding, f"engine {name}.findings[{finding_index}]")
        engines.append(engine)
    return metadata, engines


def _matches_location(location: dict[str, Any], expected: dict[str, Any]) -> bool:
    if "path" in expected and location.get("path") != expected["path"]:
        return False
    line = location.get("start_line")
    if "line" in expected and line != expected["line"]:
        return False
    if "line_min" in expected and (
        not isinstance(line, int) or line < expected["line_min"]
    ):
        return False
    if "line_max" in expected and (
        not isinstance(line, int) or line > expected["line_max"]
    ):
        return False
    column = location.get("start_column")
    if "column_min" in expected and (
        not isinstance(column, int) or column < expected["column_min"]
    ):
        return False
    if "column_max" in expected and (
        not isinstance(column, int) or column > expected["column_max"]
    ):
        return False
    return "label" not in expected or location.get("label") == expected["label"]


def _matches_metric(metric: dict[str, Any], expected: dict[str, Any]) -> bool:
    value = metric.get("value")
    if "value" in expected and value != expected["value"]:
        return False
    if "minimum" in expected and (
        not isinstance(value, (int, float)) or value < expected["minimum"]
    ):
        return False
    if "maximum" in expected and (
        not isinstance(value, (int, float)) or value > expected["maximum"]
    ):
        return False
    return "unit" not in expected or metric.get("unit") == expected["unit"]


def _validate_location_predicate(value: Any, label: str) -> dict[str, Any]:
    predicate = _object(value, label)
    allowed = {
        "path",
        "line",
        "line_min",
        "line_max",
        "column_min",
        "column_max",
        "label",
    }
    if not set(predicate) <= allowed:
        raise ContractError(
            "expectation-schema",
            f"{label} has unknown keys {sorted(set(predicate) - allowed)!r}",
        )
    if "path" in predicate:
        _canonical_report_path(predicate["path"], f"{label}.path")
    if "label" in predicate:
        require_string(predicate["label"], f"{label}.label", nonempty=False)
    for name in ("line", "line_min", "line_max", "column_min", "column_max"):
        if name in predicate:
            require_int(predicate[name], f"{label}.{name}", minimum=1)
    if predicate.get("line_min", 1) > predicate.get("line_max", 2**31):
        raise ContractError("expectation-schema", f"{label} line range is reversed")
    if predicate.get("column_min", 1) > predicate.get("column_max", 2**31):
        raise ContractError("expectation-schema", f"{label} column range is reversed")
    return predicate


def _validate_finding_predicate(
    value: Any, label: str, *, allow_count: bool
) -> dict[str, Any]:
    predicate = _object(value, label)
    allowed = {
        "engine",
        "engine_status",
        "evidence",
        "rule_id",
        "tool_rule_id",
        "severity",
        "confidence",
        "category",
        "tool_name",
        "tool_version",
        "location",
        "message_contains",
        "snippet_contains",
        "suppressed",
        "metrics",
        "related_locations",
    }
    if allow_count:
        allowed.add("count")
    if not set(predicate) <= allowed:
        raise ContractError(
            "expectation-schema",
            f"{label} has unknown keys {sorted(set(predicate) - allowed)!r}",
        )
    for name in (
        "engine",
        "rule_id",
        "tool_rule_id",
        "category",
        "tool_name",
        "tool_version",
    ):
        if name in predicate:
            require_string(
                predicate[name], f"{label}.{name}", nonempty=name != "tool_rule_id"
            )
    if "engine_status" in predicate and predicate["engine_status"] not in STATUSES:
        raise ContractError("expectation-schema", f"{label}.engine_status is invalid")
    if "evidence" in predicate and predicate["evidence"] not in EVIDENCE:
        raise ContractError("expectation-schema", f"{label}.evidence is invalid")
    if "severity" in predicate and predicate["severity"] not in SEVERITIES:
        raise ContractError("expectation-schema", f"{label}.severity is invalid")
    if "confidence" in predicate and predicate["confidence"] not in CONFIDENCES:
        raise ContractError("expectation-schema", f"{label}.confidence is invalid")
    if "suppressed" in predicate:
        require_bool(predicate["suppressed"], f"{label}.suppressed")
    if "count" in predicate:
        require_int(predicate["count"], f"{label}.count", minimum=1)
    if "location" in predicate:
        _validate_location_predicate(predicate["location"], f"{label}.location")
    for name in ("message_contains", "snippet_contains"):
        if name in predicate:
            values = _array(predicate[name], f"{label}.{name}")
            for index, item in enumerate(values):
                require_string(item, f"{label}.{name}[{index}]")
    metrics = _object(predicate.get("metrics", {}), f"{label}.metrics")
    for name, raw_metric in metrics.items():
        require_string(name, f"{label}.metric name")
        metric = _object(raw_metric, f"{label}.metrics.{name}")
        if not set(metric) <= {"value", "minimum", "maximum", "unit"}:
            raise ContractError(
                "expectation-schema", f"{label}.metrics.{name} has unknown keys"
            )
        for field in ("value", "minimum", "maximum"):
            if field in metric:
                _number(metric[field], f"{label}.metrics.{name}.{field}")
        if "unit" in metric:
            require_string(
                metric["unit"], f"{label}.metrics.{name}.unit", nonempty=False
            )
    related = _array(
        predicate.get("related_locations", []), f"{label}.related_locations"
    )
    for index, item in enumerate(related):
        _validate_location_predicate(item, f"{label}.related_locations[{index}]")
    return predicate


def _validate_expectation(expectation: dict[str, Any]) -> None:
    allowed = {
        "schema",
        "scenario_id",
        "class",
        "project_root",
        "profile",
        "command",
        "suite_status",
        "producer_version",
        "engines",
        "findings",
        "forbidden_findings",
        "required_capabilities",
        "skip_policy",
    }
    if set(expectation) != allowed:
        raise ContractError(
            "expectation-schema",
            f"scenario fields differ: {sorted(set(expectation) ^ allowed)!r}",
        )
    if expectation.get("schema") != 1:
        raise ContractError("expectation-schema", "scenario schema must be 1")
    if expectation.get("suite_status") not in STATUSES:
        raise ContractError("expectation-schema", "expected suite status is invalid")
    require_string(expectation.get("producer_version"), "producer_version")
    if expectation.get("skip_policy") not in {"forbid", "allow-missing-capability"}:
        raise ContractError("expectation-schema", "skip_policy is invalid")
    capabilities = _array(
        expectation.get("required_capabilities"), "required_capabilities"
    )
    for index, capability in enumerate(capabilities):
        require_string(capability, f"required_capabilities[{index}]")
    engines = _array(expectation.get("engines"), "engines")
    for index, raw_engine in enumerate(engines):
        engine = _object(raw_engine, f"engines[{index}]")
        if not set(engine) <= {"name", "status", "evidence", "required", "extra"}:
            raise ContractError(
                "expectation-schema", f"engines[{index}] has unknown keys"
            )
        require_string(engine.get("name"), f"engines[{index}].name")
        if "status" in engine and engine["status"] not in STATUSES:
            raise ContractError(
                "expectation-schema", f"engines[{index}].status is invalid"
            )
        if "evidence" in engine and engine["evidence"] not in EVIDENCE:
            raise ContractError(
                "expectation-schema", f"engines[{index}].evidence is invalid"
            )
        if "required" in engine:
            require_bool(engine["required"], f"engines[{index}].required")
        if "extra" in engine:
            _object(engine["extra"], f"engines[{index}].extra")
    findings = _array(expectation.get("findings"), "findings")
    for index, item in enumerate(findings):
        _validate_finding_predicate(item, f"findings[{index}]", allow_count=True)
    forbidden = _array(expectation.get("forbidden_findings"), "forbidden_findings")
    for index, item in enumerate(forbidden):
        _validate_finding_predicate(
            item, f"forbidden_findings[{index}]", allow_count=False
        )


def _matches_finding(
    engine: dict[str, Any], finding: dict[str, Any], expected: dict[str, Any]
) -> bool:
    scalar_fields = (
        "rule_id",
        "tool_rule_id",
        "severity",
        "confidence",
        "category",
        "tool_name",
        "tool_version",
    )
    if "engine" in expected and engine.get("engine_name") != expected["engine"]:
        return False
    if (
        "engine_status" in expected
        and engine.get("status") != expected["engine_status"]
    ):
        return False
    if "evidence" in expected and engine.get("evidence") != expected["evidence"]:
        return False
    if any(
        field in expected and finding.get(field) != expected[field]
        for field in scalar_fields
    ):
        return False
    if "location" in expected and not _matches_location(
        finding["primary_location"], _object(expected["location"], "expected location")
    ):
        return False
    for needle in expected.get("message_contains", []):
        if not isinstance(needle, str) or needle not in finding.get("message", ""):
            return False
    for needle in expected.get("snippet_contains", []):
        if not isinstance(needle, str) or needle not in finding.get("snippet", ""):
            return False
    if (
        "suppressed" in expected
        and finding["suppression"].get("suppressed") is not expected["suppressed"]
    ):
        return False
    metrics = finding.get("metrics", {})
    for name, predicate in expected.get("metrics", {}).items():
        if name not in metrics or not _matches_metric(
            metrics[name], _object(predicate, "metric predicate")
        ):
            return False
    related = finding.get("related_locations", [])
    for predicate in expected.get("related_locations", []):
        expected_location = _object(predicate, "related location predicate")
        if not any(
            _matches_location(location, expected_location) for location in related
        ):
            return False
    return True


def _match_expected_findings(
    observed: list[tuple[dict[str, Any], dict[str, Any]]],
    expected: list[dict[str, Any]],
) -> tuple[int, list[ContractFailure]]:
    slots: list[tuple[int, dict[str, Any]]] = []
    for index, predicate in enumerate(expected):
        count = require_int(
            predicate.get("count", 1), f"findings[{index}].count", minimum=1
        )
        slots.extend((index, predicate) for _ in range(count))
    candidates = [
        [
            index
            for index, (engine, finding) in enumerate(observed)
            if _matches_finding(engine, finding, pred)
        ]
        for _, pred in slots
    ]
    matched_slot_by_finding: dict[int, int] = {}

    def assign(slot_index: int, visited: set[int]) -> bool:
        for finding_index in candidates[slot_index]:
            if finding_index in visited:
                continue
            visited.add(finding_index)
            previous = matched_slot_by_finding.get(finding_index)
            if previous is None or assign(previous, visited):
                matched_slot_by_finding[finding_index] = slot_index
                return True
        return False

    failures: list[ContractFailure] = []
    matched = 0
    for slot_index, (expectation_index, predicate) in enumerate(slots):
        if assign(slot_index, set()):
            matched += 1
        else:
            failures.append(
                ContractFailure(
                    engine=str(predicate.get("engine", SUITE_SCOPE)),
                    kind="finding-missing",
                    detail=f"expected finding #{expectation_index} was not matched",
                )
            )
    return matched, failures


def evaluate_contract(
    report: dict[str, Any], expectation: dict[str, Any]
) -> ContractResult:
    """Return PASS only when schema, statuses, findings and absences all match."""

    _validate_expectation(expectation)
    metadata, engines = validate_report(report)
    scenario_id = require_string(expectation.get("scenario_id"), "scenario_id")
    expected_status = require_string(expectation.get("suite_status"), "suite_status")
    failures: list[ContractFailure] = []
    if report["suite_status"] != expected_status:
        failures.append(
            ContractFailure(
                SUITE_SCOPE,
                "suite-status",
                f"suite status {report['suite_status']} != {expected_status}",
            )
        )
    expected_version = expectation.get("producer_version")
    producer_version = require_string(metadata["producer_version"], "producer_version")
    if expected_version is not None and producer_version != expected_version:
        failures.append(
            ContractFailure(
                SUITE_SCOPE,
                "producer-version",
                f"producer version {producer_version} != {expected_version}",
            )
        )
    required_capabilities = expectation["required_capabilities"]
    if required_capabilities:
        inventory = _object(report.get("capability_inventory"), "capability_inventory")
        tools = _array(inventory.get("tools"), "capability_inventory.tools")
        by_tool: dict[str, dict[str, Any]] = {}
        for item in tools:
            tool_payload = _object(item, "capability tool")
            name = require_string(tool_payload.get("name"), "capability name")
            if name in by_tool:
                raise ContractError("report-schema", f"duplicate capability {name!r}")
            by_tool[name] = tool_payload
        for capability in required_capabilities:
            tool = by_tool.get(capability)
            if tool is None:
                failures.append(
                    ContractFailure(
                        SUITE_SCOPE,
                        "capability-absent",
                        f"required capability {capability!r} is absent",
                    )
                )
            elif tool.get("state") != "ready" or tool.get("complete") is not True:
                failures.append(
                    ContractFailure(
                        SUITE_SCOPE,
                        "capability-incomplete",
                        f"required capability {capability!r} is not ready and complete",
                    )
                )
    by_name = {engine["engine_name"]: engine for engine in engines}
    for index, raw_expected in enumerate(expectation.get("engines", [])):
        expected_engine = _object(raw_expected, f"engines[{index}]")
        name = require_string(expected_engine.get("name"), f"engines[{index}].name")
        engine = by_name.get(name)
        if engine is None:
            failures.append(
                ContractFailure(name, "engine-absent", f"expected engine {name!r} is absent")
            )
            continue
        for field in ("status", "evidence", "required"):
            if field in expected_engine and engine.get(field) != expected_engine[field]:
                failures.append(
                    ContractFailure(
                        name,
                        f"engine-{field}",
                        f"engine {name} {field} {engine.get(field)!r} "
                        f"!= {expected_engine[field]!r}",
                    )
                )
        for key, value in expected_engine.get("extra", {}).items():
            if engine.get("extra", {}).get(key) != value:
                failures.append(
                    ContractFailure(
                        name, "engine-extra", f"engine {name} extra.{key} differs"
                    )
                )
    observed = [
        (engine, finding)
        for engine in engines
        for finding in engine.get("findings", [])
    ]
    expected_findings = [
        _object(item, f"findings[{index}]")
        for index, item in enumerate(expectation.get("findings", []))
    ]
    matched, finding_failures = _match_expected_findings(observed, expected_findings)
    failures.extend(finding_failures)
    for index, raw_forbidden in enumerate(expectation.get("forbidden_findings", [])):
        predicate = _object(raw_forbidden, f"forbidden_findings[{index}]")
        offenders = [
            finding
            for engine, finding in observed
            if finding["severity"] != "info"
            and not finding["suppression"]["suppressed"]
            and _matches_finding(engine, finding, predicate)
        ]
        if offenders:
            failures.append(
                ContractFailure(
                    engine=str(predicate.get("engine", SUITE_SCOPE)),
                    kind="finding-forbidden",
                    detail=f"forbidden finding #{index} matched {len(offenders)} result(s)",
                )
            )
    return ContractResult(
        scenario_id=scenario_id,
        verdict="PASS" if not failures else "FAIL",
        observed_suite_status=report["suite_status"],
        producer_version=producer_version,
        matched_findings=matched,
        # The strings stay the contract's message surface; ``failures`` adds the
        # engine attribution the one-screen summary groups by. Deriving one from
        # the other is what keeps them from drifting apart.
        errors=tuple(failure.detail for failure in failures),
        failures=tuple(failures),
    )
