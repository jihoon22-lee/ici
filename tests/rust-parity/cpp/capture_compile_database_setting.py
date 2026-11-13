"""Development-only frozen `project.compile_database` load checks on a C++ project.

Frozen `_config_paths._validate_compile_database` (non-empty string, native path syntax,
`resolve_project_path` inside the root) runs while the configuration loads; the database it
names is then the one `ici export-compilation-context` reads (no `--database`, no compiler run).
Each case copies parity-fixtures/cpp/legacy-scope-fixture into argv[1]/project, writes the
legacy C++ `ici.toml` with one `compile_database` value (`<root>` is the project's absolute
path) and a recorded database, and records the exit code, stderr and payload. Never called by
the Rust product.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = ROOT / "parity-fixtures/cpp/legacy-scope-fixture"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
DATABASE = (
    '[{"directory": ".", "file": "src/main.cpp", '
    '"arguments": ["g++", "-std=c++17", "-c", "src/main.cpp", "-o", "main.o"]}]\n'
)
FILES = {"build/compile_commands.json": DATABASE}
# name -> TOML value of project.compile_database
CASES = {
    "relative": '"build/compile_commands.json"',
    "absolute-inside": '"<root>/build/compile_commands.json"',
    "dot-segments-inside": '"build/../build/compile_commands.json"',
    "empty": '""',
    "not-a-string": "7",
    "parent-outside": '"../compile_commands.json"',
    "absolute-outside": '"/nonexistent-ici-outside/compile_commands.json"',
    "backslash": '"build\\\\compile_commands.json"',
    "symlink-outside": '"outside-link/compile_commands.json"',
    "missing-inside": '"build/absent.json"',
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(work: Path, value: str) -> dict:
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        for name, text in FILES.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        outside = work / "outside"
        outside.mkdir()
        (outside / "compile_commands.json").write_text(DATABASE, encoding="utf-8")
        (project / "outside-link").symlink_to(outside)
        config = BASE + "compile_database = " + value.replace("<root>", str(project)) + "\n"
        (project / "ici.toml").write_text(config, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "export-compilation-context", "--output", "-"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        stderr = result.stderr.replace(str(project), "<root>").replace(str(work), "<work>")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "value": value,
        "exit_code": result.returncode,
        "payload": json.loads(result.stdout) if result.returncode == 0 else None,
        "stdout_empty": result.returncode == 0 or not result.stdout,
        "stderr": stderr.strip(),
    }


def main() -> None:
    work = Path(sys.argv[1]).resolve()
    cases = {name: run(work, value) for name, value in CASES.items()}
    (ROOT / "tests/rust-parity/cpp/compile-database-setting-oracle.json").write_text(
        json.dumps(
            {"oracle": EXPECTED, "base": BASE, "files": FILES, "cases": cases},
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    for name, case in cases.items():
        print(name, case["exit_code"], case["stderr"][:160])


if __name__ == "__main__":
    main()
