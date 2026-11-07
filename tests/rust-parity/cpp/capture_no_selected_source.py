"""Capture frozen v0.13.0 saved reports for projects where no source is selected.

    python -I capture_no_selected_source.py <oracle-root> <output.json>

Development only; never called by the Rust product. No `ici.toml` and nothing under a source
directory, with an empty HOME and VCS discovery bounded by the project's `.git`. The saved report
is recorded with the project root as `<root>`; `legacy_engine_extras.rs` compares it whole.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CASES = {
    "python-at-root": (
        {"app.py": "def main():\n    return 1\n"},
        ("verify", "--no-cache", "--report"),
    ),
    "python-at-root-deep": (
        {"app.py": "def main():\n    return 1\n"},
        ("verify", "--profile", "deep", "--no-cache", "--report"),
    ),
    "cpp-at-root": (
        {"app.cpp": "int main() { return 0; }\n"},
        ("verify", "--no-cache", "--report"),
    ),
    "cpp-at-root-deep": (
        {"app.cpp": "int main() { return 0; }\n"},
        ("verify", "--profile", "deep", "--no-cache", "--report"),
    ),
    "declared-cpp-without-sources": (
        {"ici.toml": '[project]\ntype = "cpp"\n', "app.txt": "text\n"},
        ("verify", "--no-cache", "--report"),
    ),
    "empty-project": ({}, ("verify", "--no-cache", "--report")),
    "text-only": (
        {"README.md": "# readme\n"},
        ("verify", "--profile", "deep", "--no-cache", "--report"),
    ),
    "empty-source-directory": ({"src/.keep": ""}, ("verify", "--no-cache", "--report")),
}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {"oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188", "cases": {}}
    for name, (tree, argv) in CASES.items():
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "project"
            base.mkdir()
            (base / ".git").mkdir()
            for path, text in tree.items():
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
                cwd=base,
                capture_output=True,
                text=True,
                env={
                    "HOME": home,
                    "XDG_CONFIG_HOME": home,
                    "PATH": "/usr/bin:/bin",
                    "PYTHONHASHSEED": "0",
                    "LC_ALL": "C.UTF-8",
                    "GIT_CEILING_DIRECTORIES": temporary,
                },
                check=False,
            )
            text = (base / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(base), "<root>"))
        document["cases"][name] = {
            "tree": tree,
            "argv": list(argv),
            "exit_code": completed.returncode,
            "report": report,
        }
        print(
            name, completed.returncode, [(r["engine_name"], r["status"]) for r in report["results"]]
        )
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
