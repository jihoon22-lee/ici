"""Development-only capture: frozen legacy `ici test --report` on C++ trees without any test."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
FIXTURES = Path(__file__).resolve().parents[3] / "parity-fixtures" / "cpp" / "legacy-no-tests"


def capture(oracle: Path, python: Path, fixture: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-legacy-no-tests-") as temporary:
        root = Path(temporary) / "project"
        shutil.copytree(fixture, root)
        process = subprocess.run(
            [str(python), "-m", "ici", "test", "--report"],
            cwd=root,
            env=dict(os.environ, PATH="/usr/bin:/bin", PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "test_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    return {
        "exit_code": process.returncode,
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [
            {
                key: target[key]
                for key in ("file_path", "start_line", "target_name", "status", "message")
            }
            for target in engine["targets"]
        ],
        "findings": [
            {
                "rule_id": finding["rule_id"],
                "path": finding["primary_location"]["path"],
                "message": finding["message"],
                "fingerprint": finding["fingerprint"],
            }
            for finding in engine["findings"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "fixtures": "parity-fixtures/cpp/legacy-no-tests",
        "runs": {
            fixture.name: capture(args.oracle, args.python, fixture)
            for fixture in sorted(FIXTURES.iterdir())
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
