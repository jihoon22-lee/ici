#!/usr/bin/env python3
"""Frozen legacy `verify` launched through an interpreter path that spells `..`.

Frozen's launcher execs `$ICI_PYTHON` as given, so `sys.executable` keeps the dots and every
Python tool runs under that spelling. This records per-engine status, evidence, summary and finding
fingerprints, the exit code and how test/sanitize spelled the interpreter for both launches, then a
baseline written under one spelling and compared under the other, and the cache across spellings.

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
    "src/a.py": (
        "import subprocess\n\n\ndef f(command):\n    return subprocess.call(command, shell=True)\n"
    ),
    "tests/test_a.py": (
        'import sys\nsys.path.insert(0, "src")\nimport a\n\n\n'
        'def test_f():\n    assert a.f("true") == 0\n'
    ),
}


def run(oracle_src: str, interpreter: str, project: Path, base: Path, *options: str) -> dict:
    environment = {
        "HOME": str(base),
        "PATH": "/usr/bin:/bin",
        "ICI_PYTHON": interpreter,
        "PYTHONPATH": oracle_src,
        "GIT_CEILING_DIRECTORIES": str(base),
    }
    completed = subprocess.run(
        [interpreter, "-m", "ici", "verify", "--report", *options],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads((project / "verify_report.json").read_text())
    report["exit_code"] = completed.returncode
    return report


def facts(report: dict, replace: str) -> dict:
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
            "fingerprints": sorted(item["fingerprint"] for item in engine.get("findings") or []),
            "interpreter": first[0].replace(replace, "<venv>") if first else None,
        }
    return {"exit_code": report["exit_code"], "engines": engines}


def main() -> None:
    oracle_src, venv_bin, output = sys.argv[1:]
    venv = os.path.dirname(venv_bin)
    clean, dotted = f"{venv_bin}/python", f"{venv_bin}/../bin/python"
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        project = base / "project"
        for relative, text in TREE.items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        cases = {
            "clean": facts(run(oracle_src, clean, project, base, "--no-cache"), venv),
            "dotted": facts(run(oracle_src, dotted, project, base, "--no-cache"), venv),
        }
        # A baseline written under one spelling, compared under the other.
        run(oracle_src, clean, project, base, "--no-cache", "--write-baseline", "base.json")
        compared = run(oracle_src, dotted, project, base, "--no-cache", "--baseline", "base.json")
        comparison = compared["baseline_comparison"]
        cases["baseline"] = {
            "exit_code": compared["exit_code"],
            "counts": {
                key: comparison[key]
                for key in ("new_count", "resolved_count", "unchanged_count", "moved_count")
            },
            "gate_failed": comparison["gate_failed"],
        }
        # The cache across spellings: frozen's key covers sys.executable for every engine.
        warm = [run(oracle_src, clean, project, base), run(oracle_src, clean, project, base)]
        other = run(oracle_src, dotted, project, base)
        keys = lambda report: {
            e["engine_name"]: e.get("cache_key") or "" for e in report["results"]
        }
        hits = lambda report: sorted(
            e["engine_name"] for e in report["results"] if e.get("cache_hit")
        )
        cases["cache"] = {
            "warm_same_spelling_hits": hits(warm[1]),
            "other_spelling_hits": hits(other),
            "keyed_engines_with_a_changed_key": sorted(
                name for name, key in keys(warm[1]).items() if key and key != keys(other)[name]
            ),
        }
    Path(output).write_text(
        json.dumps({"tree": TREE, "cases": cases}, indent=1, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
