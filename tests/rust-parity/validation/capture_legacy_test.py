"""Development-only capture: frozen legacy `ici test --report` targets, findings and summary."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
CONFIG = (
    '[ici]\nprofile = "standard"\n[project]\nsource_dirs = ["src"]\n'
    '[engines.test]\npython = "<PYTHON>"\n'
)
SCENARIOS = {
    "failing": {
        "src/calc.py": "def add(a, b):\n    return a + b\n\n\ndef unused():\n    return 0\n",
        "tests/test_calc.py": (
            "import pytest\nfrom calc import add\n\n\ndef test_add():\n"
            "    assert add(1, 2) == 3\n\n\ndef test_bad():\n    assert add(1, 1) == 3\n\n\n"
            '@pytest.mark.skip(reason="x")\ndef test_skip():\n    pass\n\n\n'
            "@pytest.mark.xfail\ndef test_xfail():\n    assert False\n"
        ),
    },
    "passing": {
        "src/calc.py": (
            "def classify(value):\n    if value > 0:\n        return 'positive'\n"
            "    return 'other'\n\n\ndef unused(value):\n    total = value + 1\n"
            "    return total\n"
        ),
        "tests/test_calc.py": (
            "from calc import classify\n\n\ndef test_positive():\n"
            "    assert classify(1) == 'positive'\n"
        ),
        "extra_config": 'changed_lines = ["src/calc.py:2-4"]\n',
    },
    "changed_unexecutable": {
        "src/calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "extra_config": 'changed_lines = ["src/calc.py:3-4"]\n',
    },
    "changed_invalid": {
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "extra_config": 'changed_lines = ["src/calc.py:1-2", "src/calc.py:2"]\n',
    },
    "unittest": {
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": (
            "import unittest\nfrom calc import add\n\n\nclass CalcTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n"
            "    def test_bad(self):\n        self.assertEqual(add(1, 1), 3)\n\n"
            '    @unittest.skip("x")\n    def test_skip(self):\n        pass\n\n'
            "    @unittest.expectedFailure\n    def test_xfail(self):\n        self.assertTrue(False)\n"
        ),
        "interpreter": "without-pytest",
    },
    "empty": {
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/helper.py": "VALUE = 1\n",
    },
    "no_coverage": {
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "interpreter": "without-coverage",
    },
}
EXTRA = (
    "function_coverage",
    "line_coverage",
    "metrics_summary",
    "pass_rate",
    "passed_tests",
    "quality_enabled",
    "skipped_tests",
    "tem_score",
    "test_suites",
    "total_tests",
)


def capture(
    oracle: Path, python: Path, bare: Path, no_pytest: Path, files: dict[str, str]
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-legacy-test-") as temporary:
        root = Path(temporary)
        for relative, text in files.items():
            if relative in ("extra_config", "interpreter"):
                continue
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        project = {"without-coverage": bare, "without-pytest": no_pytest}.get(
            files.get("interpreter", ""), python
        )
        config = CONFIG.replace("<PYTHON>", str(project)) + files.get("extra_config", "")
        (root / "ici.toml").write_text(config, encoding="utf-8")
        process = subprocess.run(
            [str(python), "-m", "ici", "test", "--report"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "test_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    return {
        "exit_code": process.returncode,
        "engine": {
            key: engine[key]
            for key in ("status", "summary", "score", "max_score", "evidence", "required")
        },
        "targets": [
            {
                key: target.get(key)
                for key in (
                    "file_path",
                    "start_line",
                    "end_line",
                    "target_name",
                    "status",
                    "message",
                    "metrics",
                )
            }
            for target in engine["targets"]
        ],
        "findings": sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "message": finding["message"],
                    "fingerprint": finding["fingerprint"],
                }
                for finding in engine["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        ),
        "extra": {key: engine["extra"].get(key) for key in EXTRA},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument(
        "--python-without-coverage",
        type=Path,
        required=True,
        help="a project interpreter with pytest but without coverage.py",
    )
    parser.add_argument(
        "--python-without-pytest",
        type=Path,
        required=True,
        help="a project interpreter with neither pytest nor coverage.py",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "config": CONFIG,
        "scenarios": SCENARIOS,
        "runs": {
            name: capture(
                args.oracle,
                args.python,
                args.python_without_coverage,
                args.python_without_pytest,
                files,
            )
            for name, files in SCENARIOS.items()
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
