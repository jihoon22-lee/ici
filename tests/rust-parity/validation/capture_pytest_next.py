"""Development-only capture of frozen `next verify --python` pytest provider findings."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
FAILING = "def test_bad():\n    assert 1 == 2\n\n\ndef test_ok():\n    pass\n"
BROKEN = "import nonexistent_mod\n\n\ndef test_x():\n    pass\n"
SKIPPED = "import pytest\n\n\n@pytest.mark.skip(reason='later')\ndef test_later():\n    pass\n"
GREEN = "def test_ok():\n    pass\n"
EMPTY = "X = 1\n"
INTERRUPTED = "import pytest\n\n\ndef test_stop():\n    pytest.exit('stop', returncode=2)\n"
# A plugin that reports node ids outside the project (frozen `_node_location` keeps the
# verdict at the stable `tests` scope instead of pointing outside).
ESCAPING = (
    "def pytest_collection_modifyitems(items):\n"
    "    for item in items:\n"
    "        item._nodeid = '../outside/' + item.nodeid.rsplit('/', 1)[-1]\n"
)
SCENARIOS = {
    "failing": {"tests/test_a.py": FAILING},
    "collection": {"tests/test_b.py": BROKEN},
    # The provider limitation and exit-contract paths of adapters/providers/pytest.py.
    "green": {"tests/test_c.py": GREEN},
    "all-skipped": {"tests/test_d.py": SKIPPED},
    "no-tests": {"tests/test_e.py": EMPTY},
    "interrupted": {"tests/test_f.py": INTERRUPTED},
    "escaping-node": {"tests/conftest.py": ESCAPING, "tests/test_g.py": FAILING},
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
    document: dict[str, object] = {"source": SOURCE, "config": config("<PYTHON>"), "runs": {}}
    for name, files in SCENARIOS.items():
        with tempfile.TemporaryDirectory(prefix="ici-pytest-oracle-") as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "app.py").write_text("X = 1\n", encoding="utf-8")
            for path, text in files.items():
                (root / path).write_text(text, encoding="utf-8")
            (root / "ici.toml").write_text(config(str(args.python)), encoding="utf-8")
            completed = subprocess.run(
                [str(args.python), "-m", "ici", "next", "verify", "--python", "--no-cache"],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(args.oracle / "src")),
                capture_output=True,
                check=False,
                timeout=900,
            )
            result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
        document["runs"][name] = {
            "files": files,
            "checks": {
                item["check_id"]: item["state"]
                for item in result["checks"]
                if item["check_id"] == "python.test"
            },
            "findings": [
                {key: item.get(key) for key in ("rule_id", "message", "fingerprint")}
                | {"path": item["primary_location"]["path"]}
                for item in result["findings"]
                if item.get("provider") == "pytest"
            ],
            "metrics": [item for item in result["metrics"] if item["name"] == "pytest.cases"],
            "test_check": {
                key: item.get(key)
                for item in result["checks"]
                if item["check_id"] == "python.test"
                for key in ("state", "evidence_complete", "limitations", "reason")
            },
            "limitations": [
                text for text in result["limitations"] if "pytest" in text or "test" in text
            ],
            "gate": result["gate"]["selected"],
            "exit_code": completed.returncode,
        }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
