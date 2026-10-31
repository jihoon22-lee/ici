"""Development-only capture of the frozen legacy integration engine through `ici verify`.

The fixture uses an explicit absolute Python target so the frozen `current` (= ici's own
interpreter) mapping is not exercised; native never falls back to its own runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
DISABLED = (
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "python_compat",
    "cognitive",
    "resource",
    "security",
    "cycle",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "binary_compat",
)


def config(python: str) -> str:
    text = (
        '[ici]\nprofile = "deep"\n[engines]\n[engines.integration]\nenabled = true\n'
        f"required = true\npython_targets = {{ py = {json.dumps(python)} }}\n"
        '[[engines.integration.cases]]\nname = "contract one"\n'
        'argv = ["{python:py}", "contract.py"]\nstdout_contains = ["missing"]\n'
        '[[engines.integration.cases]]\nname = "advisory two"\nrequired = false\n'
        'argv = ["{python:py}", "advisory.py"]\n'
    )
    for engine in DISABLED:
        text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = true\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-integration-oracle-") as temporary:
        root = Path(temporary)
        (root / "ici.toml").write_text(config(str(args.python)), encoding="utf-8")
        (root / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / "contract.py").write_text("print('actual')\n", encoding="utf-8")
        (root / "advisory.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(args.oracle / "src"))
        completed = subprocess.run(
            [
                str(args.python),
                "-m",
                "ici",
                "verify",
                "--report",
                "--profile",
                "deep",
                "--no-cache",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        report = json.loads((root / "verify_report.json").read_text(encoding="utf-8"))
    engine = next(item for item in report["results"] if item["engine_name"] == "integration")
    for case in engine["extra"]["integration"]["cases"]:
        case.pop("duration_seconds")
    document = {
        "source": SOURCE,
        "exit_code": completed.returncode,
        "suite_status": report["suite_status"],
        "engines": {item["engine_name"]: item["status"] for item in report["results"]},
        "fixture_config": config("<PYTHON>"),
        "integration": {
            key: engine[key]
            for key in ("status", "summary", "required", "evidence", "targets", "findings")
        }
        | {"extra": engine["extra"]},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
