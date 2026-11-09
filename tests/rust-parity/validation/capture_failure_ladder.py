"""Capture frozen v0.13.0 reports of tools that fail in each way; never product code.

    python -I capture_failure_ladder.py <oracle-root> <output.json> [case ...]

With case names, only those are captured and merged into the existing output.

Each case is a fresh small project. Tree entries under `.wrap/` are written to a directory beside
the project, marked executable, and reported as `<wrap>/...` by the capture and by the replay; a
tool inside the project would be a project input. That directory comes first on PATH, and a
`.wrap/.path-only` entry makes it the only one, which is how a missing g++ is staged.

A suite wrapper floods a stream with an exact byte count and then writes `<wrap>/completed`, so the
boundary (exactly 1,000,000 bytes, one byte over, per stream) and whether the process ran to its end
are both recorded. Frozen's `_BoundedCapture` truncates bytes although the argument is named
`max_output_chars`. `SUITE_TIMEOUT` cases are not here: they take 1200 s each.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
LIMIT = 1_000_000
REAL = "CAPTURE_PYTHON"
BASE = '[project]\nsource_dirs = ["src"]\n'
WRAP = "CAPTURE_WRAP"
WRAPPED = BASE + f'[engines.test]\npython = "{WRAP}/python"\n'
REQUIRED = BASE + "[engines.test]\ncoverage_required = true\n"
PYTHON_TREE = {
    "ici.toml": WRAPPED,
    "src/__init__.py": '"""P."""\n',
    "src/core.py": "def f(x):\n    return x + 1\n",
    "tests/test_core.py": "from src.core import f\n\n\ndef test_f():\n    assert f(1) == 2\n",
}
REQUIRED_PYTHON_TREE = {**PYTHON_TREE, "ici.toml": WRAPPED + "coverage_required = true\n"}
UNITTEST_TREE = {
    **PYTHON_TREE,
    "tests/test_core.py": (
        "import unittest\nfrom src.core import f\n\n\nclass T(unittest.TestCase):\n"
        "    def test_f(self):\n        self.assertEqual(f(1), 2)\n"
    ),
}
CPP_TREE = {
    "ici.toml": BASE,
    "src/calc.cpp": "int twice(int a) { return a * 2; }\n",
    "tests/test_calc.cpp": "int twice(int a);\nint main() { return twice(2) == 4 ? 0 : 1; }\n",
}
PATTERNS = {
    "covversion": "*-m\\ coverage\\ --version*",
    "covrun": "*-m\\ coverage\\ run*",
    "covjson": "*-m\\ coverage\\ json*",
    "pytest": "*-m\\ pytest*",
    "unittest": "*-m\\ unittest*",
}
# The interpreter's own diagnostic names the absolute executable; native checks that exact path.
ABSOLUTE = '$(cd "$(dirname "$0")" && pwd)/$(basename "$0")'
NO_COVERAGE = f'echo "{ABSOLUTE}: No module named coverage" >&2; exit 1'
NO_PYTEST = f'echo "{ABSOLUTE}: No module named pytest" >&2; exit 1'
GONE = 'mv "$0" "$0.gone"'


def python_wrapper(**arms):
    body = '#!/bin/bash\ncase "$*" in\n'
    for name, pattern in PATTERNS.items():
        if name in arms:
            body += f"  {pattern}) {arms[name]} ;;\n"
    return body + f'  *) exec {REAL} "$@" ;;\nesac\n'


def flood(count, stream):
    redirect = " >&2" if stream == "stderr" else ""
    return (
        f'head -c {count} /dev/zero | tr "\\0" x{redirect}; : > "$(dirname "$0")/completed"; exit 0'
    )


def python_case(tree=None, **arms):
    return {"tree": {**(tree or PYTHON_TREE), ".wrap/python": python_wrapper(**arms)}}


def gxx_case(tree, body, command=None):
    case = {"tree": {**tree, ".wrap/g++": f"#!/bin/bash\n{body}\n"}}
    if command:
        case["command"] = command
    return case


SANITIZE = ["sanitize", "--report"]
GXX_FLOOD = f'head -c {LIMIT + 100000} /dev/zero | tr "\\0" x; echo'
CASES = {
    "cov-signal": python_case(covrun="kill -9 $$"),
    "cov-exit5": python_case(covrun="exit 5"),
    "covjson-exit7-required": python_case(REQUIRED_PYTHON_TREE, covjson="exit 7"),
    "covjson-exit7": python_case(covjson="exit 7"),
    "covjson-gone": python_case(covrun=f'{REAL} "$@"; rc=$?; {GONE}; exit $rc'),
    "pytest-signal": python_case(covversion=NO_COVERAGE, pytest="kill -9 $$"),
    "pytest-gone": python_case(covversion=f"{GONE}; {NO_COVERAGE}"),
    "unittest-signal": python_case(
        UNITTEST_TREE, covversion=NO_COVERAGE, pytest=NO_PYTEST, unittest="kill -9 $$"
    ),
    "unittest-exit3": python_case(
        UNITTEST_TREE, covversion=NO_COVERAGE, pytest=NO_PYTEST, unittest="exit 3"
    ),
    "unittest-gone": python_case(
        UNITTEST_TREE, covversion=NO_COVERAGE, pytest=f"{GONE}; {NO_PYTEST}"
    ),
    "test-no-gxx": {"tree": {**CPP_TREE, ".wrap/.path-only": ""}},
    "sanitize-no-gxx": {"tree": {**CPP_TREE, ".wrap/.path-only": ""}, "command": SANITIZE},
    "no-tests-required": {"tree": {"ici.toml": REQUIRED, "src/a.txt": "x\n"}},
    "cpp-no-sources-required": {
        "tree": {"ici.toml": REQUIRED, "tests/test_only.cpp": "int main() { return 0; }\n"}
    },
    "cpp-test-overflow": {
        "tree": {
            **CPP_TREE,
            "tests/test_calc.cpp": (
                "#include <cstdio>\nint twice(int a);\nint main() {\n"
                "  for (int i = 0; i < 1100000; i++) putchar(120);\n  fflush(stdout);\n"
                f'  FILE* f = fopen("{WRAP}/completed", "w");\n  if (f) fclose(f);\n'
                "  return twice(2) == 4 ? 0 : 1;\n}\n"
            ),
            ".wrap/.keep": "",
        }
    },
    "sanitize-marker-only": {
        "tree": {
            **CPP_TREE,
            "tests/test_calc.cpp": (
                '#include <cstdio>\nint main() { fputs("ERROR: AddressSanitizer\\n", stderr);'
                " return 1; }\n"
            ),
        },
        "command": SANITIZE,
    },
    "sanitize-gxx-exit2": gxx_case(CPP_TREE, "echo bad >&2; exit 2", SANITIZE),
    "sanitize-gxx-signal": gxx_case(CPP_TREE, "kill -9 $$", SANITIZE),
    "sanitize-gxx-trunc": gxx_case(CPP_TREE, GXX_FLOOD, SANITIZE),
    "test-gxx-signal": gxx_case(CPP_TREE, "kill -9 $$"),
    "test-gxx-trunc": gxx_case(CPP_TREE, GXX_FLOOD),
}
for offset, label in ((0, "at"), (1, "over")):
    for stream in ("stdout", "stderr"):
        body = flood(LIMIT + offset, stream)
        tag = f"{label}-{stream}"
        CASES[f"cov-{tag}"] = python_case(covrun=body)
        CASES[f"covjson-{tag}"] = python_case(covjson=body)
        CASES[f"pytest-{tag}"] = python_case(covversion=NO_COVERAGE, pytest=body)
        CASES[f"unittest-{tag}"] = python_case(
            UNITTEST_TREE, covversion=NO_COVERAGE, pytest=NO_PYTEST, unittest=body
        )


def run_case(oracle, case):
    command = case.get("command", ["test", "--report"])
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        wrappers = Path(temporary).resolve() / "wrap"
        wrappers.mkdir()
        for name, text in case["tree"].items():
            text = text.replace(REAL, sys.executable).replace(WRAP, str(wrappers))
            if name.startswith(".wrap/"):
                path = wrappers / name.removeprefix(".wrap/")
            else:
                path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if name.startswith(".wrap/"):
                path.chmod(0o755)
        (base / ".git").mkdir()
        only = (wrappers / ".path-only").exists()
        search = str(wrappers) if only else f"{wrappers}:/usr/bin:/bin"
        environment = {
            "HOME": home,
            "XDG_CONFIG_HOME": home,
            "PATH": search,
            "ICI_PYTHON": sys.executable,
        }
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *command],
            cwd=base,
            capture_output=True,
            text=True,
            errors="replace",
            env=environment,
            check=False,
        )
        saved = base / f"{command[0]}_report.json"
        assert saved.exists(), completed.stdout[-2000:] + completed.stderr[-2000:]
        text = saved.read_text(encoding="utf-8")
        report = json.loads(text.replace(str(base), "<root>").replace(str(wrappers), "<wrap>"))
        return {
            "tree": case["tree"],
            "command": command,
            "exit_code": completed.returncode,
            "completed": (wrappers / "completed").exists(),
            "report": report,
        }


def main():
    oracle, output, only = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    if only:
        document = json.loads(output.read_text(encoding="utf-8"))
        document["cases"].update({name: run_case(oracle, CASES[name]) for name in only})
    else:
        document = {
            "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
            "python": sys.version.split()[0],
            "interpreter": sys.executable,
            "limit_bytes_per_stream": LIMIT,
            "cases": {name: run_case(oracle, case) for name, case in CASES.items()},
        }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name in sorted(only or document["cases"]):
        case = document["cases"][name]
        report = case["report"]
        print(
            f"{name}: exit {case['exit_code']} completed {case['completed']} "
            f"{report.get('status')} {report.get('evidence')} {str(report.get('summary'))[:90]}"
        )


if __name__ == "__main__":
    main()
