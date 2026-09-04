"""Configured handwritten Make adapter integration."""

from __future__ import annotations

from pathlib import Path

import pytest

import ici.core.cmake as adapter
from ici.core.backend import BACKEND_MAKE, select_backend
from ici.core.cmake import ConfigureOptions, build, configure, run_tests
from ici.core.context import BuildVariant
from ici.core.runner import ProcessResult


def _config(**make_overrides: object) -> dict[str, object]:
    table: dict[str, object] = {
        "enabled": True,
        "workdir": ".",
        "shadow_dir": "build/ici-make",
        "out_of_tree": "allow",
        "configure_argv": ["make", "configure"],
        "clean_argv": ["make", "clean"],
        "build_argv": ["make", "all"],
        "test_argv": ["make", "check"],
        "jobs": 1,
    }
    table.update(make_overrides)
    return {"build": {"make": table}}


def test_make_backend_requires_explicit_enablement(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

    assert select_backend(tmp_path).kind is None
    choice = select_backend(tmp_path, _config())
    assert choice.kind == BACKEND_MAKE
    assert choice.descriptor == "Makefile"


def test_make_adapter_runs_exact_configured_argv_and_collects_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(argv: list[str], *, cwd=None, **_kwargs) -> ProcessResult:
        calls.append((argv, cwd))
        if argv[-1] == "all":
            binary = tmp_path / "build" / "ici-make" / "app"
            binary.write_bytes(b"\x7fELFpayload")
            binary.chmod(0o755)
        stdout = "./test_contract\n" if argv[-1] == "check" else ""
        return ProcessResult(0, stdout, "", 0.01)

    monkeypatch.setattr(adapter, "run_process", fake_run)
    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))

    session = configure(tmp_path, ConfigureOptions(BuildVariant.RELEASE), _config())
    assert session.configured
    assert build(session)
    tests = run_tests(session)

    assert [call[0] for call in calls] == [
        ["make", "configure"],
        ["make", "clean"],
        ["make", "all"],
        ["make", "check"],
    ]
    assert all(cwd == tmp_path for _argv, cwd in calls)
    assert len(tests) == 1 and tests[0].passed


def test_make_adapter_fails_closed_on_ignored_nonzero_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t-false\n", encoding="utf-8")

    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda argv, **_kwargs: ProcessResult(7 if argv[-1] == "all" else 0, "", "", 0.01),
    )
    session = configure(
        tmp_path,
        ConfigureOptions(BuildVariant.RELEASE),
        _config(configure_argv=[], clean_argv=[]),
    )

    assert not build(session)
    assert "did not complete successfully" in session.errors[-1]


def test_make_variant_without_explicit_command_is_not_configured(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

    session = configure(tmp_path, ConfigureOptions(BuildVariant.COVERAGE), _config())

    assert not session.configured
    assert "coverage_build_argv" in session.errors[0]
