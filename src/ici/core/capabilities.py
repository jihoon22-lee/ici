"""Immutable, policy-aware capability inventory shared across ici commands."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast

from ici.core.redaction import redact_data
from ici.core.toolchain import (
    DEFAULT_TOOL_PROBES,
    PROBE_OUTPUT_LIMIT,
    PROBE_TIMEOUT_SECONDS,
    ToolCapability,
    ToolProbe,
    collect_registered_capability,
)


@dataclass(frozen=True)
class ToolRequirement:
    """Policy sources that require or can optionally use one tool."""

    name: str
    required_by: tuple[str, ...] = ()
    optional_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool requirement name must not be empty")
        object.__setattr__(self, "required_by", tuple(sorted(set(self.required_by))))
        object.__setattr__(self, "optional_by", tuple(sorted(set(self.optional_by))))

    @property
    def required(self) -> bool:
        return bool(self.required_by)

    @property
    def optional(self) -> bool:
        return bool(self.optional_by) and not self.required


@dataclass(frozen=True)
class CapabilityInventory:
    """Immutable, policy-aware snapshot shared by diagnostics and verification."""

    capabilities: Mapping[str, ToolCapability] = field(default_factory=dict)
    requirements: Mapping[str, ToolRequirement] = field(default_factory=dict)

    def __post_init__(self) -> None:
        capabilities = dict(self.capabilities)
        requirements = dict(self.requirements)
        for name, capability in capabilities.items():
            if not name or capability.name != name:
                raise ValueError(f"capability key/name mismatch: {name!r}")
            requirements.setdefault(name, ToolRequirement(name=name))
        for name, requirement in requirements.items():
            if name not in capabilities:
                raise ValueError(f"requirement has no capability: {name}")
            if requirement.name != name:
                raise ValueError(f"requirement key/name mismatch: {name!r}")
        object.__setattr__(self, "capabilities", MappingProxyType(capabilities))
        object.__setattr__(self, "requirements", MappingProxyType(requirements))

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, requirement in self.requirements.items()
            if requirement.required and not self.capabilities[name].available
        )

    @property
    def incomplete_required(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, requirement in self.requirements.items()
            if requirement.required
            and self.capabilities[name].available
            and not self.capabilities[name].complete
        )

    @property
    def healthy(self) -> bool:
        return not self.missing_required and not self.incomplete_required


def _policy_sources(
    policy: Mapping[str, Iterable[str]] | None,
    name: str,
) -> tuple[str, ...]:
    if policy is None or name not in policy:
        return ()
    values = policy[name]
    if isinstance(values, str):
        values = (values,)
    return tuple(sorted({str(value) for value in values if str(value)}))


def collect_capability_inventory(
    cwd: Path | None = None,
    probes: tuple[ToolProbe, ...] = DEFAULT_TOOL_PROBES,
    required_by: Mapping[str, Iterable[str]] | None = None,
    optional_by: Mapping[str, Iterable[str]] | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    max_output_chars: int = PROBE_OUTPUT_LIMIT,
) -> CapabilityInventory:
    """Collect a deterministic tool snapshot and attach requirement provenance."""

    registry: dict[str, ToolProbe] = {}
    for probe in probes:
        if not probe.name:
            raise ValueError("capability probe name must not be empty")
        if probe.name in registry:
            raise ValueError(f"duplicate capability probe name: {probe.name}")
        registry[probe.name] = probe

    requested_names = set(registry)
    requested_names.update((required_by or {}).keys())
    requested_names.update((optional_by or {}).keys())
    ordered_names = [*registry, *sorted(requested_names.difference(registry))]

    capabilities: dict[str, ToolCapability] = {}
    requirements: dict[str, ToolRequirement] = {}
    for name in ordered_names:
        registered_probe = registry.get(name)
        if registered_probe is None:
            capability = ToolCapability(
                name=name,
                path="",
                available=False,
                complete=False,
                error="no registered capability probe",
            )
        else:
            capability, _results = collect_registered_capability(
                registered_probe,
                cwd=cwd,
                timeout=timeout,
                max_output_chars=max_output_chars,
            )
        capabilities[name] = capability
        requirements[name] = ToolRequirement(
            name=name,
            required_by=_policy_sources(required_by, name),
            optional_by=_policy_sources(optional_by, name),
        )
    return CapabilityInventory(capabilities=capabilities, requirements=requirements)


def _capability_state(capability: ToolCapability) -> str:
    if not capability.available:
        return "unavailable"
    if not capability.complete:
        return "incomplete"
    return "ready"


def serialize_capability_inventory(inventory: CapabilityInventory) -> dict[str, object]:
    """Return the stable, recursively-redacted JSON representation."""

    tools: list[dict[str, object]] = []
    for name, capability in inventory.capabilities.items():
        requirement = inventory.requirements[name]
        tools.append(
            {
                "name": capability.name,
                "state": _capability_state(capability),
                "available": capability.available,
                "complete": capability.complete,
                "required": requirement.required,
                "optional": requirement.optional,
                "required_by": list(requirement.required_by),
                "optional_by": list(requirement.optional_by),
                "path": capability.path,
                "version": capability.version,
                "version_tuple": list(capability.version_tuple),
                "error": capability.error,
                "details": dict(capability.details),
                "probe_argv": list(capability.probe_argv),
                "returncode": capability.returncode,
                "timed_out": capability.timed_out,
                "truncated": capability.truncated,
                "evidence": [
                    {
                        "purpose": item.purpose,
                        "argv": list(item.argv),
                        "returncode": item.returncode,
                        "timed_out": item.timed_out,
                        "truncated": item.truncated,
                    }
                    for item in capability.evidence
                ],
            }
        )

    ready = sum(tool["state"] == "ready" for tool in tools)
    incomplete = sum(tool["state"] == "incomplete" for tool in tools)
    unavailable = sum(tool["state"] == "unavailable" for tool in tools)
    data: dict[str, object] = {
        "schema_version": "ici.capabilities/v1",
        "status": "PASS" if inventory.healthy else "WARN",
        "healthy": inventory.healthy,
        "counts": {
            "total": len(tools),
            "ready": ready,
            "incomplete": incomplete,
            "unavailable": unavailable,
            "required": sum(bool(tool["required"]) for tool in tools),
            "optional": sum(bool(tool["optional"]) for tool in tools),
        },
        "missing_required": list(inventory.missing_required),
        "incomplete_required": list(inventory.incomplete_required),
        "tools": tools,
    }
    return cast(dict[str, object], redact_data(data))
