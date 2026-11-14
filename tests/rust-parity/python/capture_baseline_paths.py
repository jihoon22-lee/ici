#!/usr/bin/env python3
"""Frozen legacy `ici verify` on baseline paths: accepted, refused and refused after comparing.

usage: capture_baseline_paths.py <oracle-src> <python> <output.json>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = 'type = "python"\n[project]\nname = "p"\n'
CLEAN = "def a():\n    return 1\n"
# A finding the fail-on-new gate counts.
REGRESSED = "def a(value):\n    return eval(value)\n"
RUN = ["verify", "--no-cache"]
CASES = {
    "in-place": {"seed": "clean", "args": ["--baseline", "b.json", "--write-baseline", "b.json"]},
    "in-place-failed-gate": {
        "seed": "regressed",
        "args": ["--baseline", "b.json", "--fail-on-new", "--write-baseline", "b.json"],
    },
    "failed-gate-other-output": {
        "seed": "regressed",
        "args": ["--baseline", "b.json", "--fail-on-new", "--write-baseline", "c.json"],
    },
    "no-baseline-for-gate": {"args": ["--fail-on-new"]},
    "baseline-missing": {"args": ["--baseline", "none.json"]},
    "baseline-wrong-schema": {
        "files": {"b.json": '{"schema_version":"x"}'},
        "args": ["--baseline", "b.json"],
    },
    "baseline-no-schema": {"files": {"b.json": "{}"}, "args": ["--baseline", "b.json"]},
    "baseline-bad-json": {"files": {"b.json": "{"}, "args": ["--baseline", "b.json"]},
    "baseline-outside": {"args": ["--baseline", "../x.json"]},
    "write-outside": {"args": ["--write-baseline", "../x.json"]},
    "write-is-directory": {"directories": ["bdir"], "args": ["--write-baseline", "bdir"]},
    "write-parent-is-file": {
        "files": {"afile": "x"},
        "args": ["--write-baseline", "afile/b.json"],
    },
    "write-creates-parents": {"args": ["--write-baseline", "new/sub/b.json"]},
    "write-over-foreign-file": {
        "files": {"notes.txt": "kept\n"},
        "args": ["--write-baseline", "notes.txt"],
    },
    "write-over-config": {"args": ["--write-baseline", "ici.toml"]},
    "write-over-source": {"args": ["--write-baseline", "src/a.py"]},
    "write-over-report": {"args": ["--report", "--write-baseline", "verify_report.json"]},
}


def capture(oracle_src: str, python: str, case: dict) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "src/a.py").write_text(CLEAN)
        (root / "ici.toml").write_text(CONFIG)
        env = {
            "HOME": str(base),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": oracle_src,
            "GIT_CEILING_DIRECTORIES": str(base),
        }

        def run(args: list) -> subprocess.CompletedProcess:
            return subprocess.run(
                [python, "-m", "ici", *args],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        if "seed" in case:
            run([*RUN, "--write-baseline", "b.json"])
            if case["seed"] == "regressed":
                (root / "src/a.py").write_text(REGRESSED)
        for name in case.get("directories", ()):
            (root / name).mkdir()
        for name, text in case.get("files", {}).items():
            (root / name).write_text(text)
        watched = (
            "b.json",
            "c.json",
            "ici.toml",
            "src/a.py",
            "new/sub/b.json",
            "verify_report.json",
            "notes.txt",
        )
        before = {name: (root / name).read_bytes() for name in watched if (root / name).is_file()}
        done = run([*RUN, *case["args"]])
        written = sorted(
            name
            for name in watched
            if (root / name).is_file() and (root / name).read_bytes() != before.get(name)
        )
        error = [
            line.replace(str(root), "<root>")
            for line in done.stderr.splitlines()
            if line.startswith("Baseline error: ")
        ]
    return {"exit": done.returncode, "written": written, "error": error}


def main() -> None:
    oracle_src, python, output = sys.argv[1:4]
    results = {}
    for name, case in CASES.items():
        results[name] = {**case, **capture(oracle_src, python, case)}
    document = {"config": CONFIG, "clean": CLEAN, "regressed": REGRESSED, "cases": results}
    Path(output).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
