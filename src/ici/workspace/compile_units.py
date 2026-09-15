"""The compile database as an analysis input service, not an instruction to build.

#211 moves the existing bounded parser (:mod:`ici.core.compile_db`) behind the
question the next path actually asks: *for this component's C++ scope, which
translation units have a declared compile invocation, and which do not?*

Three properties are the WP's acceptance criteria, stated here because each is
easy to lose:

**Variants are different facts.** Debug and Release rows for one source carry
different defines and search paths; ``TranslationUnit.variant`` is the parser's
configuration digest so a later entry never silently overwrites an earlier one
and ``verify`` can count them separately.

**Coverage is computed, not assumed.** ``expected`` comes from the component's
inventoried sources, ``covered`` from the database — a database that names half
the TUs is a partial input, and the run says so in ``missing`` rather than
letting a partial read pass as a full one.

**An absent or stale database names its remedy, never performs it.** ``prepare``
lists the declared build units whose configure step would produce the file;
finding nothing, it names the config key that would declare one. Inventing a
configure command — or picking the first ``.pro`` on disk — is exactly the
behaviour SPEC-01 section 4 forbids, so this module returns requirements and
runs nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ici.core.compile_db import load_compilation_context
from ici.core.context import CompilationDiagnostic, CompilationUnit
from ici.domain.workspace import BuildUnit, Component
from ici.workspace.qmake_project import QmakeTarget, resolve_qmake_project

__all__ = ["CompileInputs", "HeaderUse", "TranslationUnit", "load_compile_inputs"]

#: Compilable translation-unit inputs. Headers are deliberately absent — a
#: header is consumed *by* TUs, and ``HeaderUse`` preserves which ones.
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".m", ".mm")
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")

#: Launch wrappers that sit in front of the real compiler in argv[0]. The
#: wrapper is recorded as the launch chain; the compiler identity comes from
#: the first argument that is not one.
_LAUNCHERS = ("ccache", "sccache", "distcc", "icecc", "icecream")

#: `#include "x"` and `#include <x>`. Only quoted/project includes resolve into
#: workspace headers; angle-bracket hits on declared include paths still count.
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')

#: How much of one source file the include scan reads. A header prologue is
#: enough — directives beyond it are rare and the bound keeps a hostile file
#: from making coverage scan a whole translation unit per entry.
_INCLUDE_SCAN_BYTES = 256 * 1024


@dataclass(frozen=True)
class TranslationUnit:
    """One compile invocation, original and normalized side by side."""

    source: str
    directory: str
    #: The command as the database wrote it — wrappers included.
    argv: tuple[str, ...]
    #: The launch wrappers stripped from ``argv`` to find the compiler.
    launchers: tuple[str, ...]
    compiler: str
    #: Which compile of this source this is — Debug and Release differ here.
    variant: str
    output: str
    target: str
    standard: str
    diagnostics: tuple[CompilationDiagnostic, ...]


@dataclass(frozen=True)
class HeaderUse:
    """A scope header and the translation units that include it directly."""

    header: str
    consumers: tuple[str, ...]


@dataclass(frozen=True)
class CompileInputs:
    """The compile-database answer for one component's C++ scope."""

    component_id: str
    database_path: str = ""
    database_digest: str = ""
    origin: str = ""
    units: tuple[TranslationUnit, ...] = ()
    expected: tuple[str, ...] = ()
    covered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    #: Database entries whose source is inside the workspace but outside this
    #: component's scope — visible so a shared build's coverage reads honestly.
    extra: tuple[str, ...] = ()
    #: Database entries whose source lives under a linked build's directory —
    #: moc/uic/rcc output the build generated rather than the user wrote.
    generated: tuple[str, ...] = ()
    #: qmake ``SUBDIRS`` targets the linked build's root project resolves to.
    targets: tuple[QmakeTarget, ...] = ()
    headers: tuple[str, ...] = ()
    header_uses: tuple[HeaderUse, ...] = ()
    diagnostics: tuple[CompilationDiagnostic, ...] = ()
    #: What would produce the missing input — declared build units, or the
    #: config key that would declare one. Requirements, never commands run.
    prepare: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every expected TU has a compile invocation."""

        return bool(self.database_path) and not self.missing


def load_compile_inputs(
    root: Path,
    component: Component,
    scope_files: tuple[str, ...],
    builds: tuple[BuildUnit, ...] = (),
) -> CompileInputs:
    """Resolve the component's compile database and score its coverage.

    ``scope_files`` are the component's inventoried, workspace-relative paths —
    the inventory decides what the component's sources *are*; this service only
    splits them into TUs and headers. The database is looked up where the
    component's declared build units would write it, then at the conventional
    locations under the component root and the workspace root — never at the
    first ``.pro`` found lying around.
    """

    expected = tuple(path for path in scope_files if path.endswith(_SOURCE_SUFFIXES))
    headers = tuple(path for path in scope_files if path.endswith(_HEADER_SUFFIXES))
    linked = [build for build in builds if build.id in component.build_ids]
    build_dirs = tuple(build.directory.rstrip("/") for build in linked)
    targets, target_diagnostics = _qmake_targets(root, component, linked)
    database = _database_path(root, component, builds)
    if database is None:
        return CompileInputs(
            component_id=component.id,
            expected=expected,
            missing=expected,
            targets=targets,
            headers=headers,
            prepare=_prepare(root, component, builds),
            diagnostics=(
                CompilationDiagnostic(
                    code="database-missing",
                    message=(
                        f"{component.id}: no compilation database found; "
                        "C++ checks that read compile invocations cannot run"
                    ),
                    level="warning",
                ),
                *target_diagnostics,
            ),
        )

    context = load_compilation_context(root, {"project": {"compile_database": database}})
    scope_set = set(expected) | set(headers)
    units: list[TranslationUnit] = []
    extra: list[str] = []
    generated: list[str] = []
    generated_diagnostics: list[CompilationDiagnostic] = []
    for unit in context.units:
        if unit.source in scope_set:
            units.append(_to_unit(unit))
        elif build_dirs and _under(unit.source, build_dirs):
            generated.append(unit.source)
            if not (root / unit.source).exists():
                generated_diagnostics.append(
                    CompilationDiagnostic(
                        code="generated-input-missing",
                        message=(
                            f"{unit.source}: the database names a build-generated "
                            "input that is not on disk — the capture is partial or stale"
                        ),
                        level="warning",
                    )
                )
        else:
            extra.append(unit.source)
    covered = tuple(sorted({unit.source for unit in units} & set(expected)))
    missing = tuple(sorted(set(expected) - set(covered)))
    return CompileInputs(
        component_id=component.id,
        database_path=context.database_path or database,
        database_digest=context.database_digest,
        origin=context.origin,
        units=tuple(units),
        expected=expected,
        covered=covered,
        missing=missing,
        extra=tuple(sorted(extra)),
        generated=tuple(sorted(generated)),
        targets=targets,
        headers=headers,
        header_uses=_header_uses(root, units, headers),
        diagnostics=tuple(context.diagnostics) + tuple(target_diagnostics + generated_diagnostics),
        prepare=_prepare(root, component, builds) if missing else (),
    )


def _to_unit(unit: CompilationUnit) -> TranslationUnit:
    """The parser's row as the service's entry — argv kept verbatim."""

    launchers: list[str] = []
    rest = list(unit.argv)
    while rest and PurePosixPath(rest[0]).name.casefold() in _LAUNCHERS:
        launchers.append(rest.pop(0))
    compiler = unit.compiler
    if launchers and rest:
        compiler = PurePosixPath(rest[0]).name
    return TranslationUnit(
        source=unit.source,
        directory=unit.directory,
        argv=unit.argv,
        launchers=tuple(launchers),
        compiler=compiler,
        variant=unit.configuration,
        output=unit.output,
        target=unit.target,
        standard=unit.standard,
        diagnostics=unit.diagnostics,
    )


def _database_path(root: Path, component: Component, builds: tuple[BuildUnit, ...]) -> str | None:
    """The database this component would be compiled by, as a workspace path."""

    candidates: list[str] = []
    for build in builds:
        if build.id in component.build_ids:
            candidates.append(f"{build.directory}/compile_commands.json")
    candidates += [
        f"{component.root}/compile_commands.json",
        f"{component.root}/build/compile_commands.json",
        "compile_commands.json",
        "build/compile_commands.json",
    ]
    for candidate in candidates:
        try:
            if (root / candidate).is_file():
                return candidate
        except OSError:
            continue
    return None


def _prepare(root: Path, component: Component, builds: tuple[BuildUnit, ...]) -> tuple[str, ...]:
    """What would produce the missing database — declared, never invented."""

    suggestions: list[str] = []
    linked = [build for build in builds if build.id in component.build_ids]
    for build in linked:
        if build.prepare_argv:
            suggestions.append(
                f"prepare {build.id}: {' '.join(build.prepare_argv)} (writes {build.directory})"
            )
        else:
            suggestions.append(
                f"build '{build.id}' declares no approved configure command — "
                f"give it a prepare vector or generate {build.directory}/compile_commands.json"
            )
    if not linked:
        suggestions.append(
            f"no build unit is linked to {component.id}; declare [builds.<id>] with "
            "a directory that will hold compile_commands.json and set the component's "
            "build, or generate the database out of band"
        )
    return tuple(suggestions)


def _header_uses(
    root: Path, units: list[TranslationUnit], headers: tuple[str, ...]
) -> tuple[HeaderUse, ...]:
    """Which in-scope headers each covered TU includes, read once per TU.

    Direct includes only — the claim being made is "this TU's text names this
    header", which is auditable; transitive closure is the dependency engine's
    job and is not implied here.
    """

    header_set = set(headers)
    consumers: dict[str, set[str]] = {header: set() for header in headers}
    for unit in units:
        source = root / unit.source
        try:
            with source.open("rb") as handle:
                text = handle.read(_INCLUDE_SCAN_BYTES).decode("utf-8", "replace")
        except OSError:
            continue
        source_dir = PurePosixPath(unit.source).parent
        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if match is None:
                continue
            resolved = _resolve_include(match.group(1), source_dir, header_set)
            if resolved is not None:
                consumers[resolved].add(unit.source)
    return tuple(
        HeaderUse(header=header, consumers=tuple(sorted(consumers[header])))
        for header in headers
        if consumers[header]
    )


def _resolve_include(name: str, source_dir: PurePosixPath, header_set: set[str]) -> str | None:
    """A `#include`'s referent, only when it lands on an in-scope header."""

    candidate = source_dir / name
    normalized = PurePosixPath(str(candidate).replace("\\", "/"))
    # ``..`` and absolute spellings collapse lexically; a header outside the
    # declared scope is not claimed by this component no matter what the text
    # says — external headers stay the inventory's ``external`` role's job.
    parts: list[str] = []
    for part in normalized.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    resolved = "/".join(parts)
    return resolved if resolved in header_set else None


def _under(source: str, directories: tuple[str, ...]) -> bool:
    """Whether a workspace-relative path lives inside one of the directories."""

    return (
        any(
            source == directory or source.startswith(f"{directory}/")
            for directory in directories
            if directory not in ("", ".")
        )
        or "." in directories
    )


def _qmake_targets(
    root: Path,
    component: Component,
    linked: list[BuildUnit],
) -> tuple[tuple[QmakeTarget, ...], list[CompilationDiagnostic]]:
    """What the component's qmake builds would compile — and coverage gaps.

    A component linking a qmake build whose ``SUBDIRS`` tree never reaches the
    component's root is a mislink the coverage numbers alone would hide: the
    database would show zero units for it, indistinguishable from "nothing to
    compile". The diagnostic names the difference.
    """

    targets: list[QmakeTarget] = []
    diagnostics: list[CompilationDiagnostic] = []
    covered_dirs: list[str] = []
    for build in linked:
        if build.system != "qmake" or not build.definition:
            continue
        project = resolve_qmake_project(root, build.definition)
        targets.extend(project.targets)
        diagnostics.extend(project.diagnostics)
        covered_dirs.extend(project.directories)
    component_root = component.root.rstrip("/") or "."
    if covered_dirs and not any(
        component_root == directory or component_root.startswith(f"{directory}/")
        for directory in covered_dirs
    ):
        diagnostics.append(
            CompilationDiagnostic(
                code="qmake-target-missing",
                message=(
                    f"{component.id}: its root is not reached by the declared "
                    "root project's SUBDIRS — qmake would not compile its sources"
                ),
                level="warning",
            )
        )
    return tuple(targets), diagnostics
