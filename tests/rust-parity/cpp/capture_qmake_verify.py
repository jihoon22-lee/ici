"""Development-only frozen legacy qmake `ici verify`/`ici test` captures; never called by Rust.

Runs the frozen CLI over parity-fixtures/cpp/test-qmake (a SUBDIRS project whose QTest binary
lives in tests/) in a git checkout with `source_dirs=['tests']` and with the missing
`source_dirs=['src']`, and keeps the exit code, the suite counters, every engine's verdict, the
test/sanitize targets and findings and the verify `analysis_context`. The project root is
replaced by `<ROOT>`. The frozen interpreter runs under the C locale and coerces LC_CTYPE=C.UTF-8
(PEP 538) for its children; the Rust replay passes the same LC_CTYPE.
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-qmake-verify"
BASE = "[project]\ntype='cpp'\n"
# name: (fixture, extra configuration, command)
CASES = {
    "verify-tests": ("test-qmake", "source_dirs=['tests']\n", "verify"),
    "test-tests": ("test-qmake", "source_dirs=['tests']\n", "test"),
    "sanitize-tests": ("test-qmake", "source_dirs=['tests']\n", "sanitize"),
    "verify-missing-src": ("test-qmake", "source_dirs=['src']\n", "verify"),
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
QMAKE = subprocess.check_output(["qmake6", "--version"], text=True).splitlines()[0]
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def run(fixture: str, extra: str, command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp" / fixture, project)
        (project / "ici.toml").write_text(BASE + extra, encoding="utf-8")
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
        "frozen": run(fixture, extra, command),
    }
    for name, (fixture, extra, command) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/qmake-verify-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "qmake": QMAKE, "gxx": GXX, "base": BASE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} qmake cases with {QMAKE}")
