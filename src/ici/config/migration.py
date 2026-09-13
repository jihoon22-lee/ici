"""What the current configuration files do, before anything is changed.

#203 item 7: the existing XDG, ``dev.toml`` and ``ICI_CONFIG`` behaviour is to
be *explained*, not quietly folded into the new path. SPEC-01 section 3 says why
it needs explaining — those files can overwrite the whole quality policy, and
after ``_deep_merge`` nothing records which one won.

So this reports rather than converts. It reads the same files the stable loader
reads, in the same order, and for every key more than one file sets it names the
winner **and the losers**. That list is the migration: it is exactly the set of
values a user would be surprised by, and there is no way to obtain it from the
loader's output.

Nothing here writes, and nothing here produces a next-path config. Converting is
a separate decision, and doing it from inside a report would be the silent
mixing item 7 forbids.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli

__all__ = ["Contested", "MigrationReport", "report"]


@dataclass(frozen=True)
class Contested:
    """One key that more than one file sets, and who won."""

    key: str
    winner: str
    winning_value: object
    overridden: tuple[tuple[str, object], ...]

    def __str__(self) -> str:
        losers = ", ".join(f"{source}={value!r}" for source, value in self.overridden)
        return f"{self.key}: {self.winner}={self.winning_value!r} overrides {losers}"


@dataclass(frozen=True)
class MigrationReport:
    """Which legacy files are in play, and what they do to each other."""

    sources: tuple[tuple[str, Path], ...]
    contested: tuple[Contested, ...]

    @property
    def policy_is_contested(self) -> bool:
        """Whether any contested key decides whether a check must pass.

        Separated from the rest because this is the subset that can change a
        gate's verdict. A build directory being overridden is a convenience; a
        ``required`` being overridden is the defect SPEC-01 section 3 names.
        """

        return any(_is_policy(entry.key) for entry in self.contested)

    def lines(self) -> tuple[str, ...]:
        """The report as text, for a CLI or a log."""

        if not self.sources:
            return ("No legacy configuration files were found.",)
        found = [f"{label}: {path}" for label, path in self.sources]
        if not self.contested:
            return (*found, "", "No key is set by more than one of them.")
        return (
            *found,
            "",
            "These keys are set in more than one file. The stable loader keeps",
            "the last one and records nothing about the others:",
            *(f"  {entry}" for entry in self.contested),
        )


def report(base: Path, *, environment: dict[str, str] | None = None) -> MigrationReport:
    """Describe the legacy configuration in effect for ``base``.

    ``environment`` is taken as an argument so a test can ask about an
    ``ICI_CONFIG`` without setting one for the whole process.
    """

    env = os.environ if environment is None else environment
    candidates: list[tuple[str, Path]] = [
        ("XDG global", _global_path(env)),
        ("project ici.toml", base / "ici.toml"),
        ("dev.toml", base / "dev.toml"),
    ]
    explicit = env.get("ICI_CONFIG")
    if explicit:
        candidates.append(("ICI_CONFIG", Path(explicit).expanduser()))

    present = [(label, path) for label, path in candidates if path.is_file()]
    return MigrationReport(sources=tuple(present), contested=_contested(present))


def _global_path(env: dict[str, str] | os._Environ[str]) -> Path:
    home = env.get("XDG_CONFIG_HOME")
    base = Path(home) if home else Path(env.get("HOME", "~")).expanduser() / ".config"
    return base / "ici" / "config.toml"


def _contested(sources: list[tuple[str, Path]]) -> tuple[Contested, ...]:
    """Every key set by more than one file, in the loader's own order."""

    seen: dict[str, list[tuple[str, object]]] = {}
    for label, path in sources:
        for key, value in _leaves(_read(path)):
            seen.setdefault(key, []).append((label, value))

    contested: list[Contested] = []
    for key, entries in sorted(seen.items()):
        if len(entries) < 2:
            continue
        # The loader merges in order, so the last file to mention a key wins.
        winner_label, winner_value = entries[-1]
        overridden = tuple((label, value) for label, value in entries[:-1] if value != winner_value)
        if not overridden:
            continue
        contested.append(
            Contested(
                key=key,
                winner=winner_label,
                winning_value=winner_value,
                overridden=overridden,
            )
        )
    return tuple(contested)


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomli.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomli.TOMLDecodeError):
        # A malformed legacy file is the stable loader's error to report. This
        # report is about what wins, and a file that cannot be read wins nothing.
        return {}


def _leaves(document: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for key, value in document.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            found.extend(_leaves(value, dotted))
            continue
        found.append((dotted, value))
    return found


def _is_policy(key: str) -> bool:
    """Whether this key can change whether a check has to pass."""

    tail = key.rsplit(".", 1)[-1]
    return tail in {"required", "enabled", "mode", "threshold", "min_score"}
