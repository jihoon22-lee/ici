"""Capture frozen v0.13.0 `ici analyze --json` cycle identity under a tree move and a hash seed.

    python -I capture_next_cycle_identity.py <oracle-root> <output.json>

Development only; never called by the Rust product. Frozen's `cycle.include` fingerprint hashes
absolute checkout paths and its member order follows Python set order, so neither is stable; the
native identity (decisions.md "C++ cycle identity") hashes workspace-relative members.
`next_cpp_cycle_identity.rs` writes the same trees and proves the native side.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
MAIN = '#include "a.hpp"\nint main() { return 0; }\n'
CYCLE = {
    "src/main.cpp": MAIN,
    "src/a.hpp": '#include "b.hpp"\n#include "c.hpp"\n',
    "src/b.hpp": '#include "a.hpp"\n',
    "src/c.hpp": '#include "a.hpp"\n',
}
# One more member: a real change of the cycle.
CHANGED = {
    **CYCLE,
    "src/c.hpp": '#include "d.hpp"\n',
    "src/d.hpp": '#include "a.hpp"\n',
}
TREES = {"cycle": CYCLE, "changed": CHANGED}
RUNS = [("one", 0), ("one", 1), ("one", 2), ("one", 3), ("two/deeper", 0)]


def run(oracle, tree, parent, seed):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / parent / "project"
        for name, text in tree.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text, encoding="utf-8")
        (base.parent / ".hg").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "analyze", "--json"],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": str(seed),
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        document = json.loads(completed.stdout)
        finding = next(
            item for item in document["findings"] if item["rule_id"].startswith("cycle.")
        )
        return {
            "parent": parent,
            "seed": seed,
            "exit_code": completed.returncode,
            "rule_id": finding["rule_id"],
            "message": finding["message"],
            "fingerprint": finding["fingerprint"],
            "related": [item["path"] for item in finding["related_locations"]],
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "trees": TREES,
        "frozen": {
            name: [run(oracle, tree, parent, seed) for parent, seed in RUNS]
            for name, tree in TREES.items()
        },
    }
    for name, runs in document["frozen"].items():
        for item in runs:
            print(name, item["parent"], item["seed"], item["fingerprint"][:19], item["related"])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
