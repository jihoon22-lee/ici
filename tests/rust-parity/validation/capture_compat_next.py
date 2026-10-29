"""Development-only capture of frozen `next verify` runtime-version/compile-failure findings."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
FILES = {
    "app.py": "def f():\n    try:\n        pass\n    except* ValueError:\n        pass\n",
    "pyproject.toml": '[project]\nname = "demo"\nversion = "1.0"\nrequires-python = ">=3.11"\n',
}


def config(python: str) -> str:
    return (
        "schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n[[components]]\n"
        "id='project'\nroot='.'\nlanguages=['python']\n[components.python]\n"
        f"executable={json.dumps(python)}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-compat-oracle-") as temporary:
        root = Path(temporary)
        for path, text in FILES.items():
            (root / path).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(str(args.python)), encoding="utf-8")
        subprocess.run(
            [str(args.python), "-m", "ici", "next", "verify", "--python", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(args.oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
    document = {
        "source": SOURCE,
        "config": config("<PYTHON>"),
        "files": FILES,
        "checks": {
            item["check_id"]: item["state"]
            for item in result["checks"]
            if item["check_id"] == "python.compat-runtime"
        },
        "findings": [
            {
                key: item.get(key)
                for key in ("rule_id", "message", "severity", "category", "provider")
            }
            | {"location": item["primary_location"]}
            for item in result["findings"]
            if str(item.get("rule_id", "")).startswith("python.compat.")
        ],
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
