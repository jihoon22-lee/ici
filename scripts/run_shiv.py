#!/usr/bin/env python3
"""Run shiv with deterministic ordering for its embedded bootstrap files."""

from __future__ import annotations

import runpy
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from shiv import builder  # type: ignore[import-untyped]

_unsorted_package_files = builder.iter_package_files


def _sorted_package_files(package: str | ModuleType) -> Iterator[tuple[Path, str]]:
    """Stabilize the private bootstrap-resource iterator used by shiv 1.0.8."""

    resources = list(_unsorted_package_files(package))
    resources.sort(key=lambda item: item[1])
    yield from resources


def main() -> None:
    """Patch the known nondeterministic boundary, then delegate to shiv's CLI."""

    builder.iter_package_files = _sorted_package_files
    runpy.run_module("shiv", run_name="__main__")


if __name__ == "__main__":
    main()
