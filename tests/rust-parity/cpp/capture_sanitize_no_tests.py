"""Development-only frozen legacy sanitizer captures for a build system without tests.

Never called by Rust. Runs the frozen `ici sanitize --report`, `ici thread-sanitize --report`
and deep `ici verify --report` over small CMake and Make trees whose sanitizer build succeeds
but whose build system reports no test: CMake with `enable_testing()` and no `add_test`, CMake
without `enable_testing()`, and the parity-fixtures/cpp/tsan-make Make backend whose sanitizer
test argv runs `true`. Frozen
`_validated_adapter_process` (engines/sanitize.py:449) then records one `C++Sanitizer` ERROR at
the descriptor through `_append_scope_error` (sanitize.py:838); a failing case that prints past the
output bound is that scope error too ('Sanitizer test process truncated its output', :467), on
either side of which two more trees sit, and a qmake `make check` without a test binary is the
no-tests scope error at project.pro. Each run is in a fresh git
checkout under PATH=/usr/bin:/bin with LC_CTYPE=C.UTF-8 (the frozen interpreter coerces the C
locale for its children, PEP 538). The project root is replaced by `<ROOT>`.

Usage: python -I -B capture_sanitize_no_tests.py --oracle <frozen checkout> --out <json>
"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[3] / "parity-fixtures/cpp"
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
LIB = "int add(int a, int b) { return a + b; }\n"
TEST = "int add(int, int);\nint main() { return add(1, 2) == 3 ? 0 : 1; }\n"
CMAKE_HEAD = "cmake_minimum_required(VERSION 3.16)\nproject(calc LANGUAGES CXX)\n"
CMAKE_BODY = (
    "add_library(calc STATIC src/calc.cpp)\n"
    "add_executable(test_ok tests/test_ok.cpp)\n"
    "target_link_libraries(test_ok calc)\n"
)
CPP = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
MAKE = (
    CPP + "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
    "sanitize_build_argv=['make','all','VARIANT=sanitize']\n"
    "thread_sanitize_build_argv=['make','all','VARIANT=thread']\n"
    "test_argv=['true']\nsanitize_test_argv=['true']\nthread_sanitize_test_argv=['true']\n"
)
SOURCES = {"src/calc.cpp": LIB, "tests/test_ok.cpp": TEST}
TREES = {
    "cmake-no-add-test": {
        "files": {"CMakeLists.txt": CMAKE_HEAD + "enable_testing()\n" + CMAKE_BODY, **SOURCES},
        "config": CPP,
    },
    "cmake-no-enable-testing": {
        "files": {"CMakeLists.txt": CMAKE_HEAD + CMAKE_BODY, **SOURCES},
        "config": CPP,
    },
    # parity-fixtures/cpp/tsan-make (sanitize and thread variants in build/ici-make).
    "make-silent-test": {"fixture": "tsan-make", "config": MAKE},
}
# name: (tree, command, extra configuration)
CASES = {
    f"{command}:{tree}": (tree, command, "")
    for tree in TREES
    for command in ("sanitize", "thread-sanitize")
}
# A failing CTest case that prints past frozen run_process's 1,000,000-character bound (and
# native's 1 MiB suite bound): sanitize.py:467 'Sanitizer test process truncated its output'.
FLOOD = (
    "#include <cstdio>\nint add(int, int);\n"
    "int main() { for (int i = 0; i < 1100000; ++i) std::fputc('x', stdout);\n"
    "  return add(1, 2) == 4 ? 0 : 1; }\n"
)
TREES["cmake-truncated-suite"] = {
    "files": {
        "CMakeLists.txt": CMAKE_HEAD
        + "enable_testing()\n"
        + CMAKE_BODY
        + "add_test(NAME test_ok COMMAND test_ok)\n",
        "src/calc.cpp": LIB,
        "tests/test_ok.cpp": FLOOD,
    },
    "config": CPP,
}
for _command in ("sanitize", "thread-sanitize"):
    CASES[f"{_command}:cmake-truncated-suite"] = ("cmake-truncated-suite", _command, "")
# Both sides of frozen's bound (cppval17, root ruling 2026-11-13): run_process truncates a stream
# past 1,000,000 bytes (core/runner.py:480, `_BoundedCapture`); native's suite bound was 1 MiB
# (1,048,576). A failing CTest case printing 990,000 ASCII bytes stays under it with ctest's own
# lines (a few hundred bytes); 1,010,000 is past frozen's bound and under 1 MiB.
for _tree, _count in (("cmake-suite-under-bound", 990000), ("cmake-suite-between-bounds", 1010000)):
    TREES[_tree] = {
        "files": {
            **TREES["cmake-truncated-suite"]["files"],
            "tests/test_ok.cpp": FLOOD.replace("1100000", str(_count)),
        },
        "config": CPP,
    }
    for _command in ("sanitize", "thread-sanitize"):
        CASES[f"{_command}:{_tree}"] = (_tree, _command, "")
# A qmake project whose `make check` runs no test binary (cppval17): the subdirs check target of
# an app without `CONFIG += testcase` exits 0 and prints none, frozen `_qmake_results` reads no
# case (core/_cmake_test_results.py:385-387, any exit) and sanitize.py:449 records the scope error
# at project.pro.
TREES["qmake-no-testcase"] = {
    "files": {
        "project.pro": "TEMPLATE = subdirs\nSUBDIRS = tests\n",
        "tests/tests.pro": "TEMPLATE = app\nCONFIG += console\nCONFIG -= app_bundle qt\n"
        "TARGET = test_unit\nSOURCES += test_unit.cpp\n",
        "tests/test_unit.cpp": "int main() { return 0; }\n",
        "src/calc.cpp": LIB,
    },
    "config": CPP,
}
for _command in ("sanitize", "thread-sanitize"):
    CASES[f"{_command}:qmake-no-testcase"] = ("qmake-no-testcase", _command, "")
CASES["verify:cmake-no-add-test"] = ("cmake-no-add-test", "verify", "[ici]\nprofile='deep'\n")
TARGET_FIELDS = ["target_name", "status", "message", "file_path", "start_line"]
FINDING_FIELDS = [
    "rule_id",
    "severity",
    "confidence",
    "category",
    "message",
    "primary_location",
    "fingerprint",
    "tool_name",
]


def engine_view(engine: dict) -> dict:
    return {
        "status": engine["status"],
        "evidence": engine["evidence"],
        "required": engine["required"],
        "summary": engine["summary"],
        "score": engine["score"],
        "extra": engine["extra"],
        "targets": [{key: target[key] for key in TARGET_FIELDS} for target in engine["targets"]],
        "findings": [
            {key: finding[key] for key in FINDING_FIELDS} for finding in engine["findings"]
        ],
        "tool_evidence": [record["name"] for record in engine["tool_evidence"]],
    }


def run(oracle: Path, work: Path, tree: dict, command: str, extra: str) -> dict:
    shutil.rmtree(work, ignore_errors=True)
    root = work / "project"
    if "fixture" in tree:
        shutil.copytree(FIXTURES / tree["fixture"], root)
    for relative, text in tree.get("files", {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "ici.toml").write_text(extra + tree["config"], encoding="utf-8")
    git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
    for args in (["init", "-q", "."], ["add", "-A"], ["commit", "-qm", "fixture"]):
        subprocess.run([*git, *args], cwd=root, check=True)
    home = work / "home"
    home.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LC_CTYPE": "C.UTF-8",
        "PYTHONPATH": str(oracle / "src"),
        "GIT_CEILING_DIRECTORIES": str(work),
    }
    proc = subprocess.run(
        [PYTHON, "-B", "-m", "ici", command, "--report"],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=1200,
        check=False,
    )
    text = (root / f"{command.replace('-', '_')}_report.json").read_text(encoding="utf-8")
    report = json.loads(text.replace(str(root), "<ROOT>"))
    if command == "verify":
        engines = {
            engine["engine_name"]: engine_view(engine)
            for engine in report["results"]
            if engine["engine_name"] in ("sanitize", "thread_sanitize")
        }
    else:
        engines = {report["engine_name"]: engine_view(report)}
    return {"exit_code": proc.returncode, "engines": engines}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(
        ["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head == EXPECTED, head
    tools = {
        name: subprocess.check_output([f"/usr/bin/{name}", "--version"], text=True).splitlines()[0]
        for name in ("cmake", "g++", "make", "qmake")
    }
    runs = {}
    with tempfile.TemporaryDirectory(prefix="ici-sanitize-no-tests-") as scratch:
        for name, (tree, command, extra) in CASES.items():
            runs[name] = run(args.oracle, Path(scratch) / "w", TREES[tree], command, extra)
            print(name, runs[name]["exit_code"], flush=True)
    document = {"source": head, "tools": tools, "trees": TREES, "cases": CASES, "runs": runs}
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
