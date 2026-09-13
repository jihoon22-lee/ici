"""Configuration problems, reported with the key and origin that caused them.

SPEC-01 section 8 puts config structure errors at exit code 2, and the
acceptance criteria of WP05 #203 ask for errors that name the key *and* its
origin. That pairing is the point: "unknown key: strictness" sends a user
searching four files, while "root.toml: checks.lint.strictness" does not.

Problems are collected rather than thrown one at a time. A file with three
typos should take one run to fix, not three.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from ici.config.origin import Origin

__all__ = ["ConfigProblem", "NextConfigError", "collect", "fail"]


@dataclass(frozen=True)
class ConfigProblem:
    """One thing wrong with a configuration file."""

    message: str
    origin: Origin
    hint: str | None = None

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("config problem message must not be empty")

    def __str__(self) -> str:
        text = f"{self.origin}: {self.message}"
        return f"{text} ({self.hint})" if self.hint else text


class NextConfigError(Exception):
    """Every problem found in one document, not just the first.

    Named for the next path so it cannot be confused with ``ici.config``'s
    stable ``ConfigError``, which the current CLI maps to its own exit code.
    """

    def __init__(self, problems: tuple[ConfigProblem, ...]) -> None:
        if not problems:
            raise ValueError("a config error must carry at least one problem")
        self.problems = problems
        super().__init__("\n".join(str(problem) for problem in problems))


def collect(problems: list[ConfigProblem]) -> None:
    """Raise if anything was collected, otherwise return.

    Keeps the "did we find anything" branch in one place rather than at every
    call site, where it is the branch most likely to be forgotten.
    """

    if problems:
        raise NextConfigError(tuple(problems))


def fail(problems: list[ConfigProblem]) -> NoReturn:
    """Stop now, reporting everything found so far.

    Separate from :func:`collect` so that a reader can stop at a problem that
    makes the rest of the file unreadable — no ``[workspace]`` table means
    there is nothing further to say about it — and so that the type checker
    knows the code after the call is unreachable without an assert to say so.
    """

    raise NextConfigError(tuple(problems))
