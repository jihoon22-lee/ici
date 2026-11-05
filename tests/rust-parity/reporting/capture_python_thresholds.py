"""Capture frozen v0.13.0 `ici test --report` TEM/branch threshold targets of Python projects.

    python -I capture_python_thresholds.py <oracle-root> <output.json>

Each case runs the frozen stable `ici test --report` in a fresh small Python project (empty
HOME, PATH without tools, the capture interpreter with pytest and coverage) and records the
saved engine report with the project root as `<root>`. `breach`: branch coverage under the
default minimum (TEM minimum lowered to 0.0), one `Threshold: Branch coverage` WARN target;
`met`: every branch covered, no threshold target; `failure`: a failing test, which frozen
`_threshold_breaches` never judges; `coverage-failure`: a `.coveragerc` naming a missing
coverage plugin, so the `coverage pytest` process itself fails (exit 1) and frozen records it
with an empty tool evidence `error`. The `pytest-*` cases are frozen's pytest evidence reading
(`pytest_result_has_evidence` and the `_parse_pytest_result` ERROR branches, see below). The
`functions-*` cases are frozen's Python function rows and function coverage (see below).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
SOURCE = (
    "def classify(x):\n"
    "    if x > 0:\n"
    '        return "pos"\n'
    "    if x < 0:\n"
    '        return "neg"\n'
    '    return "zero"\n'
)
HEADER = "from src.calc import classify\n\n\n"
POSITIVE = 'def test_positive():\n    assert classify(1) == "pos"\n'
EVERY = (
    POSITIVE
    + '\n\ndef test_negative():\n    assert classify(-1) == "neg"\n'
    + '\n\ndef test_zero():\n    assert classify(0) == "zero"\n'
)
FAILING = POSITIVE + '\n\ndef test_wrong():\n    assert classify(-1) == "pos"\n'
CONFIG = "[engines.test]\nmin_tem_score = 0.0\n"
# frozen `pytest_result_has_evidence` (coverage_support.py:70-76) and the legacy
# `_parse_pytest_result` ERROR branches (test_output.py:91-107): conftest plugins that remove the
# terminal reporter (printing only `collected 1 items`, only a `1 passed` summary, or only a `1
# failed` summary of a failing suite), force exit 1 without a failure, or make collection fail
# (exit 2).
_SILENT = (
    "import pytest\n\n\n@pytest.hookimpl(trylast=True)\ndef pytest_configure(config):\n"
    "    reporter = config.pluginmanager.getplugin('terminalreporter')\n"
    "    if reporter is not None:\n"
    "        config.pluginmanager.unregister(reporter)\n"
    "    print({line!r}, flush=True)\n"
)
# frozen `compute_python_function_coverage` (coverage_support.py:408-450): rows in `ast.walk`
# breadth-first order, i.e. by `ast` depth (module functions; nested functions, methods and
# `if`/`try` bodies; `elif`/`else`/`except` bodies one level deeper), a function covered when one
# of its body lines ran.
FUNCTIONS = (
    "def outer(x):\n"
    "    def inner(y):\n"
    "        return y + 1\n"
    "    return inner(x)\n\n\n"
    "class Box:\n"
    "    def get(self):\n"
    "        return 1\n\n"
    "    def unused(self):\n"
    "        return 2\n\n\n"
    "def later(x):\n"
    "    if x:\n"
    "        return 1\n"
    "    return 0\n\n\n"
    "if True:\n"
    "    def guarded():\n"
    "        return 1\n"
    "elif False:\n"
    "    def never():\n"
    "        return 2\n"
    "else:\n"
    "    def other():\n"
    "        return 3\n\n"
    "try:\n"
    "    def tried():\n"
    "        return 4\n"
    "except ImportError:\n"
    "    def fallback():\n"
    "        return 5\n"
)
FUNCTION_TESTS = (
    "from src.calc import Box, later, outer\n\n\n"
    "def test_all():\n"
    "    assert outer(1) == 2\n"
    "    assert Box().get() == 1\n"
    "    assert later(1) == 1\n"
)
# frozen `_measure_coverage` (test_coverage.py:353-356): function coverage is the unrounded
# `covered_funcs / len(function_rows) * 100.0` (2 of 3: 66.666...), which the TEM, the summary and
# `extra.function_coverage` read; only the policy snapshot rounds it.
TWO_OF_THREE = (
    "def first(x):\n"
    "    return x + 1\n\n\n"
    "def second(x):\n"
    "    return x + 2\n\n\n"
    "def third(x):\n"
    "    return x + 3\n"
)
TWO_OF_THREE_TESTS = (
    "from src.calc import first, second\n\n\n"
    "def test_two():\n"
    "    assert first(1) == 2\n"
    "    assert second(1) == 3\n"
)
# frozen `compute_python_function_coverage` (coverage_support.py:434-449): a one-line function's
# body starts on its `def` line, which runs at import, so a called and an uncalled one-liner are
# both covered (line data alone cannot tell them apart).
ONE_LINE = (
    "def called(x): return x + 1\n\n\n"
    "def uncalled(x): return x + 2\n\n\n"
    "def spread(x):\n"
    "    return x + 3\n"
)
ONE_LINE_TESTS = (
    "from src.calc import called\n\n\n"
    "def test_called():\n"
    "    assert called(1) == 2\n"
)
CASES = {
    "breach": (HEADER + POSITIVE, {}),
    "met": (HEADER + EVERY, {}),
    "failure": (HEADER + FAILING, {}),
    "coverage-failure": (
        HEADER + POSITIVE,
        {".coveragerc": "[run]\nplugins = ici_missing_plugin\n"},
    ),
    "functions-order": (FUNCTION_TESTS, {"src/calc.py": FUNCTIONS}),
    "functions-two-of-three": (TWO_OF_THREE_TESTS, {"src/calc.py": TWO_OF_THREE}),
    # The branch minimum is lowered so this case judges the function rows alone.
    "functions-one-line": (
        ONE_LINE_TESTS,
        {"src/calc.py": ONE_LINE, "ici.toml": CONFIG + "min_branch_cov = 0.0\n"},
    ),
    "pytest-success-unparseable": (
        HEADER + POSITIVE,
        {"tests/conftest.py": _SILENT.format(line="collected 1 items")},
    ),
    "pytest-summary-only": (
        HEADER + POSITIVE,
        {"tests/conftest.py": _SILENT.format(line="1 passed")},
    ),
    "pytest-summary-failed": (
        HEADER + FAILING,
        {"tests/conftest.py": _SILENT.format(line="1 failed")},
    ),
    "pytest-exit-one-no-failure": (
        HEADER + POSITIVE,
        {
            "tests/conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n"
            )
        },
    ),
    "pytest-exit-outside-contract": (
        "import nonexistent_module_for_ici\n\n\n" + POSITIVE,
        {},
    ),
}


def run_case(oracle, tests, extra):
    tree = {
        "ici.toml": CONFIG,
        "src/__init__.py": "",
        "src/calc.py": SOURCE,
        "tests/test_calc.py": tests,
        **extra,
    }
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": os.defpath}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "test", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        report = (base / "test_report.json").read_text(encoding="utf-8")
        return {
            "tree": tree,
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(base), ROOT)),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "cases": {name: run_case(oracle, *case) for name, case in CASES.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        targets = [t["target_name"] for t in case["report"]["targets"]]
        print(name, case["exit_code"], case["report"]["status"], targets)


if __name__ == "__main__":
    main()
