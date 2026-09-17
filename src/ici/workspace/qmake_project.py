"""Resolving a declared ``.pro`` to the targets qmake would build.

#212 item: ``SUBDIRS`` projects declare their parts rather than being
discovered — a component that links a qmake build can only be compiled by the
targets the root ``.pro`` actually reaches. This module reads that reach.

Two boundaries keep it honest:

**It reads the file, it does not evaluate qmake.** ``SUBDIRS`` entries,
``.subdir``/``.file`` location overrides and ``.depends`` edges are extracted
from the text; variables like ``$$PWD`` and conditional scopes are reported as
what they are (unresolved, conditional) rather than guessed. Reimplementing
qmake's language would make this file's opinion of the targets diverge from
the real one's.

**It never writes.** A ``.pro`` is the user's file; resolution produces a map
of directories the build would compile, nothing more.

The result is what :mod:`ici.workspace.compile_units` uses to tell "the
database does not cover this component" apart from "qmake would never have
compiled this component — it is not a target of the declared root project".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ici.core.context import CompilationDiagnostic

__all__ = ["QmakeProject", "QmakeTarget", "resolve_qmake_project"]

#: A .pro larger than this is not a project file, it is data — read no more.
_MAX_PRO_BYTES = 512 * 1024

#: Nested ``SUBDIRS`` deeper than this is treated as malformed rather than
#: recursed — a cycle is reported, but a genuinely deep tree stops here too.
_MAX_DEPTH = 8

_ASSIGNMENT_RE_OPERATORS = ("~=", "-=", "+=", "*=", "=")


@dataclass(frozen=True)
class QmakeTarget:
    """One ``SUBDIRS`` entry, resolved to the project file that builds it."""

    name: str
    #: The .pro the entry resolves to, workspace-relative; "" when unresolved.
    project: str
    #: The directory whose sources the target compiles.
    directory: str
    #: Declared under a conditional scope (``unix:SUBDIRS += x``) — qmake may
    #: or may not build it; coverage treats it as a maybe, never a promise.
    conditional: bool
    #: ``.depends`` names — ordering hints qmake uses, recorded for provenance.
    depends: tuple[str, ...]


@dataclass(frozen=True)
class QmakeProject:
    """The targets a root ``.pro`` reaches, and what could not be resolved."""

    definition: str
    targets: tuple[QmakeTarget, ...] = ()
    #: Directories a qmake build of ``definition`` would compile — the root
    #: file's own directory included, since ``SOURCES`` and ``SUBDIRS`` mix.
    directories: tuple[str, ...] = ()
    diagnostics: tuple[CompilationDiagnostic, ...] = ()


def resolve_qmake_project(root: Path, definition: str) -> QmakeProject:
    """Read ``definition``'s ``SUBDIRS`` tree and return what it covers."""

    diagnostics: list[CompilationDiagnostic] = []
    targets: list[QmakeTarget] = []
    directories: list[str] = []
    seen: set[str] = set()

    def visit(project: str, depth: int) -> None:
        canonical = str((root / project).resolve(strict=False))
        if canonical in seen:
            return
        seen.add(canonical)
        if depth > _MAX_DEPTH:
            diagnostics.append(
                _diagnostic(
                    "qmake-subdirs-depth",
                    f"{project}: SUBDIRS nesting deeper than {_MAX_DEPTH} is not followed",
                )
            )
            return
        pro_path = root / project
        try:
            text = pro_path.read_bytes()[: _MAX_PRO_BYTES + 1].decode("utf-8", "replace")
        except OSError:
            diagnostics.append(
                _diagnostic("qmake-project-missing", f"{project}: project file is not readable")
            )
            return
        if len(text.encode("utf-8")) > _MAX_PRO_BYTES:
            diagnostics.append(
                _diagnostic(
                    "qmake-project-large",
                    f"{project}: larger than {_MAX_PRO_BYTES // 1024} KiB — read truncated",
                )
            )
        base = PurePosixPath(project).parent
        directories.append(str(base) if str(base) != "." else ".")
        entries = _subdirs(text)
        modifiers = _modifiers(text, {entry[0] for entry in entries})
        removed = {name for name, op, _ in entries if op == "-="}
        for name, op, conditional in entries:
            if op == "-=" or name in removed:
                continue
            target = _resolve_target(
                root, base, name, modifiers.get(name, {}), conditional, diagnostics
            )
            if target is None:
                continue
            targets.append(target)
            if target.project:
                visit(target.project, depth + 1)

    visit(definition, 0)
    return QmakeProject(
        definition=definition,
        targets=tuple(targets),
        directories=tuple(dict.fromkeys(directories)),
        diagnostics=tuple(diagnostics),
    )


def _diagnostic(code: str, message: str, level: str = "warning") -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


def _logical_lines(text: str) -> list[str]:
    """qmake's line grammar: ``\\`` continues, ``#`` comments to end of line."""

    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        if current.strip():
            lines.append(current.strip())
        current = ""
    if current.strip():
        lines.append(current.strip())
    return lines


def _split_statement(line: str) -> tuple[str, str, str, bool] | None:
    """``[scope:]VAR OP value`` → (var, op, value, conditional)."""

    conditional = False
    statement = line
    if ":" in statement:
        scope, _, rest = statement.partition(":")
        # ``name.subdir = x`` uses ``.`` not ``:``; a scope prefix is what
        # makes the entry conditional.
        if scope.strip() and "=" in rest:
            conditional = True
            statement = rest.strip()
    for op in _ASSIGNMENT_RE_OPERATORS:
        if op in statement:
            var, _, value = statement.partition(op)
            var = var.strip()
            if var and " " not in var and "\t" not in var:
                return var, op, value.strip(), conditional
    return None


def _subdirs(text: str) -> list[tuple[str, str, bool]]:
    """``(name, op, conditional)`` for every ``SUBDIRS`` assignment token."""

    found: list[tuple[str, str, bool]] = []
    for line in _logical_lines(text):
        parsed = _split_statement(line)
        if parsed is None:
            continue
        var, op, value, conditional = parsed
        if var != "SUBDIRS":
            continue
        for token in value.split():
            found.append((token, op, conditional))
    return found


def _modifiers(text: str, names: set[str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """``name.subdir``/``.file``/``.depends`` assignments for known entries."""

    found: dict[str, dict[str, tuple[str, ...]]] = {}
    for line in _logical_lines(text):
        parsed = _split_statement(line)
        if parsed is None:
            continue
        var, _op, value, _conditional = parsed
        if "." not in var:
            continue
        name, _, modifier = var.rpartition(".")
        if name not in names or modifier not in ("subdir", "file", "depends", "target"):
            continue
        found.setdefault(name, {})[modifier] = tuple(value.split())
    return found


def _resolve_target(
    root: Path,
    base: PurePosixPath,
    name: str,
    modifiers: dict[str, tuple[str, ...]],
    conditional: bool,
    diagnostics: list[CompilationDiagnostic],
) -> QmakeTarget | None:
    """One entry to its project file, or a diagnostic naming why not."""

    depends = tuple(
        item for item in modifiers.get("depends", ()) if item not in _ASSIGNMENT_RE_OPERATORS
    )
    if "$$" in name or "${" in name:
        diagnostics.append(
            _diagnostic(
                "qmake-var-unresolved",
                f"{name}: a variable reference is not resolved — qmake's own "
                "expansion decides this target",
            )
        )
        return None
    if name.endswith(".pro") or modifiers.get("file"):
        project = modifiers.get("file", (name,))[0]
        resolved = _norm(base / project)
        return QmakeTarget(
            name=name,
            project=resolved,
            directory=str(PurePosixPath(resolved).parent),
            conditional=conditional,
            depends=depends,
        )
    directory = modifiers.get("subdir", (name,))[0]
    resolved_dir = _norm(base / directory)
    project = _project_in(root, resolved_dir, name, diagnostics)
    return QmakeTarget(
        name=name,
        project=project,
        directory=resolved_dir,
        conditional=conditional,
        depends=depends,
    )


def _project_in(
    root: Path, directory: str, name: str, diagnostics: list[CompilationDiagnostic]
) -> str:
    """The .pro a directory target builds — qmake's convention, kept narrow.

    qmake builds ``<dir>/<dirname>.pro`` first, then the single ``.pro`` the
    directory holds; two or more is an ambiguity this resolves by reporting
    rather than picking.
    """

    target_dir = root / directory
    conventional = f"{directory}/{PurePosixPath(directory).name}.pro"
    if (root / conventional).is_file():
        return conventional
    try:
        projects = sorted(item.name for item in target_dir.glob("*.pro"))
    except OSError:
        projects = []
    if len(projects) == 1:
        return f"{directory}/{projects[0]}"
    if not projects:
        diagnostics.append(
            _diagnostic(
                "qmake-subdir-missing",
                f"{name}: {directory}/ holds no .pro — qmake would skip this target",
            )
        )
    else:
        diagnostics.append(
            _diagnostic(
                "qmake-subdir-ambiguous",
                f"{name}: {directory}/ holds {len(projects)} .pro files — qmake's "
                "choice cannot be reproduced without evaluating it",
            )
        )
    return ""


def _norm(path: PurePosixPath) -> str:
    """Lexical ``..``/``.`` collapse — the filesystem is not consulted."""

    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or "."
