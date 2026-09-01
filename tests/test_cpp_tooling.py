"""Shared compiler-context contracts for diagnostic-only C++ tooling."""

from pathlib import Path

import pytest

from ici.core.cpp_replay import ReplayCommandError, replay_environment
from ici.core.runner import ProcessResult
from ici.engines._cpp_tooling import (
    gcc_standard_library_projection,
    parse_compiler_include_search,
    tooling_arguments,
    tooling_include_roots,
)


def test_tooling_arguments_demote_fatal_warning_policy_without_losing_checks() -> None:
    source = Path("/project/src/main.cpp")
    replay = (
        "/usr/bin/clang++",
        "-std=c++20",
        "-Werror",
        "-Werror=return-type",
        "-Werror=error=unused-result",
        "-Werror=error",
        "-pedantic-errors",
        "--pedantic-errors",
        "-Werror-implicit-function-declaration",
        "-Wno-error",
        "-Wno-error=deprecated-declarations",
        "-Wconversion",
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        str(source),
    )

    assert tooling_arguments(replay, source) == [
        "-std=c++20",
        "-Wreturn-type",
        "-Wunused-result",
        "-pedantic",
        "-pedantic",
        "-Wimplicit-function-declaration",
        "-Wno-error",
        "-Wno-error=deprecated-declarations",
        "-Wconversion",
        "-fdiagnostics-color=never",
    ]


@pytest.mark.parametrize(
    "argument",
    [
        "-Werror=p,-MD,/tmp/ici-tooling-dependency.d",
        "-Werror=l,-Map,/tmp/ici-tooling-link.map",
        "-Werror=a,-o,/tmp/ici-tooling-object.o",
        "-Werror=error=p,-MD,/tmp/ici-tooling-nested.d",
    ],
)
def test_tooling_arguments_reject_unsafe_forwarding_after_demotion(argument: str) -> None:
    source = Path("/project/src/main.cpp")
    replay = (
        "/usr/bin/g++",
        argument,
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        str(source),
    )

    with pytest.raises(ReplayCommandError, match="safe diagnostic flag") as caught:
        tooling_arguments(replay, source)

    assert caught.value.code == "unsafe-tooling-warning-policy"


@pytest.mark.parametrize(
    "replay",
    [
        ("clang++", "-fsyntax-only", "/project/src/main.cpp"),
        (
            "clang++",
            "-Wall",
            "-Wextra",
            "-fsyntax-only",
            "/project/src/other.cpp",
        ),
    ],
)
def test_tooling_arguments_reject_unexpected_replay_shape(replay: tuple[str, ...]) -> None:
    with pytest.raises(ReplayCommandError, match="expected analysis suffix") as caught:
        tooling_arguments(replay, Path("/project/src/main.cpp"))

    assert caught.value.code == "unexpected-replay-shape"


def test_tooling_include_roots_resolve_exact_split_and_joined_flags(tmp_path: Path) -> None:
    build = tmp_path / "build"
    relative = tmp_path / "include"
    system = tmp_path / "qt" / "include"
    framework = tmp_path / "Frameworks"
    for directory in (build, relative, system, framework):
        directory.mkdir(parents=True, exist_ok=True)

    roots = tooling_include_roots(
        [
            "-I../include",
            "-isystem",
            str(system),
            f"-F{framework}",
            f"-I{relative}",
            "-I/does/not/exist",
        ],
        build,
    )

    assert roots == (relative.resolve(), system.resolve(), framework.resolve())


def test_tooling_include_roots_do_not_misclassify_longer_compiler_options(
    tmp_path: Path,
) -> None:
    build = tmp_path / "build"
    intended = tmp_path / "include"
    false_system = build / "atic"
    false_framework = build / "withsysroot"
    for directory in (build, intended, false_system, false_framework):
        directory.mkdir(parents=True, exist_ok=True)

    roots = tooling_include_roots(
        [
            "-isystematic",
            "-iframeworkwithsysroot",
            str(false_framework),
            f"-I{intended}",
        ],
        build,
    )

    assert roots == (intended.resolve(),)


def _stdlib_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve(strict=True)


def _stdlib_search_output(paths: tuple[Path, ...]) -> str:
    body = "\n".join(f" {path}" for path in paths)
    return (
        "Using built-in specs.\n"
        "COLLECT_GCC=g++\n"
        "#include <...> search starts here:\n"
        f"{body}\n"
        "End of search list.\n"
        "COMPILER_PATH=/usr/lib/gcc\n"
    )


def _stdlib_directories(tmp_path: Path, *names: str) -> tuple[Path, ...]:
    paths = tuple(tmp_path / name for name in names)
    for path in paths:
        path.mkdir(parents=True)
    return paths


def test_parser_extracts_one_bounded_angle_bracket_search_block(tmp_path: Path) -> None:
    paths = _stdlib_directories(tmp_path, "cxx", "target", "backward")

    assert parse_compiler_include_search(_stdlib_search_output(paths), tmp_path) == tuple(
        path.resolve() for path in paths
    )


@pytest.mark.parametrize(
    "output",
    [
        "g++: fatal error: no input files\n",
        "#include <...> search starts here:\nEnd of search list.\n"
        "#include <...> search starts here:\n /tmp/duplicate\nEnd of search list.\n",
        "#include <...> search starts here:\n /tmp/missing-end\n",
        "#include <...> search starts here:\n relative/include\nEnd of search list.\n",
        "#include <...> search starts here:\n /\nEnd of search list.\n",
        "#include <...> search starts here:\n {non-directory}\nEnd of search list.\n",
        "#include <...> search starts here:\n /tmp/a trailing-token\nEnd of search list.\n",
    ],
    ids=[
        "missing",
        "duplicate",
        "malformed",
        "relative",
        "root",
        "non-directory",
        "malformed-entry",
    ],
)
def test_parser_rejects_ambiguous_or_unsafe_search_blocks(
    tmp_path: Path,
    output: str,
) -> None:
    if "{non-directory}" in output:
        non_directory = tmp_path / "not-a-dir"
        non_directory.write_text("file", encoding="utf-8")
        output = output.replace("{non-directory}", str(non_directory))

    with pytest.raises(ValueError):
        parse_compiler_include_search(output, tmp_path)


def test_parser_rejects_an_excessive_number_of_search_paths(tmp_path: Path) -> None:
    paths = _stdlib_directories(tmp_path, *[f"root-{index}" for index in range(65)])

    with pytest.raises(ValueError):
        parse_compiler_include_search(_stdlib_search_output(paths), tmp_path)


def test_gcc_projection_preserves_ordered_cpp_only_directories(tmp_path: Path) -> None:
    common_first, cpp_one, common_second, cpp_two, c_only = _stdlib_directories(
        tmp_path,
        "common-first",
        "cpp-one",
        "common-second",
        "cpp-two",
        "c-only",
    )
    cxx_output = _stdlib_search_output((common_first, cpp_one, common_second, cpp_two))
    c_output = _stdlib_search_output((common_first, common_second, c_only))

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        language = command[command.index("-x") + 1]
        return ProcessResult(0, "", cxx_output if language == "c++" else c_output, 0.01)

    projection = gcc_standard_library_projection(
        _stdlib_executable(tmp_path / "tools" / "g++"),
        tmp_path,
        [],
        runner=runner,
    )

    assert projection.arguments == (
        "-nostdinc++",
        "-isystem",
        str(cpp_one.resolve()),
        "-isystem",
        str(cpp_two.resolve()),
    )


def test_gcc_projection_is_atomic_when_either_search_output_is_invalid(
    tmp_path: Path,
) -> None:
    valid = _stdlib_directories(tmp_path, "valid")

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        language = command[command.index("-x") + 1]
        return ProcessResult(
            0,
            "",
            _stdlib_search_output(valid)
            if language == "c++"
            else "#include <...> search starts here:\n relative\nEnd of search list.\n",
            0.01,
        )

    projection = gcc_standard_library_projection(
        _stdlib_executable(tmp_path / "tools" / "g++"),
        tmp_path,
        [],
        runner=runner,
    )

    assert projection.arguments == ()
    assert projection.error


def test_gcc_projection_probe_uses_resolved_compiler_and_bounded_context(
    tmp_path: Path,
) -> None:
    compiler = _stdlib_executable(tmp_path / "tools" / "g++")
    cxx_paths = _stdlib_directories(tmp_path, "stdlib", "stdlib-target")
    c_paths = _stdlib_directories(tmp_path, "c-runtime")
    outputs = {
        "c++": _stdlib_search_output(cxx_paths + c_paths),
        "c": _stdlib_search_output(c_paths),
    }
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        language = command[command.index("-x") + 1]
        return ProcessResult(0, "", outputs[language], 0.01)

    projection = gcc_standard_library_projection(
        compiler,
        tmp_path,
        ["-m64"],
        runner=runner,
    )

    assert projection.arguments == (
        "-nostdinc++",
        "-isystem",
        str(cxx_paths[0].resolve()),
        "-isystem",
        str(cxx_paths[1].resolve()),
    )
    assert len(calls) == 2
    assert {command[command.index("-x") + 1] for command, _ in calls} == {"c", "c++"}
    for command, kwargs in calls:
        assert command[0] == str(compiler.resolve())
        assert "-m64" in command
        assert "-E" in command
        assert "-v" in command
        assert command[-1] == "-"
        assert kwargs["input_text"] == ""
        assert kwargs["replace_env"] is True
        assert kwargs["env"] == replay_environment()
        assert kwargs["timeout"] == 5.0
        assert kwargs["max_output_chars"] == 131_072


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult(7, "", "compiler failed", 0.01),
        ProcessResult(124, "", "", 0.01, timed_out=True),
        ProcessResult(0, "partial", "", 0.01, truncated=True),
    ],
    ids=["nonzero", "timeout", "truncated"],
)
def test_gcc_projection_probe_failures_never_return_partial_arguments(
    tmp_path: Path,
    result: ProcessResult,
) -> None:
    compiler = _stdlib_executable(tmp_path / "tools" / "g++")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return result

    projection = gcc_standard_library_projection(compiler, tmp_path, [], runner=runner)

    assert calls
    assert projection.arguments == ()
    assert projection.error
