"""Adversarial contracts for safe C++ translation-unit replay commands."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import CompilationUnit
from ici.core.cpp_replay import (
    MAX_REPLAY_ARGUMENT_CHARS,
    MAX_REPLAY_ARGUMENTS,
    ReplayCommandError,
    build_replay_command,
    replay_environment,
)
from ici.core.toolchain import ToolCapability


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "build").mkdir()
    (root / "src" / "main.cpp").write_text("int main_value = 1;\n", encoding="utf-8")
    (root / "src" / "other.cpp").write_text("int other_value = 2;\n", encoding="utf-8")
    return root


def _compiler(tmp_path: Path, name: str = "g++") -> Path:
    """Create a non-project executable that tests can identify without running."""

    path = tmp_path / "toolchain" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _inventory(
    compiler: Path,
    *,
    name: str | None = None,
    available: bool = True,
    complete: bool = True,
    path: Path | None = None,
) -> CapabilityInventory:
    capability_name = name or compiler.name
    capability = ToolCapability(
        name=capability_name,
        path=str(path or compiler),
        available=available,
        complete=complete,
    )
    return CapabilityInventory(capabilities={capability_name: capability})


def _unit(
    compiler: Path,
    args: tuple[str, ...] = (),
    *,
    argv0: str | None = None,
    source: str = "src/main.cpp",
    directory: str = "build",
) -> CompilationUnit:
    command_driver = argv0 or str(compiler)
    return CompilationUnit(
        source=source,
        directory=directory,
        argv=(command_driver, *args),
        compiler=Path(command_driver).name,
        language="c++",
    )


def _error(
    root: Path,
    unit: CompilationUnit,
    inventory: CapabilityInventory,
    *,
    operation: str = "syntax",
) -> ReplayCommandError:
    with pytest.raises(ReplayCommandError) as caught:
        build_replay_command(root, unit, inventory, operation=operation)  # type: ignore[arg-type]
    return caught.value


def test_preserves_exact_semantic_arguments_and_counts_only_the_real_source(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    source = root / "src" / "main.cpp"
    unit = _unit(
        compiler,
        (
            "-std=c++20",
            "-DNAME=1",
            "-D",
            "VALUE=two",
            "-UOLD_NAME",
            "-I",
            "include",
            "-Igenerated",
            "-isystem",
            "/opt/qt/include",
            "-iquote",
            "generated",
            "-x",
            "c++",
            "--target=x86_64-linux-gnu",
            "-target",
            "aarch64-linux-gnu",
            "-march=x86-64",
            "-mcpu",
            "native",
            "--sysroot",
            "/opt/sdk",
            "-pthread",
            "-fPIC",
            "-I",
            "../src/main.cpp",
            "-c",
            "-o",
            "../src/main.cpp",
            "-MMD",
            "-MF",
            "../src/main.cpp",
            "-fsyntax-only",
            "../src/main.cpp",
        ),
    )

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.cwd == root / "build"
    assert replay.source == source
    assert replay.argv == (
        str(compiler),
        "-std=c++20",
        "-DNAME=1",
        "-D",
        "VALUE=two",
        "-UOLD_NAME",
        "-I",
        "include",
        "-Igenerated",
        "-isystem",
        "/opt/qt/include",
        "-iquote",
        "generated",
        "-x",
        "c++",
        "--target=x86_64-linux-gnu",
        "-target",
        "aarch64-linux-gnu",
        "-march=x86-64",
        "-mcpu",
        "native",
        "--sysroot",
        "/opt/sdk",
        "-pthread",
        "-fPIC",
        "-I",
        "../src/main.cpp",
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        str(source),
    )


def test_strips_compile_output_dependency_color_and_diagnostic_write_options(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(
        compiler,
        (
            "-c",
            "-S",
            "-E",
            "-fsyntax-only",
            "-M",
            "-MM",
            "-MD",
            "-MMD",
            "-MP",
            "-MG",
            "-H",
            "-save-temps",
            "--save-temps",
            "-ftime-trace",
            "-fcolor-diagnostics",
            "-fno-color-diagnostics",
            "-fdiagnostics-color=always",
            "-fdiagnostics-format=json",
            "-Wp,-MMD,build/main.d",
            "-o",
            "build/main.o",
            "-MF",
            "build/main.d",
            "-MT",
            "main.o",
            "-MQ",
            "main.o",
            "-MJ",
            "build/entry.json",
            "-dumpdir",
            "build",
            "-dumpbase",
            "main",
            "-dumpbase-ext",
            ".o",
            "-auxbase",
            "main",
            "-auxbase-strip",
            "main",
            "-serialize-diagnostics",
            "build/main.dia",
            "-ojoined.o",
            "-MFjoined.d",
            "-MTjoined",
            "-MQjoined",
            "-MJjoined.json",
            "-save-temps=obj",
            "--save-temps=cwd",
            "-ftime-trace=trace.json",
            "-fdiagnostics-color=never",
            "../src/main.cpp",
        ),
    )

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv == (
        str(compiler),
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        str(root / "src" / "main.cpp"),
    )


@pytest.mark.parametrize(
    "write_option",
    [
        "-fprofile-generate",
        "-fprofile-generate=build/profile",
        "-fprofile-dir=build/profile",
        "-fstack-usage",
        "-fdump-tree-all",
        "-fopt-info-vec-missed=build/optimization.txt",
        "-Wl,-Map=build/link.map",
    ],
)
def test_rejects_unhandled_write_producing_options(tmp_path: Path, write_option: str) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, (write_option, "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unsafe-compiler-option"


@pytest.mark.parametrize(
    "unsafe_option",
    [
        "--specs=/tmp/evil.specs",
        "-Xclang=-load",
        "-mllvm=unsafe-pass",
        "--language=assembler",
        "--for-assembler=--listing=/tmp/replay.lst",
        "-Wa,--listing=/tmp/replay.lst",
        "-Wp,-include,/tmp/extra.h",
        "-time=/tmp/replay.log",
        "-fdiagnostics-add-output=sarif:file=/tmp/replay.sarif",
        "-aux-info=/tmp/replay.aux",
        "--coverage",
        "--save-stats=/tmp/replay.stats",
        "--prefix=/tmp/tools",
        "-working-directory=/tmp/elsewhere",
        "--vfsoverlay=/tmp/map.yaml",
        "--config-system-dir=/tmp/config",
        "-funknown-future-side-effect",
    ],
)
def test_rejects_forwarding_output_config_and_unknown_options(
    tmp_path: Path, unsafe_option: str
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, (unsafe_option, "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unsafe-compiler-option"


@pytest.mark.parametrize("args", [("-", "../src/main.cpp"), ("-x", "assembler", "../src/main.cpp")])
def test_rejects_stdin_and_non_c_cpp_language_inputs(tmp_path: Path, args: tuple[str, ...]) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)

    error = _error(root, _unit(compiler, args), _inventory(compiler))

    assert error.code in {"extra-compiler-operand", "unsafe-compiler-option"}


@pytest.mark.parametrize(
    ("language_args", "expected_args"),
    [
        (("--language=c",), ("--language=c",)),
        (("--language=c++",), ("--language=c++",)),
        (("--language", "c"), ("--language", "c")),
        (("--language", "c++"), ("--language", "c++")),
    ],
)
def test_preserves_only_c_and_cpp_long_language_forms(
    tmp_path: Path,
    language_args: tuple[str, ...],
    expected_args: tuple[str, ...],
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, (*language_args, "../src/main.cpp"))

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv[1 : 1 + len(expected_args)] == expected_args


@pytest.mark.parametrize(
    "language_args",
    [
        ("--language=assembler", "../src/main.cpp"),
        ("--language", "assembler", "../src/main.cpp"),
    ],
)
def test_rejects_assembler_long_language_forms(
    tmp_path: Path, language_args: tuple[str, ...]
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)

    error = _error(root, _unit(compiler, language_args), _inventory(compiler))

    assert error.code == "unsafe-compiler-option"


def test_replay_environment_drops_compiler_override_and_loader_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CCC_OVERRIDE_OPTIONS",
        "COMPILER_PATH",
        "GCC_EXEC_PREFIX",
        "GCC_DIAGNOSTICS_LOG",
        "DEPENDENCIES_OUTPUT",
        "SUNPRO_DEPENDENCIES",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "CPATH",
        "CPLUS_INCLUDE_PATH",
        "TMPDIR",
    ):
        monkeypatch.setenv(name, "/tmp/project-controlled")

    environment = replay_environment()

    assert environment["LC_ALL"] == "C"
    assert all(
        name not in environment
        for name in (
            "CCC_OVERRIDE_OPTIONS",
            "COMPILER_PATH",
            "GCC_EXEC_PREFIX",
            "GCC_DIAGNOSTICS_LOG",
            "DEPENDENCIES_OUTPUT",
            "SUNPRO_DEPENDENCIES",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "CPATH",
            "CPLUS_INCLUDE_PATH",
            "TMPDIR",
        )
    )


def test_syntax_operation_adds_only_controlled_syntax_flags(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("-Wall", "../src/main.cpp"))

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv == (
        str(compiler),
        "-Wall",
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        str(root / "src" / "main.cpp"),
    )


def test_includes_operation_uses_bounded_preprocessor_trace_and_dev_null(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("-E", "-H", "-o", "ignored.i", "../src/main.cpp"))

    replay = build_replay_command(root, unit, _inventory(compiler), operation="includes")

    assert replay.argv == (
        str(compiler),
        "-fdiagnostics-color=never",
        "-w",
        "-E",
        "-H",
        "-o",
        os.devnull,
        str(root / "src" / "main.cpp"),
    )


def test_rejects_unknown_operation_after_validating_command(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("../src/main.cpp",))

    error = _error(root, unit, _inventory(compiler), operation="preprocess")

    assert error.code == "unsupported-operation"


@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        (("-c",), "invalid-source-operands"),
        (("-c", "../src/other.cpp"), "extra-compiler-operand"),
        (("-c", "../src/main.cpp", "../src/other.cpp"), "extra-compiler-operand"),
        (("-c", "../src/main.cpp", "../src/main.cpp"), "invalid-source-operands"),
    ],
)
def test_requires_one_exact_source_operand(
    tmp_path: Path,
    args: tuple[str, ...],
    expected_code: str,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, args)

    error = _error(root, unit, _inventory(compiler))

    assert error.code == expected_code


def test_accepts_the_exact_source_after_option_separator(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("-c", "--", "../src/main.cpp"))

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv[-1] == str(root / "src" / "main.cpp")


def test_option_values_that_look_like_sources_do_not_count_as_source_operands(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(
        compiler,
        (
            "-I",
            "../src/main.cpp",
            "-D",
            "SOURCE=../src/main.cpp",
            "-o",
            "../src/main.cpp",
            "-MF",
            "../src/main.cpp",
            "-c",
            "../src/main.cpp",
        ),
    )

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv[-1] == str(root / "src" / "main.cpp")
    assert replay.argv[replay.argv.index("-I") + 1] == "../src/main.cpp"
    assert replay.argv[replay.argv.index("-D") + 1] == "SOURCE=../src/main.cpp"
    assert "-o" not in replay.argv
    assert "-MF" not in replay.argv


@pytest.mark.parametrize(
    "option",
    [
        "-D",
        "-U",
        "-I",
        "-F",
        "-L",
        "-x",
        "-std",
        "-include",
        "-include-pch",
        "-imacros",
        "-isystem",
        "-iquote",
        "-idirafter",
        "-iprefix",
        "-isysroot",
        "--sysroot",
        "-target",
        "--target",
        "-arch",
        "-march",
        "-mcpu",
        "-mtune",
        "-mabi",
        "-resource-dir",
        "-Xassembler",
        "-Xlinker",
        "-o",
        "-MF",
        "-MT",
        "-MQ",
        "-MJ",
        "-dumpdir",
        "-dumpbase",
        "-dumpbase-ext",
        "-auxbase",
        "-auxbase-strip",
        "-serialize-diagnostics",
    ],
)
def test_missing_separated_option_values_fail_closed(tmp_path: Path, option: str) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, (option, "-c", "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    expected = (
        "unsafe-compiler-option"
        if option in {"-L", "-Xassembler", "-Xlinker"}
        else "missing-option-value"
    )
    assert error.code == expected


def test_rejects_response_files_even_when_the_path_is_project_relative(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    (root / "build" / "flags.rsp").write_text("-DSECRET=1\n", encoding="utf-8")
    unit = _unit(compiler, ("@flags.rsp", "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unexpanded-response-file"


def test_rejects_argument_count_above_replay_bound(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    args = ("-fPIC",) * MAX_REPLAY_ARGUMENTS + ("../src/main.cpp",)
    unit = _unit(compiler, args)

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "command-too-large"


def test_rejects_total_argument_bytes_above_replay_bound(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    oversized = "x" * (MAX_REPLAY_ARGUMENT_CHARS + 1)
    unit = _unit(compiler, (oversized, "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "command-too-large"


def test_rejects_missing_source_as_stale_translation_unit(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("../src/missing.cpp",), source="src/missing.cpp")

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "stale-translation-unit"


def test_rejects_missing_working_directory_as_stale_translation_unit(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("../src/main.cpp",), directory="missing-build")

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "stale-translation-unit"


def test_rejects_source_symlink_that_escapes_project_root(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside_value;\n", encoding="utf-8")
    escaped = root / "src" / "escaped.cpp"
    try:
        escaped.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    unit = _unit(compiler, ("../src/escaped.cpp",), source="src/escaped.cpp")

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unsafe-source"


def test_rejects_working_directory_symlink_that_escapes_project_root(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    outside = tmp_path / "outside-build"
    outside.mkdir()
    linked_build = root / "linked-build"
    try:
        linked_build.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    unit = _unit(compiler, ("../src/main.cpp",), directory="linked-build")

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unsafe-working-directory"


def test_accepts_only_the_exact_available_probed_compiler_path(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    other = _compiler(tmp_path / "other", "g++")
    unit = _unit(compiler, ("../src/main.cpp",))

    error = _error(root, unit, _inventory(compiler, path=other))

    assert error.code == "compiler-not-probed"


@pytest.mark.parametrize("driver_name", ["g++", "clang++"])
def test_accepts_direct_gcc_or_clang_when_that_driver_was_probed(
    tmp_path: Path,
    driver_name: str,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path, driver_name)
    unit = _unit(compiler, ("../src/main.cpp",))

    replay = build_replay_command(root, unit, _inventory(compiler), operation="syntax")

    assert replay.argv[0] == str(compiler)


@pytest.mark.parametrize(
    ("available", "complete"),
    [
        (False, True),
        (True, False),
    ],
)
def test_rejects_unavailable_or_incomplete_compiler_capability(
    tmp_path: Path,
    available: bool,
    complete: bool,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, ("../src/main.cpp",))

    error = _error(
        root,
        unit,
        _inventory(compiler, available=available, complete=complete),
    )

    assert error.code == "compiler-not-probed"


def test_rejects_unprobed_clang_driver_even_if_it_is_executable(tmp_path: Path) -> None:
    root = _project(tmp_path)
    gcc = _compiler(tmp_path, "g++")
    clang = _compiler(tmp_path, "clang++")
    unit = _unit(clang, ("../src/main.cpp",))

    error = _error(root, unit, _inventory(gcc))

    assert error.code == "compiler-not-probed"


def test_rejects_wrapper_driver(tmp_path: Path) -> None:
    root = _project(tmp_path)
    wrapper = _compiler(tmp_path, "ccache")
    unit = _unit(wrapper, ("../src/main.cpp",))

    error = _error(root, unit, _inventory(wrapper, name="ccache"))

    assert error.code == "unsupported-compiler-driver"


def test_rejects_project_contained_compiler_even_if_capability_is_approved(tmp_path: Path) -> None:
    root = _project(tmp_path)
    compiler = root / "tools" / "g++"
    compiler.parent.mkdir()
    compiler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    compiler.chmod(0o755)
    unit = _unit(compiler, ("../src/main.cpp",))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "project-compiler-rejected"


@pytest.mark.parametrize(
    "unsafe_option",
    [
        "-fplugin=/tmp/compiler-plugin.so",
        "-fpass-plugin=/tmp/compiler-pass.so",
        "-fmodules",
        "-fmodules-cache-path=build/modules",
        "-fmodule-file=build/module.pcm",
        "-fcxx-modules",
        "-fprebuilt-module-path=build/modules",
        "-iplugindir=/tmp/compiler-plugins",
        "-specs=/tmp/custom.specs",
        "-B/tmp/custom-toolchain",
        "-Xclang",
        "-mllvm",
        "-cc1",
        "-cc1as",
        "-plugin",
        "-plugin-arg-instrumentation=1",
        "-wrapper=/tmp/compiler-wrapper",
        "--config=/tmp/compiler.cfg",
    ],
)
def test_rejects_plugins_modules_specs_wrappers_and_backend_escape_hatches(
    tmp_path: Path,
    unsafe_option: str,
) -> None:
    root = _project(tmp_path)
    compiler = _compiler(tmp_path)
    unit = _unit(compiler, (unsafe_option, "../src/main.cpp"))

    error = _error(root, unit, _inventory(compiler))

    assert error.code == "unsafe-compiler-option"
