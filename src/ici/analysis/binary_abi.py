"""ELF ABI-compatibility verdicts — policy checks over parsed readelf facts.

Extracted from the stable ``ici.engines.binary_compat`` shell so the next
path's binary-compat provider and the remaining stable engine share one rule
implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ici.analysis._elf import ElfFacts, maximum_version, version_key
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
)

_BINARY_KINDS = frozenset({"executable", "shared-library"})


def binary_finding(rule_id: str, path: str, message: str, tool_rule_id: str) -> Finding:
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


def abi_violations(
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
                binary_finding(
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
                binary_finding(
                    f"ici.binary.{namespace}-floor",
                    path,
                    f"Maximum required {namespace.upper()} {actual} exceeds configured {floor}",
                    f"elf.version.{namespace}",
                )
            )
    if cfg.get("require_static", False) and facts.dynamic:
        findings.append(
            binary_finding(
                "ici.binary.dynamic-linkage",
                path,
                "Artifact is dynamically linked but the policy requires static linkage",
                "elf.linkage.dynamic",
            )
        )
    paths = (*facts.rpath, *facts.runpath)
    if cfg.get("forbid_absolute_rpath", True):
        absolute = [value for value in paths if value.startswith("/")]
        if absolute:
            findings.append(
                binary_finding(
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
                binary_finding(
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
            binary_finding(
                "ici.binary.forbidden-dependency",
                path,
                f"ELF requires disallowed dependencies: {', '.join(names)}",
                "elf.dynamic.needed",
            )
        )
    return findings
