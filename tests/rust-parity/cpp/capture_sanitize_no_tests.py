"""Development-only frozen legacy sanitizer captures for a build system without tests.

Never called by Rust. Runs the frozen `ici sanitize --report`, `ici thread-sanitize --report`
and deep `ici verify --report` over small CMake and Make trees whose sanitizer build succeeds
but whose build system reports no test: CMake with `enable_testing()` and no `add_test`, CMake
without `enable_testing()`, and the parity-fixtures/cpp/tsan-make Make backend whose sanitizer
test argv runs `true`. Frozen
`_validated_adapter_process` (engines/sanitize.py:449) then records one `C++Sanitizer` ERROR at
the descriptor through `_append_scope_error` (sanitize.py:838). Each run is in a fresh git
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
        for name in ("cmake", "g++", "make")
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
