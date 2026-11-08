"""Capture frozen v0.13.0 `ici test --report` of hybrid projects; never product code.

    python -I capture_hybrid_test.py <oracle-root> <output.json>

Frozen `TestEngine` runs pytest under coverage and the C++ tests under gcov, then evaluates one
result over both: `build_coverage_summary` pools the file rows and totals (`coverage.py/gcov`),
`_measure_coverage` pools the function rows, and the coverage policy, TEM and threshold breaches are
each evaluated once. Each case runs a fresh small project (empty HOME, PATH=/usr/bin:/bin, the
capture interpreter with pytest and coverage) and records the saved report whole, with the project
root as `<root>`.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
BASE = '[project]\nsource_dirs = ["src"]\n'
LOW = (
    "[engines.test]\nmin_line_cov = 0.0\nmin_file_cov = 0.0\nmin_func_cov = 0.0\n"
    "min_branch_cov = 0.0\nmin_tem_score = 0.0\n"
)
PYTHON_SOURCE = (
    "def f(x):\n    if x > 0:\n        return 1\n    return 2\n\n\ndef g():\n    return 3\n"
)
CPP_SOURCE = (
    "int twice(int a) {\n  if (a > 100) {\n    return 0;\n  }\n  return a * 2;\n}\n\n"
    "int thrice(int a) {\n  return a * 3;\n}\n\nint add(int a, int b) {\n  return a + b;\n}\n\n"
    "int sub(int a, int b) {\n  return a - b;\n}\n"
)
PYTHON_TEST = "from src.core import f\n\n\ndef test_f():\n    assert f(1) == 1\n"
CPP_TEST = "int twice(int a);\nint main() { return twice(2) == 4 ? 0 : 1; }\n"
TREE = {
    "ici.toml": BASE,
    "src/__init__.py": '"""Package."""\n',
    "src/core.py": PYTHON_SOURCE,
    "src/calc.cpp": CPP_SOURCE,
    "tests/test_core.py": PYTHON_TEST,
    "tests/test_calc.cpp": CPP_TEST,
}
FULL_PYTHON = (
    "from src.core import f, g\n\n\ndef test_f():\n    assert f(1) == 1\n    assert f(0) == 2\n\n\n"
    "def test_g():\n    assert g() == 3\n"
)
FULL_CPP = (
    "int twice(int a);\nint thrice(int a);\nint add(int a, int b);\nint sub(int a, int b);\n"
    "int main() {\n  return twice(2) == 4 && twice(200) == 0 && thrice(1) == 3 && add(1, 1) == 2"
    " && sub(1, 1) == 0 ? 0 : 1;\n}\n"
)
ROUNDING_SOURCE = (
    "def a():\n" + "    x = 1\n" * 21 + "\n\ndef b():\n" + "    y = 2\n" * 56 + "    return y\n"
)
POOLED_ROUNDING_SOURCE = (
    "def a():\n" + "    x = 1\n" * 19 + "\n\ndef b():\n" + "    y = 2\n" * 56 + "    return y\n"
)
ROUNDING_TEST = "from src.core import a\n\n\ndef test_a():\n    a()\n"
CASES = {
    # Both measured, every default threshold breached.
    "measured": TREE,
    # Thresholds met by the pooled numbers, so no breach and a PASS.
    "thresholds-low": {**TREE, "ici.toml": BASE + LOW},
    "every-line-covered": {
        **TREE,
        "tests/test_core.py": FULL_PYTHON,
        "tests/test_calc.cpp": FULL_CPP,
    },
    # The verdict flips on pooling, in both directions. Python alone is 100% and C++ alone 30% of
    # lines: at a 50% minimum C++ alone would fail, while the pooled 56.2% passes.
    "pooled-passes-a-language-minimum": {
        **TREE,
        "ici.toml": BASE + LOW.replace("min_line_cov = 0.0", "min_line_cov = 50.0"),
        "tests/test_core.py": FULL_PYTHON,
    },
    # At 60% the same pool fails.
    "pooled-misses-the-minimum": {
        **TREE,
        "ici.toml": BASE + LOW.replace("min_line_cov = 0.0", "min_line_cov = 60.0"),
        "tests/test_core.py": FULL_PYTHON,
    },
    # TEM multiplies line by function coverage, and the two pool over different denominators. Each
    # language alone scores above 1.5: Python has high lines and 1/3 of its functions, C++ all of
    # its function and 30% of many lines. Pooled, the lines lean on C++ and the functions on
    # Python, and the score falls under 1.5.
    "pooled-fails-tem-each-language-meets": {
        **TREE,
        "ici.toml": BASE + LOW.replace("min_tem_score = 0.0", "min_tem_score = 1.5"),
        "src/core.py": "def f(x):\n"
        + "    y = x\n" * 8
        + "    return y\n\n\ndef g():\n    return 2\n\n\ndef h():\n    return 3\n",
        "tests/test_core.py": PYTHON_TEST,
        "src/calc.cpp": "int twice(int a) {\n  int v = a;\n"
        + "  v = v + 1;\n" * 27
        + "  if (a > 100) {\n"
        + "    v = v + 2;\n" * 69
        + "  }\n  return v - 27 + a;\n}\n",
    },
    "python-failing": {
        **TREE,
        "tests/test_core.py": PYTHON_TEST + "\n\ndef test_bad():\n    assert f(1) == 9\n",
    },
    "cpp-failing": {
        **TREE,
        "tests/test_calc.cpp": "int twice(int a);\nint main() { return twice(2) == 5 ? 0 : 1; }\n",
    },
    # One language has no test: the summary is pooled over what ran and the label is partial.
    "no-cpp-test": {key: value for key, value in TREE.items() if key != "tests/test_calc.cpp"},
    "no-python-test": {key: value for key, value in TREE.items() if key != "tests/test_core.py"},
    "no-cpp-test-required": {
        **{key: value for key, value in TREE.items() if key != "tests/test_calc.cpp"},
        "ici.toml": BASE + "[engines.test]\ncoverage_required = true\n",
    },
    # Changed lines across both languages, each measured by its own tool.
    "changed-lines": {
        **TREE,
        "ici.toml": BASE
        + '[engines.test]\nchanged_lines = ["src/core.py:1-4", "src/calc.cpp:1-5"]\n',
    },
    # A file under the statement minimum has no `Coverage:File` target.
    "file-minimum": {**TREE, "ici.toml": BASE + "[engines.test]\nmin_file_statements = 7\n"},
    "deep": {**TREE, "ici.toml": '[ici]\nprofile = "deep"\n' + BASE},
    # The configured mode decides what the pooled breach is.
    "warn-mode": {**TREE, "ici.toml": BASE + '[engines.test]\nmode = "pass_warn"\n'},
    "not-required": {**TREE, "ici.toml": BASE + "[engines.test]\nrequired = false\n"},
    # Frozen rounds `covered / total * 100.0`: 23 of 80 is 28.749999999999996, so 28.7.
    # The pool is 21 of 78 Python statements and 2 of 2 C++ lines.
    "pooled-rounding": {
        **TREE,
        "ici.toml": BASE + LOW,
        "src/core.py": POOLED_ROUNDING_SOURCE,
        "tests/test_core.py": ROUNDING_TEST,
        "src/calc.cpp": "int twice(int a) {\n  return a * 2;\n}\n",
    },
    "python-only-rounding": {
        "ici.toml": BASE + LOW,
        "src/__init__.py": '"""Package."""\n',
        "src/core.py": ROUNDING_SOURCE,
        "tests/test_core.py": ROUNDING_TEST,
    },
    # One half did not complete: a test that does not compile, a pytest that cannot collect.
    "cpp-test-does-not-compile": {**TREE, "tests/test_calc.cpp": "int main( { return 0; }\n"},
    "python-collection-error": {**TREE, "tests/test_core.py": "import does_not_exist\n"},
    # `tests/**/*.cpp` exists, so this is not the zero-tests branch, whatever else fails.
    "cpp-test-without-main": {**TREE, "tests/test_calc.cpp": "int helper() { return 1; }\n"},
    # Each kind of path a changed-line spec can name; frozen's `parse_changed_lines` decides.
    **{
        f"changed-{name}": {
            **TREE,
            "docs/x.txt": "one\ntwo\n",
            "other/skip.py": "VALUE = 2\n",
            "ici.toml": BASE + f'[engines.test]\nchanged_lines = ["{spec}"]\n',
        }
        for name, spec in {
            "non-source-file": "docs/x.txt:1-2",
            "missing-file": "src/nope.py:1",
            "directory": "src:1",
            "outside-root": "../outside.py:1",
            "outside-source-dirs": "other/skip.py:1",
            "test-file": "tests/test_core.py:1-2",
        }.items()
    },
    # Coverage.py measures a file source discovery ignores, so frozen refuses the Python half.
    "python-coverage-refused": {**TREE, "src/vendor/skip.py": "VALUE = 1\n"},
    "python-only-coverage-refused": {
        "ici.toml": BASE,
        "src/__init__.py": '"""Package."""\n',
        "src/core.py": PYTHON_SOURCE,
        "src/vendor/skip.py": "VALUE = 1\n",
        "tests/test_core.py": PYTHON_TEST,
    },
    # A non-source file beside no other language.
    "python-only-changed-non-source": {
        "ici.toml": BASE + '[engines.test]\nchanged_lines = ["docs/x.txt:1-2"]\n',
        "docs/x.txt": "one\ntwo\n",
        "src/__init__.py": '"""Package."""\n',
        "src/core.py": PYTHON_SOURCE,
        "tests/test_core.py": PYTHON_TEST,
    },
    "cpp-only-changed-non-source": {
        "ici.toml": BASE + '[engines.test]\nchanged_lines = ["docs/x.txt:1-2"]\n',
        "docs/x.txt": "one\ntwo\n",
        "src/calc.cpp": CPP_SOURCE,
        "tests/test_calc.cpp": CPP_TEST,
    },
    # Two specs over the non-source file overlap: frozen refuses the whole list.
    "changed-overlap-in-non-source": {
        **TREE,
        "docs/x.txt": "one\ntwo\n",
        "ici.toml": BASE + '[engines.test]\nchanged_lines = ["docs/x.txt:1-2", "docs/x.txt:2"]\n',
    },
}


def run_case(oracle, tree, command="test"):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        for name, text in tree.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *COMMANDS[command]],
            cwd=base,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        saved = base / f"{command}_report.json"
        assert saved.exists(), completed.stdout + completed.stderr
        report = json.loads(saved.read_text(encoding="utf-8").replace(str(base), "<root>"))
        if command == "verify":
            # Only the test engine is under comparison; its quality pass runs under this profile.
            report = next(item for item in report["results"] if item["engine_name"] == "test")
        return {
            "tree": tree,
            "command": COMMANDS[command],
            "exit_code": completed.returncode,
            "report": report,
        }


COMMANDS = {
    "test": ["test", "--report"],
    "verify": ["verify", "--profile", "deep", "--report"],
}


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "cases": {name: run_case(oracle, tree) for name, tree in CASES.items()},
    }
    # The quality pass is only selected under a suite's deep profile.
    document["cases"]["verify-deep"] = run_case(oracle, TREE, "verify")
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["cases"].items():
        report = case["report"]
        extra = report["extra"]
        print(
            name, case["exit_code"], report["status"], report["evidence"], extra["coverage_source"]
        )
        print("  ", report["summary"][:170])
        print("  ", [(t["target_name"][:22], t["status"]) for t in report["targets"]])


if __name__ == "__main__":
    main()
