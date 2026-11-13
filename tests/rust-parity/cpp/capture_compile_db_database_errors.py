"""Development-only frozen captures of database-level compilation-database errors; never called by Rust.

    python -I -B capture_compile_db_database_errors.py

Each case is the frozen CLI's `ici verify --report` (oracle 6fe3e405) over TREE (two production
translation units, only the compile_db and lint engines enabled) with one database that frozen
`core/compile_db.load_compilation_context` refuses as a whole: not JSON, a duplicate key, a JSON
`NaN`, bytes that are not UTF-8, a JSON object root, a path that is a directory, a file without
read permission, a symlink that resolves outside the project, and a configured database that
does not exist. Records the exit code, the suite status, the compile_db engine's status, evidence, summary, score,
the extra counters and every target (with metrics), and the lint engine's status, summary and
targets (without end_line and metrics). The project path is stored as {root}.
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
GOLDEN = ROOT / "tests/rust-parity/cpp/compile-db-database-errors-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path", "metrics")
LINT_FIELDS = ("target_name", "status", "message", "start_line", "file_path")
EXTRA = ("configurations", "coverage_percent", "covered_units", "issues_count", "production_units")
ENGINES = [
    "line",
    "test",
    "type",
    "python_compat",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "build",
    "binary_compat",
    "integration",
    "cycle",
]
HEAD = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
OFF = "".join(f"[engines.{name}]\nenabled=false\n" for name in ENGINES)
TREE = {
    "src/a.cpp": "int a_fn() { return 1; }\n",
    "src/b.cpp": "int b_fn() { return 2; }\n",
}
DATABASE = "build/compile_commands.json"
# case: (extra [project] lines, {path: text | {"hex": ...} | {"symlink": ...} | {"directory": true}},
#        {path: mode})
CASES = {
    "not-json": ("", {DATABASE: "not json\n"}, {}),
    "duplicate-key": ("", {DATABASE: '[{"file":"src/a.cpp","file":"src/b.cpp"}]\n'}, {}),
    "nan-constant": ("", {DATABASE: '[{"x": NaN}]\n'}, {}),
    "not-utf8": ("", {DATABASE: {"hex": "5bff5d0a"}}, {}),
    "object-root": ("", {DATABASE: '{"file": "src/a.cpp"}\n'}, {}),
    "directory": ("", {DATABASE: {"directory": True}}, {}),
    "unreadable": ("", {DATABASE: "[]\n"}, {DATABASE: 0}),
    "outside-symlink": ("", {DATABASE: {"symlink": "../../outside.json"}}, {}),
    "configured-missing": ("compile_database='build/none.json'\n", {}, {}),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def materialize(project: Path, files: dict, modes: dict) -> None:
    for name, text in {**TREE, **files}.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, str):
            path.write_text(text, encoding="utf-8")
        elif "hex" in text:
            path.write_bytes(bytes.fromhex(text["hex"]))
        elif "symlink" in text:
            path.symlink_to(text["symlink"])
        else:
            path.mkdir()
    for name, mode in modes.items():
        (project / name).chmod(mode)


def run(project_lines: str, files: dict, modes: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-compile-db-errors-")).resolve()
    try:
        project = work / "project"
        project.mkdir()
        (work / "outside.json").write_text("[]\n", encoding="utf-8")
        (project / "ici.toml").write_text(HEAD + project_lines + OFF, encoding="utf-8")
        materialize(project, files, modes)
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "{root}"))
    finally:
        for name in modes:
            path = work / "project" / name
            if path.exists():
                path.chmod(0o644)
        shutil.rmtree(work, ignore_errors=True)
    engines = {item["engine_name"]: item for item in report["results"]}
    database = engines["compile_db"]
    lint = engines["lint"]
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "compile_db": {
            "status": database["status"],
            "evidence": database["evidence"],
            "summary": database["summary"],
            "score": database["score"],
            "extra": {key: database["extra"].get(key) for key in EXTRA},
            "targets": [{key: target.get(key) for key in FIELDS} for target in database["targets"]],
        },
        "lint": {
            "status": lint["status"],
            "summary": lint["summary"],
            "targets": [
                {key: target.get(key) for key in LINT_FIELDS} for target in lint["targets"]
            ],
        },
    }


def main() -> None:
    cases = {}
    for name, (project_lines, files, modes) in CASES.items():
        frozen = run(project_lines, files, modes)
        cases[name] = {"project": project_lines, "files": files, "modes": modes, "frozen": frozen}
        print(name, frozen["exit_code"], frozen["compile_db"]["summary"])
    document = {
        "oracle": EXPECTED,
        "gxx": GXX,
        "head": HEAD,
        "off": OFF,
        "tree": TREE,
        "outside": "[]\n",
        "cases": cases,
    }
    GOLDEN.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} database error cases")


if __name__ == "__main__":
    main()
