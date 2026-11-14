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
- `declared-selected-declared`: a selection naming only the declared record's id (frozen
  inspects it: the non-ELF FAIL).
- `declared-selected-allow-non-elf`: the same with `allow_non_elf = true` (the NonElf SKIP).
- `declared-selected-two`: two declared records selected by id and by path, declared in two
  patterns (frozen's manifest order: sorted keys, declared patterns, sorted matches).
- `glob-selected-missing`, `glob-selected-declared`: a glob declaration beside a selection.
- `glob-unmatched-selected`: a declaration whose second glob matches nothing (frozen publishes
  no `config.artifacts` manifest, so the selection of its literal path is missing).
- `declared-elf-truncated`: a declared record that is only the ELF magic (readelf fails).
- `declared-max-artifacts`: two selected declared records over `max_artifacts = 1`.
- `declared-readelf-unavailable`: a selected declared record with no readelf on PATH.
- `python-control`, `python-selected`, `python-selected-declared`: the same tree as a legacy
  `type='hybrid'` project with a Python package (`python.artifact`): frozen `_package_python`
  publishes no artifact manifest.

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
HYBRID = BASE.replace("type='cpp'", "type='hybrid'")
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
    "declared-selected-allow-non-elf": BASE
    + "artifacts=['release:project:CMakeLists.txt']\nallow_non_elf=true\n"
    + "[build.artifacts]\nreport=['CMakeLists.txt']\n",
    "declared-selected-two": BASE
    + "artifacts=['release:project:tests/test_pass.cpp', 'CMakeLists.txt']\n"
    + "[build.artifacts]\nreport=['tests/*.cpp', 'CMakeLists.txt']\n",
    "glob-selected-missing": BASE
    + "artifacts=['release:shadow:calc']\n[build.artifacts]\nreport=['*.txt']\n",
    "glob-selected-declared": BASE
    + "artifacts=['release:project:tests/test_fail.cpp']\n[build.artifacts]\nreport=['tests/*.cpp']\n",
    "glob-unmatched-selected": BASE
    + "artifacts=['CMakeLists.txt']\n[build.artifacts]\nreport=['CMakeLists.txt', '*.none']\n",
    "python-control": HYBRID,
    "python-selected": HYBRID
    + "artifacts=['release:shadow:calc', '0.1.0/x86_64/lib/calcpy/__init__.py']\n",
    "python-selected-declared": HYBRID
    + "artifacts=['release:project:pyproject.toml']\n[build.artifacts]\nreport=['pyproject.toml']\n",
    "declared-elf-truncated": BASE
    + "artifacts=['release:project:tool.bin']\n[build.artifacts]\nreport=['tool.bin']\n",
    "declared-max-artifacts": BASE
    + "artifacts=['release:project:tests/test_pass.cpp', 'release:project:tests/test_fail.cpp']\n"
    + "max_artifacts=1\n[build.artifacts]\nreport=['tests/*.cpp']\n",
    "declared-readelf-unavailable": BASE
    + "artifacts=['release:project:CMakeLists.txt']\n[build.artifacts]\nreport=['CMakeLists.txt']\n",
}
# Files a case adds (text; `tool.bin` is only the four ELF magic bytes, which readelf refuses).
FILES = {"declared-elf-truncated": {"tool.bin": "\x7fELF"}}
# Executables a case hides: PATH becomes a symlink farm of every other /usr/bin and /bin entry.
HIDDEN = {"declared-readelf-unavailable": ["readelf"]}
# Files the hybrid cases add (a legacy `type='hybrid'` project packages its Python sources).
HYBRID_FILES = {
    "pyproject.toml": "[project]\nname = 'calcpy'\nversion = '0.1.0'\n",
    "src/calcpy/__init__.py": "def add(a, b):\n    return a + b\n",
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


def run(name: str, config: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-compat-declared-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / ".git").mkdir()
        (work / ".hg").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        (project / "CMakeLists.txt").write_text(FAILING, encoding="utf-8")
        for relative, text in FILES.get(name, {}).items():
            (project / relative).write_text(text, encoding="utf-8")
        search = "/usr/bin:/bin"
        if name in HIDDEN:
            farm = work / "farm"
            farm.mkdir()
            for directory in ("/usr/bin", "/bin"):
                for entry in sorted(Path(directory).iterdir()):
                    link = farm / entry.name
                    if entry.name not in HIDDEN[name] and not link.is_symlink():
                        link.symlink_to(entry)
            search = str(farm)
        if name.startswith("python-"):
            for relative, text in HYBRID_FILES.items():
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text(text, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={
                "PATH": search,
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
    cases[name] = {
        "config": config,
        "files": FILES.get(name, {}),
        "hidden": HIDDEN.get(name, []),
        "frozen": run(name, config),
    }
    print(name, cases[name]["frozen"]["exit_code"], flush=True)
OUTPUT.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cmake": CMAKE,
            "fixture": "test-cmake/plain",
            "cmake_lists": FAILING,
            "hybrid_files": HYBRID_FILES,
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
