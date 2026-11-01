"""Development-only frozen legacy Make `build.make.*test_argv` captures; never called by Rust.

Runs the frozen `ici test` and `ici sanitize` over parity-fixtures/cpp/test-make (a root
Makefile whose `check` target runs one passing test program and `check-all` also a failing one)
in a git checkout with an enabled `[build.make]` table, and keeps the exit code, every engine's
verdict, targets and findings. The frozen Make adapter runs the configured test argv in the
workdir and parses its transcript with `parse_make_check_stdout` (no xunit merge). The project
root is replaced by `<ROOT>`; the Rust replay passes the same LC_CTYPE.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-make-test"
BASE = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n"
    "build_argv=['make','all']\ncoverage_build_argv=['make','all','VARIANT=coverage']\n"
    "sanitize_build_argv=['make','all','VARIANT=sanitize']\n"
)
NO_COVERAGE = BASE.replace("coverage_build_argv=['make','all','VARIANT=coverage']\n", "")
NO_SANITIZE = BASE.replace("sanitize_build_argv=['make','all','VARIANT=sanitize']\n", "")
THREAD = BASE + "thread_sanitize_build_argv=['make','all','VARIANT=thread']\n"
# Cases whose `[build.make]` table differs from BASE: a variant build argv that is missing is
# frozen `make_plan`'s MakeConfigError; parity-fixtures/cpp/tsan-make adds a thread variant.
BASES = {
    "test-no-coverage-build-argv": NO_COVERAGE,
    "sanitize-no-sanitize-build-argv": NO_SANITIZE,
    "thread-sanitize-pass": THREAD,
    "thread-sanitize-race": THREAD,
    "thread-sanitize-argv": THREAD,
    "thread-sanitize-no-build-argv": BASE,
}
# name: (fixture, extra configuration, command)
CASES = {
    "test-pass": ("test-make", "test_argv=['make','check']\n", "test"),
    "test-fail": ("test-make", "test_argv=['make','check-all']\n", "test"),
    "test-coverage-argv": (
        "test-make",
        "test_argv=['make','check']\ncoverage_test_argv=['make','check-all']\n",
        "test",
    ),
    "sanitize-pass": ("test-make", "test_argv=['make','check']\n", "sanitize"),
    "sanitize-argv": (
        "test-make",
        "test_argv=['make','check']\nsanitize_test_argv=['make','check-all']\n",
        "sanitize",
    ),
    "test-no-coverage-build-argv": ("test-make", "test_argv=['make','check']\n", "test"),
    "sanitize-no-sanitize-build-argv": ("test-make", "test_argv=['make','check']\n", "sanitize"),
    "thread-sanitize-pass": ("tsan-make", "test_argv=['make','check']\n", "thread-sanitize"),
    "thread-sanitize-race": ("tsan-make", "test_argv=['make','check-race']\n", "thread-sanitize"),
    "thread-sanitize-argv": (
        "tsan-make",
        "test_argv=['make','check']\nthread_sanitize_test_argv=['make','check-race']\n",
        "thread-sanitize",
    ),
    "thread-sanitize-no-build-argv": (
        "tsan-make",
        "test_argv=['make','check']\n",
        "thread-sanitize",
    ),
}
COUNTERS = [
    "suite_status",
    "passed_count",
    "failed_count",
    "error_count",
    "warned_count",
    "skipped_count",
    "total_count",
    "tem_score",
    "max_tem_score",
]
TARGET_FIELDS = ["target_name", "status", "message", "file_path", "start_line", "metrics"]
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
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
MAKE = subprocess.check_output(["make", "--version"], text=True).splitlines()[0]
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def run(fixture: str, base: str, extra: str, command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp" / fixture, project)
        (project / "ici.toml").write_text(base + extra, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        name = command.replace("-", "_")
        text = (project / f"{name}_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<ROOT>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = {}
    context = report.get("analysis_context") if command == "verify" else None
    for engine in report["results"] if command == "verify" else [report]:
        view = {key: engine.get(key) for key in ["status", "evidence", "summary", "score"]}
        if engine["engine_name"] in {"test", "sanitize", "thread_sanitize"}:
            view["targets"] = [
                {key: target.get(key) for key in TARGET_FIELDS} for target in engine["targets"]
            ]
            view["findings"] = [
                {key: finding.get(key) for key in FINDING_FIELDS} for finding in engine["findings"]
            ]
        engines[engine["engine_name"]] = view
    return {
        "exit_code": result.returncode,
        "counters": {key: report.get(key) for key in COUNTERS} if command == "verify" else {},
        "engines": engines,
        "analysis_context": context,
    }


cases = {
    name: {
        "fixture": fixture,
        "config": extra,
        "command": command,
        "frozen": run(fixture, BASES.get(name, BASE), extra, command),
    }
    for name, (fixture, extra, command) in CASES.items()
}
for name, base in BASES.items():
    cases[name]["base"] = base
(ROOT / "tests/rust-parity/cpp/make-test-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "make": MAKE, "gxx": GXX, "base": BASE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} Make test cases with {MAKE}")
