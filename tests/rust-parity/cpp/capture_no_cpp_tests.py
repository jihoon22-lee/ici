"""Development-only frozen captures: legacy `ici test` of C++ and hybrid projects without tests.

Never called by the Rust product. Frozen `TestEngine._run_project_tests` runs C++ tests only for a
`tests/**/*.cpp` entry (`rglob("*.cpp")`), so a project without one never configures, builds or runs
anything: `[C++] Tests` FAIL. Run with the project venv Python (pytest and coverage for the hybrid
case). Every C++ tool on PATH is a wrapper that records a non-probe invocation, so `tools_ran`
is positive evidence of what ran, on both sides.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WRAP = "CAPTURE_WRAP"
REAL = "CAPTURE_PYTHON"
TOOLS = {
    f".wrap/{tool}": (
        '#!/bin/sh\ncase "$1" in --version|--help|-dumpversion|-dumpfullversion) ;; '
        f'*) touch "{WRAP}/completed" ;; esac\nexec /usr/bin/{tool} "$@"\n'
    )
    for tool in ("cmake", "ctest", "make", "gcov", "g++")
}
CPP = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
REQUIRED = "[engines.test]\ncoverage_required=true\n"
MAKE = "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
COVERAGE_ARGV = "coverage_build_argv=['make','all']\n"
CALC = {"src/calc.cpp": "int add(int a,int b){return a+b;}\n"}
LISTS = (
    "cmake_minimum_required(VERSION 3.16)\nproject(calc LANGUAGES CXX)\nenable_testing()\n"
    "add_library(calc STATIC src/calc.cpp)\nadd_executable(t {main})\nadd_test(NAME t COMMAND t)\n"
)
MAIN = "int main(){return 0;}\n"
CTEST = {**CALC, "CMakeLists.txt": LISTS.format(main="src/t_main.cc"), "src/t_main.cc": MAIN}
MAKE_TREE = {
    "Makefile": "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n",
    "src/main.cpp": MAIN,
}
FAILING_MAKE = "[build.make]\nenabled=true\nbuild_argv=['false']\ncoverage_build_argv=['false']\n"
FAILED_CONFIGURE = (
    "cmake_minimum_required(VERSION 3.16)\nproject(calc LANGUAGES CXX)\nmessage(FATAL_ERROR boom)\n"
)
HYBRID = f'[project]\ntype="hybrid"\nsource_dirs=["src"]\n[engines.test]\npython="{WRAP}/python"\n'
TEST = ["test", "--report"]
CASES = {
    "plain": (CPP, CALC, TEST),
    "plain-required": (CPP + REQUIRED, CALC, TEST),
    "cmake-ctest": (CPP, CTEST, TEST),
    "cmake-configure-fail": (CPP, {**CALC, "CMakeLists.txt": FAILED_CONFIGURE}, TEST),
    "make-no-coverage-argv": (CPP + MAKE, MAKE_TREE, TEST),
    "make-no-coverage-argv-required": (CPP + MAKE + REQUIRED, MAKE_TREE, TEST),
    "make-coverage-argv": (CPP + MAKE + COVERAGE_ARGV, MAKE_TREE, TEST),
    "make-coverage-argv-fail": (CPP + FAILING_MAKE, MAKE_TREE, TEST),
    "tests-only-cc-cxx": (CPP, {**CTEST, "tests/a.cc": MAIN, "tests/b.cxx": MAIN}, TEST),
    "test-cpp-outside-tests": (CPP, {**CTEST, "src/calc_test.cpp": MAIN}, TEST),
    "tests-directory-named-cpp": (CPP, {**CTEST, "tests/x.cpp/keep.txt": "x\n"}, TEST),
    "verify-cmake-ctest": (CPP, CTEST, ["verify", "--profile", "deep", "--no-cache", "--report"]),
    "hybrid-python-runs": (
        HYBRID,
        {
            **CTEST,
            "src/a.py": "def f(x):\n    return x + 1\n",
            "tests/test_a.py": "from src.a import f\n\n\ndef test_f():\n    assert f(1) == 2\n",
            ".wrap/python": f'#!/bin/sh\nexec {REAL} "$@"\n',
        },
        TEST,
    ),
}


def run_case(config, tree, command):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        wrappers = Path(temporary).resolve() / "wrap"
        wrappers.mkdir()
        # `verify` compiles for real in its own engines: only `test` is the wrapped, no-tool claim.
        full = {**(TOOLS if command[0] == "test" else {}), **tree, "ici.toml": config}
        for name, text in full.items():
            text = text.replace(REAL, sys.executable).replace(WRAP, str(wrappers))
            is_wrapper = name.startswith(".wrap/")
            path = wrappers / name.removeprefix(".wrap/") if is_wrapper else base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if is_wrapper:
                path.chmod(0o755)
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-m", "ici", *command],
            cwd=base,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": f"{wrappers}:/usr/bin:/bin",
                "LC_CTYPE": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
                "ICI_PYTHON": sys.executable,
            },
        )
        saved = base / f"{command[0]}_report.json"
        assert saved.exists(), completed.stdout[-2000:] + completed.stderr[-2000:]
        text = (
            saved.read_text(encoding="utf-8")
            .replace(str(base), "<root>")
            .replace(str(wrappers), "<wrap>")
        )
        document = json.loads(text)
        engine = next(
            (item for item in document.get("results", []) if item["engine_name"] == "test"),
            document,
        )
        return {
            "tree": {**full},
            "command": command,
            "exit_code": completed.returncode,
            "tools_ran": (wrappers / "completed").exists(),
            "report": {
                key: engine[key]
                for key in ("status", "evidence", "score", "max_score", "summary", "targets")
            }
            | {
                "extra": {
                    key: engine["extra"].get(key)
                    for key in ("passed_tests", "total_tests", "coverage_source")
                },
                "tools": [record["name"] for record in engine.get("tool_evidence", [])],
            },
        }


cases = {name: run_case(config, tree, command) for name, (config, tree, command) in CASES.items()}
(ROOT / "tests/rust-parity/cpp/no-cpp-tests-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True, ensure_ascii=False)
    + "\n",
    encoding="utf-8",
)
for name, case in sorted(cases.items()):
    report = case["report"]
    print(name, case["exit_code"], case["tools_ran"], report["status"], report["evidence"])
