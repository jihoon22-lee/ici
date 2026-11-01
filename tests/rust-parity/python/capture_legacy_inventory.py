"""Development-only capture: frozen legacy complexity/cycle engine reports (F6).

    python -I capture_legacy_inventory.py --oracle <v0.13.0 checkout> --python <venv python>

Frozen `ici complexity --report` and `ici cycle --report` on one Python project (a file without
functions, decorated/async/nested functions under if/try/except/class/match/with/for-else, and a
two-module import cycle) in a fresh HOME with PATH=/usr/bin:/bin. Records the exit code and the
engine report without its run-dependent `duration`, in `legacy-inventory-oracle.json`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
# Fixture source text the frozen analyses read; written to disk, never imported or executed.
TREE = {
    "src/__init__.py": '"""Package."""\n',
    "src/core.py": (
        "import pickle\nimport subprocess\n\n\ndef load(blob):\n    return pickle.loads(blob)\n\n\n"
        "def run(cmd):\n    return subprocess.call(cmd, shell=True)\n"
    ),
    "src/nested.py": (
        "import functools\n\n\n@functools.cache\ndef deco():\n    return 1\n\n\n"
        "if True:\n    def inif():\n        pass\n\ntry:\n    pass\nexcept Exception:\n"
        "    def inexc():\n        pass\n\n\nclass K:\n    @staticmethod\n"
        "    async def am():\n        return 2\n\n\ndef last(x):\n    def inner():\n"
        "        if x:\n            return 3\n        return 4\n    return inner\n\n\n"
        "match 1:\n    case 1:\n        def m1():\n            pass\n"
        "with open('x') as handle:\n    def w():\n        pass\n"
        "for i in range(2):\n    pass\nelse:\n    def fe():\n        pass\n"
    ),
    "src/a.py": "import b\n",
    "src/b.py": "import a\n",
}
ENGINES = ("complexity", "cycle")


def capture(oracle: Path, python: Path, engine: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "project"
        for relative, text in TREE.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / ".git").mkdir()
        process = subprocess.run(
            [str(python), "-I", "-c", ENTRY, str(oracle / "src"), engine, "--report"],
            cwd=root,
            env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        text = (root / f"{engine}_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(root), "<root>"))
    report.pop("duration", None)
    return {"exit_code": process.returncode, "report": report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    payload = {
        "oracle": SOURCE,
        "tree": TREE,
        "cases": {engine: capture(args.oracle, args.python, engine) for engine in ENGINES},
    }
    out = Path(__file__).with_name("legacy-inventory-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for engine, case in payload["cases"].items():
        report = case["report"]
        print(engine, case["exit_code"], report["status"], report["summary"])
        for target in report["targets"]:
            print("  ", target["file_path"], target["start_line"], target["target_name"])


if __name__ == "__main__":
    main()
