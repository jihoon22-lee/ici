"""Capture frozen v0.13.0 saved reports for a legacy `type = "cpp"` project with Python sources.

    python -I capture_cpp_declared_python.py <oracle-root> <output.json>

Development only; never called by the Rust product. Frozen `project_python_sources` ignores the
declared type, so Python files under `source_dirs` join the Python engines of a C++ project. An
empty HOME and VCS discovery bounded by the project's `.git`; the saved report is recorded with
the project root as `<root>`, and `legacy_engine_extras.rs` compares it whole.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
CPP = "int main() { return 0; }\n"
CLEAN = "def f(x):\n    return x + 1\n"
FINDINGS = (
    "import subprocess\n\n\n"
    "def run(command: str) -> int:\n"
    "    handle = open(command)\n"
    "    subprocess.call(command, shell=True)\n"
    "    return eval(handle.read())\n\n\n"
    "def count() -> int:\n"
    '    return "many"\n'
)
CASES = {
    "cpp-with-python": (
        {"ici.toml": CONFIG, "src/main.cpp": CPP, "src/tool.py": CLEAN},
        ("verify", "--no-cache", "--report"),
    ),
    "cpp-with-python-deep": (
        {"ici.toml": CONFIG, "src/main.cpp": CPP, "src/tool.py": CLEAN},
        ("verify", "--profile", "deep", "--no-cache", "--report"),
    ),
    "cpp-with-python-findings": (
        {"ici.toml": CONFIG, "src/main.cpp": CPP, "src/tool.py": FINDINGS},
        ("verify", "--profile", "deep", "--no-cache", "--report"),
    ),
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
