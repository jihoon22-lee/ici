#!/usr/bin/env python3
"""Which C++ check scopes a `next verify` result moves when one kind of input changes.

The C++ counterpart of `tests/rust-parity/python/capture_scope_inputs.py`: a v1 workspace with one
C++ component and a compilation database at the root, run twice; between the runs one kind of
input changes. Each case records which check records' `scope_digest` moved and each record's state, so a
native run can be compared record by record (`crates/ici-cli/tests/next_cpp_scope_inputs.rs`).

usage: capture_cpp_scope_inputs.py <python> <output.json> --frozen <oracle-src> | --native <ici>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HEADER = "inline int api() {\n    return 1;\n}\n"
SOURCE = '#include "a.hpp"\nint main() { return api(); }\n'
OTHER = "int other(int value) {\n    if (value) {\n        return 1;\n    }\n    return 0;\n}\n"
CONFIG = (
    "schema_version = 1\n[workspace]\nname = 'fixture'\n"
    "[[components]]\nid = 'app'\nroot = 'app'\nlanguages = ['cpp']\n"
)


def database(root: Path, flags: dict[str, list[str]]) -> str:
    return json.dumps(
        [
            {
                "directory": str(root),
                "file": f"app/{name}",
                "arguments": ["/usr/bin/g++", "-Wall", *flags.get(name, []), "-c", f"app/{name}"],
            }
            for name in ("a.cpp", "b.cpp")
        ]
    )


# Case: (files written before the second run, extra environment for the second run). A value
# `<database:...>` is a compilation database with those extra flags per source.
CASES: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "same": ({}, {}),
    "source-content": ({"app/a.cpp": SOURCE + "// edited\n"}, {}),
    "header-content": ({"app/a.hpp": HEADER.replace("return 1", "return 2")}, {}),
    "other-source-content": ({"app/b.cpp": OTHER.replace("return 0", "return -1")}, {}),
    "source-added": ({"app/c.cpp": "int c() { return 3; }\n"}, {}),
    "database-flag": ({"compile_commands.json": "<database:a.cpp=-DFIXTURE=1>"}, {}),
    "clang-tidy-added": ({".clang-tidy": "Checks: '-*,readability-*'\n"}, {}),
    "ici-toml-comment": ({"ici.toml": CONFIG + "# a comment\n"}, {}),
    "unrelated-file-added": ({"notes.txt": "hello\n"}, {}),
    "inherited-environment": ({}, {"ICI_SCOPE_FIXTURE_VARIABLE": "1"}),
    "compiler-environment": ({}, {"CXXFLAGS": "-O2"}),
}


def write(root: Path, path: str, text: str) -> None:
    if text.startswith("<database:"):
        name, flag = text[len("<database:") : -1].split("=", 1)
        text = database(root, {name: [flag]})
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)


def run(tool: list, root: Path, base: Path, oracle: str | None, extra: dict) -> int:
    product = {"PYTHONPATH": oracle} if oracle else {}
    return subprocess.run(
        [*tool, "next", "verify", "--result", "now.json"],
        cwd=root,
        env={
            "HOME": str(base),
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",
            "GIT_CEILING_DIRECTORIES": str(base),
            **product,
            **extra,
        },
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def scenario(tool: list, oracle: str | None, name: str) -> dict:
    files, extra = CASES[name]
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        root = base / "project"
        (root / "app").mkdir(parents=True)
        (root / "app/a.hpp").write_text(HEADER)
        (root / "app/a.cpp").write_text(SOURCE)
        (root / "app/b.cpp").write_text(OTHER)
        (root / "compile_commands.json").write_text(database(root, {}))
        (root / "ici.toml").write_text(CONFIG)
        run(tool, root, base, oracle, {})
        first = json.loads((root / "now.json").read_text())
        for path, text in files.items():
            write(root, path, text)
        code = run(tool, root, base, oracle, extra)
        now = json.loads((root / "now.json").read_text())
    # Keyed by task: frozen reports one `cpp.diagnostics` row per compilation unit.
    before = {c["task_id"]: c["scope_digest"] for c in first["checks"]}
    after = {c["task_id"]: c for c in now["checks"]}
    moved = sorted(t for t, c in after.items() if before.get(t) != c["scope_digest"])
    return {
        "files": files,
        "environment": extra,
        "exit": code,
        "tasks": {t: c["check_id"] for t, c in sorted(after.items())},
        "moved_tasks": moved,
        "moved_checks": sorted({after[t]["check_id"] for t in moved}),
        "states": {t: c["state"] for t, c in sorted(after.items())},
    }


def main() -> None:
    python, output, mode, value = sys.argv[1:5]
    tool = [python, "-m", "ici"] if mode == "--frozen" else [value]
    oracle = value if mode == "--frozen" else None
    results = {name: scenario(tool, oracle, name) for name in CASES}
    document = {
        "header": HEADER,
        "source": SOURCE,
        "other": OTHER,
        "config": CONFIG,
        "cases": results,
    }
    Path(output).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
