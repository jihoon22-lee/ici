"""Capture frozen v0.13.0 GitHub workflow annotations of a historical `ici verify`.

    python -I capture_legacy_annotations.py <oracle-root> <output.json>

Runs the frozen stable verify with `--report --github-summary` under GITHUB_ACTIONS=true in a
fresh small Python project (empty HOME, PATH without tools) and records the saved v3 suite and
the `::error`/`::warning`/`::notice` lines it printed, with the project root as `<root>`.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
# Source text the frozen analyses flag; written to disk, never executed.
TREE = {
    "src/__init__.py": '"""Package."""\n',
    "src/core.py": (
        "import pickle\nimport subprocess\n\n\ndef load(blob):\n    return pickle.loads(blob)\n\n\n"
        "def run(cmd):\n    return subprocess.call(cmd, shell=True)\n\n\n"
        "def read(path):\n    f = open(path)\n    return f.read()\n"
    ),
    "src/exc.py": "def bad():\n    try:\n        return 1 / 0\n    except:\n        pass\n",
}
ARGV = ["verify", "--report", "--github-summary"]
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in TREE.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {
            "HOME": home,
            "XDG_CONFIG_HOME": home,
            "PATH": os.defpath,
            "GITHUB_ACTIONS": "true",
            "GITHUB_STEP_SUMMARY": str(Path(home) / "summary.md"),
        }
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *ARGV],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        report = (base / "verify_report.json").read_text(encoding="utf-8")
        lines = [
            line.replace(str(base), ROOT)
            for line in completed.stdout.splitlines()
            if line.startswith("::")
        ]
        document = {
            "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
            "python": sys.version.split()[0],
            "tree": TREE,
            "exit_code": completed.returncode,
            "suite": json.loads(report.replace(str(base), ROOT)),
            "annotations": lines,
        }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print(completed.returncode, len(lines))


if __name__ == "__main__":
    main()
