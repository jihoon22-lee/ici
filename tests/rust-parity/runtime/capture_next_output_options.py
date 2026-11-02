"""Development-only capture: frozen `--output`/`--no-cache` of analyze, diff and publish (F7).

    python -I capture_next_output_options.py --oracle <v0.13.0 checkout> --python <venv python>

One workspace with a Python component whose tool checks are disabled (no external tool runs),
in a fresh HOME with PATH=/usr/bin:/bin. Each case runs its frozen CLI steps in order, editing
sources between steps, and records per step the argv, exit code, stdout/stderr lines (the
project root as `<root>`), the files outside `.ici/` and `.git/`, and for a written result the
reused task ids and gate. Written to `next-output-options-oracle.json`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = (
    'schema_version = 1\n[workspace]\nname = "demo"\n'
    '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
    + "".join(
        f'[checks."python.{check}"]\nenabled = false\n'
        for check in ("lint", "format", "type", "test", "coverage", "compat-runtime")
    )
)
# Fixture source text the frozen analyses read; written to disk, never imported or executed.
CLEAN = "def load(blob):\n    return blob\n"
PICKLE = "import pickle\n\n\ndef load(blob):\n    return pickle.loads(blob)\n"
TWO = CONFIG.replace(
    "[checks.", '[[components]]\nid = "lib"\nroot = "lib"\nlanguages = ["python"]\n[checks.', 1
)
CASES = {
    # --output is --result: only that file is written outside .ici/; --no-cache reuses nothing;
    # diff reads the current result from --output/--result and prints the gate change.
    "analyze-output-diff": [
        ({}, ["analyze", "--output", "out/a.json"]),
        ({"app/core.py": PICKLE}, ["analyze", "--output", "out/b.json", "--no-cache"]),
        ({}, ["analyze", "--result", "out/c.json", "--no-cache"]),
        ({}, ["next", "diff", "out/a.json", "--output", "out/b.json"]),
        ({}, ["next", "diff", "out/a.json", "--result", "out/b.json"]),
        ({}, ["next", "diff", "out/b.json", "--output", "out/c.json"]),
        ({}, ["next", "diff", "out/a.json", "--output", "out/missing.json"]),
    ],
    # A component the newer run did not select carries its findings (the carried note).
    "diff-carried": [
        ({"ici.toml": TWO, "lib/core.py": PICKLE}, ["analyze", "--output", "out/a.json"]),
        ({}, ["analyze", "--output", "out/b.json", "--component", "app"]),
        ({}, ["next", "diff", "out/a.json", "--output", "out/b.json"]),
    ],
    # publish reads the result named by --output/--result and the page by --out.
    "publish-output": [
        ({}, ["analyze", "--output", "out/a.json"]),
        ({}, ["next", "publish", "--output", "out/a.json"]),
        ({}, ["next", "publish", "--result", "out/missing.json"]),
        ({}, ["next", "publish", "--output", "out/a.json", "--out", "out/none.html"]),
    ],
}


def files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] not in {".ici", ".git"}
    )


def result_facts(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        "reused_task_ids": document["execution"]["reused_task_ids"],
        "gate": document["gate"]["selected"],
        "findings": len(document["findings"]),
    }


def capture(oracle: Path, python: Path, steps: list) -> list[dict[str, object]]:
    records = []
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "project"
        (root / "app").mkdir(parents=True)
        (root / "lib").mkdir()
        (root / ".git").mkdir()
        (root / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (root / "app/core.py").write_text(CLEAN, encoding="utf-8")
        for edits, argv in steps:
            for relative, text in edits.items():
                (root / relative).write_text(text, encoding="utf-8")
            process = subprocess.run(
                [str(python), "-I", "-c", ENTRY, str(oracle / "src"), *argv],
                cwd=root,
                env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
            written = None
            if argv[0] == "analyze":
                written = (
                    result_facts(root / argv[argv.index("--output") + 1])
                    if ("--output" in argv)
                    else result_facts(root / argv[argv.index("--result") + 1])
                )
            records.append(
                {
                    "edits": sorted(edits),
                    "argv": argv,
                    "exit_code": process.returncode,
                    "stdout": process.stdout.replace(str(root), "<root>").splitlines(),
                    "stderr": process.stderr.replace(str(root), "<root>").splitlines(),
                    "files": files(root),
                    "result": written,
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    payload = {
        "oracle": SOURCE,
        "config": CONFIG,
        "two_components": TWO,
        "sources": {"clean": CLEAN, "pickle": PICKLE},
        "cases": {name: capture(args.oracle, args.python, steps) for name, steps in CASES.items()},
    }
    out = Path(__file__).with_name("next-output-options-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, records in payload["cases"].items():
        print("==", name)
        for record in records:
            print(" ", record["argv"], record["exit_code"], record["result"])
            for line in record["stdout"] + record["stderr"]:
                print("    ", line)


if __name__ == "__main__":
    main()
