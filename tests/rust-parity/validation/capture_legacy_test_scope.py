"""Development-only capture: frozen legacy TestEngine suite scope (`ici test`, `verify`'s test).

    python -I -B capture_legacy_test_scope.py --oracle <oracle-root> --python <py> --out <json>

Frozen `TestEngine._run_project_tests` (engines/test.py:249-276) attempts the Python suite for
`tests/**/test_*.py` whatever the project type (or a python/hybrid type with a selected Python
source) and, decided separately, the C++ suite for `tests/**/*.cpp` or a cpp/hybrid type with a
selected C/C++ source. An attempted suite without tests is `_mark_zero_tests("<language>")`;
neither attempted is `[Project] Tests`. Not the sanitizer's scope (any `tests/**/*.py`). These
trees reach every branch: only tests/test_*.py, a helper or an empty tests/, Python outside
`tests/`, a cpp project's selected Python sources with and without tests, a C/C++ source beside
tests/test_*.py (zero C++ tests; the coverage json of a `src` without Python fails), a measured
C++ test beside it, tests/*.cpp beside selected Python, python and hybrid types, the disabled
engine and the fast/standard profiles under `verify`, and a next (schema_version = 1) project.
`--python` is a project CPython with pytest and coverage; g++ comes from PATH=/usr/bin:/bin.
`verify` records every engine's status, evidence and required flag, so its exit is attributable.

Later trees (cppval16): a hybrid project whose scope selects no source (only tests/test_*.py, or an
empty tests/) under `verify`; a project interpreter with pytest and without coverage
(`--python-no-coverage`, recorded as <PYTHON_NO_COVERAGE>) beside C/C++ sources, a hybrid
project and tests alone; `coverage_required` beside the failed `coverage json` of zero C++ tests;
a hybrid src+tests tree under `verify`; and (section "open") a `type = "python"` project with
tests/*.cpp, whose two suites frozen pools.

Pooled state (cppval18, root ruling 2026-11-13): a measured Python half beside a C++ suite that
ran with no production C/C++ source (frozen `_run_cpp_tests` records "C++ source files for gcov
were unavailable", test.py:543-545, and gcov reads nothing). The open section adds the same tree
under `type = "cpp"` and `"hybrid"` and each of the three under `coverage_required = true`; these
views also record `coverage_provenance`, `coverage_files`, `coverage_totals`, `coverage_policy`
and `function_rows`, so the native view's provenance keys are compared with frozen's. The same
six trees with a CMakeLists.txt that builds only the tests/*.cpp case (cppval19, root ruling
2026-11-14) record the CMake adapter's reading of that state, optional and required, in their own
section "open_adapter" (recorded only: frozen runs gcov over the test-only notes there).

Replay pin (cppval17): frozen `verify` probes mypy, pytest and coverage as modules of its own
`sys.executable`, the `--python` capture runtime (core/toolchain.py:440-449, no project .venv in
these trees). The document records that runtime's path (`capture_python`) and each `verify` view
its frozen capability_inventory rows for those three modules, so the native replay, which names
the runtime through ICI_PYTHON, can assert it replays with the interpreter frozen ran.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
APP = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
SELF_TEST = "def test_add():\n    assert 1 + 2 == 3\n"
CPP = "int twice(int value) { return value * 2; }\n"
CPP_TEST = "int twice(int value);\nint main() { return twice(2) == 4 ? 0 : 1; }\n"
MAIN = "int main() { return 0; }\n"
TESTS = {"tests/test_calc.py": SELF_TEST}
# A file value None is an empty directory.
# scenario: (project type or "next", files, extra [engines.test] lines, profile)
SCENARIOS = {
    # Python attempted by tests/test_*.py alone, whatever the type.
    "cpp-tests-python": ("cpp", TESTS, "", "deep"),
    "cpp-nested-tests-python": ("cpp", {"tests/a/b/test_x.py": SELF_TEST}, "", "deep"),
    "cpp-outside-python-tests-python": ("cpp", {"tools/helper.py": APP, **TESTS}, "", "deep"),
    "python-tests-python": ("python", TESTS, "", "deep"),
    "hybrid-tests-python": ("hybrid", TESTS, "", "deep"),
    # Selected Python sources beside tests (positive controls).
    "cpp-src-python-tests-python": (
        "cpp",
        {"src/calc.py": APP, "tests/test_calc.py": TEST},
        "",
        "deep",
    ),
    "hybrid-src-python-tests-python": (
        "hybrid",
        {"src/calc.py": APP, "tests/test_calc.py": TEST},
        "",
        "deep",
    ),
    # Neither attempted: `[Project] Tests` (a helper, an empty tests/, Python outside tests/, a cpp
    # project's selected Python sources).
    "cpp-tests-helper-only": ("cpp", {"tests/helper.py": "X = 1\n"}, "", "deep"),
    "cpp-tests-empty": ("cpp", {"tests": None}, "", "deep"),
    "cpp-outside-python": ("cpp", {"tools/helper.py": APP}, "", "deep"),
    "cpp-src-python": ("cpp", {"src/calc.py": APP}, "", "deep"),
    # A real C++ scope: zero C++ tests alone and beside the Python suite (whose coverage json of a
    # `src` without Python fails), a measured C++ test beside it, tests/*.cpp beside selected Python.
    "cpp-src-cpp": ("cpp", {"src/twice.cpp": CPP}, "", "deep"),
    "cpp-src-cpp-tests-python": ("cpp", {"src/twice.cpp": CPP, **TESTS}, "", "deep"),
    "cpp-src-cpp-tests-cpp-python": (
        "cpp",
        {"src/twice.cpp": CPP, "tests/test_twice.cpp": CPP_TEST, **TESTS},
        "",
        "deep",
    ),
    "cpp-src-python-tests-cpp": (
        "cpp",
        {"src/calc.py": APP, "tests/test_main.cpp": MAIN},
        "",
        "deep",
    ),
    # The disabled engine (a typed `ici test` re-enables it; `verify` omits it) and profiles.
    "cpp-tests-python-disabled": ("cpp", TESTS, "enabled = false\n", "deep"),
    "cpp-tests-python-fast": ("cpp", TESTS, "", "fast"),
    "cpp-tests-python-standard": ("cpp", TESTS, "", "standard"),
    # Negative: a next (schema_version = 1) project is not the legacy engine.
    "next-cpp-tests-python": ("next", TESTS, "", "deep"),
    # A hybrid project whose scope selects no source: tests/test_*.py alone, an empty tests/.
    "hybrid-tests-empty": ("hybrid", {"tests": None}, "", "deep"),
    # A hybrid project with Python sources only (frozen `verify` lint WARN, type PASS).
    # (hybrid-src-python-tests-python above.)
    # `coverage_required` beside the failed `coverage json` of a `src` without Python, zero C++
    # tests; and beside a measured Python half.
    "cpp-src-cpp-tests-python-required": (
        "cpp",
        {"src/twice.cpp": CPP, **TESTS},
        "coverage_required = true\n",
        "deep",
    ),
    "hybrid-src-cpp-python-tests-python-required": (
        "hybrid",
        {"src/calc.py": APP, "src/twice.cpp": CPP, "tests/test_calc.py": TEST},
        "coverage_required = true\n",
        "deep",
    ),
}
# The project interpreter has pytest and no coverage module (frozen `_find_coverage_cmd`'s
# `coverage --version` exits 1, so the plain pytest suite runs).
NO_COVERAGE = {
    "no-coverage-cpp-src-cpp-tests-python": ("cpp", {"src/twice.cpp": CPP, **TESTS}, "", "deep"),
    "no-coverage-hybrid-src-cpp-python-tests-python": (
        "hybrid",
        {"src/calc.py": APP, "src/twice.cpp": CPP, "tests/test_calc.py": TEST},
        "",
        "deep",
    ),
    "no-coverage-hybrid-src-python-tests-python": (
        "hybrid",
        {"src/calc.py": APP, "tests/test_calc.py": TEST},
        "",
        "deep",
    ),
    "no-coverage-hybrid-tests-python": ("hybrid", TESTS, "", "deep"),
    "no-coverage-cpp-tests-python": ("cpp", TESTS, "", "deep"),
    "no-coverage-cpp-src-cpp-tests-cpp-python": (
        "cpp",
        {"src/twice.cpp": CPP, "tests/test_twice.cpp": CPP_TEST, **TESTS},
        "",
        "deep",
    ),
}
SCENARIOS.update(NO_COVERAGE)
# A `type = "python"` project's tests/*.cpp (frozen pools both suites), and the pooled state of
# a C++ suite without a production C/C++ source under every type, optional and required.
POOLED_FILES = {"src/calc.py": APP, "tests/test_calc.py": TEST, "tests/test_main.cpp": MAIN}
REQUIRED = "coverage_required = true\n"
CMAKE_POOLED = (
    "cmake_minimum_required(VERSION 3.16)\nproject(pooled LANGUAGES CXX)\nenable_testing()\n"
    "add_executable(test_main tests/test_main.cpp)\nadd_test(NAME test_main COMMAND test_main)\n"
)
CMAKE_POOLED_FILES = {**POOLED_FILES, "CMakeLists.txt": CMAKE_POOLED}
OPEN = {
    "python-src-python-tests-cpp": ("python", POOLED_FILES, "", "deep"),
    "python-src-python-tests-cpp-required": ("python", POOLED_FILES, REQUIRED, "deep"),
    "cpp-src-python-tests-python-cpp": ("cpp", POOLED_FILES, "", "deep"),
    "cpp-src-python-tests-python-cpp-required": ("cpp", POOLED_FILES, REQUIRED, "deep"),
    "hybrid-src-python-tests-python-cpp": ("hybrid", POOLED_FILES, "", "deep"),
    "hybrid-src-python-tests-python-cpp-required": ("hybrid", POOLED_FILES, REQUIRED, "deep"),
    "python-src-cpp-python-tests-cpp": (
        "python",
        {
            "src/calc.py": APP,
            "src/twice.cpp": CPP,
            "tests/test_calc.py": TEST,
            "tests/test_twice.cpp": CPP_TEST,
        },
        "",
        "deep",
    ),
}
# The same pooled state on the CMake adapter path (root ruling 2026-11-14, cppval19), recorded
# only (section "open_adapter"): a CMakeLists.txt whose only executable is the tests/*.cpp case,
# so the adapter's coverage build, ctest and gcov run with no production C/C++ source in scope.
OPEN_ADAPTER = {
    "python-cmake-src-python-tests-cpp": ("python", CMAKE_POOLED_FILES, "", "deep"),
    "python-cmake-src-python-tests-cpp-required": ("python", CMAKE_POOLED_FILES, REQUIRED, "deep"),
    "cpp-cmake-src-python-tests-python-cpp": ("cpp", CMAKE_POOLED_FILES, "", "deep"),
    "cpp-cmake-src-python-tests-python-cpp-required": ("cpp", CMAKE_POOLED_FILES, REQUIRED, "deep"),
    "hybrid-cmake-src-python-tests-python-cpp": ("hybrid", CMAKE_POOLED_FILES, "", "deep"),
    "hybrid-cmake-src-python-tests-python-cpp-required": (
        "hybrid",
        CMAKE_POOLED_FILES,
        REQUIRED,
        "deep",
    ),
}
TEST_RUNS = tuple(name for name in SCENARIOS if name != "cpp-tests-python-standard")
VERIFY = (
    "cpp-tests-python",
    "cpp-tests-helper-only",
    "cpp-src-python",
    "cpp-src-cpp-tests-python",
    "cpp-src-cpp-tests-cpp-python",
    "cpp-tests-python-disabled",
    "cpp-tests-python-fast",
    "cpp-tests-python-standard",
    "next-cpp-tests-python",
    "hybrid-tests-python",
    "hybrid-tests-empty",
    "hybrid-src-python-tests-python",
    "cpp-src-cpp-tests-python-required",
    "no-coverage-cpp-src-cpp-tests-python",
    "no-coverage-hybrid-src-cpp-python-tests-python",
    "no-coverage-hybrid-src-python-tests-python",
)
# The frozen capability_inventory rows `verify` probes as modules of its `sys.executable`.
PYTHON_MODULE_TOOLS = ("mypy", "pytest", "coverage")
INVENTORY_KEYS = ("name", "path", "version", "available", "complete")
EXTRA = (
    "passed_tests",
    "total_tests",
    "skipped_tests",
    "pass_rate",
    "branch_coverage",
    "function_coverage",
    "line_coverage",
    "tem_score",
    "coverage_source",
    "metrics_summary",
    "test_suites",
)
# The pooled-state views' coverage keys (POOLED): frozen emits no "cpp" provenance there.
POOLED = tuple(name for name in OPEN if name != "python-src-cpp-python-tests-cpp")
POOLED_EXTRA = (
    "coverage_provenance",
    "coverage_files",
    "coverage_totals",
    "coverage_policy",
    "function_rows",
)


def config(python: str, project_type: str, extra: str, profile: str) -> str:
    if project_type == "next":
        return (
            'schema_version = 1\n[workspace]\nname = "demo"\n[[components]]\nid = "app"\n'
            'root = "."\nlanguages = ["cpp"]\n'
        )
    return (
        f"[ici]\nprofile = \"{profile}\"\n[project]\ntype = '{project_type}'\n"
        f'source_dirs = ["src"]\n[engines.test]\npython = {json.dumps(python)}\n{extra}'
    )


def capture(
    oracle: Path,
    python: Path,
    scenario: tuple,
    command: str,
    no_coverage: Path | None = None,
    extra_keys: tuple[str, ...] = EXTRA,
) -> dict[str, object]:
    project_type, files, extra, profile = scenario
    interpreter = no_coverage or python
    with tempfile.TemporaryDirectory(prefix="ici-legacy-test-scope-") as temporary:
        root = Path(temporary).resolve() / "project"
        home = Path(temporary).resolve() / "home"
        home.mkdir()
        root.mkdir()
        for relative, text in files.items():
            if text is None:
                (root / relative).mkdir(parents=True, exist_ok=True)
                continue
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(
            config(str(interpreter), project_type, extra, profile), "utf-8"
        )
        process = subprocess.run(
            [str(python), "-B", "-m", "ici", command, "--report"],
            cwd=root,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "PYTHONPATH": str(oracle / "src"),
                "LC_CTYPE": "C.UTF-8",
            },
            capture_output=True,
            check=False,
            timeout=900,
        )
        report = root / f"{command}_report.json"
        engine = json.loads(report.read_text("utf-8")) if report.exists() else None
        engines = None
        inventory = None
        if engine is not None and command == "verify":
            inventory = [
                {key: row.get(key) for key in INVENTORY_KEYS}
                for row in (engine.get("capability_inventory") or {}).get("tools", [])
                if row.get("name") in PYTHON_MODULE_TOOLS
            ]
            if "results" in engine:
                engines = sorted(
                    [item["engine_name"], item["status"], item["evidence"], item["required"]]
                    for item in engine["results"]
                )
                engine = next(
                    (item for item in engine["results"] if item["engine_name"] == "test"), None
                )
            else:
                engine = None
    view: dict[str, object] = {
        "interpreter": "no-coverage" if no_coverage else "project",
        "type": project_type,
        "files": files,
        "extra_config": extra,
        "profile": profile,
        "exit_code": process.returncode,
        "report": engine is not None,
    }
    if engines is not None:
        view["engines"] = engines
    if inventory is not None:
        view["capability_inventory"] = {"tools": inventory}
    if engine is not None:
        view["engine"] = {
            key: engine.get(key) for key in ("status", "summary", "evidence", "required", "score")
        }
        view["extra"] = {key: engine["extra"].get(key) for key in extra_keys}
        view["targets"] = [
            {
                key: target[key]
                for key in ("file_path", "start_line", "target_name", "status", "message")
            }
            for target in engine["targets"]
        ]
        view["findings"] = sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "path": finding["primary_location"]["path"],
                    "message": finding["message"],
                    "fingerprint": finding["fingerprint"],
                    "severity": finding["severity"],
                }
                for finding in engine["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        )
        view["tool_evidence"] = [
            {"name": item["name"], "argv": item.get("argv"), "returncode": item.get("returncode")}
            for item in engine.get("tool_evidence", [])
        ]
    text = json.dumps(view).replace(str(python), "<PYTHON>").replace(str(root), "<ROOT>")
    if no_coverage is not None:
        text = text.replace(str(no_coverage), "<PYTHON_NO_COVERAGE>")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--python-no-coverage", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    probe = subprocess.run(
        [
            str(args.python_no_coverage),
            "-I",
            "-c",
            "import pytest, importlib.util as u; "
            "raise SystemExit(u.find_spec('coverage') is not None)",
        ],
        check=False,
    )
    if probe.returncode != 0:
        raise SystemExit("--python-no-coverage must import pytest and have no coverage module")

    def interpreter(name: str) -> Path | None:
        return args.python_no_coverage if name in NO_COVERAGE else None

    def one(name: str, command: str) -> dict[str, object]:
        return capture(args.oracle, args.python, SCENARIOS[name], command, interpreter(name))

    document = {
        "source": SOURCE,
        "capture_python": str(args.python),
        "no_coverage_python": str(args.python_no_coverage),
        "test": {name: one(name, "test") for name in TEST_RUNS},
        "verify": {name: one(name, "verify") for name in VERIFY},
        "open": {
            f"{command}:{name}": capture(
                args.oracle,
                args.python,
                scenario,
                command,
                extra_keys=EXTRA + POOLED_EXTRA if name in POOLED else EXTRA,
            )
            for name, scenario in OPEN.items()
            for command in ("test", "verify")
        },
        "open_adapter": {
            f"{command}:{name}": capture(
                args.oracle, args.python, scenario, command, extra_keys=EXTRA + POOLED_EXTRA
            )
            for name, scenario in OPEN_ADAPTER.items()
            for command in ("test", "verify")
        },
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
