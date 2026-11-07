"""Capture frozen legacy `ici line --report` on documentation-only and monorepo trees.

    PYTHONPATH=<oracle>/src python capture_line_scope.py <out.json>

Each scenario runs the frozen CLI in a fresh git checkout with HOME outside the project and
records the exit code and the engine's status, summary, evidence, targets and per-file
inventory (path, scope, language, counts). Scenarios: a documentation-only project (no Python
or C++ source) and a monorepo whose subprojects own the sources while root files are extras.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCENARIOS = {
    "docs_only": {
        "README.md": "# Title\n\ntext\n",
        "docs/guide.md": "more\n",
    },
    "monorepo_root_extras": {
        "README.md": "# Mono\n",
        "Makefile": "all:\n\techo ok\n",
        "services/api/src/api.py": "def handler():\n    return 1\n",
        "services/web/src/web.py": "# web\nVALUE = 2\n\n",
        "services/web/README.md": "web docs\n",
    },
}
FILE_KEYS = ("path", "scope", "lang", "code", "comment", "blank", "total", "status")


def capture(files):
    with tempfile.TemporaryDirectory() as scratch, tempfile.TemporaryDirectory() as home:
        root = Path(scratch)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        process = subprocess.run(
            [sys.executable, "-m", "ici", "line", "--report"],
            cwd=root,
            env={"PATH": "/usr/bin:/bin", "HOME": home, "PYTHONPATH": os.environ["PYTHONPATH"]},
            capture_output=True,
            check=False,
            timeout=300,
        )
        engine = json.loads((root / "line_report.json").read_text(encoding="utf-8"))
    return {
        "exit_code": process.returncode,
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [[t["file_path"], t["status"], t["message"]] for t in engine["targets"]],
        "all": engine["extra"]["all"],
        "files": [[row[key] for key in FILE_KEYS] for row in engine["extra"]["files_data"]],
    }


def main():
    document = {
        "source": "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "scenarios": SCENARIOS,
        "runs": {name: capture(files) for name, files in SCENARIOS.items()},
    }
    Path(sys.argv[1]).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
