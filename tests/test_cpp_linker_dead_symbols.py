"""Focused contracts for exact GNU ELF linker dead-function evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

import ici.engines._cpp_linker_dead_symbols as linker
from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path.resolve(strict=True)


def _context(tmp_path: Path, *, backend: str = "cmake") -> tuple[Path, AnalysisContext, Path]:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    compiler = _executable(tmp_path / "tools" / "g++")
    capability = ToolCapability(
        name="g++",
        path=str(compiler),
        available=True,
        complete=True,
        version="g++ (GCC) 15.2.0",
        version_tuple=(15, 2, 0),
        details={"compiler_family": "gcc"},
    )
    context = AnalysisContext(
        project=ProjectModel(
            root=root,
            name="linker-test",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=("src/main.cpp",),
            compilable_cpp_sources=("src/main.cpp",),
            backend=backend,
        ),
        capabilities=CapabilityInventory(capabilities={"g++": capability}),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"test": "linker"}),
            toolchain_digest=canonical_digest({"g++": "15.2.0"}),
        ),
        compilation=CompilationContext(origin="cmake", unity_build=False),
    )
    return root, context, compiler


def _toolset(tmp_path: Path, compiler: Path) -> linker._Toolset:
    return linker._Toolset(
        cmake=_executable(tmp_path / "tools" / "cmake"),
        cmake_version="4.2.3",
        readelf=_executable(tmp_path / "tools" / "readelf"),
        readelf_version="2.46",
        addr2line=_executable(tmp_path / "tools" / "addr2line"),
        addr2line_version="2.46",
        compiler_paths=frozenset({compiler.resolve(strict=True)}),
    )


def _link_file(shadow: Path, text: str, target: str = "app") -> Path:
    path = shadow / "CMakeFiles" / f"{target}.dir" / "link.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _valid_link(compiler: Path, *values: str) -> str:
    return " ".join(
        [
            str(compiler),
            "-Wl,--gc-sections",
            "-Wl,--print-gc-sections",
            "-no-pie",
            *values,
            "-o",
            "CMakeFiles/app.dir/app",
        ]
    )


def _object(shadow: Path, relative: str) -> Path:
    path = shadow / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"object")
    return path.resolve(strict=True)


def _result(stdout: str = "", stderr: str = "", *, returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode, stdout, stderr, 0.01)


def test_bounded_link_argv_accepts_one_shell_free_utf8_line(tmp_path: Path) -> None:
    root, _context_value, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    shadow.mkdir(parents=True)
    path = _link_file(shadow, _valid_link(compiler, "CMakeFiles/app.dir/main.cpp.o"))

    assert linker._bounded_link_argv(path, shadow) == (
        str(compiler),
        "-Wl,--gc-sections",
        "-Wl,--print-gc-sections",
        "-no-pie",
        "CMakeFiles/app.dir/main.cpp.o",
        "-o",
        "CMakeFiles/app.dir/app",
    )


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("/tools/g++ -no-pie && echo unsafe", "shell operators"),
        ("/tools/g++ -no-pie > output", "shell operators"),
        ("/tools/g++ @CMakeFiles/app.dir/response.rsp", "response files"),
        ("/tools/g++ 'unterminated", "quoting is malformed"),
        ("/tools/g++\n-no-pie", "exactly one shell-free line"),
        ("/tools/g++\0-no-pie", "exactly one shell-free line"),
    ],
)
def test_bounded_link_argv_rejects_shell_response_and_malformed_input(
    tmp_path: Path,
    contents: str,
    message: str,
) -> None:
    root, _context_value, _compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    shadow.mkdir(parents=True)
    path = _link_file(shadow, contents)

    with pytest.raises(ValueError, match=message):
        linker._bounded_link_argv(path, shadow)


def test_bounded_link_argv_enforces_argument_and_character_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _context_value, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    shadow.mkdir(parents=True)
    path = _link_file(shadow, _valid_link(compiler, "CMakeFiles/app.dir/main.cpp.o"))

    monkeypatch.setattr(linker, "_MAX_LINK_ARGUMENTS", 2)
    with pytest.raises(ValueError, match="argument count"):
        linker._bounded_link_argv(path, shadow)

    monkeypatch.setattr(linker, "_MAX_LINK_ARGUMENTS", 32_768)
    monkeypatch.setattr(linker, "_MAX_LINK_ARGUMENT_CHARS", 4)
    with pytest.raises(ValueError, match="character limit"):
        linker._bounded_link_argv(path, shadow)


@pytest.mark.parametrize(
    "unsafe_flag",
    [
        "-flto",
        "-flto=auto",
        "-pie",
        "-rdynamic",
        "--export-dynamic",
        "--whole-archive",
        "-Tcustom.ld",
        "--script=custom.ld",
        "-Wl,-Tcustom.ld",
        "-Wl,--script=custom.ld",
        "-Wl,--dynamic-list=exports",
        "-Wl,--retain-symbols-file=exports",
        "-Wl,--undefined=entry",
        "-Wl,-u,entry",
    ],
)
def test_parse_link_command_rejects_unsafe_link_flags(
    tmp_path: Path,
    unsafe_flag: str,
) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    object_path = _object(shadow, "CMakeFiles/app.dir/main.cpp.o")
    path = _link_file(
        shadow,
        _valid_link(compiler, unsafe_flag, object_path.relative_to(shadow).as_posix()),
    )
    tools = _toolset(tmp_path, compiler)

    with pytest.raises(ValueError, match="exact GNU ELF executable contract"):
        linker._parse_link_command(path, shadow, context, tools)


def test_parse_link_command_excludes_shared_targets_and_non_object_inputs(tmp_path: Path) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    first = _object(shadow, "CMakeFiles/app.dir/first.o")
    second = _object(shadow, "CMakeFiles/app.dir/second.obj")
    _object(shadow, "CMakeFiles/app.dir/app")
    path = _link_file(
        shadow,
        _valid_link(
            compiler,
            "libdependency.a",
            "libshared.so",
            first.relative_to(shadow).as_posix(),
            second.relative_to(shadow).as_posix(),
        ),
    )
    command = linker._parse_link_command(path, shadow, context, _toolset(tmp_path, compiler))

    assert command is not None
    assert command.objects == tuple(sorted((first, second)))
    assert command.target == "app"

    shared_path = _link_file(
        shadow,
        _valid_link(compiler, "-shared", first.relative_to(shadow).as_posix()),
        target="shared",
    )
    assert (
        linker._parse_link_command(shared_path, shadow, context, _toolset(tmp_path, compiler))
        is None
    )


def test_parse_removals_keeps_only_valid_direct_objects_and_excludes_archives(
    tmp_path: Path,
) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    direct = _object(shadow, "CMakeFiles/app.dir/direct.o")
    _object(shadow, "CMakeFiles/app.dir/app")
    command_path = _link_file(
        shadow,
        _valid_link(compiler, direct.relative_to(shadow).as_posix()),
    )
    command = linker._parse_link_command(
        command_path, shadow, context, _toolset(tmp_path, compiler)
    )
    assert command is not None
    output = "\n".join(
        [
            ("ld: removing unused section '.text._Zdirect' in file 'CMakeFiles/app.dir/direct.o'"),
            "ld: removing unused section '.text.archive' in file 'libfoo.a(member.o)'",
            "ld: removing unused section '.text.other' in file 'other.o'",
            "ld: removing unused section '.data.direct' in file 'CMakeFiles/app.dir/direct.o'",
        ]
    )

    removals = linker._parse_removals(command, output, shadow)

    assert len(removals) == 1
    assert removals[0].object_path == direct
    assert removals[0].section == ".text._Zdirect"


def test_parse_removals_rejects_an_unparseable_gnu_ld_diagnostic(tmp_path: Path) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "shadow"
    direct = _object(shadow, "CMakeFiles/app.dir/direct.o")
    _object(shadow, "CMakeFiles/app.dir/app")
    path = _link_file(shadow, _valid_link(compiler, direct.relative_to(shadow).as_posix()))
    command = linker._parse_link_command(path, shadow, context, _toolset(tmp_path, compiler))
    assert command is not None

    with pytest.raises(ValueError, match="unparseable discarded-section"):
        linker._parse_removals(
            command,
            "ld: removing unused section '.text.dead' from file 'direct.o'",
            shadow,
        )


def test_readelf_section_index_requires_one_progbits_match() -> None:
    headers = "\n".join(
        [
            "  [ 4] .text.dead PROGBITS 0000000000000000 000040 000010 00 AX 0 0 1",
            "  [ 5] .data.dead NOBITS 0000000000000000 000040 000010 00 WA 0 0 1",
        ]
    )
    assert linker._section_index(headers, ".text.dead") == "4"
    assert linker._section_index(headers, ".data.dead") is None
    duplicate = f"{headers}\n  [ 6] .text.dead PROGBITS 0 0 1 00 AX 0 0 1"
    assert linker._section_index(duplicate, ".text.dead") is None


def test_readelf_symbols_accept_only_positive_unique_local_or_hidden_functions() -> None:
    symbols = "\n".join(
        [
            "   3: 0000000000000000    12 FUNC LOCAL  DEFAULT 4 local_default",
            "   4: 0000000000000000    12 FUNC GLOBAL HIDDEN  4 hidden_global",
            "   5: 0000000000000000    12 FUNC WEAK   INTERNAL 4 internal_weak",
            "   6: 0000000000000000    12 FUNC GLOBAL DEFAULT 4 exported_default",
            "   7: 0000000000000000     0 FUNC LOCAL  DEFAULT 4 zero_sized",
            "   8: 0000000000000000    12 OBJECT LOCAL DEFAULT 4 object_symbol",
            "   9: 0000000000000000    12 FUNC LOCAL  DEFAULT 5 other_section",
        ]
    )

    assert linker._section_symbols(symbols, "4") == (
        "local_default",
        "hidden_global",
        "internal_weak",
    )


def _inspect_fixture(
    tmp_path: Path,
    *,
    symbols: str,
    groups: str = "",
    addr2line: str | None = None,
    source_text: str = "one\ntwo\nthree\nfour\n",
) -> linker.CppLinkerDeadSymbol | None:
    root, _context_value, compiler = _context(tmp_path)
    source = root / "src" / "main.cpp"
    source.write_text(source_text, encoding="utf-8")
    shadow = root / "build" / "shadow"
    object_path = _object(shadow, "CMakeFiles/app.dir/main.cpp.o")
    tools = _toolset(tmp_path, compiler)
    unit = CompilationUnit(
        source="src/main.cpp",
        directory="build",
        argv=(str(compiler), "-ffunction-sections", "-c", str(source), "-o", "main.o"),
        output="main.o",
        compiler="g++",
        language="c++",
        standard="c++17",
        configuration=canonical_digest({"source": "src/main.cpp"}),
    )
    removal = linker._DiscardedSection(
        target="app",
        object_path=object_path,
        section=".text.dead",
        command_digest=canonical_digest({"target": "app"}),
        driver_name="g++",
        driver_version="g++ (GCC) 15.2.0",
    )
    headers = "  [ 4] .text.dead PROGBITS 0 0 16 00 AX 0 0 1"
    addr2line = addr2line or f"{source}:3"

    def runner(argv: list[str], **_kwargs: object) -> ProcessResult:
        if "-WS" in argv:
            return _result(headers)
        if "-Ws" in argv:
            return _result(symbols)
        if "--section-groups" in argv:
            return _result(groups)
        return _result(addr2line)

    outcome = linker.CppLinkerDeadOutcome()
    return linker._inspect_removal(
        removal,
        tools,
        outcome,
        runner,
        root,
        shadow,
        {"src/main.cpp": source_text},
        {object_path: unit},
        10_000.0,
    )


def test_inspect_removal_accepts_one_local_function_and_maps_its_source_line(
    tmp_path: Path,
) -> None:
    finding = _inspect_fixture(
        tmp_path,
        symbols="   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol",
    )

    assert finding is not None
    assert finding.symbol == "dead_symbol"
    assert finding.target.file_path == "src/main.cpp"
    assert finding.target.start_line == 3
    assert finding.target.status == EngineStatus.WARN


@pytest.mark.parametrize(
    ("symbols", "groups"),
    [
        (
            "   1: 0000000000000000    12 FUNC GLOBAL DEFAULT 4 exported_default",
            "",
        ),
        (
            "\n".join(
                [
                    "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 first",
                    "   2: 0000000000000000    12 FUNC GLOBAL HIDDEN 4 second",
                ]
            ),
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol.cold",
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol.constprop.0",
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol.isra.0",
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol.llvm.1",
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol.part.0",
            "",
        ),
        (
            "   1: 0000000000000000    12 FUNC LOCAL DEFAULT 4 dead_symbol",
            "COMDAT group contains .text.dead",
        ),
    ],
)
def test_inspect_removal_excludes_default_visible_ambiguous_or_grouped_symbols(
    tmp_path: Path,
    symbols: str,
    groups: str,
) -> None:
    assert _inspect_fixture(tmp_path, symbols=symbols, groups=groups) is None


@pytest.mark.parametrize(
    ("addr_output", "expected"),
    [
        ("{source}:1", ("src/main.cpp", 1)),
        ("{source}:4 (discriminator 2)", ("src/main.cpp", 4)),
        ("{source}:5", None),
        ("{outside}:1", None),
        ("??:0", None),
    ],
)
def test_source_location_is_project_contained_and_line_bounded(
    tmp_path: Path,
    addr_output: str,
    expected: tuple[str, int] | None,
) -> None:
    root, _context_value, compiler = _context(tmp_path)
    source = root / "src" / "main.cpp"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    shadow = root / "build" / "shadow"
    object_path = _object(shadow, "CMakeFiles/app.dir/main.cpp.o")
    tools = _toolset(tmp_path, compiler)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside;\n", encoding="utf-8")
    rendered = addr_output.format(source=source, outside=outside)

    def runner(_argv: list[str], **_kwargs: object) -> ProcessResult:
        return _result(rendered)

    outcome = linker.CppLinkerDeadOutcome()
    assert (
        linker._source_location(
            tools,
            outcome,
            runner,
            root,
            shadow,
            object_path,
            ".text.dead",
            {"src/main.cpp": source.read_text(encoding="utf-8")},
            10_000.0,
        )
        == expected
    )


@pytest.mark.parametrize("policy", ["auto", "required"])
def test_missing_context_is_unavailable_without_tool_execution(
    tmp_path: Path,
    policy: str,
) -> None:
    called = False

    def runner(*_args: object, **_kwargs: object) -> ProcessResult:
        nonlocal called
        called = True
        return _result()

    outcome = linker.run_cpp_linker_dead_symbols(
        tmp_path,
        None,
        source_texts={"src/main.cpp": "int main() { return 0; }\n"},
        policy=policy,
        runner=runner,
    )

    assert outcome.mode == "unavailable"
    assert outcome.errors == []
    assert outcome.warnings
    assert not called


def test_off_policy_short_circuits_before_context_or_tool_execution(tmp_path: Path) -> None:
    outcome = linker.run_cpp_linker_dead_symbols(
        tmp_path,
        None,
        source_texts={},
        policy="off",
        runner=lambda *_args, **_kwargs: pytest.fail("off mode must not execute tools"),
    )

    assert outcome.mode == "off"
    assert outcome.targets == []
    assert outcome.evidence == []


def test_non_linux_platform_is_unavailable_without_running_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, context, _compiler = _context(tmp_path)
    monkeypatch.setattr(linker.sys, "platform", "darwin")

    outcome = linker.run_cpp_linker_dead_symbols(
        root,
        context,
        source_texts={"src/main.cpp": "int main() { return 0; }\n"},
        policy="auto",
        runner=lambda *_args, **_kwargs: pytest.fail("unsupported platform must not execute tools"),
    )

    assert outcome.mode == "unavailable"
    assert outcome.symbols == []
    assert outcome.evidence == []
    assert outcome.warnings == ["Exact GNU ELF reachability is supported only on Linux"]


@pytest.mark.parametrize(
    ("policy", "mode", "has_error"),
    [("auto", "unavailable", False), ("required", "error", True)],
)
def test_invalid_link_target_is_atomic_and_policy_aware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    mode: str,
    has_error: bool,
) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "link-shadow"
    _link_file(shadow, f"{compiler} -no-pie && echo unsafe")
    tools = _toolset(tmp_path, compiler)
    monkeypatch.setattr(linker, "_toolset", lambda *_args, **_kwargs: tools)
    monkeypatch.setattr(linker, "_configure_shadow", lambda *_args, **_kwargs: shadow)

    outcome = linker.run_cpp_linker_dead_symbols(
        root,
        context,
        source_texts={"src/main.cpp": "int main() { return 0; }\n"},
        policy=policy,
        runner=lambda *_args, **_kwargs: pytest.fail("invalid link command must not run"),
    )

    assert outcome.mode == mode
    assert bool(outcome.errors) is has_error
    assert outcome.symbols == []
    assert outcome.link_targets_checked == 0
    if has_error:
        assert any(target.status == EngineStatus.ERROR for target in outcome.targets)
    else:
        assert outcome.warnings


def test_relink_failure_discards_all_observations_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, context, compiler = _context(tmp_path)
    shadow = root / "build" / "link-shadow"
    link_path = _link_file(shadow, _valid_link(compiler))
    output = _object(shadow, "CMakeFiles/app.dir/app")
    command = linker._LinkCommand(
        target="app",
        path=link_path,
        argv=tuple(_valid_link(compiler).split()),
        driver=compiler,
        driver_name="g++",
        driver_version="g++ (GCC) 15.2.0",
        objects=(),
        output=output,
        digest=canonical_digest({"target": "app"}),
    )
    tools = _toolset(tmp_path, compiler)
    monkeypatch.setattr(linker, "_toolset", lambda *_args, **_kwargs: tools)
    monkeypatch.setattr(linker, "_configure_shadow", lambda *_args, **_kwargs: shadow)
    monkeypatch.setattr(linker, "_discover_links", lambda *_args, **_kwargs: (command,))
    monkeypatch.setattr(linker, "_verify_gnu_linkers", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(linker, "_object_source_map", lambda *_args, **_kwargs: {})

    outcome = linker.run_cpp_linker_dead_symbols(
        root,
        context,
        source_texts={"src/main.cpp": "int main() { return 0; }\n"},
        policy="required",
        runner=lambda *_args, **_kwargs: _result("", "link failed", returncode=1),
    )

    assert outcome.mode == "error"
    assert outcome.symbols == []
    assert outcome.link_targets_checked == 0
    assert any("relink failed" in error for error in outcome.errors)
    assert any(target.status == EngineStatus.ERROR for target in outcome.targets)
