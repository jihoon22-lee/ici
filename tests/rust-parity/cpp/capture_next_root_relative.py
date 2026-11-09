"""Capture frozen v0.13.0 `ici next verify` finding paths of a C++ component with root="src".

    python -I capture_next_root_relative.py <oracle-root> <output.json>

Development only; never called by the Rust product. Frozen next findings are component-relative;
native keeps workspace-relative paths (decisions.md "하위 root component의 next finding 경로").
`next_cpp_root_relative.rs` writes the same tree, compares through the real CLI and feeds the
recorded `result.json` to native `next verify --baseline` for the upgrade impact.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = (
    "schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n"
    "[[components]]\nid='lib'\nroot='src'\nlanguages=['cpp']\n"
)
TREE = {
    "ici.toml": CONFIG,
    "src/a.hpp": '#include "b.hpp"\n',
    "src/b.hpp": '#include "a.hpp"\n',
    "src/main.cpp": '#include "a.hpp"\nint main() { return 0; }\n',
}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in TREE.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text, encoding="utf-8")
        (base / ".hg").mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                ENTRY,
                str(oracle / "src"),
                "next",
                "verify",
                "--no-cache",
            ],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        result = json.loads((base / ".ici/next/result.json").read_text(encoding="utf-8"))
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "tree": TREE,
        "exit_code": completed.returncode,
        "result": result,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print(completed.returncode, [finding["message"] for finding in result["findings"]])


if __name__ == "__main__":
    main()
