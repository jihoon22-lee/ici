"""Bounded, read-only capability probes for local development tools."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ici.core.redaction import redact_text
from ici.core.runner import ProcessResult, run_process

PROBE_TIMEOUT_SECONDS = 5.0
PROBE_OUTPUT_LIMIT = 65_536


@dataclass(frozen=True)
class ToolProbe:
    """Declarative, shell-free probe for one canonical tool capability."""

    name: str
    candidates: tuple[str, ...]
    version_args: tuple[str, ...]
    detail_kind: str = ""
    detail_args: tuple[str, ...] = ()
    static_details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProbeEvidence:
    """Bounded execution evidence retained for one capability probe."""

    purpose: str
    argv: tuple[str, ...]
    returncode: int
    timed_out: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class ToolCapability:
    """Observed availability and bounded metadata for one external tool."""

    name: str
    path: str
    available: bool
    version: str = ""
    version_tuple: tuple[int, ...] = ()
    complete: bool = True
    error: str = ""
    details: Mapping[str, str] = field(default_factory=dict)
    probe_argv: tuple[str, ...] = ()
    returncode: int | None = None
    timed_out: bool = False
    truncated: bool = False
    evidence: tuple[ProbeEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


DEFAULT_TOOL_PROBES: tuple[ToolProbe, ...] = (
    ToolProbe("gcc", ("gcc",), ("-dumpfullversion", "-dumpversion"), "target", ("-dumpmachine",)),
    ToolProbe("g++", ("g++",), ("-dumpfullversion", "-dumpversion"), "target", ("-dumpmachine",)),
    ToolProbe("clang", ("clang",), ("--version",), "target", ("-dumpmachine",)),
    ToolProbe("clang++", ("clang++",), ("--version",), "target", ("-dumpmachine",)),
    ToolProbe("clang-format", ("clang-format",), ("--version",)),
    ToolProbe("clang-tidy", ("clang-tidy",), ("--version",)),
    ToolProbe("clangd", ("clangd",), ("--version",)),
    ToolProbe("clang-check", ("clang-check",), ("--version",)),
    ToolProbe("cmake", ("cmake",), ("--version",), "cmake-capabilities", ("-E", "capabilities")),
    ToolProbe("ctest", ("ctest",), ("--version",)),
    ToolProbe("qmake", ("qmake6", "qmake"), ("-v",), "qmake-query", ("-query",)),
    ToolProbe("make", ("make",), ("--version",)),
    ToolProbe("ninja", ("ninja",), ("--version",)),
    ToolProbe("gcov", ("gcov",), ("--version",)),
    ToolProbe("readelf", ("readelf",), ("--version",)),
    ToolProbe("objdump", ("objdump",), ("--version",)),
    ToolProbe("nm", ("nm",), ("--version",)),
    ToolProbe("ld", ("ld",), ("--version",)),
    ToolProbe("ar", ("ar",), ("--version",)),
    ToolProbe("strip", ("strip",), ("--version",)),
    ToolProbe("pkg-config", ("pkg-config",), ("--version",)),
    ToolProbe(
        "qt5",
        ("pkg-config",),
        ("--modversion", "Qt5Core"),
        static_details=(("provider", "pkg-config"), ("module", "Qt5Core"), ("qt_major", "5")),
    ),
    ToolProbe(
        "qt6",
        ("pkg-config",),
        ("--modversion", "Qt6Core"),
        static_details=(("provider", "pkg-config"), ("module", "Qt6Core"), ("qt_major", "6")),
    ),
    ToolProbe("git", ("git",), ("--version",)),
    ToolProbe("ruff", ("ruff",), ("--version",)),
    ToolProbe("mypy", ("mypy",), ("--version",)),
    ToolProbe("pytest", ("pytest",), ("--version",)),
    ToolProbe("uv", ("uv",), ("--version",)),
    ToolProbe("python3", ("python3",), ("-VV",)),
)

# Compatibility surface used by doctor while it migrates to the shared inventory. Keep the
# historical qmake command here so Qt 5-only hosts do not regress before that migration.
DEFAULT_PROBES: dict[str, list[str]] = {
    "gcc": ["gcc", "-dumpfullversion"],
    "g++": ["g++", "-dumpfullversion"],
    "make": ["make", "--version"],
    "cmake": ["cmake", "--version"],
    "qmake": ["qmake", "-query", "QT_VERSION"],
    "gcov": ["gcov", "--version"],
    "git": ["git", "--version"],
    "python3": ["python3", "-VV"],
}


_TOOL_VERSION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python3": (re.compile(r"\bPython\s+(?P<version>\d+(?:\.\d+){1,3})", re.I),),
    "cmake": (re.compile(r"\bcmake\s+version\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
    "ctest": (re.compile(r"\bctest\s+version\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
    "qmake": (re.compile(r"\bQMake\s+version\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
    "make": (re.compile(r"\b(?:GNU\s+)?Make\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
    "git": (re.compile(r"\bgit\s+version\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
    "pytest": (re.compile(r"\bpytest\s+(?P<version>\d+(?:\.\d+)+)", re.I),),
}
_COMPILER_VERSION_RE = re.compile(
    r"\b(?:Apple\s+|Ubuntu\s+)?(?:gcc|g\+\+|clang(?:\+\+)?)(?:\s+version)?"
    r"(?:\s+\([^)]*\))?\s+(?P<version>\d+(?:\.\d+)+)",
    re.I,
)
_GENERIC_VERSION_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<version>\d+(?:\.\d+)+)")
_TARGET_TRIPLE_RE = re.compile(r"^[A-Za-z0-9_+.]+(?:-[A-Za-z0-9_+.]+){1,4}$")
_TARGET_ARCH_RE = re.compile(
    r"^(?:aarch64|arm(?:v[0-9.]+)?|avr|bpf|hexagon|i[3-6]86|loongarch64|mips(?:64)?(?:el)?|"
    r"msp430|nvptx64|powerpc(?:64le)?|ppc(?:64le)?|riscv(?:32|64)|s390x|sparc(?:64)?|"
    r"wasm(?:32|64)|x86_64)$",
    re.I,
)
_TARGET_PLATFORM_PREFIXES = (
    "android",
    "darwin",
    "dragonfly",
    "eabi",
    "elf",
    "freebsd",
    "gnu",
    "haiku",
    "ios",
    "linux",
    "mingw",
    "msvc",
    "musl",
    "netbsd",
    "none",
    "openbsd",
    "solaris",
    "wasi",
    "windows",
)
_SECRET_ARG_FLAGS = {
    "--api-key",
    "--api_key",
    "--password",
    "--passwd",
    "--secret",
    "--token",
}


def _safe_argv(argv: Iterable[str]) -> tuple[str, ...]:
    """Redact explicit secret flag values before retaining probe evidence."""

    safe: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            safe.append("***REDACTED***")
            hide_next = False
            continue
        safe.append(redact_text(value))
        hide_next = value.casefold() in _SECRET_ARG_FLAGS
    return tuple(safe)


def _combined_lines(stdout: str, stderr: str) -> list[str]:
    return [line.strip() for line in (stdout + "\n" + stderr).splitlines() if line.strip()]


def _is_target_triple(value: str) -> bool:
    if not _TARGET_TRIPLE_RE.fullmatch(value):
        return False
    parts = value.casefold().split("-")
    if not _TARGET_ARCH_RE.fullmatch(parts[0]):
        return False
    return any(part.startswith(_TARGET_PLATFORM_PREFIXES) for part in parts[1:])


def parse_tool_version(name: str, stdout: str, stderr: str = "") -> tuple[str, tuple[int, ...]]:
    """Parse a display line and numeric tuple from vendor or multiline output."""

    lines = _combined_lines(stdout, stderr)
    patterns = _TOOL_VERSION_PATTERNS.get(name, ())
    if name in {
        "gcc",
        "g++",
        "clang",
        "clang++",
        "clang-format",
        "clang-tidy",
        "clangd",
        "clang-check",
    }:
        patterns = (*patterns, _COMPILER_VERSION_RE)
    for pattern in (*patterns, _GENERIC_VERSION_RE):
        for line in lines:
            match = pattern.search(line)
            if match:
                numeric = match.group("version")
                return redact_text(line[:200]), tuple(int(part) for part in numeric.split("."))
    return "", ()


def parse_qmake_query(text: str) -> dict[str, str]:
    """Normalize stable Qt metadata from ``qmake -query`` output."""

    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            values[key.strip()] = redact_text(value.strip()[:2_000])

    details: dict[str, str] = {}
    if values.get("QT_VERSION"):
        details["qt_version"] = values["QT_VERSION"]
        match = re.match(r"(\d+)", values["QT_VERSION"])
        if match:
            details["qt_major"] = match.group(1)
    specification = values.get("QMAKE_XSPEC") or values.get("QMAKE_SPEC")
    if specification:
        details["generator"] = specification
    if values.get("QT_INSTALL_PREFIX"):
        details["qt_prefix"] = values["QT_INSTALL_PREFIX"]
    if values.get("QT_CONFIG"):
        details["features"] = " ".join(values["QT_CONFIG"].split())
    return details


def parse_cmake_capabilities(text: str) -> dict[str, str]:
    """Extract stable generator and feature names from CMake capability JSON."""

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    generators = data.get("generators")
    names = sorted(
        {
            redact_text(str(item.get("name")))[:200]
            for item in generators or []
            if isinstance(item, dict) and item.get("name")
        }
    )[:100]
    details: dict[str, str] = {}
    if names:
        details["generators"] = ", ".join(names)[:4_000]
    if "serverMode" in data:
        details["server_mode"] = "true" if bool(data["serverMode"]) else "false"
    return details


def _vendor(version: str) -> str:
    folded = version.casefold()
    if folded.startswith("apple "):
        return "Apple"
    if folded.startswith("ubuntu "):
        return "Ubuntu"
    if folded.startswith("gnu "):
        return "GNU"
    if "(ubuntu " in folded or " for ubuntu" in folded:
        return "Ubuntu"
    if folded.startswith("llvm ") or (" clang version" in folded and "llvm" in folded):
        return "LLVM"
    if folded.startswith("microsoft "):
        return "Microsoft"
    return ""


def _failure_reason(result: ProcessResult, label: str = "probe") -> str:
    if result.timed_out:
        return f"{label} timed out"
    if result.truncated:
        return f"{label} output truncated"
    return f"{label} exited {result.returncode}"


def _run_probe(
    argv: list[str], cwd: Path | None, timeout: float, max_output_chars: int
) -> ProcessResult:
    return run_process(argv, cwd=cwd, timeout=timeout, max_output_chars=max_output_chars)


def collect_tool_capability(
    name: str,
    probe: list[str],
    cwd: Path | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    max_output_chars: int = PROBE_OUTPUT_LIMIT,
) -> tuple[ToolCapability, ProcessResult | None]:
    """Probe one explicit argv while preserving the historical public API."""

    if not probe:
        raise ValueError("probe argv must not be empty")
    resolved = shutil.which(probe[0])
    if resolved is None:
        return ToolCapability(name=name, path="", available=False, complete=False), None

    argv = [resolved, *probe[1:]]
    result = _run_probe(argv, cwd, timeout, max_output_chars)
    evidence = {
        "probe_argv": _safe_argv(argv),
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "truncated": result.truncated,
    }
    if result.timed_out or result.truncated or result.returncode != 0:
        return (
            ToolCapability(
                name=name,
                path=resolved,
                available=False,
                complete=False,
                error=_failure_reason(result),
                evidence=(
                    ProbeEvidence(
                        purpose="version",
                        argv=_safe_argv(argv),
                        returncode=result.returncode,
                        timed_out=result.timed_out,
                        truncated=result.truncated,
                    ),
                ),
                **evidence,
            ),
            result,
        )

    version, version_tuple = parse_tool_version(name, result.stdout, result.stderr)
    details: dict[str, str] = {}
    vendor = _vendor(version)
    if vendor:
        details["vendor"] = vendor
    complete = bool(version_tuple)
    return (
        ToolCapability(
            name=name,
            path=resolved,
            available=True,
            version=version,
            version_tuple=version_tuple,
            complete=complete,
            error="" if complete else "probe did not report a parseable version",
            details=details,
            evidence=(
                ProbeEvidence(
                    purpose="version",
                    argv=_safe_argv(argv),
                    returncode=result.returncode,
                    timed_out=result.timed_out,
                    truncated=result.truncated,
                ),
            ),
            **evidence,
        ),
        result,
    )


def _resolve_candidate(candidates: tuple[str, ...]) -> tuple[str, str] | None:
    seen: set[str] = set()
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved is None:
            continue
        real_path = os.path.realpath(resolved)
        if real_path in seen:
            continue
        seen.add(real_path)
        return candidate, resolved
    return None


def collect_registered_capability(
    probe: ToolProbe,
    cwd: Path | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    max_output_chars: int = PROBE_OUTPUT_LIMIT,
) -> tuple[ToolCapability, tuple[ProcessResult, ...]]:
    """Run a registry probe and its optional metadata probe without a shell."""

    resolution = _resolve_candidate(probe.candidates)
    if resolution is None:
        return ToolCapability(name=probe.name, path="", available=False, complete=False), ()
    alias, resolved = resolution
    base, version_result = collect_tool_capability(
        probe.name,
        [resolved, *probe.version_args],
        cwd=cwd,
        timeout=timeout,
        max_output_chars=max_output_chars,
    )
    if version_result is None:
        return base, ()

    details = dict(probe.static_details)
    details.update(base.details)
    details["resolved_alias"] = alias
    results = [version_result]
    complete = base.complete
    error = base.error
    timed_out = base.timed_out
    truncated = base.truncated

    if base.available and probe.detail_args:
        detail_argv = [resolved, *probe.detail_args]
        detail_result = _run_probe(detail_argv, cwd, timeout, max_output_chars)
        results.append(detail_result)
        detail_evidence = ProbeEvidence(
            purpose=probe.detail_kind or "metadata",
            argv=_safe_argv(detail_argv),
            returncode=detail_result.returncode,
            timed_out=detail_result.timed_out,
            truncated=detail_result.truncated,
        )
        timed_out = timed_out or detail_result.timed_out
        truncated = truncated or detail_result.truncated
        if detail_result.timed_out or detail_result.truncated or detail_result.returncode != 0:
            complete = False
            error = _failure_reason(detail_result, "metadata probe")
        elif probe.detail_kind == "target":
            target = next(iter(_combined_lines(detail_result.stdout, detail_result.stderr)), "")
            if _is_target_triple(target):
                details["target_triple"] = redact_text(target[:200])
            else:
                complete = False
                error = "metadata probe returned an invalid target triple"
        elif probe.detail_kind == "qmake-query":
            query_details = parse_qmake_query(detail_result.stdout or detail_result.stderr)
            details.update(query_details)
            if "qt_version" not in query_details:
                complete = False
                error = "metadata probe did not report QT_VERSION"
        elif probe.detail_kind == "cmake-capabilities":
            capability_details = parse_cmake_capabilities(
                detail_result.stdout or detail_result.stderr
            )
            details.update(capability_details)
            if not capability_details:
                complete = False
                error = "metadata probe returned malformed CMake capabilities"

    return (
        ToolCapability(
            name=base.name,
            path=base.path,
            available=base.available,
            version=base.version,
            version_tuple=base.version_tuple,
            complete=complete,
            error=error,
            details=details,
            probe_argv=base.probe_argv,
            returncode=base.returncode,
            timed_out=timed_out,
            truncated=truncated,
            evidence=(
                (*base.evidence, detail_evidence)
                if base.available and probe.detail_args
                else base.evidence
            ),
        ),
        tuple(results),
    )
