#!/usr/bin/env python3
"""Frozen legacy `ici verify` lint result on compile databases with refused replays.

A unit's recorded command may be refused before it runs: a secret-bearing option, a linker option,
an extra operand, a response file outside the project. These capture the lint engine result for
databases where every unit is refused and where a refused unit sits beside a replayed or an
uncovered one, with synthetic option values only.

usage: capture_lint_replay_refusals.py <oracle-src> <python> <output.json>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = 'type = "cpp"\n[project]\nname = "p"\n'
SOURCES = ("a", "b", "c")
TOKEN = ["--token", "synthetic-value"]
LINKER = ["-Wl,-rpath,/opt/lib"]
CASES = {
    "operand": [["a", ["synthetic-operand=1"]], ["b", []], ["c", []]],
    "all-refused-secret": [["a", TOKEN], ["b", TOKEN], ["c", TOKEN]],
    "all-refused-linker": [["a", LINKER], ["b", LINKER], ["c", LINKER]],
    "all-refused-response": [
        ["a", ["@/opt/r.txt"]],
        ["b", ["@/opt/r.txt"]],
        ["c", ["@/opt/r.txt"]],
    ],
    "refused-and-uncovered": [["a", TOKEN], ["b", LINKER]],
    "only-refused-unit": [["a", TOKEN]],
    "replayed-refused-uncovered": [["a", []], ["b", TOKEN]],
}


def capture(oracle_src: str, python: str, units: list) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "ici.toml").write_text(CONFIG)
        for name in SOURCES:
            (root / "src" / f"{name}.cpp").write_text("int %s() { return 0; }\n" % name)
        database = [
            {
                "directory": str(root),
                "arguments": ["/usr/bin/g++", "-std=c++17", *extra, "-c", f"src/{name}.cpp"],
                "file": f"src/{name}.cpp",
            }
            for name, extra in units
        ]
        (root / "compile_commands.json").write_text(json.dumps(database))
        completed = subprocess.run(
            [python, "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env={
                "HOME": str(base),
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": oracle_src,
                "GIT_CEILING_DIRECTORIES": str(base),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads((root / "verify_report.json").read_text())
    lint = next(item for item in report["results"] if item["engine_name"] == "lint")
    return {"exit_code": completed.returncode, "lint": lint}


def main() -> None:
    oracle_src, python, output = sys.argv[1:]
    runs = {name: capture(oracle_src, python, units) for name, units in CASES.items()}
    Path(output).write_text(
        json.dumps(
            {"config": CONFIG, "sources": SOURCES, "cases": CASES, "runs": runs},
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
