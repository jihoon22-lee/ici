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


def test_make_clean_removes_only_ici_gcov_output_before_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reused Make shadow clears ici output without broad deletion."""

    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    shadow = tmp_path / "build" / "ici-make"
    output = shadow / "ici-gcov"
    output.mkdir(parents=True)
    (output / "stale.gcov").write_text("stale\n", encoding="utf-8")
    keep = shadow / "project-output.txt"
    keep.write_text("keep\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))

    def _run(argv, **_kwargs):
        calls.append(argv)
        if argv[-1] == "clean":
            assert not output.exists()
            assert keep.exists()
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(adapter, "run_process", _run)
    session = configure(
        tmp_path,
        ConfigureOptions(BuildVariant.RELEASE),
        _config(configure_argv=[]),
    )

    assert build(session)
    assert calls == [["make", "clean"], ["make", "all"]]
    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_make_clean_rejects_ici_gcov_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink at the exact analyzer-output path is never followed or removed."""

    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    shadow = tmp_path / "build" / "ici-make"
    shadow.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    (shadow / "ici-gcov").symlink_to(outside, target_is_directory=True)
    calls: list[list[str]] = []

    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda argv, **_kwargs: calls.append(argv) or ProcessResult(0, "", "", 0.01),
    )
    session = configure(
        tmp_path,
        ConfigureOptions(BuildVariant.RELEASE),
        _config(configure_argv=[]),
    )

    assert not build(session)
    assert calls == []
    assert (shadow / "ici-gcov").is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert "symlinked ici-gcov" in session.errors[-1]


def test_make_clean_rejects_non_directory_ici_gcov_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file at the analyzer-output path is not eligible for deletion."""

    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    shadow = tmp_path / "build" / "ici-make"
    shadow.mkdir(parents=True)
    output = shadow / "ici-gcov"
    output.write_text("not a directory\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda argv, **_kwargs: calls.append(argv) or ProcessResult(0, "", "", 0.01),
    )
    session = configure(
        tmp_path,
        ConfigureOptions(BuildVariant.RELEASE),
        _config(configure_argv=[]),
    )

    assert not build(session)
    assert calls == []
    assert output.is_file()
    assert "non-directory ici-gcov" in session.errors[-1]


def test_make_clean_fails_closed_when_ici_gcov_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed analyzer-output removal must prevent the project clean."""

    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    shadow = tmp_path / "build" / "ici-make"
    output = shadow / "ici-gcov"
    output.mkdir(parents=True)
    calls: list[list[str]] = []

    monkeypatch.setattr(adapter, "resolved_argv", lambda argv: list(argv))

    def _fail_rmtree(_path: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(adapter.shutil, "rmtree", _fail_rmtree)
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda argv, **_kwargs: calls.append(argv) or ProcessResult(0, "", "", 0.01),
    )
    session = configure(
        tmp_path,
        ConfigureOptions(BuildVariant.RELEASE),
        _config(configure_argv=[]),
    )

    assert not build(session)
    assert calls == []
    assert output.is_dir()
    assert "ici-gcov cleanup failed" in session.errors[-1]
    assert "permission denied" in session.errors[-1]


def test_make_variant_without_explicit_command_is_not_configured(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

    session = configure(tmp_path, ConfigureOptions(BuildVariant.COVERAGE), _config())

    assert not session.configured
    assert "coverage_build_argv" in session.errors[0]
