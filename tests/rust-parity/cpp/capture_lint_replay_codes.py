#!/usr/bin/env python3
"""Frozen legacy `ici verify` lint result for each `build_replay_command` refusal code.

Development only; never called by the Rust product. Each case writes src/a.cpp and src/b.cpp
(b always replays cleanly), a `compile_commands.json` whose src/a.cpp entry the syntax replay
refuses with one frozen `ReplayCommandError` code (`core/cpp_replay.py`), and any extra files.
`<root>` in a case is the project root. The lint engine result is recorded with the project root
written as `<root>`. Synthetic option values only.

usage: capture_lint_replay_codes.py <oracle-src> <python> <output.json>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = 'type = "cpp"\n[project]\nname = "p"\n'
SOURCES = {"src/a.cpp": "int a() { return 0; }\n", "src/b.cpp": "int b() { return 0; }\n"}
GXX = "/usr/bin/g++"
CLEAN_B = {"directory": "<root>", "arguments": [GXX, "-std=c++17", "-c", "src/b.cpp"]}


def entry(*arguments, directory: str = "<root>", compiler: str = GXX) -> dict:
    return {"directory": directory, "arguments": [compiler, "-std=c++17", *arguments]}


def expanded(unit: dict) -> dict:
    arguments = []
    for argument in unit["arguments"]:
        if isinstance(argument, dict):
            value, count = argument["repeat"]
            arguments.extend([value] * count)
        else:
            arguments.append(argument)
    return {**unit, "arguments": arguments}


CASES = {
    # `_filtered_arguments` / `_consume_replay_argument`.
    "source-twice": {"a": entry("-c", "src/a.cpp", "src/a.cpp")},
    "stdin-operand": {"a": entry("-c", "-", "src/a.cpp")},
    "second-separator": {"a": entry("-c", "--", "src/a.cpp", "--")},
    "empty-argument": {"a": entry("", "-c", "src/a.cpp")},
    "bare-response-token": {"a": entry("@", "-c", "src/a.cpp")},
    # The syntax replay keeps warning-as-error options (frozen demotes them only for the
    # unused-function replay and the clang tools): an unprojectable one reaches GCC, and a
    # projectable one keeps its error severity.
    "warning-policy-linker": {"a": entry("-Werror=l,--synthetic", "-c", "src/a.cpp")},
    "werror-kept": {
        "a": entry("-Werror=unused-variable", "-c", "src/a.cpp"),
        "files": {"src/a.cpp": "int a() { int unused = 0; return 0; }\n"},
    },
    "werror-plain-kept": {
        "a": entry("-Werror", "-c", "src/a.cpp"),
        "files": {"src/a.cpp": "int a() { int unused = 0; return 0; }\n"},
    },
    # `{"repeat": [value, count]}` stands for `count` copies of `value` (kept compact here).
    "too-many-arguments": {"a": entry({"repeat": ["-DSYNTHETIC", 32_768]}, "-c", "src/a.cpp")},
    # 32,768 arguments pass ingestion (MAX_COMPILE_ARGUMENTS); the sanitized replay with its
    # diagnostic suffix does not (MAX_REPLAY_ARGUMENTS, `command-too-large`).
    "replay-too-large": {"a": entry({"repeat": ["-DSYNTHETIC", 32_764]}, "-c", "src/a.cpp")},
    # `_resolve_compiler`.
    "compiler-path-miss": {"a": entry("-c", "src/a.cpp", compiler="g++-99")},
    "compiler-unresolved": {"a": entry("-c", "src/a.cpp", compiler="/nonexistent-ici/g++")},
    "unsupported-driver": {"a": entry("-c", "src/a.cpp", compiler="/usr/bin/true")},
    "project-compiler": {
        "a": entry("-c", "src/a.cpp", compiler="<root>/bin/g++"),
        "files": {"bin/g++": '#!/bin/sh\nexec /usr/bin/g++ "$@"\n'},
    },
    # The working directory and source checks of `build_replay_command`.
    "directory-is-file": {"a": entry("-c", "src/a.cpp", directory="<root>/src/a.cpp")},
}


def capture(oracle_src: str, python: str, case: dict) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "ici.toml").write_text(CONFIG)
        for name, text in {**SOURCES, **case.get("files", {})}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if name.startswith("bin/"):
                path.chmod(0o755)
        units = [{**expanded(case["a"]), "file": "src/a.cpp"}, {**CLEAN_B, "file": "src/b.cpp"}]
        text = json.dumps(units).replace("<root>", str(root))
        (root / "compile_commands.json").write_text(text)
        completed = subprocess.run(
            [python, "-I", "-c", ENTRY, oracle_src, "verify", "--report", "--no-cache"],
            cwd=root,
            env={
                "HOME": str(base),
                "PATH": "/usr/bin:/bin",
                "GIT_CEILING_DIRECTORIES": str(base),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads((root / "verify_report.json").read_text().replace(str(root), "<root>"))
    lint = next(item for item in report["results"] if item["engine_name"] == "lint")
    return {"exit_code": completed.returncode, "lint": lint}


ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1))\nfrom ici.__main__ import app; app()"


def main() -> None:
    oracle_src, python, output = sys.argv[1:]
    runs = {}
    for name, case in CASES.items():
        runs[name] = capture(oracle_src, python, case)
        lint = runs[name]["lint"]
        print(name, runs[name]["exit_code"], lint["status"], lint["summary"][:120])
        for target in lint["targets"]:
            print("   ", target["target_name"], target["status"], target["message"][:140])
    Path(output).write_text(
        json.dumps(
            {
                "config": CONFIG,
                "sources": SOURCES,
                "clean_b": CLEAN_B,
                "cases": CASES,
                "runs": runs,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
