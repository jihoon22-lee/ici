#!/usr/bin/env python3
"""Which check scopes a `next verify` result moves when one kind of input changes.

Frozen's check record (application/check_scope.py) hashes, per check, the selected files, the
analysis units, externals, linked builds and the task invocation: argv, cwd, provider, input
references, the bytes of the tool configuration files among those inputs (ruff.toml, .ruff.toml,
pyproject.toml, mypy.ini, setup.cfg) and the task's own environment overlay. It does not hash
source contents or the inherited environment. Each case runs `next verify`, changes one kind of
input, runs again and records which checks' `scope_digest` moved, so a native run can be compared
record by record. `capture_policy_identity.py` covers `ici.toml` edits, tool paths and moved
checkouts; this covers the other inputs.

usage: capture_scope_inputs.py <python> <output.json> --frozen <oracle-src> | --native <ici>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "def called(value):\n    if value:\n        return 1\n    return 0\n"
TEST = "import app\n\n\ndef test_called():\n    assert app.called(True) == 1\n"
PYPROJECT = (
    "[project]\nname='fixture'\nversion='1'\nrequires-python='>=3.10'\n"
    "[tool.pytest.ini_options]\npythonpath=['src']\n"
)


def config(python: str) -> str:
    return (
        "schema_version=1\n[workspace]\nname='fixture'\n"
        "[[components]]\nid='app'\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n"
        f"[components.python]\nexecutable={json.dumps(python)}\n"
        "test_tools='project'\ntest_paths=['tests']\ntype_provider='mypy'\n"
    )


# Case: (files written before the second run, extra environment for the second run).
CASES: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "same": ({}, {}),
    "source-content": ({"src/app.py": SOURCE.replace("return 0", "return -1")}, {}),
    "source-added": ({"src/extra.py": "VALUE = 1\n"}, {}),
    "test-content": ({"tests/test_app.py": TEST.replace("== 1", "== 1  # same")}, {}),
    "ruff-toml-added": ({"ruff.toml": "line-length = 100\n"}, {}),
    "pyproject-tool-edit": ({"pyproject.toml": PYPROJECT + "[tool.ruff]\nline-length = 100\n"}, {}),
    "setup-cfg-added": ({"setup.cfg": "[metadata]\nname = fixture\n"}, {}),
    "unrelated-file-added": ({"notes.txt": "hello\n"}, {}),
    "pytest-ini-added": ({"pytest.ini": "[pytest]\nmarkers = slow\n"}, {}),
    "coveragerc-added": ({".coveragerc": "[run]\nbranch = True\n"}, {}),
    "mypy-ini-added": ({"mypy.ini": "[mypy]\nwarn_unused_ignores = True\n"}, {}),
    "inherited-environment": ({}, {"ICI_SCOPE_FIXTURE_VARIABLE": "1"}),
}


def run(tool: list, root: Path, base: Path, oracle: str | None, python: str, extra: dict) -> int:
    product = {"PYTHONPATH": oracle} if oracle else {}
    return subprocess.run(
        [*tool, "next", "verify", "--result", "now.json"],
        cwd=root,
        env={
            "HOME": str(base),
            "PATH": f"{Path(python).parent}:/usr/bin:/bin",
            "GIT_CEILING_DIRECTORIES": str(base),
            **product,
            **extra,
        },
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def scenario(tool: list, python: str, oracle: str | None, name: str) -> dict:
    files, extra = CASES[name]
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "src/app.py").write_text(SOURCE)
        (root / "tests/test_app.py").write_text(TEST)
        (root / "pyproject.toml").write_text(PYPROJECT)
        (root / "ici.toml").write_text(config(python))
        run(tool, root, base, oracle, python, {})
        first = json.loads((root / "now.json").read_text())
        for path, text in files.items():
            (root / path).write_text(text)
        code = run(tool, root, base, oracle, python, extra)
        now = json.loads((root / "now.json").read_text())
    before = {c["check_id"]: c["scope_digest"] for c in first["checks"]}
    after = {c["check_id"]: c["scope_digest"] for c in now["checks"]}
    return {
        "files": files,
        "environment": extra,
        "exit": code,
        "checks": sorted(after),
        "moved_scopes": sorted(c for c in after if before.get(c) != after[c]),
    }


def main() -> None:
    python, output, mode, value = sys.argv[1:5]
    tool = [python, "-m", "ici"] if mode == "--frozen" else [value]
    oracle = value if mode == "--frozen" else None
    results = {name: scenario(tool, python, oracle, name) for name in CASES}
    document = {"source": SOURCE, "test": TEST, "pyproject": PYPROJECT, "cases": results}
    Path(output).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
