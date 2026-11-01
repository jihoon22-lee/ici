"""Development-only capture: frozen pytest case order/counts and legacy ERROR summaries.

    python -I capture_pytest_views.py --oracle <oracle> --python <interpreter> --out <json>

Each scenario's files are run twice by the immutable v0.13.0 CLI, with an explicit project
interpreter that has pytest and coverage.py:

* legacy `ici test --report` (stable `TestEngine`): engine status/summary/evidence, the case
  targets in the order frozen wrote them, passed/total extras;
* `ici next verify --python --no-cache` (next pytest provider): the `pytest.cases` metric,
  pytest findings with paths, and the test check state/limitations.

Scenarios: execution order differing from node-id order (with an xfail and parameters), a
verdict line a plugin repeats, a collection-error banner naming an absolute path inside and
outside the project, and the three legacy `_parse_pytest_result` ERROR branches (exit 0
without parseable results, an exit outside 0/1, exit 1 without a failure).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
LEGACY = '[ici]\nprofile = "standard"\n[project]\nsource_dirs = ["src"]\n'
LEGACY += '[engines.test]\npython = "<PYTHON>"\n'
NEXT = (
    "schema_version=1\n[workspace]\nname='w'\nprofile='standard'\n[[components]]\n"
    "id='project'\nroot='.'\nlanguages=['python']\n[components.python]\n"
    "executable=<PYTHON_JSON>\n"
)
SOURCE_FILE = {"src/calc.py": "def add(a, b):\n    return a + b\n"}
ORDER = (
    "import pytest\n\n\ndef test_b():\n    pass\n\n\ndef test_a():\n    pass\n\n\n"
    "@pytest.mark.xfail\ndef test_c_xfail():\n    assert False\n\n\n"
    '@pytest.mark.parametrize("v", [2, 1])\ndef test_p(v):\n    pass\n'
)
# A plugin that repeats one verdict line at the end of the terminal report.
REPEAT = (
    "def pytest_terminal_summary(terminalreporter):\n"
    "    terminalreporter.write_line('tests/test_order.py::test_b PASSED')\n"
)
# Banners naming a collection error by absolute path (inside, then outside, the project).
BANNERS = (
    "import os\n\n\ndef pytest_terminal_summary(terminalreporter):\n"
    "    here = os.path.dirname(os.path.abspath(__file__))\n"
    "    terminalreporter.write_line('ERROR collecting ' + os.path.join(here, 'test_x.py'))\n"
    "    terminalreporter.write_line('ERROR collecting /nonexistent-elsewhere/test_y.py')\n"
)
GREEN = "def test_ok():\n    pass\n"
# The terminal reporter is removed, so the run prints nothing parseable except this line.
SILENT = (
    "import pytest\n\n\n@pytest.hookimpl(trylast=True)\ndef pytest_configure(config):\n"
    "    reporter = config.pluginmanager.getplugin('terminalreporter')\n"
    "    if reporter is not None:\n"
    "        config.pluginmanager.unregister(reporter)\n"
    "    print('collected 2 items', flush=True)\n"
)
EXIT_ONE = "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n"
BROKEN = "import nonexistent_module_for_ici\n\n\ndef test_x():\n    pass\n"
SCENARIOS = {
    "order": {"tests/test_order.py": ORDER},
    "repeated-line": {"tests/test_order.py": ORDER, "tests/conftest.py": REPEAT},
    "absolute-collection": {"tests/test_order.py": GREEN, "tests/conftest.py": BANNERS},
    "success-unparseable": {"tests/test_ok.py": GREEN, "tests/conftest.py": SILENT},
    "exit-outside-contract": {"tests/test_broken.py": BROKEN},
    "exit-one-no-failure": {"tests/test_ok.py": GREEN, "tests/conftest.py": EXIT_ONE},
}


def write(root: Path, files: dict[str, str]) -> None:
    for relative, text in {**SOURCE_FILE, **files}.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")


def run(python: Path, oracle: Path, root: Path, argv: list[str]) -> int:
    completed = subprocess.run(
        [str(python), "-m", "ici", *argv],
        cwd=root,
        env=dict(
            os.environ,
            PYTHONPATH=str(oracle / "src"),
            PATH="/usr/bin:/bin",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ),
        capture_output=True,
        check=False,
        timeout=900,
    )
    return completed.returncode


def legacy(python: Path, oracle: Path, files: dict[str, str]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-pytest-views-") as temporary:
        root = Path(temporary)
        write(root, files)
        (root / "ici.toml").write_text(LEGACY.replace("<PYTHON>", str(python)), encoding="utf-8")
        code = run(python, oracle, root, ["test", "--report"])
        engine = json.loads((root / "test_report.json").read_text(encoding="utf-8"))
    return {
        "exit_code": code,
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "cases": [
            [target["target_name"], target["status"], target["message"]]
            for target in engine["targets"]
            if "::" in target["target_name"] or target["target_name"].startswith("[Python]")
        ],
        "extra": {
            key: engine["extra"].get(key) for key in ("passed_tests", "total_tests", "pass_rate")
        },
    }


def next_view(python: Path, oracle: Path, files: dict[str, str]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-pytest-views-") as temporary:
        root = Path(temporary)
        write(root, files)
        config = NEXT.replace("<PYTHON_JSON>", json.dumps(str(python)))
        (root / "ici.toml").write_text(config, encoding="utf-8")
        code = run(python, oracle, root, ["next", "verify", "--python", "--no-cache"])
        result = json.loads((root / ".ici/next/result.json").read_text(encoding="utf-8"))
        prefix = str(root)
    check = next(item for item in result["checks"] if item["check_id"] == "python.test")
    return {
        "exit_code": code,
        "metrics": [item for item in result["metrics"] if item["name"] == "pytest.cases"],
        "findings": [
            {
                "rule_id": item["rule_id"],
                "message": item["message"].replace(prefix, "<root>"),
                "fingerprint": item["fingerprint"],
                "path": item["primary_location"]["path"].replace(prefix, "<root>"),
                "line": item["primary_location"]["start_line"],
            }
            for item in result["findings"]
            if item.get("provider") == "pytest"
        ],
        "check": {
            key: check.get(key) for key in ("state", "evidence_complete", "limitations", "reason")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "legacy_config": LEGACY,
        "next_config": NEXT,
        "source_file": SOURCE_FILE,
        "scenarios": SCENARIOS,
        "runs": {
            name: {
                "legacy": legacy(args.python, args.oracle, files),
                "next": next_view(args.python, args.oracle, files),
            }
            for name, files in SCENARIOS.items()
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, run_ in document["runs"].items():
        print(name, json.dumps(run_["legacy"]["summary"]), run_["next"]["metrics"])


if __name__ == "__main__":
    main()
