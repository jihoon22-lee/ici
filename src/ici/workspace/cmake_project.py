"""Resolving a declared ``CMakeLists.txt`` to what a cmake build would compile.

#213 item: a component that links a cmake build can only be covered by the
directories the root ``CMakeLists.txt`` actually reaches — the same claim
:mod:`ici.workspace.qmake_project` makes for ``SUBDIRS``, expressed in cmake's
vocabulary. The answer feeds the same :class:`CompileInputs` coverage, so a
cmake input and a qmake input land in one provenance shape.

Two boundaries keep it honest, the same ones the qmake reader holds:

**It reads the file, it does not evaluate cmake.** ``add_subdirectory``
entries, ``add_executable``/``add_library`` target names and
``add_dependencies`` edges are extracted from the text; ``${var}`` expansions,
generator expressions and ``include()``d modules are reported as unresolved
rather than guessed. Reimplementing cmake's language — or running it, even in
script mode — would make this file's opinion of the build diverge from the
real one's, and would mean touching the machine for a plan-time answer.

**It never writes.** A ``CMakeLists.txt`` is the user's file; resolution
produces a map of directories a build would compile, nothing more. There is
no configure, no generator pick, no preset interpretation — the user's
toolchain choices stay the user's.

Entries guarded by ``if()``/``elseif()``/``else()`` or written inside a
``foreach``/``function`` block are marked ``conditional`` — coverage treats
them as a maybe, never a promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ici.core.context import CompilationDiagnostic

__all__ = ["CmakeProject", "CmakeTarget", "resolve_cmake_project"]

#: A CMakeLists.txt larger than this is not a project file, it is data.
_MAX_LIST_BYTES = 512 * 1024

#: add_subdirectory chains deeper than this are treated as malformed rather
#: than recursed — a cycle is reported, a genuinely deep tree stops here too.
_MAX_DEPTH = 16

_COMMAND_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.DOTALL)
_COMMENT_RE = re.compile(r"#\[\[.*?]]|#[^\n]*", re.DOTALL)
_BLOCK_OPENERS = {"if", "foreach", "while", "function", "macro"}
_BLOCK_CLOSERS = {"endif", "endforeach", "endwhile", "endfunction", "endmacro"}


@dataclass(frozen=True)
class CmakeTarget:
    """One thing a cmake build would make, resolved to the file declaring it."""

    name: str
    #: The CMakeLists.txt the target was declared in, workspace-relative.
    project: str
    #: The directory whose sources the target compiles.
    directory: str
    #: Declared inside a conditional or parameterized block — cmake may or may
    #: not build it; coverage treats it as a maybe, never a promise.
    conditional: bool
    #: ``add_dependencies`` names — ordering hints, recorded for provenance.
    depends: tuple[str, ...]


@dataclass(frozen=True)
class CmakeTest:
    """One ``add_test`` a build declares — the suite ctest would run."""

    name: str
    #: The CMakeLists.txt the test was declared in, workspace-relative.
    project: str
    #: The directory whose build tree holds the test's ``CTestTestfile``.
    directory: str
    #: Declared inside a conditional block — the build may or may not add it.
    conditional: bool


@dataclass(frozen=True)
class CmakeProject:
    """The directories a root ``CMakeLists.txt`` reaches, and the unresolved."""

    definition: str
    targets: tuple[CmakeTarget, ...] = ()
    #: ``add_test`` declarations — what ctest would run from a built tree.
    tests: tuple[CmakeTest, ...] = ()
    #: Directories a cmake build of ``definition`` would compile — the root
    #: file's own directory included.
    directories: tuple[str, ...] = ()
    diagnostics: tuple[CompilationDiagnostic, ...] = ()


def resolve_cmake_project(root: Path, definition: str) -> CmakeProject:
    """Read ``definition``'s ``add_subdirectory`` tree and return its reach."""

    diagnostics: list[CompilationDiagnostic] = []
    targets: list[CmakeTarget] = []
    tests: list[CmakeTest] = []
    directories: list[str] = []
    dependencies: dict[str, list[str]] = {}
    seen: set[str] = set()

    def visit(lists: str, depth: int, inherited: bool = False) -> None:
        canonical = str((root / lists).resolve(strict=False))
        if canonical in seen:
            return
        seen.add(canonical)
        if depth > _MAX_DEPTH:
            diagnostics.append(
                _diagnostic(
                    "cmake-subdirs-depth",
                    f"{lists}: add_subdirectory nesting deeper than {_MAX_DEPTH} is not followed",
                )
            )
            return
        try:
            text = (root / lists).read_bytes()[: _MAX_LIST_BYTES + 1].decode("utf-8", "replace")
        except OSError:
            diagnostics.append(
                _diagnostic("cmake-project-missing", f"{lists}: project file is not readable")
            )
            return
        if len(text.encode("utf-8")) > _MAX_LIST_BYTES:
            diagnostics.append(
                _diagnostic(
                    "cmake-project-large",
                    f"{lists}: larger than {_MAX_LIST_BYTES // 1024} KiB — read truncated",
                )
            )
        base = PurePosixPath(lists).parent
        base_str = str(base) if str(base) != "." else "."
        directories.append(base_str)
        for name, args, conditional in _commands(text):
            conditional = conditional or inherited
            if name == "add_subdirectory" and args:
                _follow_subdirectory(
                    root, lists, base, args[0], conditional, diagnostics, visit, depth
                )
            elif name in ("add_executable", "add_library") and args:
                _record_target(lists, base_str, args[0], conditional, targets, diagnostics)
            elif name == "add_test" and args:
                _record_test(lists, base_str, args, conditional, tests, diagnostics)
            elif name == "add_dependencies" and len(args) > 1 and _literal(args[0]):
                dependencies.setdefault(args[0], []).extend(
                    dep for dep in args[1:] if _literal(dep)
                )
            elif name == "include" and args:
                diagnostics.append(
                    _diagnostic(
                        "cmake-include-unresolved",
                        f"{lists}: include({args[0]}) is not followed — "
                        "module contents are not evaluated",
                    )
                )

    visit(definition, 0)
    resolved = tuple(
        CmakeTarget(
            name=target.name,
            project=target.project,
            directory=target.directory,
            conditional=target.conditional,
            depends=tuple(dependencies.get(target.name, ())),
        )
        for target in targets
    )
    return CmakeProject(
        definition=definition,
        targets=resolved,
        tests=tuple(tests),
        directories=tuple(dict.fromkeys(directories)),
        diagnostics=tuple(diagnostics),
    )


def _follow_subdirectory(
    root: Path,
    lists: str,
    base: PurePosixPath,
    subdir: str,
    conditional: bool,
    diagnostics: list[CompilationDiagnostic],
    visit,
    depth: int,
) -> None:
    """Follow one literal ``add_subdirectory`` into its own CMakeLists.txt."""

    if not _literal(subdir):
        diagnostics.append(
            _diagnostic(
                "cmake-subdir-unresolved",
                f"{lists}: add_subdirectory({subdir}) is not a literal "
                "path — the expansion is not guessed",
            )
        )
        return
    child_lists = f"{PurePosixPath(str(base / subdir))}/CMakeLists.txt"
    if not (root / child_lists).is_file():
        diagnostics.append(
            _diagnostic(
                "cmake-subdir-unresolved",
                f"{lists}: add_subdirectory({subdir}) has no CMakeLists.txt to follow",
            )
        )
        return
    visit(child_lists, depth + 1, conditional)


def _record_target(
    lists: str,
    base_str: str,
    target_name: str,
    conditional: bool,
    targets: list[CmakeTarget],
    diagnostics: list[CompilationDiagnostic],
) -> None:
    """Record one ``add_executable``/``add_library`` name."""

    if not _literal(target_name):
        diagnostics.append(
            _diagnostic(
                "cmake-target-unresolved",
                f"{lists}: target name {target_name} is not literal",
            )
        )
        return
    targets.append(
        CmakeTarget(
            name=target_name,
            project=lists,
            directory=base_str,
            conditional=conditional,
            depends=(),
        )
    )


def _record_test(
    lists: str,
    base_str: str,
    args: list[str],
    conditional: bool,
    tests: list[CmakeTest],
    diagnostics: list[CompilationDiagnostic],
) -> None:
    """Record one ``add_test`` — both NAME and legacy forms name it first."""

    # ``add_test(NAME n COMMAND …)`` and the legacy ``add_test(n command …)``
    # forms both name the test first — except in NAME form, where the name
    # follows the keyword.
    test_name = (
        args[args.index("NAME") + 1]
        if "NAME" in args and args.index("NAME") + 1 < len(args)
        else args[0]
    )
    if not _literal(test_name):
        diagnostics.append(
            _diagnostic(
                "cmake-test-unresolved",
                f"{lists}: test name {test_name} is not literal",
            )
        )
        return
    tests.append(
        CmakeTest(
            name=test_name,
            project=lists,
            directory=base_str,
            conditional=conditional,
        )
    )


def _diagnostic(code: str, message: str, level: str = "warning") -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


def _literal(token: str) -> bool:
    """Whether a cmake argument is a plain token rather than an expansion."""

    return bool(token) and "$" not in token and "(" not in token and '"' not in token


def _commands(text: str) -> list[tuple[str, list[str], bool]]:
    """``(command, args, conditional)`` for every command in the file.

    Comments — ``#`` line and ``#[[ ]]`` block — are stripped first so a
    commented-out ``add_subdirectory`` never claims reach. A command inside an
    ``if``/``foreach``/``function`` block is conditional: the file does not say
    whether it runs.
    """

    stripped = _COMMENT_RE.sub("", text)
    commands: list[tuple[str, list[str], bool]] = []
    block_depth = 0
    for match in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", stripped
    ):
        name = match.group(1).lower()
        args = match.group(2).split()
        if name in _BLOCK_CLOSERS:
            block_depth = max(0, block_depth - 1)
            continue
        conditional = block_depth > 0
        commands.append((name, args, conditional))
        if name in _BLOCK_OPENERS:
            block_depth += 1
    return commands
