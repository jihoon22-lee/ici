"""Reading a TOML table against a declared set of keys.

Every section of the schema needs the same four things: coerce a value to a
type, remember where it came from, complain about a key nobody declared, and
keep going so one run reports every problem. Writing that per section is how
sections drift — one forgets the unknown-key check, and a typo in it silently
does nothing for a release.

So a section declares what it knows by asking for it, and :meth:`Table.done`
reports whatever is left. A key that is never asked for is an unknown key by
construction; there is no separate list to keep in step.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

from ici.config.errors import ConfigProblem
from ici.config.origin import Origin, Sourced
from ici.config.paths import DeclaredPath, Executable, SourceGlob

__all__ = ["Table"]


class Table:
    """One TOML table, read key by key against what a section declares."""

    def __init__(self, data: Mapping[str, Any], origin: Origin, problems: list[ConfigProblem]):
        self._data = dict(data)
        self._origin = origin
        self._problems = problems
        self._seen: set[str] = set()

    @property
    def origin(self) -> Origin:
        return self._origin

    def _take(self, key: str) -> tuple[Any, Origin] | None:
        self._seen.add(key)
        if key not in self._data:
            return None
        return self._data[key], self._origin.child(key)

    def _wrong_type(self, key: str, origin: Origin, expected: str, value: object) -> None:
        self._problems.append(
            ConfigProblem(
                f"{key} must be {expected}, not {type(value).__name__}",
                origin,
            )
        )

    def text(self, key: str) -> Sourced[str] | None:
        taken = self._take(key)
        if taken is None:
            return None
        value, origin = taken
        if not isinstance(value, str):
            self._wrong_type(key, origin, "a string", value)
            return None
        return Sourced(value=value, origin=origin)

    def flag(self, key: str) -> Sourced[bool] | None:
        taken = self._take(key)
        if taken is None:
            return None
        value, origin = taken
        if not isinstance(value, bool):
            self._wrong_type(key, origin, "true or false", value)
            return None
        return Sourced(value=value, origin=origin)

    def integer(self, key: str) -> Sourced[int] | None:
        taken = self._take(key)
        if taken is None:
            return None
        value, origin = taken
        # bool is an int in Python; accepting it here would let schema_version
        # = true through as 1.
        if isinstance(value, bool) or not isinstance(value, int):
            self._wrong_type(key, origin, "an integer", value)
            return None
        return Sourced(value=value, origin=origin)

    def text_list(self, key: str) -> Sourced[tuple[str, ...]] | None:
        taken = self._take(key)
        if taken is None:
            return None
        value, origin = taken
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            self._wrong_type(key, origin, "a list of strings", value)
            return None
        return Sourced(value=tuple(value), origin=origin)

    def declared_path(self, key: str) -> DeclaredPath | None:
        found = self.text(key)
        return None if found is None else DeclaredPath(raw=found.value, origin=found.origin)

    def executable(self, key: str) -> Executable | None:
        found = self.text(key)
        if found is None:
            return None
        if not found.value:
            self._problems.append(ConfigProblem(f"{key} must not be empty", found.origin))
            return None
        return Executable(raw=found.value, origin=found.origin)

    def globs(self, key: str) -> tuple[SourceGlob, ...]:
        found = self.text_list(key)
        if found is None:
            return ()
        patterns: list[SourceGlob] = []
        for index, pattern in enumerate(found.value):
            if not pattern:
                self._problems.append(
                    ConfigProblem("a source glob must not be empty", found.origin.item(index))
                )
                continue
            patterns.append(SourceGlob(raw=pattern, origin=found.origin.item(index)))
        return tuple(patterns)

    def table(self, key: str) -> Table | None:
        taken = self._take(key)
        if taken is None:
            return None
        value, origin = taken
        if not isinstance(value, dict):
            self._wrong_type(key, origin, "a table", value)
            return None
        return Table(value, origin, self._problems)

    def tables(self, key: str) -> tuple[Table, ...]:
        """A table of tables, as ``[checks.line]`` and ``[builds.native]`` are."""

        found = self.table(key)
        if found is None:
            return ()
        return found.children()

    def children(self) -> tuple[Table, ...]:
        """Every entry of this table, as a table. Marks them all as read."""

        result: list[Table] = []
        for name, value in self._data.items():
            self._seen.add(name)
            origin = self._origin.child(name)
            if not isinstance(value, dict):
                self._wrong_type(name, origin, "a table", value)
                continue
            result.append(Table(value, origin, self._problems))
        return tuple(result)

    def array_of_tables(self, key: str) -> tuple[Table, ...]:
        """``[[components]]``, each entry with its index in the origin."""

        taken = self._take(key)
        if taken is None:
            return ()
        value, origin = taken
        if not isinstance(value, list):
            self._wrong_type(key, origin, "an array of tables", value)
            return ()
        result: list[Table] = []
        for index, item in enumerate(value):
            entry = origin.item(index)
            if not isinstance(item, dict):
                self._problems.append(ConfigProblem(f"{key} entries must be tables", entry))
                continue
            result.append(Table(item, entry, self._problems))
        return tuple(result)

    def name(self) -> str:
        """The last segment of this table's key, which is its id in the schema."""

        return self._origin.key.rsplit(".", 1)[-1]

    def has(self, key: str) -> bool:
        """Whether a key is present, without consuming it.

        Used where presence itself is the question — a root entry carrying both
        an inline definition and a ``config`` reference is an error about the
        combination, not about either key.
        """

        return key in self._data

    def done(self, known: tuple[str, ...] = ()) -> None:
        """Report every key nobody asked for.

        ``known`` names keys read elsewhere, for the few tables whose body is
        interpreted by more than one reader.
        """

        for key in self._data:
            if key in self._seen or key in known:
                continue
            self._problems.append(
                ConfigProblem("unknown key", self._origin.child(key), hint=self._suggest(key))
            )

    def _suggest(self, key: str) -> str | None:
        candidates = difflib.get_close_matches(key, sorted(self._seen), n=1)
        return f"did you mean {candidates[0]}?" if candidates else None
