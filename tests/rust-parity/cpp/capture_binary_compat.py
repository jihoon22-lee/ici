"""Development-only frozen legacy binary_compat captures; never called by the Rust product.

Frozen BinaryCompatibilityEngine runs only in the deep pipeline after the build engine
publishes its artifact manifest, so each case runs `ici verify --profile deep --report` with
the build and binary_compat engines enabled and records those two engine results.
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
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-binary-compat-capture"
TOOLS = sorted({str(Path(shutil.which(name)).parent) for name in ("make", "g++", "readelf")})
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
ENABLE = "[engines.build]\nenabled=true\n[engines.binary_compat]\nenabled=true\n"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
NON_ELF_MAKE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n"
    "\tprintf 'MZ-not-elf' > build/ici-make/tool.exe\n\tchmod +x build/ici-make/tool.exe\n"
)
NOTHING_MAKE = "all:\n\tmkdir -p build/ici-make\n\ttouch build/ici-make/notes.txt\n"
CASES = {
    "pass": {},
    "violations": {
        "ici.toml": BASE
        + ENABLE
        + "forbidden_needed=['libc.so.6']\nmax_glibc='2.0'\nforbid_build_paths=true\n"
    },
    "non-elf-allowed": {
        "Makefile": NON_ELF_MAKE,
        "ici.toml": BASE + ENABLE + "allow_non_elf=true\n",
    },
    "non-elf-rejected": {"Makefile": NON_ELF_MAKE},
    "no-artifacts": {"Makefile": NOTHING_MAKE},
    "no-artifacts-required": {
        "Makefile": NOTHING_MAKE,
        "ici.toml": BASE + ENABLE + "required=true\n",
    },
    "unpublished-selection": {"ici.toml": BASE + ENABLE + "artifacts=['release:shadow:nope']\n"},
}


def run(changes: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / "package-make", project)
        (project / "ici.toml").write_text(BASE + ENABLE, encoding="utf-8")
        for relative, text in changes.items():
            (project / relative).write_text(text, encoding="utf-8")
        subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={
                "PATH": os.pathsep.join([*TOOLS, "/usr/bin", "/bin"]),
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = {}
    for engine in report["results"]:
        if engine.get("engine_name") in ("build", "binary_compat"):
            engines[engine["engine_name"]] = {
                "status": engine["status"],
                "summary": engine["summary"],
                "targets": [
                    {key: target.get(key) for key in FIELDS} for target in engine["targets"]
                ],
            }
    return engines


cases = {name: {"changes": changes, "frozen": run(changes)} for name, changes in CASES.items()}
(ROOT / "tests/rust-parity/cpp/binary-compat-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "base": BASE + ENABLE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} binary compatibility cases")
