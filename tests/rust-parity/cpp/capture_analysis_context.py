"""Development-only frozen `ici verify` top-level `analysis_context` captures; never called by Rust.

Runs the frozen CLI over a C++ project (parity-fixtures/cpp/test-cmake/plain), a Python project
(the two Python files below) and a hybrid project (both) in a git checkout and keeps the
`ici.analysis-context/v1` object of `verify_report.json`. The project root is replaced by `<ROOT>`.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-analysis-context"
CPP = "test-cmake/plain"
PYTHON_FILES = {
    "src/app.py": "def add(a, b):\n    return a + b\n",
    "tests/test_app.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}
CASES = ["cpp", "python", "hybrid"]
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CMAKE = subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]


def run(kind: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        project.mkdir()
        if kind != "python":
            shutil.copytree(ROOT / "parity-fixtures/cpp" / CPP, project, dirs_exist_ok=True)
        if kind != "cpp":
            for name, text in PYTHON_FILES.items():
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text(text, encoding="utf-8")
        config = f"[project]\ntype='{kind}'\nsource_dirs=['src']\n"
        (project / "ici.toml").write_text(config, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        return json.loads(text.replace(str(project), "<ROOT>"))["analysis_context"]
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


cases = {kind: run(kind) for kind in CASES}
(ROOT / "tests/rust-parity/cpp/analysis-context-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cmake": CMAKE,
            "cpp_fixture": CPP,
            "python_files": PYTHON_FILES,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} analysis contexts with {CMAKE}")
