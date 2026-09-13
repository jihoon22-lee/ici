"""``ici init``: propose a configuration, change nothing.

#203 item 5 lists what init must not do — no overwrite, no package install, no
source, no qmake configure, no test run — and the acceptance criterion states
the positive form: it creates the ici config and leaves the project alone.

So the shape here is a *proposal*. :func:`propose` only reads, and returns the
text it would write along with what it could not decide. Writing is a separate
call that refuses an existing file unless told otherwise. That split is what
makes "init changed nothing" checkable rather than promised: the reading half
has no way to write.

Two things it deliberately will not guess:

- **which build to use when a repository holds more than one.** SPEC-01 section
  6 forbids picking the first ``.pro``; a build chosen by directory order is a
  build nobody decided on, and the analysis it produces looks exactly like one
  somebody did decide.
- **what a tool's own config says.** SPEC-01 section 7 keeps Ruff, pytest and
  mypy discovery to those tools. init records that a file exists and where, and
  does not copy a single setting out of it — a flattened copy is a second source
  of truth that drifts the first time someone edits the original.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Candidate", "Proposal", "propose", "write"]

# Enough to recognise a language without reading source. Extended deliberately
# slowly: a wrong guess here becomes a config the user has to undo.
_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
_PYTHON_SUFFIXES = (".py",)
_CPP_SUFFIXES = (".cpp", ".cc", ".cxx", ".hpp", ".h")
_BUILD_FILES = ("CMakeLists.txt", "*.pro")

# Config files that belong to another tool. Referenced, never read into ici's
# own settings (SPEC-01 section 7).
_FOREIGN_CONFIGS = (
    "pyproject.toml",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    "pytest.ini",
    "tox.ini",
)

_IGNORED_DIRECTORIES = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
)


@dataclass(frozen=True)
class Candidate:
    """A component init believes it found, and what made it think so."""

    component_id: str
    root: str
    languages: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Proposal:
    """What init would write, and what it refused to decide."""

    text: str
    candidates: tuple[Candidate, ...]
    undecided: tuple[str, ...] = ()
    referenced_tool_configs: tuple[str, ...] = ()
    existing: Path | None = None

    @property
    def would_overwrite(self) -> bool:
        return self.existing is not None


def propose(root: Path) -> Proposal:
    """Read the tree and describe the configuration it suggests.

    Reads. Does not write, install, configure or run anything — which is why
    this returns text rather than taking a destination.
    """

    root = root.resolve()
    languages = _languages(root)
    builds = _build_files(root)
    tool_configs = tuple(name for name in _FOREIGN_CONFIGS if (root / name).is_file())

    candidate = Candidate(
        component_id=root.name or "workspace",
        root=".",
        languages=languages,
        evidence=_evidence(root, languages),
    )
    undecided = _undecided(builds)
    existing = root / "ici.toml"
    return Proposal(
        text=_render(candidate, undecided=undecided, tool_configs=tool_configs),
        candidates=(candidate,) if languages else (),
        undecided=undecided,
        referenced_tool_configs=tool_configs,
        existing=existing if existing.is_file() else None,
    )


def write(proposal: Proposal, path: Path, *, overwrite: bool = False) -> Path:
    """Write the proposal, refusing an existing file unless told twice.

    ``overwrite`` has no default that writes. #203 item 5 forbids automatic
    overwrite, and a default of ``True`` would make every accidental rerun a
    silent loss of whatever the user had configured.
    """

    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass overwrite=True to replace it")
    path.write_text(proposal.text, encoding="utf-8")
    return path


def _languages(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    if any((root / marker).is_file() for marker in _PYTHON_MARKERS) or _has_suffix(
        root, _PYTHON_SUFFIXES
    ):
        found.append("python")
    if _has_suffix(root, _CPP_SUFFIXES):
        found.append("cpp")
    return tuple(found)


def _has_suffix(root: Path, suffixes: tuple[str, ...]) -> bool:
    return any(True for _ in _sources(root, suffixes))


def _sources(root: Path, suffixes: tuple[str, ...]):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if _IGNORED_DIRECTORIES.intersection(path.relative_to(root).parts):
            continue
        yield path


def _evidence(root: Path, languages: tuple[str, ...]) -> tuple[str, ...]:
    """Why init thinks this, in the user's own filenames."""

    found: list[str] = []
    for marker in _PYTHON_MARKERS:
        if (root / marker).is_file():
            found.append(marker)
    if "cpp" in languages:
        first = next(_sources(root, _CPP_SUFFIXES), None)
        if first is not None:
            found.append(str(first.relative_to(root)))
    return tuple(found)


def _build_files(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    for pattern in _BUILD_FILES:
        for path in sorted(root.rglob(pattern)):
            if _IGNORED_DIRECTORIES.intersection(path.relative_to(root).parts):
                continue
            found.append(str(path.relative_to(root)))
    return tuple(found)


def _undecided(builds: tuple[str, ...]) -> tuple[str, ...]:
    """What init found but will not choose between.

    One build file is not a decision — it is the only option, so naming it is
    reporting rather than guessing. Two or more is a choice, and SPEC-01
    section 6 says the choice is the user's.
    """

    if len(builds) < 2:
        return ()
    return (
        "more than one build file was found, so no [builds] entry was written: "
        + ", ".join(builds),
    )


def _render(
    candidate: Candidate, *, undecided: tuple[str, ...], tool_configs: tuple[str, ...]
) -> str:
    lines = [
        "# Written by `ici init`. Nothing else in this project was changed.",
        "schema_version = 1",
        "",
        "[workspace]",
        f'name = "{candidate.component_id}"',
        'profile = "standard"',
        "",
    ]
    if tool_configs:
        lines += [
            "# These belong to their own tools. ici records that they exist and",
            "# reads none of their settings — a copy here would be a second source",
            "# of truth that drifts the first time the original is edited.",
            *(f"#   {name}" for name in tool_configs),
            "",
        ]
    for note in undecided:
        lines += [
            f"# UNDECIDED: {note}",
            "# Add a [builds.<id>] entry naming the one you want.",
            "",
        ]
    if not candidate.languages:
        lines += [
            "# No Python or C++ sources were found, so no component was written.",
            "# Add one with an id, a root and a languages list.",
            "",
        ]
        return "\n".join(lines)

    languages = ", ".join(f'"{language}"' for language in candidate.languages)
    lines += [
        "[[components]]",
        f'id = "{candidate.component_id}"',
        f'root = "{candidate.root}"',
        f"languages = [{languages}]",
        "",
    ]
    return "\n".join(lines)
