"""Development-only frozen legacy `ici exception` C++ read-failure captures; never called by Rust.

Frozen ExceptionSafetyEngine reads every C++ source with a strict UTF-8 `read_text`; a decode
failure is a `ReadError` ERROR target carrying CPython's `UnicodeDecodeError` text and the
engine summary joins the first three analysis errors. Sources are stored as hex bytes.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-exception-read"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
CONFIG = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
GOOD = b"void f() { try { } catch (...) { } }\n"
CASES = {
    "invalid-start": {"src/bad.cpp": b"int x = 1;\n\xff\xfe broken\n", "src/good.cpp": GOOD},
    "invalid-continuation": {"src/bad.cpp": b"int y;\n\xe2Ax\n"},
    "truncated": {"src/bad.cpp": b"int z;\n\xe2\x82"},
    "four-errors": {f"src/b{index}.cpp": b"\x80\n" for index in range(4)},
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(files: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        (project / "src").mkdir(parents=True)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        for name, data in files.items():
            (project / name).write_bytes(data)
        result = subprocess.run(
            [PYTHON, "-m", "ici", "exception", "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        report = json.loads((project / "exception_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


cases = {
    name: {
        "files": {path: data.hex() for path, data in files.items()},
        "frozen": run(files),
    }
    for name, files in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/exception-read-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "config": CONFIG, "cases": cases}, indent=1, sort_keys=True)
    + "\n",
    encoding="utf-8",
)
for name, case in cases.items():
    print(name, case["frozen"]["exit_code"], case["frozen"]["summary"][:120])
