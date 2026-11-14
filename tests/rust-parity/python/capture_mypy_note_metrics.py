"""Capture frozen v0.13.0 `ici type` target metrics for repeated and single mypy notes.

    python -I capture_mypy_note_metrics.py <oracle-root> <mypy> <output.json>

Development only; never called by the Rust product. `legacy-engines.json` keeps only the target
fields it compares, so `_merge_note_target`'s `metrics` (public in the JSON report) live here.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
CASES = {
    "repeated-note": "def f(x: int) -> int:\n    return x\n\n\nreveal_type(f)\nreveal_type(f)\n",
    "single-note": "def f(x: int) -> int:\n    return x\n\n\nreveal_type(f)\n",
}


def run(oracle, mypy, source):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        tools = root / "tools"
        tools.mkdir()
        (tools / "mypy").symlink_to(mypy)
        project = root / "project"
        (project / "src").mkdir(parents=True)
        (project / "src/a.py").write_text(source, encoding="utf-8")
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "type", "--report"],
            cwd=project,
            capture_output=True,
            text=True,
            env={"HOME": str(root), "PATH": f"{tools}:/usr/bin:/bin", "PYTHONHASHSEED": "0"},
            check=False,
        )
        report = json.loads((project / "type_report.json").read_text(encoding="utf-8"))
        engine = report
        return {
            "exit_code": completed.returncode,
            "status": engine["status"],
            "targets": engine["targets"],
        }


def main():
    oracle, mypy, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    version = subprocess.run([mypy, "--version"], capture_output=True, text=True, check=True)
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "mypy": version.stdout.strip(),
        "base": BASE,
        "cases": {
            name: {"source": source, "frozen": run(oracle, mypy, source)}
            for name, source in CASES.items()
        },
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
