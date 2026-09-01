"""Shared compiler-context contracts for diagnostic-only C++ tooling."""

from pathlib import Path

import pytest

from ici.core.cpp_replay import ReplayCommandError
from ici.engines._cpp_tooling import tooling_arguments


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
