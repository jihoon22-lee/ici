#!/usr/bin/env python3
"""Which tools a `next verify` `cpp.tidy` runs for each mix of clang-tidy and clazy on PATH.

Frozen's `cpp.tidy` is one provider, clang-tidy (`ici/languages/cpp/checks.py`,
`ici/adapters/providers/tidy.py`): clazy alone neither runs nor unblocks it. The tools are synthetic
wrappers that report no diagnostics and log each real run; PATH hides every host clang-tidy and
clazy, so a host with those installed captures the same result. Compared by
`crates/ici-cli/tests/next_cpp_scope_inputs.rs`.

usage: capture_cpp_tidy_providers.py <python> <output.json> --frozen <oracle-src> | --native <ici>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "int main() { return 0; }\n"
CONFIG = (
    "schema_version = 1\n[workspace]\nname = 'fixture'\n"
    "[[components]]\nid = 'app'\nroot = 'app'\nlanguages = ['cpp']\n"
)
BANNERS = {"clang-tidy": "LLVM version 21.1.6", "clazy-standalone": "clazy version: 1.0"}
CASES = {
    "neither": [],
    "clazy-only": ["clazy-standalone"],
    "clang-tidy-only": ["clang-tidy"],
    "both": ["clang-tidy", "clazy-standalone"],
}


def hidden(name: str) -> bool:
    return name.startswith(("clang-tidy", "clazy"))


def tools(base: Path, names: list[str]) -> None:
    binaries = base / "bin"
    binaries.mkdir()
    for parent in ("/usr/bin", "/bin"):
        for entry in sorted(os.listdir(parent)):
            link = binaries / entry
            if not hidden(entry) and not link.is_symlink():
                link.symlink_to(Path(parent) / entry)
    for name in names:
        wrapper = binaries / name
        log = base / f"{name}.log"
        wrapper.write_text(
            '#!/bin/sh\ncase "$1" in\n'
            f'--version) echo "{BANNERS[name]}" ;;\n*) echo run >> "{log}" ;;\nesac\nexit 0\n'
        )
        wrapper.chmod(0o755)


def scenario(tool: list, oracle: str | None, names: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        (base / ".hg").write_text("")
        tools(base, names)
        root = base / "project"
        (root / "app").mkdir(parents=True)
        (root / "app/a.cpp").write_text(SOURCE)
        (root / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(root),
                        "file": "app/a.cpp",
                        "arguments": ["/usr/bin/g++", "-Wall", "-c", "app/a.cpp"],
                    }
                ]
            )
        )
        (root / "ici.toml").write_text(CONFIG)
        code = subprocess.run(
            [*tool, "next", "verify", "--result", "now.json"],
            cwd=root,
            env={
                "HOME": str(base),
                "PATH": str(base / "bin"),
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": str(base),
                **({"PYTHONPATH": oracle} if oracle else {}),
            },
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        now = json.loads((root / "now.json").read_text())
        ran = sorted(name for name in BANNERS if (base / f"{name}.log").exists())
    return {
        "exit": code,
        "state": next(c["state"] for c in now["checks"] if c["task_id"] == "app.cpp.tidy"),
        "limitations": sorted(
            text for text in now["limitations"] if "tidy" in text or "clazy" in text.lower()
        ),
        "ran": ran,
    }


def main() -> None:
    python, output, mode, value = sys.argv[1:5]
    tool = [python, "-m", "ici"] if mode == "--frozen" else [value]
    oracle = value if mode == "--frozen" else None
    document = {
        "source": SOURCE,
        "config": CONFIG,
        "banners": BANNERS,
        "cases": {
            name: {"tools": names, **scenario(tool, oracle, names)} for name, names in CASES.items()
        },
    }
    Path(output).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
