"""Development-only capture: frozen legacy sanitize for a `type = "cpp"` project's Python tests.

    python -I -B capture_legacy_sanitize_tests_python.py --oracle <oracle-root> --python <py> \
        --out <json>

Frozen `SanitizeEngine._analysis_scopes` (engines/sanitize.py:100-125) keeps the Python scope for
`tests/**/*.py` (`_has_python_tests`) whatever the declared type, so a `type = "cpp"` project whose
only Python is under `tests/` runs the ResourceWarning suite; Python that `source_dirs` does not
select, outside `tests/`, puts no scope in play, and ThreadSanitizeEngine has no Python scope.
The project files, configuration and view are those of `capture_legacy_sanitize_engine.py`
(loaded by path).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def _sibling():
    path = Path(__file__).resolve().with_name("capture_legacy_sanitize_engine.py")
    spec = importlib.util.spec_from_file_location("capture_legacy_sanitize_engine", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _sibling()
SELF_TEST = "def test_add():\n    assert 1 + 2 == 3\n"
# The reported case: the test imports a module from a `src` path that does not exist.
REPORTED = (
    'import sys\nsys.path.insert(0, "src")\nfrom calc import add\n\n'
    "def test_add():\n    assert add(1, 2) == 3\n"
)
TESTS = {"tests/test_calc.py": SELF_TEST}
CPP = {"src/twice.cpp": ENGINE.CPP}
OUTSIDE = {"tools/helper.py": ENGINE.APP}
# scenario: (project type, files, extra [engines.sanitize] lines)
SCENARIOS = {
    # Positive: only tests/**/*.py, flat or nested, a helper, beside a C++ source, under the
    # disabled or optional engine (a typed `ici sanitize` re-enables its engine).
    "cpp-tests-python": ("cpp", TESTS, ""),
    "cpp-nested-tests-python": ("cpp", {"tests/a/b/test_x.py": SELF_TEST}, ""),
    "cpp-src-cpp-tests-python": ("cpp", {**CPP, **TESTS}, ""),
    "cpp-tests-python-disabled": ("cpp", TESTS, "enabled = false\n"),
    "cpp-tests-python-optional": ("cpp", TESTS, "required = false\n"),
    # Failure: a helper without test files (the scope refusal), and a suite pytest cannot report.
    "cpp-tests-helper-only": ("cpp", {"tests/helper.py": "X = 1\n"}, ""),
    "cpp-tests-reported": ("cpp", {"tests/test_calc.py": REPORTED}, ""),
    # Negative: a tests path without Python, and Python outside tests/ that source_dirs does not
    # select: no Python scope.
    "cpp-tests-data": ("cpp", {"tests/data.txt": "x\n"}, ""),
    "cpp-src-cpp-tests-data": ("cpp", {**CPP, "tests/data.txt": "x\n"}, ""),
    "cpp-outside-python-tests-data": ("cpp", {**OUTSIDE, "tests/data.txt": "x\n"}, ""),
    "cpp-outside-python": ("cpp", OUTSIDE, ""),
    "cpp-src-cpp-outside-python": ("cpp", {**CPP, **OUTSIDE, "tests/data.txt": "x\n"}, ""),
}
VERIFY = ("cpp-tests-python", "cpp-src-cpp-tests-python", "cpp-outside-python-tests-data")
THREAD = ("cpp-tests-python", "cpp-src-cpp-tests-python")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    def capture(name: str, command: str) -> dict[str, object]:
        return ENGINE.capture(args.oracle, args.python, SCENARIOS[name], command)

    document = {
        "source": ENGINE.SOURCE,
        "runs": {name: capture(name, "sanitize") for name in SCENARIOS},
        "verify": {name: capture(name, "verify") for name in VERIFY},
        "thread_sanitize": {name: capture(name, "thread-sanitize") for name in THREAD},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    with ENGINE._private_home():
        main()
