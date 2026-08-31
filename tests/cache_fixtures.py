"""Shared builders for deterministic analysis-cache tests."""

from __future__ import annotations

from pathlib import Path

from ici.core.cache import (
    AnalysisCache,
    AnalysisCacheKey,
    build_analysis_cache_key,
)
from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
    ProjectModel,
    canonical_digest,
)
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    SourceLocation,
    SuppressionKind,
    ToolEvidence,
)
from ici.core.pipeline import EngineDescriptor, EngineExecution

_SOURCE_DIGEST = "sha256:" + "a" * 64
_CONFIG_DIGEST = "sha256:" + "b" * 64
_TOOLCHAIN_DIGEST = "sha256:" + "c" * 64
_OTHER_DIGEST = "sha256:" + "d" * 64


class _ImplementationOne:
    """First implementation used to exercise implementation identity."""

    def execute(self) -> str:
        return "one"


class _ImplementationTwo:
    """Second implementation with different source."""

    def execute(self) -> str:
        return "two"


def _context(
    root: Path,
    *,
    config_digest: str = _CONFIG_DIGEST,
    toolchain_digest: str = _TOOLCHAIN_DIGEST,
) -> AnalysisContext:
    project = ProjectModel(
        root=root,
        name="cache-project",
        version="1.0.0",
        project_type="python",
        python_sources=("src/app.py",),
    )
    return AnalysisContext(
        project=project,
        capabilities=CapabilityInventory(),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=config_digest,
            toolchain_digest=toolchain_digest,
        ),
    )


def _descriptor(
    *,
    name: str = "line",
    variant: BuildVariant | None = None,
) -> EngineDescriptor:
    return EngineDescriptor(
        name=name,
        factory_name=f"{name.title()}Engine",
        produces=(f"findings:{name}",),
        execution=EngineExecution.BUILD if variant is not None else EngineExecution.READ_ONLY,
        build_variant=variant,
    )


def _key(
    context: AnalysisContext,
    *,
    descriptor: EngineDescriptor | None = None,
    source_digest: str = _SOURCE_DIGEST,
    implementation: object | type[object] | None = None,
) -> AnalysisCacheKey:
    return build_analysis_cache_key(
        descriptor or _descriptor(),
        context,
        source_digest,
        implementation=implementation,
    )


def _result(
    *,
    engine_name: str = "line",
    status: EngineStatus = EngineStatus.PASS,
    evidence: EvidenceState = EvidenceState.MEASURED,
    tool_evidence: tuple[ToolEvidence, ...] = (),
    artifact_manifests: tuple[ArtifactManifest, ...] = (),
    findings: tuple[Finding, ...] = (),
    summary: str = "analysis completed",
) -> EngineResult:
    return EngineResult(
        engine_name=engine_name,
        status=status,
        summary=summary,
        score=0.9,
        max_score=1.0,
        duration=0.25,
        raw_output="analysis output",
        extra={"nested": {"stable": True}, "count": 2},
        required=True,
        evidence=evidence,
        tool_evidence=list(tool_evidence),
        findings=list(findings),
        artifact_manifests=artifact_manifests,
    )


def _entry_path(cache: AnalysisCache, key: AnalysisCacheKey) -> Path:
    return cache.entries_dir / f"{key.digest.removeprefix('sha256:')}.json"


def _roundtrip_finding() -> Finding:
    location = SourceLocation(path="src/app.py", start_line=2, end_line=2, label="check")
    rule_id = "ici.cache.roundtrip"
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        fingerprint=finding_fingerprint(rule_id, location, symbol=location.label),
        primary_location=location,
        related_locations=[],
        message="cache finding",
        explanation="cache roundtrip explanation",
        remediation="cache roundtrip remediation",
        tool_rule_id="tool-rule",
        tool_name="checker",
        tool_version="1.2",
        suppression=FindingSuppression(
            suppressed=False,
            kind=SuppressionKind.NONE,
            reason="",
        ),
        metrics={"count": FindingMetric(value=1.0, unit="items")},
        snippet="value = 1",
    )


def _artifact_manifest(root: Path, identity: AnalysisIdentity) -> tuple[ArtifactManifest, Path]:
    shadow = root / "build"
    shadow.mkdir(parents=True, exist_ok=True)
    output = shadow / "app"
    output.write_bytes(b"artifact-v1")
    manifest = ArtifactManifest.create(
        project_root=root,
        shadow_root=shadow,
        variant=BuildVariant.RELEASE,
        identity=identity,
        paths=[(Path("app"), ArtifactScope.SHADOW, "binary")],
        producer="cache-test",
    )
    return manifest, output


def _snapshot_files(root: Path) -> dict[str, tuple[int, int, bytes]]:
    snapshot: dict[str, tuple[int, int, bytes]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        details = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            details.st_mode,
            details.st_mtime_ns,
            path.read_bytes(),
        )
    return snapshot


__all__ = [
    "_CONFIG_DIGEST",
    "_OTHER_DIGEST",
    "_SOURCE_DIGEST",
    "_TOOLCHAIN_DIGEST",
    "_ImplementationOne",
    "_ImplementationTwo",
    "_artifact_manifest",
    "_context",
    "_descriptor",
    "_entry_path",
    "_key",
    "_result",
    "_roundtrip_finding",
    "_snapshot_files",
    "canonical_digest",
]
