"""Target-local GNU ELF dead-function evidence from linker section GC.

The adapter intentionally makes a narrower claim than whole-program reachability:
it reports only project-owned function sections that GNU ld explicitly discarded
while relinking one supported CMake executable in an isolated Release shadow.
Dynamic lookup, exported/default-visible symbols, archives, shared objects, LTO,
linker scripts, COMDAT groups and other ambiguous roots are excluded.
"""

from __future__ import annotations

import re
import shlex
import stat
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TypeGuard

from ici.core._build_paths import prepare_owned_shadow, shadow_dir
from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.cmake import BACKEND_CMAKE, ConfigureOptions, cmake_configure_argv
from ici.core.compile_db import load_compilation_context
from ici.core.context import AnalysisContext, BuildVariant, CompilationUnit, canonical_digest
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability, compiler_family_from_version
from ici.engines._cpp_tooling import compiler_capability, regular_executable

_SHADOW_SUFFIX = "-link-reachability"
_MAX_LINK_FILES = 256
_MAX_LINK_FILE_BYTES = 4 * 1024 * 1024
_MAX_LINK_ARGUMENTS = 32_768
_MAX_LINK_ARGUMENT_CHARS = 1024 * 1024
_MAX_LINK_OUTPUT_CHARS = 4 * 1024 * 1024
_MAX_DISCARDED_SECTIONS = 16_384
_MAX_OBJECTS = 4_096
_GLOBAL_TIMEOUT_SECONDS = 900.0
_COMMAND_TIMEOUT_SECONDS = 180.0
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TARGET_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,256}$")
_SECTION_RE = re.compile(r"^\.(?:text|gnu\.linkonce\.t)\.[A-Za-z0-9_.$+-]{1,1024}$")
_REMOVAL_RE = re.compile(
    r"(?:^|:\s+)removing unused section ['`](?P<section>[^'`]+)['`]"
    r" in file ['`](?P<object>[^'`]+)['`]\s*$"
)
_SECTION_HEADER_RE = re.compile(r"^\s*\[\s*(?P<index>\d+)\]\s+(?P<name>\S+)\s+(?P<kind>\S+)\s+")
_SYMBOL_RE = re.compile(
    r"^\s*\d+:\s+(?P<value>[0-9A-Fa-f]+)\s+(?P<size>\d+)\s+"
    r"(?P<kind>\S+)\s+(?P<bind>\S+)\s+(?P<visibility>\S+)\s+"
    r"(?P<section>\d+)\s+(?P<name>\S+)\s*$"
)
_SOURCE_RE = re.compile(
    r"^(?P<path>.+):(?P<line>[1-9]\d*)(?:\s+\(discriminator\s+\d+\))?$", re.ASCII
)
_UNSAFE_LINK_EXACT = frozenset(
    {
        "-flto",
        "-flto=auto",
        "-pie",
        "-rdynamic",
        "-shared",
        "-u",
        "--undefined",
        "--export-dynamic",
        "--whole-archive",
        "-Wl,--export-dynamic",
        "-Wl,--no-gc-sections",
        "-Wl,--whole-archive",
    }
)
_UNSAFE_LINK_PREFIXES = (
    "-flto=",
    "-fuse-ld=",
    "-B",
    "-T",
    "--script=",
    "-Wl,-T",
    "-Wl,--script=",
    "-Wl,--dynamic-list",
    "-Wl,--retain-symbols-file",
    "-Wl,--undefined",
    "-Wl,-u",
)
_CLONE_MARKERS = (".cold", ".constprop.", ".isra.", ".llvm.", ".part.")


@dataclass(frozen=True)
class CppLinkerDeadSymbol:
    """One exact target-local discarded function section."""

    target: InspectionTarget
    link_target: str
    symbol: str
    section: str
    object_path: str
    tool_name: str
    tool_version: str
    link_command_digest: str


@dataclass
class CppLinkerDeadOutcome:
    """Atomic GNU ELF section-GC result consumed by the dead-code facade."""

    targets: list[InspectionTarget] = field(default_factory=list)
    symbols: list[CppLinkerDeadSymbol] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "unavailable"
    link_targets_checked: int = 0
    sources_checked: int = 0
    discarded_sections_observed: int = 0
    ambiguous_sections_excluded: int = 0


@dataclass(frozen=True)
class _Toolset:
    cmake: Path
    cmake_version: str
    readelf: Path
    readelf_version: str
    addr2line: Path
    addr2line_version: str
    compiler_paths: frozenset[Path]


@dataclass(frozen=True)
class _LinkCommand:
    target: str
    path: Path
    argv: tuple[str, ...]
    driver: Path
    driver_name: str
    driver_version: str
    objects: tuple[Path, ...]
    output: Path
    digest: str


@dataclass(frozen=True)
class _DiscardedSection:
    target: str
    object_path: Path
    section: str
    command_digest: str
    driver_name: str
    driver_version: str


def _append_error(outcome: CppLinkerDeadOutcome, file_path: str, message: str) -> None:
    outcome.errors.append(message)
    outcome.targets.append(
        InspectionTarget(
            file_path=file_path,
            start_line=1,
            target_name="C++LinkerReachabilityError",
            status=EngineStatus.ERROR,
            message=message,
        )
    )


def _capability_path(
    root: Path,
    context: AnalysisContext,
    name: str,
) -> tuple[Path | None, ToolCapability | None]:
    capability = context.capabilities.capabilities.get(name)
    path = regular_executable(root, capability)
    return path, capability


def _toolset(
    root: Path, context: AnalysisContext, outcome: CppLinkerDeadOutcome
) -> _Toolset | None:
    if context.project.root != root:
        _append_error(outcome, ".", "C++ linker context belongs to another project")
        outcome.mode = "error"
        return None
    required: dict[str, tuple[Path, ToolCapability]] = {}
    for name in ("cmake", "readelf", "addr2line"):
        path, capability = _capability_path(root, context, name)
        if path is None or capability is None:
            outcome.warnings.append(f"Exact GNU ELF reachability requires {name}")
            return None
        required[name] = (path, capability)

    compilers: set[Path] = set()
    for name in ("gcc", "g++"):
        path, capability = _capability_path(root, context, name)
        if path is None or capability is None:
            continue
        family = capability.details.get("compiler_family", "") or compiler_family_from_version(
            capability.version
        )
        if family == "gcc":
            compilers.add(path)
    if not compilers:
        outcome.warnings.append("Exact GNU ELF reachability requires an observed GCC driver")
        return None
    return _Toolset(
        cmake=required["cmake"][0],
        cmake_version=required["cmake"][1].version,
        readelf=required["readelf"][0],
        readelf_version=required["readelf"][1].version,
        addr2line=required["addr2line"][0],
        addr2line_version=required["addr2line"][1].version,
        compiler_paths=frozenset(compilers),
    )


def _run(
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    *,
    name: str,
    version: str,
    argv: list[str],
    cwd: Path,
    timeout: float,
    evidence_argv: list[str] | None = None,
    max_output_chars: int = _MAX_LINK_OUTPUT_CHARS,
) -> ProcessResult | None:
    try:
        result = runner(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )
    except Exception as exc:
        outcome.evidence.append(
            ToolEvidence(
                name=name,
                path=argv[0],
                version=version,
                argv=evidence_argv or argv,
                error=f"{name} could not execute: {type(exc).__name__}",
            )
        )
        return None
    outcome.evidence.append(
        ToolEvidence(
            name=name,
            path=argv[0],
            version=version,
            argv=evidence_argv or argv,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )
    )
    return result


def _successful(result: ProcessResult | None) -> TypeGuard[ProcessResult]:
    return bool(
        result is not None
        and result.returncode == 0
        and not result.timed_out
        and not result.truncated
    )


def _configure_shadow(
    root: Path,
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    deadline: float,
) -> Path | None:
    shadow = shadow_dir(root, BACKEND_CMAKE, _SHADOW_SUFFIX)
    prepared, error = prepare_owned_shadow(root, shadow)
    if prepared is None:
        _append_error(outcome, ".", error)
        return None
    options = ConfigureOptions(
        BuildVariant.RELEASE,
        extra_c_flags=("-g", "-ffunction-sections", "-fno-lto", "-fno-pie"),
        extra_cxx_flags=("-g", "-ffunction-sections", "-fno-lto", "-fno-pie"),
        extra_link_flags=(
            "-Wl,--gc-sections",
            "-Wl,--print-gc-sections",
            "-fno-lto",
            "-no-pie",
        ),
        analysis_database=True,
        generator="Unix Makefiles",
        shadow_suffix_override=_SHADOW_SUFFIX,
    )
    argv = cmake_configure_argv(str(tools.cmake), root, prepared, options)
    result = _run(
        outcome,
        runner,
        name="cmake linker-reachability configure",
        version=tools.cmake_version,
        argv=argv,
        cwd=root,
        timeout=min(_COMMAND_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())),
    )
    if not _successful(result):
        _append_error(outcome, "CMakeLists.txt", "GNU ELF reachability configure did not complete")
        return None
    build_argv = [str(tools.cmake), "--build", str(prepared), "--parallel", "1"]
    result = _run(
        outcome,
        runner,
        name="cmake linker-reachability build",
        version=tools.cmake_version,
        argv=build_argv,
        cwd=root,
        timeout=min(_GLOBAL_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())),
    )
    if not _successful(result):
        _append_error(outcome, "CMakeLists.txt", "GNU ELF reachability build did not complete")
        return None
    return prepared


def _bounded_link_argv(path: Path, shadow: Path) -> tuple[str, ...]:
    try:
        encoded = _read_bounded_regular(
            path,
            _MAX_LINK_FILE_BYTES,
            containment_root=shadow,
        )
        text = encoded.decode("utf-8")
    except (FileNotFoundError, UnicodeError, _ReadError) as err:
        raise ValueError("link command is not a bounded regular UTF-8 file") from err
    if "\0" in text or len(text.splitlines()) != 1:
        raise ValueError("link command must contain exactly one shell-free line")
    try:
        argv = tuple(shlex.split(text, posix=True))
    except ValueError as err:
        raise ValueError("link command quoting is malformed") from err
    if not argv or len(argv) > _MAX_LINK_ARGUMENTS:
        raise ValueError("link command argument count is outside the bounded limit")
    if sum(len(item) for item in argv) > _MAX_LINK_ARGUMENT_CHARS:
        raise ValueError("link command arguments exceed the bounded character limit")
    if any(item in {"&&", "||", ";", "|", "<", ">"} for item in argv):
        raise ValueError("link command contains unsupported shell operators")
    if any(item.startswith("@") for item in argv):
        raise ValueError("link command response files are outside the exact contract")
    return argv


def _target_name(path: Path) -> str:
    parent = path.parent.name
    if not parent.endswith(".dir"):
        raise ValueError("link command is outside a CMake target directory")
    target = parent[: -len(".dir")]
    if _TARGET_RE.fullmatch(target) is None:
        raise ValueError("link target name is not bounded")
    return target


def _resolve_inside(root: Path, cwd: Path, value: str) -> Path | None:
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else cwd / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        details = resolved.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if not stat.S_ISREG(details.st_mode):
        return None
    return resolved


def _unsafe_link_argument(value: str) -> bool:
    return value in _UNSAFE_LINK_EXACT or any(
        value.startswith(prefix) for prefix in _UNSAFE_LINK_PREFIXES
    )


def _parse_link_command(
    path: Path,
    shadow: Path,
    context: AnalysisContext,
    tools: _Toolset,
) -> _LinkCommand | None:
    argv = _bounded_link_argv(path, shadow)
    executable = (
        _resolve_inside(Path("/"), shadow, argv[0]) if Path(argv[0]).is_absolute() else None
    )
    if executable is None:
        return None
    capability = compiler_capability(executable, context.capabilities)
    if capability is None or executable not in tools.compiler_paths:
        return None
    if "-shared" in argv:
        return None
    required = {"-Wl,--gc-sections", "-Wl,--print-gc-sections", "-no-pie"}
    if not required.issubset(argv) or any(_unsafe_link_argument(item) for item in argv):
        raise ValueError("link target uses flags outside the exact GNU ELF executable contract")
    objects: list[Path] = []
    for value in argv[1:]:
        if not value.endswith((".o", ".obj")):
            continue
        resolved = _resolve_inside(shadow, shadow, value)
        if resolved is None:
            raise ValueError("link target references an unsafe or missing direct object")
        objects.append(resolved)
    if not objects:
        return None
    if len(objects) > _MAX_OBJECTS:
        raise ValueError("link target direct-object count exceeds the bounded limit")
    output_indexes = [index for index, value in enumerate(argv) if value == "-o"]
    if len(output_indexes) != 1 or output_indexes[0] + 1 >= len(argv):
        raise ValueError("link target must declare exactly one output")
    output = _resolve_inside(shadow, shadow, argv[output_indexes[0] + 1])
    if output is None:
        raise ValueError("link target output is unsafe or missing")
    digest = canonical_digest({"target": _target_name(path), "argv": list(argv)})
    return _LinkCommand(
        target=_target_name(path),
        path=path,
        argv=argv,
        driver=executable,
        driver_name=capability.name,
        driver_version=capability.version,
        objects=tuple(sorted(set(objects))),
        output=output,
        digest=digest,
    )


def _discover_links(
    shadow: Path,
    context: AnalysisContext,
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    *,
    required: bool,
) -> tuple[_LinkCommand, ...] | None:
    paths = sorted(shadow.glob("**/CMakeFiles/*.dir/link.txt"))
    if len(paths) > _MAX_LINK_FILES:
        _append_error(
            outcome, "CMakeLists.txt", "CMake link-target count exceeds the bounded limit"
        )
        return None
    commands: list[_LinkCommand] = []
    for path in paths:
        try:
            command = _parse_link_command(path, shadow, context, tools)
        except ValueError as err:
            message = f"Link target {path.parent.name[:-4]} was excluded: {err}"
            if required:
                _append_error(outcome, "CMakeLists.txt", message)
                return None
            outcome.warnings.append(message)
            continue
        if command is not None:
            commands.append(command)
    if not commands:
        outcome.warnings.append("No supported direct-object CMake executable link target was found")
        return None
    return tuple(commands)


def _verify_gnu_linkers(
    commands: tuple[_LinkCommand, ...],
    shadow: Path,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    deadline: float,
) -> bool:
    """Prove each selected GCC driver actually delegates to GNU ld."""

    observed: set[Path] = set()
    for command in commands:
        if command.driver in observed:
            continue
        observed.add(command.driver)
        argv = [str(command.driver), "-Wl,--version"]
        result = _run(
            outcome,
            runner,
            name=f"{command.driver_name} linker identity",
            version=command.driver_version,
            argv=argv,
            cwd=shadow,
            timeout=min(30.0, max(0.1, deadline - time.monotonic())),
            max_output_chars=65_536,
        )
        banner = "" if result is None else result.stdout + "\n" + result.stderr
        if not _successful(result) or "GNU ld" not in banner or "LLD" in banner:
            _append_error(
                outcome,
                "CMakeLists.txt",
                f"Target {command.target} linker identity is not supported GNU ld",
            )
            return False
    return True


def _parse_removals(
    command: _LinkCommand, output: str, shadow: Path
) -> tuple[_DiscardedSection, ...]:
    direct = set(command.objects)
    removals: list[_DiscardedSection] = []
    for line in output.splitlines():
        if "removing unused section" not in line:
            continue
        match = _REMOVAL_RE.search(line)
        if match is None:
            raise ValueError("GNU ld emitted an unparseable discarded-section diagnostic")
        section = match.group("section")
        object_value = match.group("object")
        if "(" in object_value or ")" in object_value or _SECTION_RE.fullmatch(section) is None:
            continue
        object_path = _resolve_inside(shadow, shadow, object_value)
        if object_path is None or object_path not in direct:
            continue
        removals.append(
            _DiscardedSection(
                target=command.target,
                object_path=object_path,
                section=section,
                command_digest=command.digest,
                driver_name=command.driver_name,
                driver_version=command.driver_version,
            )
        )
        if len(removals) > _MAX_DISCARDED_SECTIONS:
            raise ValueError("discarded-section count exceeds the bounded limit")
    return tuple(removals)


def _relink(
    command: _LinkCommand,
    shadow: Path,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    deadline: float,
) -> tuple[_DiscardedSection, ...] | None:
    evidence_argv = [
        str(command.driver),
        f"<validated-link-command:{command.digest}>",
        f"<target:{command.target}>",
    ]
    result = _run(
        outcome,
        runner,
        name=f"{command.driver_name} GNU ELF relink",
        version=command.driver_version,
        argv=list(command.argv),
        evidence_argv=evidence_argv,
        cwd=shadow,
        timeout=min(_COMMAND_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())),
    )
    if not _successful(result):
        _append_error(
            outcome, "CMakeLists.txt", f"GNU ELF relink failed for target {command.target}"
        )
        return None
    try:
        return _parse_removals(command, result.stdout + "\n" + result.stderr, shadow)
    except ValueError as err:
        _append_error(outcome, "CMakeLists.txt", f"Target {command.target}: {err}")
        return None


def _run_readelf(
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    shadow: Path,
    object_path: Path,
    flag: str,
    deadline: float,
) -> str | None:
    relative = object_path.relative_to(shadow).as_posix()
    argv = [str(tools.readelf), flag, str(object_path)]
    result = _run(
        outcome,
        runner,
        name=f"readelf {flag}",
        version=tools.readelf_version,
        argv=argv,
        evidence_argv=[str(tools.readelf), flag, relative],
        cwd=shadow,
        timeout=min(30.0, max(0.1, deadline - time.monotonic())),
        max_output_chars=2 * 1024 * 1024,
    )
    return result.stdout if _successful(result) else None


def _section_index(headers: str, section: str) -> str | None:
    matches = [
        match.group("index")
        for line in headers.splitlines()
        if (match := _SECTION_HEADER_RE.match(line)) is not None
        and match.group("name") == section
        and match.group("kind") == "PROGBITS"
    ]
    return matches[0] if len(matches) == 1 else None


def _section_symbols(symbols: str, section_index: str) -> tuple[str, ...]:
    accepted: list[str] = []
    for line in symbols.splitlines():
        match = _SYMBOL_RE.match(line)
        if match is None or match.group("section") != section_index:
            continue
        if match.group("kind") != "FUNC" or int(match.group("size")) <= 0:
            continue
        if match.group("bind") != "LOCAL" and match.group("visibility") not in {
            "HIDDEN",
            "INTERNAL",
        }:
            continue
        name = match.group("name")
        if any(marker in name for marker in _CLONE_MARKERS):
            continue
        accepted.append(name)
    return tuple(accepted)


def _section_is_grouped(groups: str, section: str) -> bool:
    return any(section in line.split() for line in groups.splitlines())


def _source_location(
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    root: Path,
    shadow: Path,
    object_path: Path,
    section: str,
    source_texts: Mapping[str, str],
    deadline: float,
) -> tuple[str, int] | None:
    relative_object = object_path.relative_to(shadow).as_posix()
    argv = [str(tools.addr2line), "-e", str(object_path), "-j", section, "0"]
    result = _run(
        outcome,
        runner,
        name="addr2line discarded function",
        version=tools.addr2line_version,
        argv=argv,
        evidence_argv=[
            str(tools.addr2line),
            "-e",
            relative_object,
            "-j",
            section,
            "0",
        ],
        cwd=shadow,
        timeout=min(30.0, max(0.1, deadline - time.monotonic())),
        max_output_chars=16_384,
    )
    if not _successful(result):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or (match := _SOURCE_RE.fullmatch(lines[0])) is None:
        return None
    source_value = match.group("path")
    source_path = Path(source_value)
    source_path = source_path if source_path.is_absolute() else shadow / source_path
    try:
        resolved = source_path.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    text = source_texts.get(relative)
    line = int(match.group("line"))
    if text is None or line > len(text.splitlines()):
        return None
    return relative, line


def _object_source_map(root: Path, shadow: Path) -> dict[Path, CompilationUnit]:
    relative_db = (shadow / "compile_commands.json").relative_to(root).as_posix()
    context = load_compilation_context(
        root,
        {"project": {"compile_database": relative_db}},
    )
    if context.database_path is None or any(item.level == "error" for item in context.diagnostics):
        raise ValueError("analysis-shadow compilation database is incomplete")
    result: dict[Path, CompilationUnit] = {}
    for unit in context.units:
        if any(item.level == "error" for item in unit.diagnostics) or not unit.output:
            continue
        output = (root / PurePosixPath(unit.output)).resolve(strict=False)
        if output in result:
            raise ValueError("analysis-shadow object maps to multiple compilation units")
        result[output] = unit
    return result


def _unit_has_function_sections(unit: CompilationUnit) -> bool:
    relevant = [
        value for value in unit.argv if value in {"-ffunction-sections", "-fno-function-sections"}
    ]
    return (
        bool(relevant)
        and relevant[-1] == "-ffunction-sections"
        and not any(value == "-flto" or value.startswith("-flto=") for value in unit.argv)
    )


def _eligible_commands(
    commands: tuple[_LinkCommand, ...],
    unit_map: Mapping[Path, CompilationUnit],
    outcome: CppLinkerDeadOutcome,
    *,
    required: bool,
) -> tuple[_LinkCommand, ...] | None:
    accepted: list[_LinkCommand] = []
    for command in commands:
        units = [unit_map.get(object_path) for object_path in command.objects]
        if all(unit is not None and _unit_has_function_sections(unit) for unit in units):
            accepted.append(command)
            continue
        message = (
            f"Link target {command.target} was excluded because its direct-object "
            "compilation coverage is incomplete or not function-section based"
        )
        if required:
            _append_error(outcome, "CMakeLists.txt", message)
            return None
        outcome.warnings.append(message)
    if not accepted:
        outcome.warnings.append("No link target retained complete function-section coverage")
        return None
    return tuple(accepted)


def _validate_elf_executable(
    command: _LinkCommand,
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    shadow: Path,
    deadline: float,
) -> bool:
    relative = command.output.relative_to(shadow).as_posix()
    argv = [str(tools.readelf), "-Wh", str(command.output)]
    result = _run(
        outcome,
        runner,
        name="readelf linked executable",
        version=tools.readelf_version,
        argv=argv,
        evidence_argv=[str(tools.readelf), "-Wh", relative],
        cwd=shadow,
        timeout=min(30.0, max(0.1, deadline - time.monotonic())),
        max_output_chars=65_536,
    )
    if _successful(result) and re.search(r"^\s*Type:\s+EXEC\b", result.stdout, re.MULTILINE):
        return True
    _append_error(
        outcome,
        "CMakeLists.txt",
        f"Target {command.target} did not produce an exact ELF executable",
    )
    return False


def _inspect_removal(
    removal: _DiscardedSection,
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    root: Path,
    shadow: Path,
    source_texts: Mapping[str, str],
    unit_map: Mapping[Path, CompilationUnit],
    deadline: float,
) -> CppLinkerDeadSymbol | None:
    unit = unit_map.get(removal.object_path)
    if unit is None or unit.source not in source_texts:
        return None
    if not _unit_has_function_sections(unit):
        return None
    headers = _run_readelf(tools, outcome, runner, shadow, removal.object_path, "-WS", deadline)
    symbols = _run_readelf(tools, outcome, runner, shadow, removal.object_path, "-Ws", deadline)
    groups = _run_readelf(
        tools, outcome, runner, shadow, removal.object_path, "--section-groups", deadline
    )
    if headers is None or symbols is None or groups is None:
        raise ValueError("binutils inspection did not complete")
    section_index = _section_index(headers, removal.section)
    if section_index is None or _section_is_grouped(groups, removal.section):
        return None
    candidates = _section_symbols(symbols, section_index)
    if len(candidates) != 1:
        return None
    location = _source_location(
        tools,
        outcome,
        runner,
        root,
        shadow,
        removal.object_path,
        removal.section,
        source_texts,
        deadline,
    )
    if location is None:
        return None
    file_path, line = location
    symbol = candidates[0]
    target = InspectionTarget(
        file_path=file_path,
        start_line=line,
        target_name="GNU ld discarded function",
        status=EngineStatus.WARN,
        message=(
            f"GNU ld discarded function section from CMake target {removal.target} "
            "in the isolated Release reachability link"
        ),
        metrics={
            "link_target": removal.target,
            "symbol": symbol,
            "section": removal.section,
            "link_command_digest": removal.command_digest,
        },
    )
    return CppLinkerDeadSymbol(
        target=target,
        link_target=removal.target,
        symbol=symbol,
        section=removal.section,
        object_path=removal.object_path.relative_to(shadow).as_posix(),
        tool_name=removal.driver_name,
        tool_version=removal.driver_version,
        link_command_digest=removal.command_digest,
    )


def _sources_unchanged(root: Path, source_texts: Mapping[str, str]) -> bool:
    for relative, expected in source_texts.items():
        try:
            encoded = _read_bounded_regular(
                root / PurePosixPath(relative),
                max(1, len(expected.encode("utf-8")) + 1),
                containment_root=root,
            )
            if encoded.decode("utf-8") != expected:
                return False
        except (FileNotFoundError, UnicodeError, _ReadError):
            return False
    return True


def _record_pass_targets(
    source_texts: Mapping[str, str],
    symbols: list[CppLinkerDeadSymbol],
    link_targets: int,
) -> list[InspectionTarget]:
    represented = {item.target.file_path for item in symbols}
    return [
        InspectionTarget(
            file_path=file_path,
            start_line=1,
            target_name="GNU ld reachability scan",
            status=EngineStatus.PASS,
            message=f"Source participated in {link_targets} supported target-local Release link(s)",
        )
        for file_path in sorted(source_texts)
        if file_path not in represented
    ]


def _collect_removals(
    commands: tuple[_LinkCommand, ...],
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    shadow: Path,
    deadline: float,
) -> list[_DiscardedSection]:
    removals: list[_DiscardedSection] = []
    for command in commands:
        if time.monotonic() >= deadline:
            _append_error(
                outcome,
                "CMakeLists.txt",
                "GNU ELF reachability exceeded its global budget",
            )
            break
        observed = _relink(command, shadow, outcome, runner, deadline)
        if observed is None:
            break
        if not _validate_elf_executable(command, tools, outcome, runner, shadow, deadline):
            break
        outcome.link_targets_checked += 1
        removals.extend(observed)
    outcome.discarded_sections_observed = len(removals)
    return removals


def _inspect_removals(
    removals: list[_DiscardedSection],
    tools: _Toolset,
    outcome: CppLinkerDeadOutcome,
    runner: Callable[..., ProcessResult],
    root: Path,
    shadow: Path,
    source_texts: Mapping[str, str],
    unit_map: Mapping[Path, CompilationUnit],
    deadline: float,
) -> list[CppLinkerDeadSymbol]:
    accepted: list[CppLinkerDeadSymbol] = []
    for removal in removals:
        try:
            finding = _inspect_removal(
                removal,
                tools,
                outcome,
                runner,
                root,
                shadow,
                source_texts,
                unit_map,
                deadline,
            )
        except ValueError as err:
            _append_error(outcome, "CMakeLists.txt", str(err))
            break
        if finding is None:
            outcome.ambiguous_sections_excluded += 1
        else:
            accepted.append(finding)
    return accepted


def _record_success(
    outcome: CppLinkerDeadOutcome,
    accepted: list[CppLinkerDeadSymbol],
    commands: tuple[_LinkCommand, ...],
    unit_map: Mapping[Path, CompilationUnit],
    source_texts: Mapping[str, str],
) -> None:
    unique = {
        (item.target.file_path, item.target.start_line, item.link_target, item.symbol): item
        for item in accepted
    }
    outcome.symbols = [unique[key] for key in sorted(unique)]
    outcome.targets.extend(item.target for item in outcome.symbols)
    linked_sources = {
        unit_map[object_path].source
        for command in commands
        for object_path in command.objects
        if object_path in unit_map and unit_map[object_path].source in source_texts
    }
    outcome.sources_checked = len(linked_sources)
    outcome.targets.extend(
        _record_pass_targets(
            {path: source_texts[path] for path in sorted(linked_sources)},
            outcome.symbols,
            outcome.link_targets_checked,
        )
    )
    outcome.mode = "exact"


def run_cpp_linker_dead_symbols(
    project_root: Path,
    context: AnalysisContext | None,
    *,
    source_texts: Mapping[str, str],
    policy: str,
    runner: Callable[..., ProcessResult],
) -> CppLinkerDeadOutcome:
    """Measure exact target-local GNU ELF section-GC evidence atomically."""

    outcome = CppLinkerDeadOutcome()
    if policy == "off":
        outcome.mode = "off"
        return outcome
    root = project_root.resolve(strict=False)
    if not source_texts:
        outcome.mode = "not-applicable"
        return outcome
    if sys.platform != "linux":
        outcome.warnings.append("Exact GNU ELF reachability is supported only on Linux")
        return outcome
    if context is None or context.project.backend != BACKEND_CMAKE:
        outcome.warnings.append("Exact GNU ELF reachability requires a root CMake project")
        return outcome
    if context.compilation.unity_build:
        outcome.warnings.append("Unity compilation is outside the GNU ELF reachability contract")
        return outcome
    tools = _toolset(root, context, outcome)
    if tools is None:
        return outcome
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    shadow = _configure_shadow(root, tools, outcome, runner, deadline)
    if shadow is None:
        outcome.mode = "error"
        return outcome
    commands = _discover_links(
        shadow,
        context,
        tools,
        outcome,
        required=policy == "required",
    )
    if commands is None:
        outcome.mode = "error" if outcome.errors else "unavailable"
        return outcome
    if not _verify_gnu_linkers(commands, shadow, outcome, runner, deadline):
        outcome.mode = "error"
        return outcome
    try:
        unit_map = _object_source_map(root, shadow)
    except ValueError as err:
        _append_error(outcome, "CMakeLists.txt", str(err))
        outcome.mode = "error"
        return outcome
    commands = _eligible_commands(
        commands,
        unit_map,
        outcome,
        required=policy == "required",
    )
    if commands is None:
        outcome.mode = "error" if outcome.errors else "unavailable"
        return outcome
    removals = _collect_removals(commands, tools, outcome, runner, shadow, deadline)
    accepted: list[CppLinkerDeadSymbol] = []
    if not outcome.errors:
        accepted = _inspect_removals(
            removals,
            tools,
            outcome,
            runner,
            root,
            shadow,
            source_texts,
            unit_map,
            deadline,
        )
    if not outcome.errors and not _sources_unchanged(root, source_texts):
        _append_error(outcome, ".", "Project source changed during GNU ELF reachability analysis")
    if outcome.errors:
        outcome.mode = "error"
        outcome.symbols.clear()
        return outcome
    _record_success(outcome, accepted, commands, unit_map, source_texts)
    return outcome


__all__ = [
    "CppLinkerDeadOutcome",
    "CppLinkerDeadSymbol",
    "run_cpp_linker_dead_symbols",
]
