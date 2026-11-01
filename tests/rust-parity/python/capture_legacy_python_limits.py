"""Development-only frozen captures of the legacy Python bounded-analysis stops.

`ici resource` stops a file whose CPython AST exceeds MAX_RESOURCE_AST_NODES=100000
(engines/resource.py:185, `ResourceAnalysisLimit`), and `ici dup` stops a scan that exceeds a
bounded comparison limit (engines/dup.py:207, `DuplicateComparisonLimit`); `ici line` on a
file past 1,000 lines shows the summary's `{:,}` grouping (engines/line.py). The remaining
cases replay the legacy dup/dead target shapes end to end: `DuplicateScan` (dup.py:279),
`PythonSemanticShapeError` under `python_semantic='required'` (dup.py:401), AST-shape and
lexical `CloneGroup#N` (dup.py:569, dup.py:721) and the dead-code finding severities
(dead.py:885: PASS info, WARN medium, ERROR critical). The fixtures are generated from the
parameters recorded beside each case so the native test rebuilds the same bytes without checking
in large trees.
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
FINDING_FIELDS = ("rule_id", "fingerprint", "message", "severity")
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
BODY = "".join(f"    v{j} = a * {j} + b - {j}\n" for j in range(12))
SHAPE = "    y = {1} + 2\n    z = x * y\n    w = z - {0}\n    v = w + {1}\n    return v\n"
# name: (command, {path: [generator, count] or literal text}[, extra ici.toml text])
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
    "dup/no-duplicates": (
        "dup",
        {"src/a.py": "def f(a):\n    return a + 1\n", "src/b.py": "x = 2\n"},
    ),
    "dup/lexical-clone": (
        "dup",
        {
            "src/a.py": f"def f(a, b):\n{BODY}    return v0\n",
            "src/b.py": f"def g(a, b):\n{BODY}    return v0\n",
        },
    ),
    # The same callable AST shape with a different line split, so no lexical window matches.
    "dup/ast-shape-clone": (
        "dup",
        {
            "src/a.py": "def f(a, b):\n    x = a + 1\n" + SHAPE.format("a", "b"),
            "src/b.py": "def g(p, q):\n    x = (p\n         + 1)\n" + SHAPE.format("p", "q"),
        },
    ),
    "dup/required-semantic-dynamic-eval": (
        "dup",
        {"src/a.py": "def f(a):\n    return eval(a)\n"},
        "[engines.dup]\npython_semantic='required'\n",
    ),
    "dead/clean": ("dead", {"src/a.py": "def f():\n    return 1\n\n\nf()\n"}),
    "dead/nul-input": ("dead", {"src/a.py": "x = 1\x00\n"}),
    "dead/unreachable-statement": (
        "dead",
        {"src/a.py": "def f():\n    return 1\n    x = 2\n\n\nf()\n"},
    ),
}


def text(content) -> str:
    return content if isinstance(content, str) else GENERATORS[content[0]](*content[1:])


def run(command: str, files: dict, config: str = "") -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-limits-"))
    try:
        project = work / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
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
            "findings": [
                {key: finding.get(key) for key in FINDING_FIELDS} for finding in report["findings"]
            ],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {}
    for name, (command, files, *config) in CASES.items():
        cases[name] = {"command": command, "files": files, "frozen": run(command, files, *config)}
        if config:
            cases[name]["config"] = config[0]
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
    print(f"{len(cases)} bounded-analysis, grouping and target-shape cases", file=sys.stderr)


if __name__ == "__main__":
    main()
