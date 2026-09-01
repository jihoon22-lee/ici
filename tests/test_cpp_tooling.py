"""Shared compiler-context contracts for diagnostic-only C++ tooling."""

from pathlib import Path

import pytest

from ici.core.cpp_replay import ReplayCommandError
from ici.engines._cpp_tooling import tooling_arguments, tooling_include_roots


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
