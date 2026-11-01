"""Development-only frozen C++ `ici test` CLI captures; never called by the Rust product.

Each case copies a parity-fixtures/cpp tree into a fresh temporary project, runs the frozen
oracle `ici test --report` with an explicit PATH and records the exit code, verdict, summary
and normalized located targets (the project root becomes <ROOT>, typographic quotes become
ASCII, and a `Compilation Error:` message keeps its first line: frozen cuts stderr at 200
characters, so the cut point moves with the path length).
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-test-capture"
TOOLS = "/usr/bin:/bin"
# fixture, ici.toml tail, PATH kind ("tools": /usr/bin with gcov; "compiler": g++/as/ld only).
CASES = {
    "direct-mixed": ("test-direct/mixed", "", "tools"),
    "direct-broken": ("test-direct/broken", "", "tools"),
    "direct-all-broken": ("test-direct/all-broken", "", "tools"),
    "direct-abort": ("test-direct/abort", "", "tools"),
    "direct-mixed-without-gcov": ("test-direct/mixed", "", "compiler"),
    "direct-broken-without-gcov": ("test-direct/broken", "", "compiler"),
}


def normalize(message: str, project: Path) -> str:
    text = message.replace(str(project), "<ROOT>")
    text = text.replace(chr(0x2018), "'").replace(chr(0x2019), "'")
    if text.startswith("Compilation Error: "):
        text = text.split("\n", 1)[0]
    return text


def compiler_path(directory: Path) -> str:
    directory.mkdir()
    for tool in ("g++", "as", "ld", "x86_64-linux-gnu-as", "x86_64-linux-gnu-ld"):
        if Path("/usr/bin", tool).exists():
            os.symlink(Path("/usr/bin", tool), directory / tool)
    return str(directory)


def run(fixture: Path, config: str, path_kind: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(fixture, project)
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n" + config, encoding="utf-8"
        )
        path = TOOLS if path_kind == "tools" else compiler_path(WORK / "bin")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "test", "--report"],
            cwd=project,
            env={"PATH": path, "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = json.loads((project / "test_report.json").read_text(encoding="utf-8"))
        targets = sorted(
            (
                {
                    **{key: target.get(key) for key in FIELDS},
                    "message": normalize(target["message"], project),
                }
                for target in report["targets"]
            ),
            key=lambda target: (target["target_name"], target["file_path"]),
        )
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "passed_tests": report["extra"].get("passed_tests"),
        "total_tests": report["extra"].get("total_tests"),
        "targets": targets,
    }


FIXTURES = ROOT / "parity-fixtures/cpp"
cases = {
    name: run(FIXTURES / fixture, config, path_kind)
    for name, (fixture, config, path_kind) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/test-cli-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cases_input": {name: list(value) for name, value in CASES.items()},
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} C++ test cases")
