"""What the stable path assumes, and the key that replaces each assumption.

#204 item 7: the department library path and the automatic ``.venv`` preference
come out of the new path, and *"이전 설정은 migration 경고로 연결한다"* — the old
settings are connected to a warning rather than quietly dropped.

A note like this rots. The value it carries is "your build depends on this, and
here is what to write instead", and that stops being true the moment the code it
describes moves. So each entry names the file and the text it is about, and
:func:`stale` re-reads them: a note about code that is no longer there fails a
test instead of misleading someone a year from now.

Nothing here changes the stable path. Removing an assumption people's builds
depend on is a migration, and a migration that happens as a side effect of
adding its own warning is not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ASSUMPTIONS", "Assumption", "stale", "warnings_for"]

_SOURCE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Assumption:
    """One thing the stable path decides without being told."""

    name: str
    file: str
    marker: str
    what: str
    replacement: str

    def __str__(self) -> str:
        return f"{self.file}: {self.what} — in the next path, write {self.replacement}"


ASSUMPTIONS = (
    Assumption(
        name="nas-cpp-library",
        file="core/env.py",
        marker="libs/cpp/ips-core-lib/v1.2.3/x86_64",
        what=("one department's C++ library, pinned to one version, is compiled into ici"),
        replacement="the library path as a declared build or tool path in ici.toml",
    ),
    Assumption(
        name="automatic-venv",
        file="core/env.py",
        marker='project_root / ".venv"',
        what=(
            "a .venv is preferred whether or not the project mentioned one, "
            "which is the first link in the chain that ends at ici's own "
            "interpreter"
        ),
        replacement='[components.<id>.python] executable = ".venv/bin/python"',
    ),
    Assumption(
        name="interpreter-fallback",
        file="engines/test_interpreter.py",
        marker="return [sys.executable]",
        what=(
            "a project with no interpreter of its own is tested with whichever "
            "interpreter ici is running under, and the result does not say so"
        ),
        replacement=(
            "a declared executable; an undeclared one is reported as unresolved "
            "rather than substituted"
        ),
    ),
)


def warnings_for(names: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The migration lines for these assumptions, or all of them."""

    chosen = ASSUMPTIONS if not names else tuple(a for a in ASSUMPTIONS if a.name in names)
    return tuple(str(assumption) for assumption in chosen)


def stale() -> tuple[str, ...]:
    """Assumptions whose code no longer says what this claims.

    Empty is the healthy answer. A non-empty result means either the stable path
    changed — in which case the note needs rewriting — or the note was wrong
    when it was written.
    """

    missing: list[str] = []
    for assumption in ASSUMPTIONS:
        path = _SOURCE / assumption.file
        if not path.is_file():
            missing.append(f"{assumption.name}: {assumption.file} no longer exists")
            continue
        if assumption.marker not in path.read_text(encoding="utf-8"):
            missing.append(f"{assumption.name}: {assumption.file} no longer contains its marker")
    return tuple(missing)
