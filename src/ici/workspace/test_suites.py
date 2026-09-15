"""Discovering which test suites a component's builds declare.

`ici next` never builds — so a suite is only as real as the artifacts the
project's own build left behind. Discovery therefore works in two steps:

- the build *definition* says which suites should exist — cmake's
  ``add_test`` entries, or qmake subdirs whose ``.pro`` pulls in
  ``QT += testlib`` / ``CONFIG += testcase``
- the build *directory* says whether they were actually built —
  ``CTestTestfile.cmake`` for ctest, the binary itself for a QTest target

A declared suite with no built artifacts is not an empty result, it is a
missing one — the plan marks it blocked so it cannot read as a pass (#219's
"missing suite" case).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ici.domain.workspace import BuildUnit
from ici.workspace.cmake_project import resolve_cmake_project
from ici.workspace.qmake_project import resolve_qmake_project

__all__ = ["TestSuite", "suites_for_build"]


@dataclass(frozen=True)
class TestSuite:
    """One runnable suite bound to the build that produces it."""

    #: Task-id-safe identifier, unique within the component.
    id: str
    #: ``ctest`` — run through the build's ``CTestTestfile``; ``qtest`` — the
    #: target binary itself.
    kind: str
    build_id: str
    #: Build directory, workspace-relative — where the build's artifacts live.
    build_dir: str
    #: ``qtest`` only: the binary's path relative to the build directory.
    #: Empty for ctest, where discovery happens inside the build tree.
    binary: str
    conditional: bool


_TARGET_RE = re.compile(r"^\s*TARGET\s*=(?P<name>[A-Za-z0-9_.-]+)\s*$", re.MULTILINE)
_TESTLIB_RE = re.compile(r"^\s*QT\s*\+=?.*\btestlib\b", re.MULTILINE)
_TESTCASE_RE = re.compile(r"^\s*CONFIG\s*\+=?.*\b(?:testcase|test)\b", re.MULTILINE)
_MAX_PRO_BYTES = 256 * 1024


def suites_for_build(root: Path, build: BuildUnit) -> tuple[TestSuite, ...]:
    """The suites a build declares — built or not, that part is checked later."""

    if build.definition is None:
        return ()
    if build.system == "cmake":
        return _cmake_suites(root, build)
    if build.system == "qmake":
        return _qmake_suites(root, build)
    # make and custom builds declare no test structure ici can read — the
    # suite's absence is reported by the caller, not invented here.
    return ()


def _cmake_suites(root: Path, build: BuildUnit) -> tuple[TestSuite, ...]:
    """One ctest suite per build that declares tests at all.

    ``ctest --test-dir`` runs whatever the generated ``CTestTestfile`` holds,
    so the suite's boundary is the build, not each ``add_test`` line.
    """

    project = resolve_cmake_project(root, build.definition or "")
    if not project.tests:
        return ()
    return (
        TestSuite(
            id=f"{build.id}-ctest",
            kind="ctest",
            build_id=build.id,
            build_dir=build.directory,
            binary="",
            conditional=all(test.conditional for test in project.tests),
        ),
    )


def _qmake_suites(root: Path, build: BuildUnit) -> tuple[TestSuite, ...]:
    """Each ``testlib``-using ``.pro`` is one QTest binary in the build dir."""

    project = resolve_qmake_project(root, build.definition or "")
    suites: list[TestSuite] = []
    for target in project.targets:
        if not target.project:
            continue
        # qmake's default TARGET is the .pro's basename, not the SUBDIRS
        # entry name.
        binary = _qtest_binary(root / target.project, Path(target.project).stem)
        if binary is None:
            continue
        suites.append(
            TestSuite(
                id=f"{build.id}-{target.name}",
                kind="qtest",
                build_id=build.id,
                build_dir=build.directory,
                binary=binary,
                conditional=target.conditional,
            )
        )
    return tuple(suites)


def _qtest_binary(project_file: Path, default_name: str) -> str | None:
    """The binary a ``.pro`` builds, when it declares itself a test.

    ``TARGET =`` may rename the output; nothing else about the binary is
    guessed — a pro file that does not name testlib or testcase is not a
    suite.
    """

    try:
        text = project_file.read_bytes()[: _MAX_PRO_BYTES + 1].decode("utf-8", "replace")
    except OSError:
        return None
    if _TESTLIB_RE.search(text) is None and _TESTCASE_RE.search(text) is None:
        return None
    target = _TARGET_RE.search(text)
    return target.group("name") if target is not None else default_name
