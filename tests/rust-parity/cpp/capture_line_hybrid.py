"""Development-only frozen legacy `ici line` captures for hybrid projects; never called by Rust.

Frozen `LineEngine` is one engine over every language: a `type='hybrid'` project without C++
sources (or without Python sources) still counts the other language's files and exits 0. The
cases cover Python only, C++ only, both, Python only over a fail limit and C++ only over a
warning limit.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-line-hybrid"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
COUNTS = ("code", "comment", "blank", "total", "all", "files_data")
BASE = "[project]\ntype='hybrid'\nsource_dirs=['src']\n"
PY = "def value():\n    # one\n\n    return 1\n"
CPP = "// header\nint value() {\n\n    return 1;\n}\n"
CASES = {
    "hybrid-python-only": ("", {"src/app.py": PY, "docs/guide.md": "# guide\n"}),
    "hybrid-cpp-only": ("", {"src/app.cpp": CPP, "docs/guide.md": "# guide\n"}),
    "hybrid-both": ("", {"src/app.py": PY, "src/app.cpp": CPP}),
    "hybrid-python-only-over-limit": (
        "[engines.line]\nwarn_limit=1\nfail_limit=2\n",
        {"src/app.py": PY + "\n\ndef other():\n    return 2\n"},
    ),
    "hybrid-cpp-only-over-warn": (
        "[engines.line]\nwarn_limit=2\nfail_limit=5\n",
        {"src/app.cpp": CPP, "docs/guide.md": "# guide\n"},
    ),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(config: str, files: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "line", "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads((project / "line_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        "counts": {key: report["extra"].get(key) for key in COUNTS},
    }


cases = {
    name: {"config": config, "files": files, "frozen": run(config, files)}
    for name, (config, files) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/line-hybrid-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "base": BASE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} hybrid line cases")
