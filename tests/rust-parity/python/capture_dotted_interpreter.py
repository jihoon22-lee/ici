#!/usr/bin/env python3
"""Frozen legacy `verify` launched through an interpreter path that spells `..`.

Frozen's launcher execs `$ICI_PYTHON` as given, so `sys.executable` keeps the dots and every
Python tool runs under that spelling. This records per-engine status, evidence and summary, the
exit code, and how test/sanitize spelled the interpreter, for both a dotted and a clean launch.

usage: capture_dotted_interpreter.py <oracle-src> <venv-bin> <output.json>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TREE = {
    "ici.toml": 'type = "python"\n',
    "src/a.py": "def f():\n    return 1\n",
    "tests/test_a.py": (
        'import sys\nsys.path.insert(0, "src")\nimport a\n\n\n'
        "def test_f():\n    assert a.f() == 1\n"
    ),
}


def capture(oracle_src: str, interpreter: str, replace: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        project = base / "project"
        for relative, text in TREE.items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        environment = {
            "HOME": str(base),
            "PATH": "/usr/bin:/bin",
            "ICI_PYTHON": interpreter,
            "PYTHONPATH": oracle_src,
            "GIT_CEILING_DIRECTORIES": str(base),
        }
        completed = subprocess.run(
            [interpreter, "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text())
    engines = {}
    for engine in report["results"]:
        first = [
            record["argv"][0]
            for record in engine.get("tool_evidence") or []
            if record.get("argv") and record["argv"][0].endswith("/python")
        ]
        engines[engine["engine_name"]] = {
            "status": engine["status"],
            "evidence": engine["evidence"],
            "summary": engine["summary"],
            "interpreter": first[0].replace(replace, "<venv>") if first else None,
        }
    return {"exit_code": completed.returncode, "engines": engines}


def main() -> None:
    oracle_src, venv_bin, output = sys.argv[1:]
    venv = os.path.dirname(venv_bin)
    cases = {
        "clean": capture(oracle_src, f"{venv_bin}/python", venv),
        "dotted": capture(oracle_src, f"{venv_bin}/../bin/python", venv),
    }
    Path(output).write_text(json.dumps({"tree": TREE, "cases": cases}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
