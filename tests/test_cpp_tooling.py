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
        "-pedantic-errors",
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
        "-pedantic",
        "-Wno-error",
        "-Wno-error=deprecated-declarations",
        "-Wconversion",
        "-fdiagnostics-color=never",
    ]


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
