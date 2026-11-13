"""Development-only frozen captures of the legacy compile_db engine policy; never called by Rust.

    python -I -B capture_compile_db_policy.py

Each case is the frozen CLI's `ici verify --report` (oracle 6fe3e405) over TREE (two production
translation units, only the compile_db engine enabled; no translation unit is compiled) with
either no database and `engines.compile_db.database_required` true, false or unset, or a
database whose first row names a response file that frozen `core/compile_db` expands (nested
and plain) or refuses (missing, a cycle, outside the project, a directory, unreadable, an
unterminated quote, past the depth bound). Records the exit code, the suite status and the
compile_db engine's status, evidence, summary, score, the extra counters and every target
(with metrics). The project path is stored as {root}.
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
GOLDEN = ROOT / "tests/rust-parity/cpp/compile-db-policy-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path", "metrics")
EXTRA = ("configurations", "coverage_percent", "covered_units", "issues_count", "production_units")
ENGINES = [
    "lint",
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
ROW = (
    '[{"directory": "{root}", "file": "src/a.cpp", '
    '"arguments": ["g++", {argument}"-c", "src/a.cpp", "-o", "a.o"]}, '
    '{"directory": "{root}", "file": "src/b.cpp", '
    '"arguments": ["g++", "-c", "src/b.cpp", "-o", "b.o"]}]\n'
)


def database(argument: str) -> str:
    return ROW.replace("{argument}", f'"{argument}", ' if argument else "")


OUTSIDE = "-DOUTSIDE=1\n"
REQUIRED = "[engines.compile_db]\ndatabase_required=true\n"
OPTIONAL = "[engines.compile_db]\ndatabase_required=false\n"
# case: ([engines.compile_db] lines, {path: text | {"directory": true}}, {path: mode}); "{root}"
# in a file is the project path.
CASES = {
    "no-database-unset": ("", {}, {}),
    "no-database-required": (REQUIRED, {}, {}),
    "no-database-optional": (OPTIONAL, {}, {}),
    "database-required-present": (REQUIRED, {DATABASE: database("")}, {}),
    "rsp-plain": ("", {DATABASE: database("@flags.rsp"), "flags.rsp": "-DX=1 -O2\n"}, {}),
    "rsp-nested": (
        "",
        {DATABASE: database("@a.rsp"), "a.rsp": "-DA=1 @b.rsp\n", "b.rsp": "-DB=2\n"},
        {},
    ),
    "rsp-missing": ("", {DATABASE: database("@missing.rsp")}, {}),
    "rsp-cycle": ("", {DATABASE: database("@loop.rsp"), "loop.rsp": "@loop.rsp\n"}, {}),
    "rsp-outside": ("", {DATABASE: database("@../outside.rsp")}, {}),
    "rsp-directory": ("", {DATABASE: database("@dir.rsp"), "dir.rsp": {"directory": True}}, {}),
    "rsp-unreadable": (
        "",
        {DATABASE: database("@locked.rsp"), "locked.rsp": "-DX=1\n"},
        {"locked.rsp": 0},
    ),
    "rsp-malformed": ("", {DATABASE: database("@quote.rsp"), "quote.rsp": "'-DX=1\n"}, {}),
    "rsp-depth": (
        "",
        {
            DATABASE: database("@d0.rsp"),
            **{f"d{index}.rsp": f"@d{index + 1}.rsp\n" for index in range(40)},
            "d40.rsp": "-DX=1\n",
        },
        {},
    ),
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
            path.write_text(text.replace("{root}", str(project)), encoding="utf-8")
        elif "hex" in text:
            path.write_bytes(bytes.fromhex(text["hex"]))
        elif "symlink" in text:
            path.symlink_to(text["symlink"])
        else:
            path.mkdir()
    for name, mode in modes.items():
        (project / name).chmod(mode)


def run(engine_lines: str, files: dict, modes: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-compile-db-policy-")).resolve()
    try:
        project = work / "project"
        project.mkdir()
        (work / "outside.rsp").write_text(OUTSIDE, encoding="utf-8")
        (project / "ici.toml").write_text(HEAD + engine_lines + OFF, encoding="utf-8")
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
    }


def main() -> None:
    cases = {}
    for name, (engine_lines, files, modes) in CASES.items():
        frozen = run(engine_lines, files, modes)
        cases[name] = {"engine": engine_lines, "files": files, "modes": modes, "frozen": frozen}
        print(name, frozen["exit_code"], frozen["compile_db"]["summary"])
    document = {
        "oracle": EXPECTED,
        "gxx": GXX,
        "head": HEAD,
        "off": OFF,
        "tree": TREE,
        "outside_rsp": OUTSIDE,
        "cases": cases,
    }
    GOLDEN.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} compile_db policy cases")


if __name__ == "__main__":
    main()
