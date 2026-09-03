"""Contract tests for sharing capability snapshots with verify reporters.

These tests intentionally describe the next capability-inventory boundary.  The
test doubles keep the contract deterministic and make it impossible for the
tests to accidentally probe the host toolchain.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from ici.core.capabilities import CapabilityInventory, ToolRequirement
from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineStatus,
    EngineSupport,
    EvidenceState,
    FindingConfidence,
    SupportLanguage,
    SupportMatrix,
    VerificationSuiteResult,
)
from ici.core.redaction import redact_suite
from ici.core.toolchain import ProbeEvidence, ToolCapability
from ici.engines.verify import VerifyOrchestrator
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import serialize_suite_result
from ici.reporters.markdown import generate_markdown_report


def _support_matrix() -> SupportMatrix:
    """Return active and inactive rows that exercise policy filtering."""

    return SupportMatrix(
        project_languages=[SupportLanguage.PYTHON, SupportLanguage.CPP],
        project_frameworks=["qt"],
        entries=[
            EngineSupport(
                engine_name="lint",
                language=SupportLanguage.PYTHON,
                mode=AnalysisMode.TOOL_BACKED,
                active_mode=AnalysisMode.TOOL_BACKED,
                applicable=True,
                enabled=True,
                evidence=EvidenceState.MEASURED,
                confidence=FindingConfidence.HIGH,
                required_tools=["compiler"],
                optional_tools=["shared-tool", "optional-only"],
            ),
            EngineSupport(
                engine_name="test",
                language=SupportLanguage.CPP,
                mode=AnalysisMode.TOOL_BACKED,
                active_mode=AnalysisMode.TOOL_BACKED,
                applicable=True,
                enabled=True,
                evidence=EvidenceState.MEASURED,
                confidence=FindingConfidence.HIGH,
                required_tools=["compiler"],
                optional_tools=["shared-tool"],
            ),
            EngineSupport(
                engine_name="type",
                language=SupportLanguage.PYTHON,
                mode=AnalysisMode.TOOL_BACKED,
                active_mode=None,
                applicable=True,
                enabled=False,
                evidence=EvidenceState.NOT_RUN,
                confidence=FindingConfidence.LOW,
                required_tools=["inactive-required"],
                optional_tools=["inactive-optional"],
            ),
            EngineSupport(
                engine_name="dead",
                language=SupportLanguage.CPP,
                mode=AnalysisMode.UNSUPPORTED,
                active_mode=None,
                applicable=False,
                enabled=True,
                evidence=EvidenceState.NOT_APPLICABLE,
                confidence=FindingConfidence.LOW,
                required_tools=["inapplicable-required"],
                optional_tools=["inapplicable-optional"],
            ),
        ],
    )


def test_derive_tool_policy_filters_scope_and_required_wins():
    """Only active support rows contribute, and required provenance dominates."""

    from ici.core.capabilities import derive_tool_policy

    required_by, optional_by = derive_tool_policy(
        _support_matrix(),
        configured_required={"doctor-only", "shared-tool"},
    )

    assert required_by == {
        "compiler": {"lint:python", "test:cpp"},
        "doctor-only": {"doctor.config"},
        "shared-tool": {"doctor.config", "lint:python", "test:cpp"},
    }
    assert optional_by == {"optional-only": {"lint:python"}}
    assert list(required_by) == sorted(required_by)
    assert list(optional_by) == sorted(optional_by)

    # Repeated derivation must not depend on support-row or set iteration order.
    assert derive_tool_policy(
        _support_matrix(), configured_required={"shared-tool", "doctor-only"}
    ) == (required_by, optional_by)


def _inventory(*, path: str = "/usr/bin/ici-tool") -> CapabilityInventory:
    capability = ToolCapability(
        name="compiler",
        path=path,
        available=True,
        version="compiler 14.2.0",
        version_tuple=(14, 2, 0),
        details={"target_triple": "x86_64-linux-gnu"},
    )
    return CapabilityInventory(
        capabilities={"compiler": capability},
        requirements={
            "compiler": ToolRequirement(
                name="compiler", required_by=("lint:python",), optional_by=()
            )
        },
    )


def _line_only_config() -> dict[str, Any]:
    names = (
        "line",
        "lint",
        "test",
        "type",
        "complexity",
        "sanitize",
        "thread_sanitize",
        "dead",
        "dup",
        "exception",
        "cognitive",
        "security",
        "cycle",
        "resource",
    )
    return {
        "project": {"source_dirs": ["src"]},
        "doctor": {"required_tools": ["compiler"]},
        "engines": {name: {"enabled": name == "line"} for name in names},
    }


def test_verify_collects_one_snapshot_before_engines_and_attaches_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The suite owns one pre-engine snapshot; engines never trigger probes."""

    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("value = 1\n", encoding="utf-8")
    inventory = _inventory()
    events: list[tuple[str, Any]] = []

    def fake_policy(matrix, configured_required):
        events.append(("policy", (matrix, tuple(sorted(configured_required)))))
        return ({"compiler": {"doctor.config"}}, {})

    def fake_collect(*args, **kwargs):
        events.append(("collect", (args, kwargs)))
        assert kwargs["required_by"] == {"compiler": {"doctor.config"}}
        assert kwargs["optional_by"] == {}
        return inventory

    class PassingLine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            events.append(("engine", "line"))
            return EngineResult("line", EngineStatus.PASS, "ok")

    monkeypatch.setattr("ici.engines.verify.derive_tool_policy", fake_policy)
    monkeypatch.setattr("ici.engines.verify.collect_capability_inventory", fake_collect)
    monkeypatch.setattr("ici.engines.verify.LineCountEngine", PassingLine)
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda *args, **kwargs: None)

    suite = VerifyOrchestrator(tmp_path, _line_only_config()).run_all()

    assert [kind for kind, _payload in events] == ["policy", "collect", "engine"]
    assert sum(kind == "collect" for kind, _payload in events) == 1
    assert suite.capability_inventory is inventory


def test_suite_serializer_uses_attached_snapshot_without_reprobing(
    monkeypatch: pytest.MonkeyPatch,
):
    inventory = _inventory()
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
        capability_inventory=inventory,
    )

    def fail_probe(*args, **kwargs):
        pytest.fail(f"report serialization must not probe tools: {args!r} {kwargs!r}")

    monkeypatch.setattr("ici.core.capabilities.collect_capability_inventory", fail_probe)

    payload = serialize_suite_result(suite)

    assert payload["capability_inventory"]["tools"][0]["name"] == "compiler"
    assert payload["capability_inventory"]["tools"][0]["path"] == "/usr/bin/ici-tool"


def test_suite_json_keeps_capabilities_at_root_and_stable_tool_details():
    inventory = CapabilityInventory(
        capabilities={
            "gcc": ToolCapability(
                name="gcc",
                path="/usr/bin/gcc",
                available=True,
                version="gcc 14.2.0",
                version_tuple=(14, 2, 0),
                details={"target_triple": "x86_64-linux-gnu"},
            ),
            "qmake": ToolCapability(
                name="qmake",
                path="/usr/bin/qmake6",
                available=True,
                version="QMake version 6.8.0",
                version_tuple=(6, 8, 0),
                details={"qt_major": "6", "generator": "linux-g++"},
            ),
            "cmake": ToolCapability(
                name="cmake",
                path="/usr/bin/cmake",
                available=True,
                version="cmake version 3.31.0",
                version_tuple=(3, 31, 0),
                details={"generators": "Ninja, Unix Makefiles"},
            ),
        }
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
        capability_inventory=inventory,
    )

    payload = serialize_suite_result(suite)
    serialized = payload["capability_inventory"]

    assert serialized["tools"][0]["name"] == "gcc"
    rows = {row["name"]: row for row in serialized["tools"]}
    assert rows["gcc"]["details"]["target_triple"] == "x86_64-linux-gnu"
    assert rows["qmake"]["details"] == {"qt_major": "6", "generator": "linux-g++"}
    assert rows["cmake"]["details"]["generators"] == "Ninja, Unix Makefiles"
    assert sum("capability_inventory" in node for node in _walk_dicts(payload)) == 1
    assert all("capability_inventory" not in result for result in payload["results"])


def test_suite_redaction_masks_capability_metadata_and_evidence_without_losing_shape():
    capability = ToolCapability(
        name="credential-tool",
        path="/srv/api_key=path-only-secret/bin/tool",
        available=True,
        version="tool password=version-only-secret",
        details={"config": "secret=detail-only-secret"},
        error="probe token=error-only-secret",
        probe_argv=("/srv/tool", "--token", "argv-only-secret"),
        evidence=(
            ProbeEvidence(
                purpose="version",
                argv=("/srv/tool", "--api-key", "evidence-only-secret"),
                returncode=0,
            ),
        ),
    )
    inventory = CapabilityInventory(capabilities={"credential-tool": capability})
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
        capability_inventory=inventory,
    )

    redacted = redact_suite(suite)
    payload = serialize_suite_result(redacted)
    encoded = json.dumps(payload, ensure_ascii=False)

    for secret in (
        "path-only-secret",
        "version-only-secret",
        "detail-only-secret",
        "error-only-secret",
        "argv-only-secret",
        "evidence-only-secret",
    ):
        assert secret not in encoded
    row = payload["capability_inventory"]["tools"][0]
    assert set(row) >= {
        "path",
        "version",
        "error",
        "details",
        "probe_argv",
        "evidence",
    }
    assert row["probe_argv"][-1] == "***REDACTED***"
    assert row["evidence"][0]["argv"][-1] == "***REDACTED***"
    assert len(row["evidence"]) == 1
    assert len(row["details"]) == 1


def test_suite_without_inventory_remains_serializable_and_backward_compatible():
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
    )

    redacted = redact_suite(suite)
    payload = serialize_suite_result(suite)

    assert redacted.capability_inventory is None
    assert payload.get("capability_inventory") is None
    assert payload["schema_version"] == "ici.result/v3"
    assert payload["results"][0]["engine_name"] == "line"


def test_human_reporters_project_the_attached_snapshot_without_reprobing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    inventory = _inventory()
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
        capability_inventory=inventory,
    )

    def fail_probe(*args, **kwargs):
        pytest.fail(f"human reporters must not probe tools: {args!r} {kwargs!r}")

    monkeypatch.setattr("ici.core.capabilities.collect_capability_inventory", fail_probe)

    terminal = StringIO()
    print_suite_dashboard(
        suite,
        tmp_path,
        output_console=Console(file=terminal, color_system=None, width=120),
    )
    markdown = generate_markdown_report(suite)
    html_path = tmp_path / "capabilities.html"
    generate_html_report(suite, html_path, base_dir=tmp_path)
    html = html_path.read_text(encoding="utf-8")

    assert "Tool Capability Snapshot" in terminal.getvalue()
    assert "compiler=ready" in terminal.getvalue()
    assert "Tool capability snapshot" in markdown
    assert "Complete tool inventory (1)" in markdown
    assert "compiler 14.2.0" in markdown
    assert "Tool capability snapshot" in html
    assert "/usr/bin/ici-tool" in html
    assert "target_triple=x86_64-linux-gnu" in html
    assert 'id="tab-support"' in html


def test_checked_in_v3_schema_declares_optional_capability_inventory():
    schema_path = (
        Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    suite = schema["$defs"]["suite"]
    assert suite["properties"]["capability_inventory"] == {
        "$ref": "#/$defs/nullableCapabilityInventory"
    }
    assert "capability_inventory" not in suite["required"]
    assert set(schema["$defs"]["capabilityTool"]["required"]) >= {
        "name",
        "state",
        "required_by",
        "details",
        "probe_argv",
        "evidence",
    }


def _walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)
