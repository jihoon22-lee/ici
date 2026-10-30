"""Development-only capture: frozen legacy `ici sanitize --report` on Python projects."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
CONFIG = '[ici]\nprofile = "deep"\n[project]\nsource_dirs = ["src"]\n[engines.test]\npython = "<PYTHON>"\n'
SCENARIOS = {
    "clean": {
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    },
    "leak": {
        "src/calc.py": (
            "import warnings\n\n\ndef read(path):\n"
            "    warnings.warn(f'unclosed file {path}', ResourceWarning)\n    return 'x'\n"
        ),
        "tests/test_calc.py": (
            "from calc import read\n\n\ndef test_read():\n    assert read('x.txt') == 'x'\n"
        ),
    },
}


def capture(oracle: Path, python: Path, files: dict[str, str]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-legacy-pysan-") as temporary:
        root = Path(temporary)
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(CONFIG.replace("<PYTHON>", str(python)), encoding="utf-8")
        process = subprocess.run(
            [str(python), "-m", "ici", "sanitize", "--report"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "sanitize_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    return {
        "exit_code": process.returncode,
        "engine": {key: engine[key] for key in ("status", "summary", "evidence")},
        "targets": [
            {
                key: target[key]
                for key in ("file_path", "start_line", "target_name", "status", "message")
            }
            for target in engine["targets"]
        ],
        "findings": sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "line": finding["primary_location"]["start_line"],
                    "message": finding["message"],
                    "fingerprint": finding["fingerprint"],
                }
                for finding in engine["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "config": CONFIG,
        "scenarios": SCENARIOS,
        "runs": {
            name: capture(args.oracle, args.python, files) for name, files in SCENARIOS.items()
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
