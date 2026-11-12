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
)
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


def capture(oracle: Path, python: Path, scenario: tuple, command: str) -> dict[str, object]:
    project_type, files, extra, profile = scenario
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
        (root / "ici.toml").write_text(config(str(python), project_type, extra, profile), "utf-8")
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
        if engine is not None and command == "verify":
            if "results" in engine:
                engines = sorted(item["engine_name"] for item in engine["results"])
                engine = next(
                    (item for item in engine["results"] if item["engine_name"] == "test"), None
                )
            else:
                engine = None
    view: dict[str, object] = {
        "type": project_type,
        "files": files,
        "extra_config": extra,
        "profile": profile,
        "exit_code": process.returncode,
        "report": engine is not None,
    }
    if engines is not None:
        view["engines"] = engines
    if engine is not None:
        view["engine"] = {
            key: engine.get(key) for key in ("status", "summary", "evidence", "required", "score")
        }
        view["extra"] = {key: engine["extra"].get(key) for key in EXTRA}
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
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "test": {
            name: capture(args.oracle, args.python, SCENARIOS[name], "test") for name in TEST_RUNS
        },
        "verify": {
            name: capture(args.oracle, args.python, SCENARIOS[name], "verify") for name in VERIFY
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
