"""ELF parser and policy contracts."""

from __future__ import annotations

import pytest

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


def test_policy_reports_rpath_dependency_and_version_violations() -> None:
    facts = parse_readelf(_READELF)
    cfg = {
        "forbid_absolute_rpath": True,
        "forbidden_needed": ["libstdc++.so.6"],
        "max_glibc": "2.17",
        "max_glibcxx": "3.4.28",
        "max_cxxabi": "1.3.12",
        "expected_class": "ELF32",
    }

    findings = BinaryCompatibilityEngine._abi_violations("out/app", facts, cfg)

    assert {finding.rule_id for finding in findings} == {
        "ici.binary.class-mismatch",
        "ici.binary.glibc-floor",
        "ici.binary.glibcxx-floor",
        "ici.binary.cxxabi-floor",
        "ici.binary.forbidden-rpath",
        "ici.binary.forbidden-dependency",
    }
    assert all(finding.primary_location.path == "out/app" for finding in findings)
