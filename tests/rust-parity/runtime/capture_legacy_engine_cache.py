"""Development-only capture: frozen legacy `ici verify` engine cache fields (F8, verify.py).

    python -I capture_legacy_engine_cache.py --oracle <v0.13.0 checkout> --python <venv python>

Frozen `VerifyOrchestrator.run_all` stores each cache-safe engine result under an
`AnalysisCache` key and reuses it on the next run (`cache_hit`), and `--no-cache` builds no key.
Runs frozen `ici verify --report` twice and then with `--no-cache` on one Python project in a
fresh HOME with PATH=/usr/bin:/bin and records, per engine, `cache_hit` and whether a
`cache_key` was set, in `legacy-engine-cache-oracle.json`.
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
TREE = {"src/core.py": "import pickle\n\n\ndef load(blob):\n    return pickle.loads(blob)\n"}
RUNS = (["verify", "--report"], ["verify", "--report"], ["verify", "--report", "--no-cache"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    runs = []
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "project"
        for relative, text in TREE.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / ".git").mkdir()
        for argv in RUNS:
            process = subprocess.run(
                [str(args.python), "-I", "-c", ENTRY, str(args.oracle / "src"), *argv],
                cwd=root,
                env={"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
            suite = json.loads((root / "verify_report.json").read_text(encoding="utf-8"))
            runs.append(
                {
                    "argv": argv,
                    "exit_code": process.returncode,
                    "engines": {
                        engine["engine_name"]: {
                            "cache_hit": engine["cache_hit"],
                            "keyed": bool(engine["cache_key"]),
                        }
                        for engine in suite["results"]
                    },
                }
            )
    payload = {"oracle": SOURCE, "tree": TREE, "runs": runs}
    out = Path(__file__).with_name("legacy-engine-cache-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for run in runs:
        print(run["argv"], run["exit_code"], run["engines"])


if __name__ == "__main__":
    main()
