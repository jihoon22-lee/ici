"""Development-only frozen captures of the legacy Python bounded-analysis stops.

`ici resource` stops a file whose CPython AST exceeds MAX_RESOURCE_AST_NODES=100000
(engines/resource.py:185, `ResourceAnalysisLimit`), and `ici dup` stops a scan that exceeds a
bounded comparison limit (engines/dup.py:207, `DuplicateComparisonLimit`); `ici line` on a
file past 1,000 lines shows the summary's `{:,}` grouping (engines/line.py). The fixtures are
generated from the parameters recorded beside each case so the native test rebuilds the same
bytes without checking in large trees.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUT = ROOT / "tests/rust-parity/python/legacy-python-limits-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def constants(count: int) -> str:
    """`x = [0, ...]`: CPython counts `count + 6` AST nodes."""

    return "x = [" + ", ".join(["0"] * count) + "]\n"


def statements(lines: int, tuple_length: int) -> str:
    """`lines` short statements and one tuple: `1 + 4 * lines + 5 + tuple_length` AST nodes."""

    return "x = 0\n" * lines + "x = (" + ", ".join(["0"] * tuple_length) + ")\n"


def functions(count: int) -> str:
    """`count` identical 14-line function bodies (one shared duplicate window per body)."""

    body = "".join(f"    v{j} = a * {j} + b - {j}\n" for j in range(12))
    return "".join(f"def f{i}(a, b):\n" + body + "    return v0\n\n\n" for i in range(count))


def lines(code: int, comments: int, blanks: int) -> str:
    """`code` statements, `comments` comment lines and `blanks` blank lines."""

    return "x = 0\n" * code + "# c\n" * comments + "\n" * blanks


GENERATORS = {
    "constants": constants,
    "statements": statements,
    "functions": functions,
    "lines": lines,
}
# name: (command, {path: [generator, count] or literal text})
CASES = {
    "resource/ast-at-limit": ("resource", {"src/a.py": ["statements", 24_998, 2]}),
    "resource/ast-over-limit": ("resource", {"src/a.py": ["statements", 24_998, 3]}),
    "resource/ast-over-limit-second-file": (
        "resource",
        {"src/a.py": "f = open('x')\n", "src/b.py": ["statements", 24_998, 3]},
    ),
    # One logical statement of 99,994 constants (100,000 nodes): frozen analyzed it.
    "resource/long-logical-statement": ("resource", {"src/a.py": ["constants", 99_994]}),
    "dup/window-occurrence-limit": ("dup", {"src/a.py": ["functions", 2_100]}),
    "dup/raw-match-limit": ("dup", {"src/a.py": ["functions", 200]}),
    "line/thousands-grouping": ("line", {"src/a.py": ["lines", 1_234, 1_001, 2]}),
}


def text(content) -> str:
    return content if isinstance(content, str) else GENERATORS[content[0]](*content[1:])


def run(command: str, files: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-limits-"))
    try:
        project = work / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        for name, content in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text(content), encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
        return {
            "exit_code": result.returncode,
            "status": report["status"],
            "summary": report["summary"],
            "evidence": report["evidence"],
            "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {
        name: {"command": command, "files": files, "frozen": run(command, files)}
        for name, (command, files) in CASES.items()
    }
    OUT.write_text(
        json.dumps(
            {"oracle": EXPECTED, "base": BASE, "cases": cases},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} bounded-analysis cases", file=sys.stderr)


if __name__ == "__main__":
    main()
