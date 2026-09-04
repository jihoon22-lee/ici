"""ELF dependency, loader-path, and ABI-floor verification engine."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ici.core.context import ArtifactRecord, ArtifactScope
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
    ToolEvidence,
)
from ici.core.runner import run_process
from ici.engines._elf import ElfFacts, ElfParseError, maximum_version, parse_readelf, version_key
from ici.engines.base import BaseEngine

_BINARY_KINDS = frozenset({"executable", "shared-library"})


def _artifact_id(record: ArtifactRecord) -> str:
    return getattr(record, "artifact_id", "") or record.path


def _finding(rule_id: str, path: str, message: str, tool_rule_id: str) -> Finding:
    location = SourceLocation(path=path, start_line=1)
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.COMPATIBILITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.EXACT,
        fingerprint=finding_fingerprint(rule_id, location),
        primary_location=location,
        message=message,
        explanation="The linked binary contract is incompatible with the configured deployment policy.",
        remediation="Relink with compatible dependencies, ABI floors, and loader paths.",
        tool_rule_id=tool_rule_id,
        tool_name="readelf",
    )


class BinaryCompatibilityEngine(BaseEngine):
    """Inspect only validated manifest artifacts; never discover or execute binaries."""

    CACHE_REUSE_SAFE = False
    CACHE_IMPLEMENTATION_MODULES = ("ici.engines._elf", "ici.engines.binary_compat")

    def _records(self, cfg: dict[str, Any]) -> list[tuple[ArtifactRecord, Path]]:
        if self.analysis_context is None:
            return []
        selected = set(cfg.get("artifacts", []))
        records: list[tuple[ArtifactRecord, Path]] = []
        seen: set[str] = set()
        for manifest in self.analysis_context.manifests:
            manifest.validate()
            for record in manifest.artifacts:
                identity = _artifact_id(record)
                if selected:
                    if identity not in selected and record.path not in selected:
                        continue
                elif record.kind not in _BINARY_KINDS:
                    continue
                if identity in seen:
                    raise ValueError(f"artifact identity is ambiguous: {identity}")
                seen.add(identity)
                root = (
                    manifest.project_root
                    if record.scope is ArtifactScope.PROJECT
                    else manifest.shadow_root
                )
                if root is None:
                    raise ValueError(f"artifact has no declared root: {identity}")
                records.append((record, root / record.path))
        missing = selected - {
            item for record, _path in records for item in (_artifact_id(record), record.path)
        }
        if missing:
            raise ValueError(f"configured artifact was not published: {', '.join(sorted(missing))}")
        maximum = int(cfg.get("max_artifacts", 64))
        if len(records) > maximum:
            raise ValueError(f"binary artifact count exceeds configured maximum {maximum}")
        return records

    def _readelf(self) -> tuple[str, str]:
        if self.analysis_context is not None:
            capability = self.analysis_context.capabilities.capabilities.get("readelf")
            if capability is not None and capability.available and capability.complete:
                return capability.path, capability.version
        path = shutil.which("readelf")
        return (path or "", "")

    @staticmethod
    def _abi_violations(
        path: str,
        facts: ElfFacts,
        cfg: dict[str, Any],
        build_roots: tuple[Path, ...] = (),
    ) -> list[Finding]:
        findings: list[Finding] = []
        for label, actual in (("class", facts.elf_class), ("machine", facts.machine)):
            expected = str(cfg.get(f"expected_{label}", ""))
            if expected and actual != expected:
                findings.append(
                    _finding(
                        f"ici.binary.{label}-mismatch",
                        path,
                        f"ELF {label} {actual!r} does not match expected {expected!r}",
                        f"elf.header.{label}",
                    )
                )
        for namespace, values in (
            ("glibc", facts.glibc),
            ("glibcxx", facts.glibcxx),
            ("cxxabi", facts.cxxabi),
        ):
            floor = str(cfg.get(f"max_{namespace}", ""))
            actual = maximum_version(values)
            if floor and actual and version_key(actual) > version_key(floor):
                findings.append(
                    _finding(
                        f"ici.binary.{namespace}-floor",
                        path,
                        f"Maximum required {namespace.upper()} {actual} exceeds configured {floor}",
                        f"elf.version.{namespace}",
                    )
                )
        paths = (*facts.rpath, *facts.runpath)
        if cfg.get("forbid_absolute_rpath", True):
            absolute = [value for value in paths if value.startswith("/")]
            if absolute:
                findings.append(
                    _finding(
                        "ici.binary.forbidden-rpath",
                        path,
                        f"ELF loader path contains absolute entries: {', '.join(absolute)}",
                        "elf.rpath.forbidden",
                    )
                )
        if cfg.get("forbid_build_paths", True):
            leaked = []
            for value in paths:
                candidate = Path(value)
                if not candidate.is_absolute():
                    continue
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError):
                    resolved = candidate
                if any(resolved == root or root in resolved.parents for root in build_roots):
                    leaked.append(value)
            if leaked:
                findings.append(
                    _finding(
                        "ici.binary.build-path-leak",
                        path,
                        f"ELF loader path exposes build roots: {', '.join(leaked)}",
                        "elf.rpath.build-path",
                    )
                )
        forbidden = set(cfg.get("forbidden_needed", []))
        blocked = sorted(forbidden.intersection(facts.needed))
        allowed = set(cfg.get("allowed_needed", []))
        outside = sorted(set(facts.needed) - allowed) if allowed else []
        if blocked or outside:
            names = blocked or outside
            findings.append(
                _finding(
                    "ici.binary.forbidden-dependency",
                    path,
                    f"ELF requires disallowed dependencies: {', '.join(names)}",
                    "elf.dynamic.needed",
                )
            )
        return findings

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("binary_compat")
        targets: list[InspectionTarget] = []
        findings: list[Finding] = []
        evidence: list[ToolEvidence] = []
        facts_payload: list[dict[str, Any]] = []
        errors: list[str] = []
        checked = 0
        skipped = 0
        try:
            records = self._records(cfg)
            if not records:
                if cfg.get("required", False):
                    raise ValueError("no published executable or shared-library artifacts")
                return self.create_result(
                    name="binary_compat",
                    status=EngineStatus.SKIP,
                    summary="Binary compatibility not applicable: no selected manifest artifacts",
                    duration=time.time() - started,
                    targets=[
                        InspectionTarget(
                            file_path=".",
                            start_line=1,
                            target_name="BinaryCompatibility:NotApplicable",
                            status=EngineStatus.SKIP,
                            message="No executable or shared-library manifest artifacts were selected",
                        )
                    ],
                    extra={"elf": {"state": "NOT_APPLICABLE", "artifacts_checked": 0, "facts": []}},
                    required=False,
                    evidence=EvidenceState.NOT_APPLICABLE,
                )
            tool, version = self._readelf()
            if not tool:
                raise ValueError("readelf capability is unavailable")
            build_roots = {self.project_root}
            if self.analysis_context is not None:
                build_roots.update(
                    manifest.shadow_root
                    for manifest in self.analysis_context.manifests
                    if manifest.shadow_root is not None
                )
            canonical_build_roots = tuple(sorted(build_roots, key=str))
            for record, path in records:
                relative = record.path
                try:
                    with path.open("rb") as stream:
                        magic = stream.read(4)
                except OSError as err:
                    raise ValueError(f"artifact could not be read: {relative}: {err}") from err
                if magic != b"\x7fELF":
                    if cfg.get("allow_non_elf", False):
                        skipped += 1
                        targets.append(
                            InspectionTarget(
                                file_path=relative,
                                start_line=1,
                                target_name="BinaryCompatibility:NonElf",
                                status=EngineStatus.SKIP,
                                message="Manifest artifact is not ELF and policy allows it",
                            )
                        )
                        continue
                    findings.append(
                        _finding(
                            "ici.binary.non-elf",
                            relative,
                            "Selected binary manifest artifact is not an ELF object",
                            "elf.magic",
                        )
                    )
                    targets.append(
                        InspectionTarget(
                            file_path=relative,
                            start_line=1,
                            target_name=f"BinaryCompatibility:{_artifact_id(record)}",
                            status=EngineStatus.FAIL,
                            message="Selected binary manifest artifact is not an ELF object",
                        )
                    )
                    continue
                argv = [
                    tool,
                    "--file-header",
                    "--sections",
                    "--dynamic",
                    "--version-info",
                    "--wide",
                    str(path),
                ]
                process_result = run_process(
                    argv, cwd=self.project_root, timeout=30.0, max_output_chars=8 * 1024 * 1024
                )
                evidence.append(
                    ToolEvidence(
                        name="readelf binary compatibility",
                        path=tool,
                        version=version,
                        argv=argv,
                        returncode=process_result.returncode,
                        timed_out=process_result.timed_out,
                        truncated=process_result.truncated,
                        error=(
                            process_result.stderr[:512] if process_result.returncode != 0 else ""
                        ),
                    )
                )
                if (
                    process_result.returncode != 0
                    or process_result.timed_out
                    or process_result.truncated
                ):
                    raise ValueError(f"readelf did not produce complete evidence for {relative}")
                facts = parse_readelf(process_result.stdout)
                checked += 1
                artifact_findings = self._abi_violations(
                    relative,
                    facts,
                    cfg,
                    canonical_build_roots,
                )
                findings.extend(artifact_findings)
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"BinaryCompatibility:{_artifact_id(record)}",
                        status=EngineStatus.FAIL if artifact_findings else EngineStatus.PASS,
                        message=(
                            f"{len(artifact_findings)} binary compatibility violation(s)"
                            if artifact_findings
                            else "ELF dependency, loader path, and ABI evidence verified"
                        ),
                    )
                )
                facts_payload.append(
                    {
                        "id": _artifact_id(record),
                        "path": relative,
                        "class": facts.elf_class,
                        "machine": facts.machine,
                        "type": facts.elf_type,
                        "needed": list(facts.needed),
                        "rpath": list(facts.rpath),
                        "runpath": list(facts.runpath),
                        "versions": {
                            "GLIBC": list(facts.glibc),
                            "GLIBCXX": list(facts.glibcxx),
                            "CXXABI": list(facts.cxxabi),
                        },
                        "stripped": facts.stripped,
                    }
                )
        except (ElfParseError, OSError, RuntimeError, ValueError) as err:
            errors.append(str(err))

        if errors:
            status = EngineStatus.ERROR
            state = EvidenceState.NOT_RUN
            summary = f"Binary compatibility analysis incomplete: {errors[0]}"
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="BinaryCompatibility:AnalysisUnavailable",
                    status=EngineStatus.ERROR,
                    message=errors[0],
                )
            )
        else:
            status = self.evaluate_status(bool(findings), False, cfg.get("mode", "pass_warn_fail"))
            state = EvidenceState.MEASURED
            summary = f"ELF compatibility: {checked} checked, {len(findings)} violation(s), {skipped} skipped"
        engine_result = self.create_result(
            name="binary_compat",
            status=status,
            summary=summary,
            duration=time.time() - started,
            targets=targets,
            extra={
                "elf": {"state": state.value, "artifacts_checked": checked, "facts": facts_payload}
            },
            required=bool(cfg.get("required", False)),
            evidence=state,
            tool_evidence=evidence,
        )
        engine_result.findings = findings
        return engine_result
