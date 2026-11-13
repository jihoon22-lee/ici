"""Development-only frozen captures of legacy C++ test-scope edges; never called by Rust.

    python -I -B capture_legacy_cpp_test_edges.py [name-prefix ...]

Same driver and document shape as tests/rust-parity/cpp/capture_validation_full.py (its `run`,
loaded by path: a parity-fixtures/cpp tree in a fresh temporary project, the frozen oracle
`ici <command> --report` with PATH=/usr/bin:/bin, the whole saved report with the project root as
<root>), plus each command's stderr, so `legacy_engine_extras.rs` replays the cases with its
full-result comparison (`cpp_oracle_differences`).

Cases:
- Symlinked `project.source_dirs` (root ruling 2026-11-14, frozen `get_source_dirs` /
  `_validate_path_list`): `alias -> src` measures src/*.cpp on the direct and CMake paths and under
  `verify`; `alias -> missing` selects nothing; an entry resolving outside the root (a link to
  /usr, a dangling link to ../nowhere, a lexical ../outside, the second entry) is the
  configuration refusal "project.source_dirs[<i>]: path is outside project root: <entry>",
  exit 2.
- engines/test.py:611 (`_run_cpp_tests_via_adapter`'s failed coverage-variant build): a Make
  project whose coverage build fails and a qmake project whose test does not compile.
- engines/test.py:632: a CTest case whose executable does not exist ("Not Run").
- engines/test.py:654: a CTest case past its TIMEOUT property ("Timeout").
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests/rust-parity/validation/legacy-cpp-test-edges-oracle.json"
SPEC = importlib.util.spec_from_file_location(
    "capture_validation_full", ROOT / "tests/rust-parity/cpp/capture_validation_full.py"
)
cvf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cvf)

ALIAS = "[project]\ntype='cpp'\nsource_dirs=['alias']\n"
LINK_SRC = {"alias": {"symlink": "src"}}
PLAIN_CMAKE = (cvf.FIXTURES / "test-cmake/plain/CMakeLists.txt").read_text(encoding="utf-8")
SLEEP_TEST = "#include <unistd.h>\nint main() { sleep(20); return 0; }\n"
CASES = {
    # Root ruling 2026-11-14: symlinked project.source_dirs.
    "test:direct-alias": ("test-direct/mixed", "test", ALIAS, "tools", LINK_SRC),
    "test:cmake-alias": ("test-cmake/plain", "test", ALIAS, "tools", LINK_SRC),
    "verify:direct-alias": ("test-direct/mixed", "verify", ALIAS, "tools", LINK_SRC),
    "test:direct-alias-missing": (
        "test-direct/mixed",
        "test",
        ALIAS,
        "tools",
        {"alias": {"symlink": "missing"}},
    ),
    "test:direct-alias-escape": (
        "test-direct/mixed",
        "test",
        ALIAS,
        "tools",
        {"alias": {"symlink": "/usr"}},
    ),
    "verify:direct-alias-escape": (
        "test-direct/mixed",
        "verify",
        ALIAS,
        "tools",
        {"alias": {"symlink": "/usr"}},
    ),
    "test:direct-alias-dangling-escape": (
        "test-direct/mixed",
        "test",
        ALIAS,
        "tools",
        {"alias": {"symlink": "../nowhere"}},
    ),
    "test:direct-lexical-escape": (
        "test-direct/mixed",
        "test",
        "[project]\ntype='cpp'\nsource_dirs=['../outside']\n",
        "tools",
    ),
    "test:direct-alias-second-escape": (
        "test-direct/mixed",
        "test",
        "[project]\ntype='cpp'\nsource_dirs=['src', 'alias']\n",
        "tools",
        {"alias": {"symlink": "/usr"}},
    ),
    # engines/test.py:611: the coverage-variant build fails before any test runs.
    "test:make-coverage-build-failure": (
        "test-make",
        "test",
        cvf.MAKE + "test_argv=['make','check']\n",
        "tools",
        {"src/calc.cpp": '#include "calc.h"\nint broken( {\n'},
    ),
    "test:qmake-build-failure": (
        "test-qmake",
        "test",
        "[project]\ntype='cpp'\n",
        "tools",
        {"tests/test_unit.cpp": "#include <QtTest>\nthis does not compile;\n"},
    ),
    # engines/test.py:632: a CTest case whose command does not exist is "Not Run".
    "test:cmake-not-run-missing-executable": (
        "test-cmake/plain",
        "test",
        cvf.CPP,
        "tools",
        {
            "CMakeLists.txt": PLAIN_CMAKE
            + "add_test(NAME ghost COMMAND ${CMAKE_BINARY_DIR}/does_not_exist)\n"
        },
    ),
    # engines/test.py:654: a CTest case past its TIMEOUT property fails with "Timeout".
    "test:cmake-timeout": (
        "test-cmake/plain",
        "test",
        cvf.CPP,
        "tools",
        {
            "tests/test_sleep.cpp": SLEEP_TEST,
            "CMakeLists.txt": PLAIN_CMAKE
            + "add_executable(test_sleep tests/test_sleep.cpp)\n"
            + "add_test(NAME test_sleep COMMAND test_sleep)\n"
            + "set_tests_properties(test_sleep PROPERTIES TIMEOUT 2)\n",
        },
    ),
}

_last: dict[str, object] = {}
_run = cvf.subprocess.run


def _recording_run(argv, *args, **kwargs):
    """Keep the frozen CLI's stderr (cvf.run records only its stdout)."""

    result = _run(argv, *args, **kwargs)
    if list(argv[:3]) == [cvf.PYTHON, "-m", "ici"]:
        _last["stderr"] = result.stderr.replace(str(kwargs["cwd"]), "<root>")
    return result


def main() -> None:
    prefixes = sys.argv[1:]
    cvf.subprocess.run = _recording_run
    first = lambda argv: subprocess.check_output(argv, text=True).splitlines()[0]  # noqa: E731
    existing = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    cases = dict(existing.get("cases", {}))
    for name, spec in CASES.items():
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        _last.clear()
        case = cvf.run(*spec)
        case["stderr"] = _last["stderr"]
        cases[name] = case
        print(name, case["exit_code"], (case["report"] or {}).get("status"), flush=True)
    GOLDEN.write_text(
        json.dumps(
            {
                "oracle": cvf.EXPECTED,
                "gxx": first(["/usr/bin/g++", "--version"]),
                "cmake": first(["/usr/bin/cmake", "--version"]),
                "qmake": first(["/usr/bin/qmake6", "--version"]),
                "cases": dict(sorted(cases.items())),
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
