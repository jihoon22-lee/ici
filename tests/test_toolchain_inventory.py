"""Contract tests for the policy-aware shared capability inventory."""

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from ici.core import toolchain
from ici.core.runner import ProcessResult


def _result(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.01,
        timed_out=timed_out,
        truncated=truncated,
    )


def _fake_tools(monkeypatch, outputs=None, available=None):
    """Install deterministic ``which`` and process implementations."""

    outputs = outputs or {}
    available = available or {"tool": "/fake/tool"}
    which_calls = []
    run_calls = []

    def fake_which(command):
        which_calls.append(command)
        if command in available:
            return available[command]
        if command in available.values():
            return command
        return None

    def fake_run(argv, cwd=None, timeout=0, max_output_chars=0):
        del cwd, timeout, max_output_chars
        safe_key = tuple(argv)
        run_calls.append(safe_key)
        if safe_key in outputs:
            return outputs[safe_key]
        return _result(stdout=f"{Path(argv[0]).name} 1.2.3\n")

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)
    return which_calls, run_calls


def test_inventory_preserves_deterministic_registry_order_and_rejects_duplicate_names(
    tmp_path: Path, monkeypatch
):
    probes = (
        toolchain.ToolProbe("zeta", ("zeta",), ("--version",)),
        toolchain.ToolProbe("alpha", ("alpha",), ("--version",)),
    )
    _which_calls, run_calls = _fake_tools(
        monkeypatch,
        available={"zeta": "/fake/zeta", "alpha": "/fake/alpha"},
    )

    first = toolchain.collect_capability_inventory(tmp_path, probes=probes)
    first_payload = toolchain.serialize_capability_inventory(first)
    first_runs = list(run_calls)
    run_calls.clear()
    second = toolchain.collect_capability_inventory(tmp_path, probes=probes)

    assert tuple(first.capabilities) == ("zeta", "alpha")
    assert tuple(first.requirements) == ("zeta", "alpha")
    assert toolchain.serialize_capability_inventory(second) == first_payload
    assert run_calls == first_runs

    duplicate = toolchain.ToolProbe("zeta", ("other",), ("--version",))
    with pytest.raises(ValueError, match="duplicate capability probe name"):
        toolchain.collect_capability_inventory(tmp_path, probes=(*probes, duplicate))


def test_unknown_required_tool_is_explicit_missing_and_never_executed(tmp_path: Path, monkeypatch):
    which_calls = []
    run_calls = []

    def fail_which(command):
        which_calls.append(command)
        pytest.fail(f"unknown tools must not be resolved: {command!r}")

    def fail_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        pytest.fail(f"unknown tools must not execute: {args!r} {kwargs!r}")

    monkeypatch.setattr(toolchain.shutil, "which", fail_which)
    monkeypatch.setattr(toolchain, "run_process", fail_run)

    inventory = toolchain.collect_capability_inventory(
        tmp_path,
        probes=(),
        required_by={"missing-tool": ("test:python",)},
    )

    capability = inventory.capabilities["missing-tool"]
    assert capability.available is False
    assert capability.complete is False
    assert capability.path == ""
    assert capability.error
    assert capability.evidence == ()
    assert inventory.missing_required == ("missing-tool",)
    assert inventory.incomplete_required == ()
    assert inventory.healthy is False
    assert which_calls == []
    assert run_calls == []


def test_required_incomplete_capability_makes_inventory_unhealthy(tmp_path: Path, monkeypatch):
    probe = toolchain.ToolProbe("incomplete", ("incomplete",), ("--version",))
    _fake_tools(
        monkeypatch,
        available={"incomplete": "/fake/incomplete"},
        outputs={("/fake/incomplete", "--version"): _result(stdout="installed but opaque\n")},
    )

    inventory = toolchain.collect_capability_inventory(
        tmp_path,
        probes=(probe,),
        required_by={"incomplete": ("type:python",)},
    )

    capability = inventory.capabilities["incomplete"]
    assert capability.available is True
    assert capability.complete is False
    assert capability.error
    assert inventory.missing_required == ()
    assert inventory.incomplete_required == ("incomplete",)
    assert inventory.healthy is False


def test_optional_missing_capability_does_not_change_health(tmp_path: Path, monkeypatch):
    which_calls, run_calls = _fake_tools(monkeypatch, available={})

    inventory = toolchain.collect_capability_inventory(
        tmp_path,
        probes=(),
        optional_by={"optional-tool": ("lint:python",)},
    )

    capability = inventory.capabilities["optional-tool"]
    requirement = inventory.requirements["optional-tool"]
    assert capability.available is False
    assert requirement.required is False
    assert requirement.optional is True
    assert inventory.missing_required == ()
    assert inventory.incomplete_required == ()
    assert inventory.healthy is True
    assert which_calls == []
    assert run_calls == []


def test_inventory_and_nested_capability_metadata_are_mapping_proxy_immutable():
    capability = toolchain.ToolCapability(
        name="tool", path="/fake/tool", available=True, details={"vendor": "test"}
    )
    requirement = toolchain.ToolRequirement(name="tool", required_by=("engine",))
    inventory = toolchain.CapabilityInventory(
        capabilities={"tool": capability}, requirements={"tool": requirement}
    )

    assert isinstance(inventory.capabilities, MappingProxyType)
    assert isinstance(inventory.requirements, MappingProxyType)
    assert isinstance(inventory.capabilities["tool"].details, MappingProxyType)
    with pytest.raises(TypeError):
        inventory.capabilities["other"] = capability
    with pytest.raises(TypeError):
        inventory.requirements["other"] = requirement
    with pytest.raises(TypeError):
        inventory.capabilities["tool"].details["vendor"] = "changed"


def test_inventory_serialization_is_json_roundtrippable_and_redacts_evidence_argv(
    tmp_path: Path, monkeypatch
):
    probe = toolchain.ToolProbe(
        "secret-tool",
        ("secret-tool",),
        ("--token", "super-secret"),
    )
    _fake_tools(monkeypatch, available={"secret-tool": "/fake/secret-tool"})

    inventory = toolchain.collect_capability_inventory(
        tmp_path,
        probes=(probe,),
        required_by={"secret-tool": ("security:python", "security:python")},
    )
    payload = toolchain.serialize_capability_inventory(inventory)
    row = payload["tools"][0]

    assert json.loads(json.dumps(payload)) == payload
    assert payload["schema_version"] == "ici.capabilities/v1"
    assert payload["status"] == "PASS"
    assert payload["healthy"] is True
    assert row["required"] is True
    assert row["required_by"] == ["security:python"]
    assert row["probe_argv"] == ["/fake/secret-tool", "--token", "***REDACTED***"]
    assert row["evidence"][0]["argv"] == row["probe_argv"]
    assert "super-secret" not in json.dumps(payload)
