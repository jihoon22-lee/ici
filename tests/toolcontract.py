"""Fake executables that record how they were called (WP03 #201, step 2).

The engines under test shell out. Asserting on their *output* checks the parser;
asserting on the *call* checks the contract — that ici passed the argv it meant
to, from the directory it meant to, with the environment preserved, and that it
ran the tool once rather than three times.

The existing stubs in this suite write ``#!/bin/sh\\nexit 0`` and answer none of
those questions. A recorder does.

Two properties are deliberate:

- the shebang is the interpreter currently running the tests, not a name looked
  up on PATH. A recorder that resolved ``python3`` through PATH would be testing
  the machine's PATH, and these tests exist precisely to make claims about what
  ici does to PATH.
- nothing here reads a shell initialisation file. #201 requires the harness not
  to depend on one, and R01 requires ici itself never to source one, so a
  harness that needed a profile could not detect a regression in that rule.

Each invocation appends one JSON line to a log beside the executable, so a tool
called twice leaves two records in order rather than one overwriting the other.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FakeTool", "Invocation", "ToolBox"]

# Written into the recorder. Kept as a module constant so the test that checks
# the recorder needs no shell and the recorder itself stay in one place.
_RECORDER = """\
import json, os, sys
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": dict(os.environ),
    "executable": sys.argv[0],
}
with open(LOG, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\\n")
if STDOUT:
    sys.stdout.write(STDOUT)
if STDERR:
    sys.stderr.write(STDERR)
sys.exit(EXIT_CODE)
"""


@dataclass(frozen=True)
class Invocation:
    """One recorded call."""

    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    executable: str

    def env_value(self, name: str) -> str | None:
        """The variable as the tool saw it, or None when it was not set."""

        return self.env.get(name)

    @property
    def path_entries(self) -> tuple[str, ...]:
        """PATH as the tool saw it, split. The usual subject of a contract test."""

        return tuple(entry for entry in self.env.get("PATH", "").split(os.pathsep) if entry)


class FakeTool:
    """An executable that records its calls and answers as configured."""

    def __init__(
        self,
        directory: Path,
        name: str,
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.name = name
        self.directory = directory
        self.path = directory / name
        self.log = directory / f".{name}.calls.jsonl"
        directory.mkdir(parents=True, exist_ok=True)
        body = (
            f"#!{sys.executable}\n"
            f"LOG = {str(self.log)!r}\n"
            f"STDOUT = {stdout!r}\n"
            f"STDERR = {stderr!r}\n"
            f"EXIT_CODE = {int(exit_code)}\n"
            f"{_RECORDER}"
        )
        self.path.write_text(body, encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)

    @property
    def calls(self) -> list[Invocation]:
        """Every call so far, oldest first.

        A truncated final line is dropped rather than raising: the tool may be
        killed mid-write, and a harness that exploded there would report a
        harness bug where the interesting fact is the kill.
        """

        if not self.log.exists():
            return []
        records: list[Invocation] = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            records.append(
                Invocation(
                    argv=tuple(payload.get("argv", ())),
                    cwd=str(payload.get("cwd", "")),
                    env=dict(payload.get("env", {})),
                    executable=str(payload.get("executable", "")),
                )
            )
        return records

    @property
    def call_count(self) -> int:
        """How many times it ran. The point of the harness for shared providers."""

        return len(self.calls)

    def only_call(self) -> Invocation:
        """The single call, asserting there was exactly one.

        Named rather than ``calls[0]`` so that a test claiming "ici runs this
        once" fails on the second call instead of silently reading the first.
        """

        records = self.calls
        if len(records) != 1:
            raise AssertionError(f"expected {self.name} to run once, saw {len(records)}")
        return records[0]


class ToolBox:
    """A directory of fake tools, ready to be put on PATH."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self._tools: dict[str, FakeTool] = {}

    def add(self, name: str, **kwargs: object) -> FakeTool:
        tool = FakeTool(self.directory, name, **kwargs)  # type: ignore[arg-type]
        self._tools[name] = tool
        return tool

    def __getitem__(self, name: str) -> FakeTool:
        return self._tools[name]

    def prepend_to_path(self, env: dict[str, str] | None = None) -> dict[str, str]:
        """Return a copy of ``env`` with this directory first on PATH.

        A copy, not a mutation of os.environ: a test that changed the real
        environment would leak into whichever test ran next.
        """

        base = dict(os.environ if env is None else env)
        existing = base.get("PATH", "")
        base["PATH"] = (
            f"{self.directory}{os.pathsep}{existing}" if existing else str(self.directory)
        )
        return base
