"""Development-only capture of frozen `next verify --python` pytest provider findings."""

from __future__ import annotations

import argparse
import importlib.util
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
# A run that prints only a summary count (no per-test verdict line): frozen `_parse` has no
# outcome, finding or limitation to report (decisions.md "per-test 판정 없는 next pytest 실행").
SUMMARY_ONLY = (
    "import pytest\n\n\n@pytest.hookimpl(trylast=True)\ndef pytest_configure(config):\n"
    "    reporter = config.pluginmanager.getplugin('terminalreporter')\n"
    "    if reporter is not None:\n"
    "        config.pluginmanager.unregister(reporter)\n"
    "    print('1 passed', flush=True)\n"
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
    "summary-only": {"tests/conftest.py": SUMMARY_ONLY, "tests/test_h.py": GREEN},
}


# Sub-root components (decisions.md "하위 root component의 next finding 경로"): frozen findings are
# relative to the component (`_relative(path, cwd)`, ruff the same), so two components can name
# the same path; ruff is the second provider beside pytest.
UNUSED = "import os\n"
SUB_ROOT = {
    "subroot-failing": {
        "roots": {"project": "app"},
        "files": {"app/tests/test_a.py": FAILING, "app/m.py": UNUSED},
    },
    "subroot-collection": {
        "roots": {"project": "app"},
        "files": {"app/tests/test_b.py": BROKEN, "app/m.py": UNUSED},
    },
    "subroot-two-components": {
        "roots": {"a": "a", "b": "b"},
        "files": {
            "a/m.py": UNUSED,
            "b/m.py": "import sys\n",
            "a/tests/test_a.py": GREEN,
            "b/tests/test_b.py": GREEN,
        },
    },
}


# Frozen next `parse_coverage_json` without expected files: a report whose totals count no
# statement is `coverage json was absent, unreadable or inconsistent`.
NO_STATEMENTS = {
    "docstring-only": {
        "roots": {"project": "."},
        "files": {
            ".coveragerc": "[run]\nomit = tests/*\n",
            "app.py": '"""Docstring only."""\n',
            "tests/test_c.py": "import app\n\n\ndef test_ok():\n    assert app.__doc__\n",
        },
    },
}


def config(python: str, roots: dict[str, str] | None = None) -> str:
    components = "".join(
        f"[[components]]\nid='{name}'\nroot='{root}'\nlanguages=['python']\n"
        f"[components.python]\nexecutable={json.dumps(python)}\n"
        for name, root in (roots or {"project": "."}).items()
    )
    return f"schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n{components}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--ruff", type=Path, required=True)
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
                env=dict(
                    os.environ,
                    PYTHONPATH=str(args.oracle / "src"),
                    GIT_CEILING_DIRECTORIES=str(root.parent),
                ),
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
    scenarios = [("sub_root", SUB_ROOT), ("no_statements", NO_STATEMENTS)]
    for section, name, scenario in (
        (section, name, scenario)
        for section, group in scenarios
        for name, scenario in group.items()
    ):
        with tempfile.TemporaryDirectory(prefix="ici-pytest-oracle-") as temporary:
            root = Path(temporary)
            for path, text in scenario["files"].items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text, encoding="utf-8")
            (root / "ici.toml").write_text(
                config(str(args.python), scenario["roots"]), encoding="utf-8"
            )
            completed = subprocess.run(
                [str(args.python), "-m", "ici", "next", "verify", "--python", "--no-cache"],
                cwd=root,
                env=dict(
                    os.environ,
                    PYTHONPATH=str(args.oracle / "src"),
                    GIT_CEILING_DIRECTORIES=str(root.parent),
                    PATH=f"{args.ruff.parent}:{os.environ['PATH']}",
                ),
                capture_output=True,
                check=False,
                timeout=900,
            )
            result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
        document.setdefault(section, {})[name] = {
            "coverage": {
                key: item.get(key)
                for item in result["checks"]
                if item["check_id"] == "python.coverage"
                for key in ("state", "evidence_complete")
            },
            "roots": scenario["roots"],
            "files": scenario["files"],
            "exit_code": completed.returncode,
            "findings": [
                {
                    key: item.get(key)
                    for key in ("provider", "component_id", "rule_id", "message", "fingerprint")
                }
                | {"path": item["primary_location"]["path"]}
                for item in result["findings"]
                if item.get("provider") in ("pytest", "ruff")
            ],
        }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _private_home():
    """The shared private HOME/XDG context (tests/rust-parity/private_home.py), loaded by path."""
    path = Path(__file__).resolve().parents[1] / "private_home.py"
    spec = importlib.util.spec_from_file_location("rust_parity_private_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.private_home()


if __name__ == "__main__":
    with _private_home():
        main()
