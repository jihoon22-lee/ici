"""Capture frozen v0.13.0 plan announcements and engine subcommands on a `[workspace]` root.

    python -I capture_plan_and_engines.py <oracle-root> <output.json>

Each case runs the frozen CLI once in a fresh copy of a small `[workspace]` project (empty
HOME, PATH without tools) and records exit code, stdout and stderr with the project root as
`<root>`: `ici next plan` and `ici analyze --plan` in text and `--json` mode and with nothing
selected, and historical engine subcommands, which frozen refuses on a next workspace.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = (
    'schema_version = 1\n[workspace]\nname = "demo"\n'
    '[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n'
    + "".join(
        f'[checks."python.{check}"]\nenabled = false\n' for check in ("lint", "format", "type")
    )
)
TREE = {"ici.toml": CONFIG, "main.py": "def hello():\n    return 1\n"}
CASES = {
    "plan_text": ["next", "plan"],
    "plan_json": ["next", "plan", "--json"],
    "plan_nothing_selected": ["next", "plan", "--cpp"],
    "analyze_plan_text": ["analyze", "--plan"],
    "analyze_plan_json": ["analyze", "--plan", "--json"],
    "analyze_plan_nothing_selected": ["analyze", "--plan", "--language", "cpp"],
    "engine_line": ["line"],
    "engine_lint_report": ["lint", "--report"],
    "engine_complexity": ["complexity"],
    "engine_test": ["test"],
}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    cases = {}
    for name, argv in CASES.items():
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "project"
            for file, text in TREE.items():
                (base / file).parent.mkdir(parents=True, exist_ok=True)
                (base / file).write_text(text, encoding="utf-8")
            (base / ".git").mkdir()
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
                cwd=base,
                capture_output=True,
                text=True,
                env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath},
                check=False,
            )
            written = sorted(
                str(path.relative_to(base))
                for path in base.rglob("*")
                if path.is_file() and str(path.relative_to(base)) not in TREE
            )
            cases[name] = {
                "argv": argv,
                "exit_code": completed.returncode,
                "stdout": completed.stdout.replace(str(base), ROOT),
                "stderr": completed.stderr.replace(str(base), ROOT),
                "written": written,
            }
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "tree": TREE,
        "cases": cases,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
