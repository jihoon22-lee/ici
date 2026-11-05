"""Capture where frozen `ici next report` resolves a relative --result (frozen `_artifact_root`).

Each case builds a tree under a fresh temporary directory, runs frozen from `cwd` with Git
discovery bounded at the case base, and records the exit code and the stderr line, with the
case base replaced by `<base>`. The native replay is crates/ici-cli/tests/report_commands.rs.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORKSPACE = 'schema_version = 1\n[workspace]\nname = "w"\n'
CASES = {
    "repository-only": ({"repo/.git/": "", "repo/sub/a.py": "x = 1\n"}, "repo/sub"),
    "repository-pyproject": (
        {
            "repo/.git/": "",
            "repo/pyproject.toml": '[project]\nname = "p"\n',
            "repo/sub/a.py": "x = 1\n",
        },
        "repo/sub",
    ),
    "repository-workspace": (
        {"repo/.git/": "", "repo/ici.toml": WORKSPACE, "repo/sub/a.py": "x = 1\n"},
        "repo/sub",
    ),
    "repository-legacy-config": (
        {
            "repo/.git/": "",
            "repo/ici.toml": "[project]\ntype = 'python'\n",
            "repo/sub/a.py": "x = 1\n",
        },
        "repo/sub",
    ),
    "workspace-above-checkout": (
        {"ici.toml": WORKSPACE, "repo/.git/": "", "repo/sub/a.py": "x = 1\n"},
        "repo/sub",
    ),
    "hg-checkout": ({"repo/.hg/": "", "repo/sub/a.py": "x = 1\n"}, "repo/sub"),
}


def main() -> None:
    results = {}
    for name, (tree, cwd) in CASES.items():
        with tempfile.TemporaryDirectory(prefix="ici-artifact-root-") as temporary:
            base = Path(temporary).resolve()
            for relative, text in tree.items():
                path = base / relative
                if relative.endswith("/"):
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [PYTHON, "-m", "ici", "next", "report", "--result", "missing.json"],
                cwd=base / cwd,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(base),
                    "PYTHONPATH": str(ORACLE / "src"),
                    "GIT_CEILING_DIRECTORIES": str(base),
                },
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            results[name] = {
                "tree": tree,
                "cwd": cwd,
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip().replace(str(base), "<base>"),
            }
    json.dump(results, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
