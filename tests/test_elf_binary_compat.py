"""ELF parser and policy contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

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
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines import binary_compat
from ici.engines._elf import ElfParseError, maximum_version, parse_readelf
from ici.engines.binary_compat import BinaryCompatibilityEngine

_READELF = """
ELF Header:
  Class:                             ELF64
  Type:                              DYN (Shared object file)
  Machine:                           Advanced Micro Devices X86-64
Section Headers:
  [ 1] .interp PROGBITS 0000
  [27] .symtab SYMTAB 0000
Dynamic section:
 0x1 (NEEDED) Shared library: [libstdc++.so.6]
 0x1 (NEEDED) Shared library: [libc.so.6]
 0x1d (RUNPATH) Library runpath: [$ORIGIN/../lib:/tmp/build]
Version needs section:
  0x0010: Name: GLIBC_2.17  Flags: none  Version: 4
  0x0020: Name: GLIBC_2.34  Flags: none  Version: 3
  0x0030: Name: GLIBCXX_3.4.29  Flags: none  Version: 2
  0x0040: Name: CXXABI_1.3.13  Flags: none  Version: 5
"""


def _binary_context(root: Path, relative: str, kind: str) -> AnalysisContext:
    identity = AnalysisIdentity(
        source_commit="a" * 40,
        config_digest=canonical_digest({"fixture": "binary"}),
        toolchain_digest=canonical_digest({"readelf": "fixture"}),
    )
    manifest = ArtifactManifest.create(
        root,
        None,
        BuildVariant.RELEASE,
        identity,
        [(Path(relative), ArtifactScope.PROJECT, kind)],
        "fixture",
    )
    return AnalysisContext(
        project=ProjectModel(root, "binary-fixture", "1.0", "cpp"),
        capabilities=CapabilityInventory(
            {
                "readelf": ToolCapability(
                    name="readelf",
                    path="/usr/bin/readelf",
                    available=True,
                    version="GNU readelf fixture",
                )
            }
        ),
        identity=identity,
        manifests=(manifest,),
    )


def test_combined_readelf_parser_preserves_loader_and_abi_facts() -> None:
    facts = parse_readelf(_READELF)

    assert facts.elf_class == "ELF64"
    assert facts.machine == "Advanced Micro Devices X86-64"
    assert facts.elf_type == "DYN"
    assert facts.needed == ("libc.so.6", "libstdc++.so.6")
    assert facts.runpath == ("$ORIGIN/../lib", "/tmp/build")
    assert facts.glibc == ("2.17", "2.34")
    assert maximum_version(facts.glibcxx) == "3.4.29"
    assert not facts.stripped


def test_parser_rejects_missing_headers_and_oversized_output() -> None:
    with pytest.raises(ElfParseError, match="header facts"):
        parse_readelf("Class: ELF64\n")
    with pytest.raises(ElfParseError, match="bounded"):
        parse_readelf("x" * (8 * 1024 * 1024 + 1))


def test_policy_reports_rpath_dependency_version_and_build_path_violations(
    tmp_path: Path,
) -> None:
    facts = parse_readelf(_READELF)
    cfg = {
        "forbid_absolute_rpath": True,
        "forbidden_needed": ["libstdc++.so.6"],
        "max_glibc": "2.17",
        "max_glibcxx": "3.4.28",
        "max_cxxabi": "1.3.12",
        "expected_class": "ELF32",
    }

    findings = BinaryCompatibilityEngine._abi_violations(
        "out/app",
        facts,
        cfg,
        (tmp_path, Path("/tmp").resolve()),
    )

    assert {finding.rule_id for finding in findings} == {
        "ici.binary.class-mismatch",
        "ici.binary.glibc-floor",
        "ici.binary.glibcxx-floor",
        "ici.binary.cxxabi-floor",
        "ici.binary.forbidden-rpath",
        "ici.binary.build-path-leak",
        "ici.binary.forbidden-dependency",
    }
    assert all(finding.primary_location.path == "out/app" for finding in findings)


def test_engine_consumes_manifest_elf_and_publishes_complete_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "dist" / "app"
    artifact.parent.mkdir()
    artifact.write_bytes(b"\x7fELFfixture")
    context = _binary_context(tmp_path, "dist/app", "executable")
    monkeypatch.setattr(
        binary_compat,
        "run_process",
        lambda *_args, **_kwargs: ProcessResult(0, _READELF, "", 0.01),
    )
    config = {
        "engines": {
            "binary_compat": {
                "enabled": True,
                "forbid_absolute_rpath": False,
                "forbid_build_paths": False,
                "allowed_needed": ["libc.so.6", "libstdc++.so.6"],
            }
        }
    }

    result = BinaryCompatibilityEngine(tmp_path, config, context).run()

    assert result.status is EngineStatus.PASS
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["elf"]["artifacts_checked"] == 1
    assert result.extra["elf"]["facts"][0]["versions"]["GLIBC"] == ["2.17", "2.34"]
    assert result.targets[0].file_path == "dist/app"
    assert result.tool_evidence[0].argv[-1] == str(artifact)


def test_engine_locates_non_elf_and_required_empty_manifest_states(tmp_path: Path) -> None:
    artifact = tmp_path / "dist" / "app"
    artifact.parent.mkdir()
    artifact.write_bytes(b"text fixture")
    context = _binary_context(tmp_path, "dist/app", "executable")
    allowed = BinaryCompatibilityEngine(
        tmp_path,
        {"engines": {"binary_compat": {"enabled": True, "allow_non_elf": True}}},
        context,
    ).run()

    assert allowed.status is EngineStatus.PASS
    assert allowed.targets[0].status is EngineStatus.SKIP
    assert allowed.extra["elf"]["artifacts_checked"] == 0

    empty_context = AnalysisContext(
        project=context.project,
        capabilities=context.capabilities,
        identity=context.identity,
    )
    required = BinaryCompatibilityEngine(
        tmp_path,
        {"engines": {"binary_compat": {"enabled": True, "required": True}}},
        empty_context,
    ).run()
    assert required.status is EngineStatus.ERROR
    assert required.evidence is EvidenceState.NOT_RUN
    assert "no published" in required.summary
