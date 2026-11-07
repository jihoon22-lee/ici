#!/usr/bin/env python3
"""Frozen legacy Python tool runs: what pytest and compileall were given and what that meant.

Two facts the native legacy reading must keep:
- `ici test` clears the project's pytest `addopts` (`-o addopts=`), so `-k`, `-x` or an unknown
  option configured there never changes which tests run or the verdict.
- `ici python-compat` hands compileall each selected source's configured source directory
  (`_compile_targets`), so a broken file under that directory that ici's own discovery did not
  select still fails the run.

Each case is a whole fixture description, so the native test builds exactly what ran here.
Argv is recorded with the project root as `<root>` and the interpreter as `<python>`; temporary
paths frozen created for one run are `<tmp>`.

usage: capture_legacy_python_argv.py <oracle-src> <python> <output.json>
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIBRARY = "def one():\n    return 1\n"
TESTS = (
    "import sys\nsys.path.insert(0, 'src')\nimport a\n\n\n"
    "def test_one():\n    assert a.one() == 1\n\n\n"
    "def test_two():\n    assert a.one() == 2\n\n\n"
    "def test_three():\n    assert a.one() == 1\n"
)
BROKEN = "def broken(:\n"


def pyproject(extra: str = "") -> str:
    return f"[project]\nname = 'q'\nversion = '1'\nrequires-python = '>=3.10'\n{extra}"


def config(source_dirs: list[str]) -> str:
    return f"[project]\ntype = 'python'\nsource_dirs = {json.dumps(source_dirs)}\n"


def addopts(option: str | None) -> dict:
    extra = "" if option is None else f"[tool.pytest.ini_options]\naddopts = {json.dumps(option)}\n"
    return {
        "command": "test",
        "files": {
            "src/a.py": LIBRARY,
            "tests/test_a.py": TESTS,
            "pyproject.toml": pyproject(extra),
            "ici.toml": config(["src"]),
        },
    }


def compile_case(files: dict[str, str], source_dirs: list[str]) -> dict:
    return {
        "command": "python-compat",
        "files": {
            "src/a.py": LIBRARY,
            "pyproject.toml": pyproject(),
            "ici.toml": config(source_dirs),
            **files,
        },
    }


CASES = {
    "addopts-none": addopts(None),
    "addopts-deselect-all": addopts("-k nothing_matches"),
    "addopts-stop-first": addopts("-x"),
    "addopts-unknown-option": addopts("--no-such-option"),
    "compile-clean": compile_case({}, ["src"]),
    "compile-unselected-build-broken": compile_case({"src/build/n.py": BROKEN}, ["src"]),
    "compile-vendor-broken": compile_case({"src/vendor/m.py": BROKEN}, ["src"]),
    "compile-pycache-ignored": compile_case({"src/__pycache__/x.py": BROKEN}, ["src"]),
    "compile-two-directories": compile_case({"lib/b.py": LIBRARY}, ["src", "lib"]),
    "compile-missing-directory": compile_case({}, ["src", "missing"]),
    "compile-root-directory": compile_case({"tools/t.py": BROKEN}, ["."]),
    "compile-outside-broken-unread": compile_case({"scripts/s.py": BROKEN}, ["src"]),
}


def normalize(value: str, root: Path, python: str) -> str:
    value = value.replace(str(root), "<root>").replace(python, "<python>")
    return re.sub(r"/tmp/[^/\s'\"]*ici[^/\s'\"]*", "<tmp>", value)


def run(oracle: str, python: str, case: dict, base: Path) -> dict:
    root = base / "project"
    for name, text in case["files"].items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    bin_dir = str(Path(python).parent)
    process = subprocess.run(
        [python, "-m", "ici", case["command"], "--report"],
        cwd=root,
        env={
            "HOME": str(base),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PYTHONPATH": oracle,
            "ICI_PYTHON": python,
        },
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    reports = sorted(root.glob("*_report.json"))
    if len(reports) != 1:
        raise SystemExit(f"expected one report, got {reports}: {process.stderr[-400:]}")
    report = json.loads(reports[0].read_text())
    return {
        "exit": process.returncode,
        "status": report["status"],
        "targets": sorted(
            [
                normalize(target.get("file_path") or "", root, python),
                target["status"],
                target.get("target_name") or "",
            ]
            for target in report.get("targets", [])
        ),
        "findings": sorted(
            [
                finding.get("rule_id") or "",
                normalize(finding.get("file_path") or "", root, python),
                normalize(finding.get("message") or "", root, python),
            ]
            for finding in report.get("findings", [])
        ),
        "tool_evidence": [
            {
                "name": record.get("name"),
                "argv": [normalize(part, root, python) for part in record.get("argv", [])],
                "exit_code": record.get("exit_code"),
            }
            for record in report.get("tool_evidence") or []
        ],
    }


def main() -> None:
    oracle, python, output = sys.argv[1:4]
    result = {}
    for name, case in CASES.items():
        base = Path(tempfile.mkdtemp(prefix="ici-argv-"))
        try:
            result[name] = {**case, "frozen": run(oracle, python, case, base)}
        finally:
            shutil.rmtree(base)
    Path(output).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
