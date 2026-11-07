"""Development-only frozen capture of what an explicit `--result` run writes; never product code.

    python -I capture_result_placement.py

Each case runs the immutable v0.13.0 CLI in a fresh copy of a one-component next workspace
(empty HOME, PATH=/usr/bin:/bin, every Python check but `lint` disabled) and a second, separate
directory that stands for an arbitrary user path outside the project. It records the exit code
and every file the case created in both trees — including anything under `.ici/` — so the
placement of per-run state beside an explicit result path is pinned, not inferred.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUT = ROOT / "tests/rust-parity/runtime/result-placement-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

OFF = ("format", "type", "test", "coverage", "compat-runtime")
CONFIG = (
    'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "fast"\n'
    '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
    + "".join(f'[checks."python.{check}"]\nenabled = false\n' for check in OFF)
)
TREE = {"ici.toml": CONFIG, "app/main.py": "def f():\n    return 1\n"}
OUTSIDE = "@outside"
# name: steps, each an argv after `ici`; `@outside` is replaced by the outside directory.
CASES = {
    "analyze/relative-result": [["analyze", "--result", "out/r.json"]],
    "analyze/outside-result": [["analyze", "--result", f"{OUTSIDE}/r.json"]],
    "next-verify/relative-result": [["next", "verify", "--result", "out/r.json"]],
    "next-report/relative-result": [
        ["next", "verify", "--result", "out/r.json"],
        ["next", "report", "--result", "out/r.json", "--out", "out/page.html"],
    ],
}


def tree(path: Path) -> list[str]:
    return sorted(
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if item.is_file() or item.is_symlink()
    )


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {
        "HOME": str(cwd.parent / "home"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ORACLE / "src"),
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        [PYTHON, "-m", "ici", *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def main() -> None:
    cases = {}
    for name, steps in CASES.items():
        with tempfile.TemporaryDirectory(prefix="ici-placement-") as temp:
            base = Path(temp)
            project, outside = base / "project", base / "outside"
            (base / "home").mkdir()
            outside.mkdir()
            for relative, text in TREE.items():
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            before = set(tree(project))
            exits = []
            for step in steps:
                argv = [part.replace(OUTSIDE, str(outside)) for part in step]
                exits.append(run(argv, project).returncode)
            cases[name] = {
                "steps": steps,
                "exit_codes": exits,
                "project_created": sorted(set(tree(project)) - before),
                "outside_created": tree(outside),
            }
    payload = {"oracle": EXPECTED, "python": sys.version.split()[0], "cases": cases}
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(cases, indent=1))


if __name__ == "__main__":
    os.umask(0o022)
    main()
