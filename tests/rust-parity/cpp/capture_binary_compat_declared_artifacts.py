"""Development-only frozen captures: binary_compat beside a failed configure with declared artifacts.

Never called by the Rust product. Root ruling 2026-11-14 (g3): with every session of a legacy
`ici verify --profile deep` failing at its CMake configure, frozen publishes no build-session
manifest, and BinaryCompatibilityEngine (engines/binary_compat.py `_records`) reads only the
analysis context's manifests. Cases over parity-fixtures/cpp/test-cmake/plain with a
`message(FATAL_ERROR "boom")` CMakeLists (the preparation-failures-oracle.json
`verify-deep:cmake-configure-failed-default` tree, build and binary_compat enabled):

- `control`: no declared artifact (the NOT_APPLICABLE SKIP).
- `selected-artifact`: `[engines.binary_compat] artifacts = ['release:shadow:calc']`, optional.
- `selected-artifact-required`: the same selection with `required = true`.
- `selected-two`: two selections (frozen joins the missing ones sorted, comma-separated).
- `declared-report`: `[build.artifacts] report = ['CMakeLists.txt']` (a declared non-linker
  output, `BuildEngine._publish_declared_artifacts`).
- `declared-selected-missing`: that declaration with a selection it does not publish.
- `declared-selected-mixed`: a selection naming the declared path and a missing artifact (only
  the missing one is reported).
- `declared-selected-declared`: a selection naming only the declared record's id.

Each case records the exit code, the suite counters, the build and binary_compat engines
(status, evidence, required, summary, targets, findings) and the analysis context's manifests,
with the project root as `<ROOT>`. The Rust replay is
`crates/ici-cli/tests/legacy_binary_compat_declared_artifacts.rs`.

Usage: `capture_binary_compat_declared_artifacts.py [case ...]`; TMPDIR places the work trees.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUTPUT = ROOT / "tests/rust-parity/cpp/binary-compat-declared-artifacts-oracle.json"
FIXTURE = ROOT / "parity-fixtures/cpp/test-cmake/plain"
LISTS = (FIXTURE / "CMakeLists.txt").read_text(encoding="utf-8")
FAILING = LISTS + 'message(FATAL_ERROR "boom")\n'
BASE = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\n"
    "[engines.build]\nenabled=true\n[engines.binary_compat]\nenabled=true\n"
)
CASES = {
    "control": BASE,
    "selected-artifact": BASE + "artifacts=['release:shadow:calc']\n",
    "selected-artifact-required": BASE + "artifacts=['release:shadow:calc']\nrequired=true\n",
    "selected-two": BASE + "artifacts=['release:shadow:zeta', 'bin/alpha']\n",
    "declared-report": BASE + "[build.artifacts]\nreport=['CMakeLists.txt']\n",
    "declared-selected-missing": BASE
    + "artifacts=['release:shadow:calc']\n[build.artifacts]\nreport=['CMakeLists.txt']\n",
    "declared-selected-mixed": BASE
    + "artifacts=['CMakeLists.txt', 'release:shadow:calc']\n"
    + "[build.artifacts]\nreport=['CMakeLists.txt']\n",
    "declared-selected-declared": BASE
    + "artifacts=['release:project:CMakeLists.txt']\n[build.artifacts]\nreport=['CMakeLists.txt']\n",
}
ENGINES = ("build", "binary_compat")
COUNTERS = (
    "suite_status",
    "passed_count",
    "failed_count",
    "error_count",
    "warned_count",
    "skipped_count",
    "total_count",
)
TARGET_FIELDS = ("target_name", "status", "message", "file_path", "start_line")
FINDING_FIELDS = ("rule_id", "severity", "confidence", "category", "message")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CMAKE = subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]


def pick(value: dict, fields) -> dict:
    return {field: value.get(field) for field in fields}


def engine_view(engine: dict) -> dict:
    return {
        "status": engine.get("status"),
        "evidence": engine.get("evidence"),
        "required": engine.get("required"),
        "summary": engine.get("summary"),
        "targets": [pick(target, TARGET_FIELDS) for target in engine.get("targets", [])],
        "findings": [pick(finding, FINDING_FIELDS) for finding in engine.get("findings", [])],
    }


def run(config: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-compat-declared-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / ".git").mkdir()
        (work / ".hg").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        (project / "CMakeLists.txt").write_text(FAILING, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "GIT_CEILING_DIRECTORIES": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<ROOT>"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "counters": pick(report, COUNTERS),
        "engines": {
            engine["engine_name"]: engine_view(engine)
            for engine in report["results"]
            if engine["engine_name"] in ENGINES
        },
        "context_manifests": (report.get("analysis_context") or {}).get("artifact_manifests"),
    }


only = sys.argv[1:]
cases = json.loads(OUTPUT.read_text(encoding="utf-8"))["cases"] if only and OUTPUT.exists() else {}
for name, config in CASES.items():
    if only and name not in only:
        continue
    cases[name] = {"config": config, "frozen": run(config)}
    print(name, cases[name]["frozen"]["exit_code"], flush=True)
OUTPUT.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cmake": CMAKE,
            "fixture": "test-cmake/plain",
            "cmake_lists": FAILING,
            "cases": {name: cases[name] for name in CASES if name in cases},
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} declared-artifact cases with {CMAKE}")
