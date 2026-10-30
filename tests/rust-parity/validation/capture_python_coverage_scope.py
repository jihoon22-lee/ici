"""Development-only capture: frozen `next verify` silently omits a never-imported source."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-coverage-scope-") as temporary:
        root = Path(temporary)
        (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        (root / "test_slow.py").write_text("def test_slow():\n    assert True\n", encoding="utf-8")
        (root / "ici.toml").write_text(
            "schema_version=1\n[workspace]\nname='w'\nprofile='deep'\n[[components]]\n"
            "id='project'\nroot='.'\nlanguages=['python']\n[components.python]\n"
            f"executable={json.dumps(str(args.python))}\n",
            encoding="utf-8",
        )
        subprocess.run(
            [str(args.python), "-m", "ici", "next", "verify", "--profile", "deep", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(args.oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
    document = {
        "source": SOURCE,
        "checks": {
            item["check_id"]: item["state"]
            for item in result["checks"]
            if item["check_id"] in ("python.test", "python.coverage")
        },
        "metrics": [item for item in result["metrics"] if item["name"].startswith("coverage.")],
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
