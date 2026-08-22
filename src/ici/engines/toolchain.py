"""Toolchain engine — records and validates the CI tool environment."""

import time

from ici.core.env import get_system_info
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.toolchain import DEFAULT_PROBES, ToolCapability, collect_tool_capability
from ici.engines.base import BaseEngine


def _capability_target(cap: ToolCapability, required: bool) -> InspectionTarget:
    if cap.available:
        return InspectionTarget(
            file_path="",
            start_line=1,
            target_name=f"Toolchain:{cap.name}",
            status=EngineStatus.PASS,
            message=f"{cap.name} {cap.version} at {cap.path}",
            metrics={"version": cap.version, "path": cap.path},
        )
    if required:
        return InspectionTarget(
            file_path="",
            start_line=1,
            target_name=f"Toolchain:{cap.name}",
            status=EngineStatus.ERROR,
            message=f"Required tool '{cap.name}' is not available on PATH",
        )
    return InspectionTarget(
        file_path="",
        start_line=1,
        target_name=f"Toolchain:{cap.name}",
        status=EngineStatus.WARN,
        message=f"Optional tool '{cap.name}' is not installed",
    )


def _to_evidence(cap: ToolCapability, result) -> ToolEvidence:
    return ToolEvidence(
        name=cap.name,
        path=cap.path,
        version=cap.version,
        argv=[cap.name],
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=cap.error,
    )


def _build_summary(capabilities: list[dict], required_tools: set[str]) -> str:
    missing_required = [
        c["name"] for c in capabilities if not c["available"] and c["name"] in required_tools
    ]
    if missing_required:
        return f"Required tools missing: {', '.join(missing_required)}"
    available = sum(1 for c in capabilities if c["available"])
    return f"Toolchain OK — {available}/{len(capabilities)} tool(s) detected"


class ToolchainEngine(BaseEngine):
    """Probes build tools and enforces the configured required-tool policy."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("toolchain")
        mode = cfg.get("mode", "pass_warn_fail")
        required = bool(cfg.get("required", False))
        required_tools = set(cfg.get("required_tools", []) or [])
        probe_names = cfg.get("tools") or sorted(DEFAULT_PROBES)

        targets: list[InspectionTarget] = []
        evidence: list[ToolEvidence] = []
        capabilities: list[dict] = []
        has_error = False

        for name in probe_names:
            probe = DEFAULT_PROBES.get(name)
            if probe is None:
                continue
            cap, result = collect_tool_capability(name, probe, cwd=self.project_root)
            capabilities.append(_capability_record(cap))
            if result is not None:
                evidence.append(_to_evidence(cap, result))
            targets.append(_capability_target(cap, name in required_tools))
            has_error = has_error or (not cap.available and name in required_tools)

        status = (
            EngineStatus.ERROR
            if has_error
            else self.evaluate_status(False, _has_warnings(targets), mode)
        )
        return self.create_result(
            name="toolchain",
            status=status,
            summary=_build_summary(capabilities, required_tools),
            duration=time.time() - t0,
            targets=targets,
            extra={"capabilities": capabilities, "environment": get_system_info()},
            required=required,
            evidence=EvidenceState.MEASURED if capabilities else EvidenceState.NOT_RUN,
            tool_evidence=evidence,
        )


def _has_warnings(targets: list[InspectionTarget]) -> bool:
    return any(target.status == EngineStatus.WARN for target in targets)


def _capability_record(cap: ToolCapability) -> dict:
    return {
        "name": cap.name,
        "path": cap.path,
        "available": cap.available,
        "version": cap.version,
        "error": cap.error,
    }
