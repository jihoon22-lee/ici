"""Capture frozen v0.13.0 `ici next verify --json` on sources around native's input bounds.

    python -I capture_cpp_line_bound.py <oracle-root> <output.json>

Development only; never called by the Rust product. Sparse sources (one line, then NUL bytes) keep
the trees cheap on disk. `cpp.line` is the only enabled check, so these cases pin what frozen
measures at and one byte past the native 16 MiB per-file and 512 MiB aggregate input limits
(decisions.md); `next_cpp_line_bound.rs` writes the same trees through the real native CLI.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
MIB = 1024 * 1024
OFF = (
    "compile",
    "diagnostics",
    "tidy",
    "test",
    "coverage",
    "artifact",
    "binary-compat",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "exception",
    "sanitize",
    "tsan",
)
CONFIG = (
    'schema_version = 1\n[workspace]\nname = "probe"\nprofile = "standard"\n'
    '[[components]]\nid = "app"\nroot = "src"\nlanguages = ["cpp"]\n'
    + "".join(f'[checks."cpp.{check}"]\nenabled = false\n' for check in OFF)
)
CASES = {
    "file-at-limit": [16 * MIB],
    "file-past-limit": [16 * MIB + 1],
    "aggregate-at-limit": [16 * MIB] * 31 + [16 * MIB - len(CONFIG)],
    "aggregate-past-limit": [16 * MIB] * 31 + [16 * MIB - len(CONFIG) + 1],
}


def view(document):
    return {
        "gate": document["gate"],
        "checks": [
            {key: check[key] for key in ("check_id", "state", "evidence_complete")}
            for check in document["checks"]
        ],
        "metrics": [
            {key: metric[key] for key in ("name", "value", "evidence")}
            for metric in document["metrics"]
            if metric["name"].startswith("lines_")
        ],
    }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "cases": {},
    }
    for name, sizes in CASES.items():
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "project"
            (base / "src").mkdir(parents=True)
            (base.parent / ".hg").mkdir()
            (base / "ici.toml").write_text(CONFIG, encoding="utf-8")
            for index, size in enumerate(sizes):
                with (base / f"src/f{index:02}.cpp").open("wb") as stream:
                    stream.write(b"int a;\n")
                    stream.truncate(size)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    ENTRY,
                    str(oracle / "src"),
                    "next",
                    "verify",
                    "--json",
                ],
                cwd=base,
                capture_output=True,
                text=True,
                env={
                    "HOME": home,
                    "XDG_CONFIG_HOME": home,
                    "PATH": "/usr/bin:/bin",
                    "LC_ALL": "C.UTF-8",
                    "GIT_CEILING_DIRECTORIES": temporary,
                },
                check=False,
            )
        document["cases"][name] = {
            "sizes": sizes,
            "exit_code": completed.returncode,
            **view(json.loads(completed.stdout)),
        }
        case = document["cases"][name]
        print(name, case["exit_code"], case["checks"], case["metrics"][:1], flush=True)
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
