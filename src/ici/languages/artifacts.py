"""The build's declared output contract, checked against the filesystem.

``[builds.<id>] artifacts = [...]`` is a claim: *this build leaves these
files under its directory*. The stable ``build`` engine folded that check
into the build run itself; here it is separated (#220) — ici never builds,
so the contract is verified by reading the tree the user's own build left.

Three outcomes, kept distinct because they mean different things:

- A glob that matches at least one regular file inside the workspace is a
  kept promise.
- A glob that matches nothing — or only directories — is a broken claim:
  a MEASURED finding, because the filesystem was asked and answered.
- A match that resolves outside the workspace (a symlink, typically) is
  not a produced output: it is reported invalid, never counted — 잘못된
  파일을 성공 산출물로 채택하지 않는다.

Per-file identity (a digest manifest a downstream check such as
integration could consume) is a separate slice — Observation carries
findings, measurements and limitations, and a verified file list is none
of those. This check answers the contract question: does every declared
output exist where the build said it would.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.domain.workspace import BuildUnit

__all__ = ["PROVIDER_NAME", "ArtifactRequest", "verify_artifacts"]

PROVIDER_NAME = "ici.artifacts"


@dataclass(frozen=True)
class ArtifactRequest:
    """The builds whose declared artifact globs this task verifies."""

    project_root: Path
    builds: tuple[BuildUnit, ...]
    task_id: str
    component_id: str = ""


def verify_artifacts(request: ArtifactRequest) -> Observation:
    """Resolve each declared glob under its build's directory."""

    root = request.project_root.resolve()
    findings: list[Finding] = []
    limitations: list[str] = []
    declared = 0
    satisfied = 0
    produced = 0
    for build in request.builds:
        base = root / build.directory
        if not base.is_dir():
            for pattern in build.artifacts:
                declared += 1
                findings.append(_missing(request, build, pattern, "the build directory is absent"))
            continue
        for pattern in build.artifacts:
            declared += 1
            files = 0
            directories = 0
            for candidate in sorted(base.glob(pattern)):
                try:
                    resolved = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue  # vanished mid-run — counts as no match
                try:
                    resolved.relative_to(root)
                except ValueError:
                    findings.append(_invalid(request, build, candidate, root))
                    continue
                if not resolved.is_file():
                    directories += 1
                    continue
                files += 1
            if not files:
                detail = (
                    f"only {directories} non-file match(es)"
                    if directories
                    else "no match under the build directory"
                )
                findings.append(_missing(request, build, pattern, detail))
                continue
            satisfied += 1
            produced += files
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(
            Measurement(
                name="artifacts.satisfied",
                value=satisfied,
                unit="globs",
                numerator=satisfied,
                denominator=declared or None,
            ),
            Measurement(
                name="artifacts.produced",
                value=produced,
                unit="files",
            ),
        ),
        limitations=tuple(limitations),
    )


def _missing(request: ArtifactRequest, build: BuildUnit, pattern: str, detail: str) -> Finding:
    """A declared output the build did not leave — a broken contract."""

    return Finding(
        fingerprint=_fingerprint("missing", build.id, pattern),
        rule_id="artifact.missing",
        message=(f"declared artifact {pattern!r} of build '{build.id}' produced nothing: {detail}"),
        severity="high",
        confidence="high",
        primary_location=SourceSpan(path=build.directory, start_line=1),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )


def _invalid(request: ArtifactRequest, build: BuildUnit, candidate: Path, root: Path) -> Finding:
    """A declared match that resolves outside the workspace."""

    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        relative = candidate.name
    return Finding(
        fingerprint=_fingerprint("invalid", build.id, relative),
        rule_id="artifact.invalid",
        message=(
            f"declared artifact {relative} of build '{build.id}' resolves "
            "outside the workspace — it is not an artifact this run produced"
        ),
        severity="high",
        confidence="high",
        primary_location=SourceSpan(path=build.directory, start_line=1),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )


def _fingerprint(kind: str, build_id: str, pattern: str) -> str:
    digest = hashlib.sha1(
        f"{kind}:{build_id}:{pattern}".encode(), usedforsecurity=False
    ).hexdigest()[:16]
    return f"artifact-{digest}"
