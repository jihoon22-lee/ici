"""Handwritten Make command-plan validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.context import BuildVariant
from ici.core.make import MakeConfigError, make_enabled, make_plan


def _config(**overrides: object) -> dict[str, object]:
    table: dict[str, object] = {
        "enabled": True,
        "workdir": ".",
        "shadow_dir": "out",
        "out_of_tree": "allow",
        "configure_argv": [],
        "build_argv": ["make", "--jobs", "{jobs}", "all"],
        "test_argv": ["make", "check"],
        "clean_argv": ["make", "clean"],
        "coverage_build_argv": ["make", "coverage"],
        "jobs": 3,
    }
    table.update(overrides)
    return {"build": {"make": table}}


def test_release_plan_is_literal_bounded_and_project_contained(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

    plan = make_plan(tmp_path, _config(), BuildVariant.RELEASE)

    assert plan.descriptor == "Makefile"
    assert plan.workdir == tmp_path
    assert plan.shadow == tmp_path / "out"
    assert plan.build_argv == ("make", "--jobs", "3", "all")
    assert plan.test_argv == ("make", "check")


def test_variant_requires_explicit_build_and_reuses_base_test(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, _config(), BuildVariant.COVERAGE)

    assert plan.build_argv == ("make", "coverage")
    assert plan.test_argv == ("make", "check")
    with pytest.raises(MakeConfigError, match="sanitize_build_argv"):
        make_plan(tmp_path, _config(), BuildVariant.SANITIZE)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"build_argv": ["bash", "-c", "make"]}, "command shell"),
        ({"build_argv": ["make", "$(touch nope)"]}, "unknown placeholder"),
        ({"build_argv": ["make", "-c", "payload"]}, "command shell"),
        ({"workdir": "../outside"}, "project root"),
        ({"shadow_dir": "."}, "must not be the project root"),
        ({"out_of_tree": "required", "workdir": "."}, "non-root workdir"),
        ({"jobs": 65}, "1 to 64"),
        ({"build_argv": []}, "direct command argv"),
    ],
)
def test_unsafe_or_incomplete_contract_is_rejected(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(MakeConfigError, match=message):
        make_plan(tmp_path, _config(**overrides), BuildVariant.RELEASE)


def test_make_enablement_is_exact_boolean() -> None:
    assert make_enabled(_config())
    assert not make_enabled({"build": {"make": {"enabled": 1}}})
    assert not make_enabled({})
