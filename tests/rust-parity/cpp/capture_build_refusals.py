"""Capture frozen v0.13.0 `ici build` for a Make build refused by its configuration.

    python -I capture_build_refusals.py <oracle-root> <output.json>

Development only; never called by the Rust product. Native refuses these at plan time with a
more specific sentence (decisions.md "Make 준비 거부 문구"); `legacy_build_refusals.rs` proves the
failure class, the exit code and the files a run leaves equal frozen.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
FAKE = '#!/bin/sh\necho "$@" >> "$ICI_MAKE_LOG"\nexit 0\n'
TREE = {
    "src/main.cpp": "int main(){return 0;}\n",
    "Makefile": "all:\n\ttrue\n",
    "pyproject.toml": "[project]\nname='refusal'\nversion='1.2.3'\n",
    "real/.keep": "",
}
CASES = {
    "missing-workdir": "workdir='native'\nbuild_argv=['make','all']",
    "symlinked-workdir": "workdir='linked'\nbuild_argv=['make','all']",
    "unavailable-executable": "build_argv=['/nonexistent/make','all']",
}
# Directories a run owns that are not the project's outcome.
PRIVATE = {".ici", ".ici-native", "__pycache__", ".git"}


def listing(base):
    names = []
    for directory, children, files in os.walk(base):
        children[:] = sorted(name for name in children if name not in PRIVATE)
        relative = Path(directory).relative_to(base)
        names += [str(relative / name) + "/" for name in children]
        names += [str(relative / name) for name in sorted(files)]
    return sorted(names)


def run(oracle, make):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        tools = Path(temporary).resolve() / "tools"
        tools.mkdir()
        (tools / "make").write_text(FAKE, encoding="utf-8")
        (tools / "make").chmod(0o755)
        log = Path(temporary).resolve() / "make.log"
        for name, text in TREE.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text, encoding="utf-8")
        (base / "linked").symlink_to("real")
        (base / "ici.toml").write_text(
            f"[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n{make}\n",
            encoding="utf-8",
        )
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "build"],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": f"{tools}:/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "ICI_MAKE_LOG": str(log),
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "files": listing(base),
            "make_ran": log.exists(),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "tree": TREE,
        "fake": FAKE,
        "cases": {
            name: {"make": make, "frozen": run(oracle, make)} for name, make in CASES.items()
        },
    }
    for name, case in document["cases"].items():
        print(name, json.dumps(case["frozen"], sort_keys=True)[:700])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
