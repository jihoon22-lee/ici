"""Capture the real cmake and g++ invocation order of frozen v0.13.0 `ici dead`.

Wrappers named cmake, g++ and c++ first on PATH append each invocation to a marker log. Keeps the
ordered CONFIGURE/BUILD/UNUSED/compile/link events, the exit code and whether a project custom
command ran. `*replay-fails*` trees fail the compiler scope's -Wunused-function replay.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/linker-order-oracle.json"
FIXTURES = ROOT / "parity-fixtures/cpp"
CASES = {
    "unused-broken-with-linker": "linker-dead",
    "unused-link-fails-with-linker": "linker-dead",
    "coverage-required-with-unused": "linker-dead",
    "unused-replay-fails-with-linker": "linker-dead",
    "unused-and-linker": "linker-dead",
    "broken-unused-required": "linker-dead",
    "broken-unused-auto": "linker-dead",
    "custom-command-control": "linker-order",
    "custom-command-broken": "linker-order",
}

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def label(line: str) -> str | None:
    tool, _, args = line.rstrip("\n").partition("\t")
    tokens = args.split()
    if tool == "cmake":
        if "--build" in tokens:
            return "BUILD"
        if "--version" in tokens or "-E" in tokens:
            return None
        return "CONFIGURE" if any(t.startswith(("-S", "-B")) for t in tokens) else None
    if any(flag in args for flag in ("--version", "-dumpversion", "-dumpmachine")):
        return None
    if "-Wunused-function" in tokens:
        return "UNUSED:" + next(t for t in reversed(tokens) if t.endswith(".cpp")).split("/")[-1]
    if any(mark in args for mark in ("CMakeCXXCompiler", "CMakeTmp", "CMakeScratch", "TryCompile")):
        return None
    match = re.search(r"(?:^| )-c (\S+\.cpp)", args)
    if match:
        return "compile:" + match.group(1).split("/")[-1]
    return "link" if ".o" in args and "-o" in tokens else None


def run(name: str, family: str) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary).resolve()
        project, tools, marker = work / "project", work / "tools", work / "marker.log"
        shutil.copytree(FIXTURES / family / name, project)
        tools.mkdir()
        (work / "home").mkdir()
        fail = (
            'case " $* " in *" -Wunused-function "*) exit 1;; esac\n'
            if "replay-fails" in name
            else ""
        )
        for tool, extra in (("cmake", ""), ("g++", fail)):
            path = tools / tool
            path.write_text(
                f"#!/bin/sh\nprintf '%s\\t%s\\n' {tool} \"$*\" >> '{marker}'\n"
                f'{extra}exec /usr/bin/{tool} "$@"\n',
                encoding="utf-8",
            )
            path.chmod(0o755)
        (tools / "c++").symlink_to("g++")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "dead", "--report"],
            cwd=project,
            env={
                "PATH": f"{tools}:/usr/bin:/bin",
                "HOME": str(work / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        return {
            "exit_code": result.returncode,
            "events": [event for event in map(label, lines) if event],
            "custom_ran": (work / "custom-ran").exists(),
        }


GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "cases": {name: run(name, family) for name, family in CASES.items()}},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(CASES)} linker order cases")
