"""Behavioral contracts for the pinned shiv bootstrap-order adapter."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_shiv.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_shiv", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_shiv = _load_module()


def test_bootstrap_resources_are_sorted_by_archive_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = [
        (tmp_path / "third.py", "interpreter.py"),
        (tmp_path / "first.py", "environment.py"),
        (tmp_path / "second.py", "filelock.py"),
    ]

    def unordered(_package: object) -> Iterator[tuple[Path, str]]:
        yield from resources

    monkeypatch.setattr(run_shiv, "_unsorted_package_files", unordered)

    assert [name for _, name in run_shiv._sorted_package_files("shiv.bootstrap")] == [
        "environment.py",
        "filelock.py",
        "interpreter.py",
    ]


def test_main_patches_builder_before_delegating_to_shiv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        run_shiv.builder,
        "iter_package_files",
        run_shiv.builder.iter_package_files,
    )

    def record_run(module: str, *, run_name: str) -> None:
        assert run_shiv.builder.iter_package_files is run_shiv._sorted_package_files
        calls.append((module, run_name))

    monkeypatch.setattr(run_shiv.runpy, "run_module", record_run)

    run_shiv.main()

    assert calls == [("shiv", "__main__")]
