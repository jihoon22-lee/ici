"""Development-only frozen `ici build` BuildEngine target captures; never called by Rust.

Frozen `ici build` prints only the summary line, so the capture wraps the frozen CLI's own
`_print_engine_result` to record the engine result the command rendered (same code path).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-build-targets-capture"
TOOLS = sorted({str(Path(shutil.which(name)).parent) for name in ("make", "g++", "cmake", "qmake")})
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
DRIVER = """
import json, sys
import ici.__main__ as cli
rendered = cli._print_engine_result
OUT = sys.argv[1]
def record(result):
    rendered(result)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump({"status": result.status.value, "summary": result.summary,
                   "targets": [{"target_name": t.target_name, "status": t.status.value,
                                "message": t.message, "start_line": t.start_line,
                                "file_path": t.file_path} for t in result.targets]}, handle)
cli._print_engine_result = record
sys.argv = ["ici", "build"]
cli.main()
"""
BROKEN_MAKE = "all:\n\texit 5\n"
BROKEN_SOURCE = "int main() { return missing; }\n"
QMAKE_PROJECT = "TEMPLATE = app\nCONFIG += console c++17\nCONFIG -= qt\nTARGET = suite\nSOURCES += src/main.cpp\n"
PY_TOML = "[project]\ntype='python'\nsource_dirs=['src']\n"
PYPROJECT = "[project]\nname='pkg'\nversion='2.3.4'\n[project.scripts]\npkg-tool='pkg.cli:main'\n"
DECLARED = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.artifacts]\nreport=['reports/*.txt']\n"
)
# case: (fixture, {relative path: text, None to delete, or {"symlink": target}}); the
# "outside.txt" next to the project is a regular file outside the workspace.
CASES = {
    "make": ("package-make", {}),
    "direct": ("package-direct", {}),
    "cmake": ("package-cmake", {}),
    "make-failure": ("package-make", {"Makefile": BROKEN_MAKE}),
    "cmake-compile-failure": ("package-cmake", {"src/main.cpp": BROKEN_SOURCE}),
    "cmake-configure-failure": (
        "package-cmake",
        {"CMakeLists.txt": 'cmake_minimum_required(VERSION 3.16)\nmessage(FATAL_ERROR "broken")\n'},
    ),
    "qmake": ("package-cmake", {"CMakeLists.txt": None, "pkg.pro": QMAKE_PROJECT}),
    "qmake-compile-failure": (
        "package-cmake",
        {"CMakeLists.txt": None, "pkg.pro": QMAKE_PROJECT, "src/main.cpp": BROKEN_SOURCE},
    ),
    "direct-compile-failure": (
        "package-direct",
        {"src/main.cpp": "int main() { return missing; }\n"},
    ),
    "main-count": ("package-direct", {"src/main.cpp": "int helper() { return 1; }\n"}),
    "unadapted-makefile": ("package-direct", {"Makefile": "all:\n\ttrue\n"}),
    "no-artifacts": ("package-direct", {"src/main.cpp": None, "ici.toml": PY_TOML}),
    "python": (
        "package-direct",
        {
            "src/main.cpp": None,
            "ici.toml": PY_TOML,
            "pyproject.toml": PYPROJECT,
            "src/pkg/__init__.py": "",
            "src/pkg/cli.py": "def main():\n    return 0\n",
        },
    ),
    "declared-artifacts-missing": (
        "package-direct",
        {"ici.toml": DECLARED},
    ),
    "declared-artifacts-external": (
        "package-direct",
        {"ici.toml": DECLARED, "reports/summary.txt": {"symlink": "../../outside.txt"}},
    ),
    "declared-artifacts": (
        "package-direct",
        {
            "ici.toml": "[project]\ntype='cpp'\nsource_dirs=['src']\n"
            "[build.artifacts]\nreport=['reports/*.txt']\n",
            "reports/summary.txt": "ok\n",
        },
    ),
}


def run(fixture: str, changes: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (WORK / "outside.txt").write_text("outside\n", encoding="utf-8")
        for relative, text in changes.items():
            path = project / relative
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, dict):
                path.symlink_to(text["symlink"])
                continue
            path.write_text(text, encoding="utf-8")
        record = WORK / "result.json"
        result = subprocess.run(
            [PYTHON, "-c", DRIVER, str(record)],
            cwd=project,
            env={
                "PATH": os.pathsep.join([*TOOLS, "/usr/bin", "/bin"]),
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        engine = json.loads(record.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    for target in engine["targets"]:
        # Wall-clock compile durations are not reproducible.
        target["message"] = re.sub(r"\(\d+\.\d\ds\)", "(<seconds>s)", target["message"])
        target["message"] = target["message"].replace(str(WORK), "<work>")
    engine["summary"] = engine["summary"].replace(str(WORK), "<work>")
    return {"exit_code": result.returncode, "stdout": result.stdout.strip(), **engine}


cases = {
    name: {"fixture": fixture, "changes": changes} for name, (fixture, changes) in CASES.items()
}
for case in cases.values():
    case["frozen"] = run(case["fixture"], case["changes"])
(ROOT / "tests/rust-parity/cpp/build-targets-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True, ensure_ascii=False)
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} build target cases")
