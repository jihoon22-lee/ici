"""Development-only frozen legacy build-session artifact manifest captures; never called by Rust.

Frozen `core/cmake._capture_artifact_manifest` publishes the linked shadow outputs of every
successful CMake/qmake build session of `ici verify` and fails the session through `_fail` when
`_linked_artifact_paths` exceeds its bounds (cmake.py:643 `_MAX_ARTIFACT_DISCOVERY_ENTRIES`
= 200,000 visited entries, cmake.py:667 `MAX_ARTIFACT_MANIFEST_RECORDS` = 512 records). Cases:

- `qmake-deep-build`: parity-fixtures/cpp/test-qmake under `verify --profile deep` with the build
  engine: the release session manifest (producer `qmake.build`, variant release) beside the
  coverage, sanitize and thread-sanitize session manifests.
  Frozen's release build fails there: the analysis capture configured the shared release shadow
  (build/ici-qmake-build) with `-recursive` and its temporary compiler wrapper, and the release
  session's non-recursive configure leaves `tests/Makefile` naming the deleted wrapper.
  `transcript` keeps every frozen `ici.core.cmake.run_process` call in that release shadow (the
  analysis capture's and the release session's), element by element: argv, cwd, exit, stdout,
  stderr, timed_out, truncated (cpp32, parent ruling 2026-11-14: the failure stays recorded as
  frozen's; native's corrected release is the intended difference).
- `qmake-deep-build-uncaptured`: the same with `source_dirs=['src']` (missing, so no compilable
  source and no analysis capture): the release session builds and publishes its manifest.
- `qmake-verify-standard`: the same project under the standard profile: no release session.
- `cmake-record-limit`: a generated CMake project with 520 static libraries, one executable and one
  CTest executable (more than 512 linked outputs in every session), unpatched.
- `cmake-entry-limit` / `qmake-entry-limit`: the plain CMake and the qmake fixture with frozen's
  discovery entry bound patched at runtime (the oracle tree is untouched) to 40 and 3, below each
  session's shadow tree, so every session that reaches its manifest fails its walk.
- `qmake-entry-limit-uncaptured`: the qmake bound case with `source_dirs=['src']` (no analysis
  capture, like `qmake-deep-build-uncaptured`): the release session itself reaches its walk and
  fails it (cpp36: the frozen golden for native's release session under the bound, parent ruling
  2026-11-14).
- `qmake-entry-limit-analysis`: `qmake-entry-limit` with the analysis view kept too (root ruling
  2026-11-14 (g5)): the release session fails only the BuildEngine; every source engine keeps
  the analysis capture's context. `analysis` keeps every other engine's status, evidence and
  summary and the analysis context's compilation record.

Each case keeps the exit code, the suite counters, the build/test/sanitize/thread_sanitize/
binary_compat engines (status, evidence, summary, targets, findings, tool evidence names,
manifests) and the analysis context's manifests. Digests are replaced by their shape and the
project root by `<ROOT>`. `capture_artifact_manifest_bounds.py <name>...` recaptures only those;
`ICI_CAPTURE_CPU_COUNT=<n>` patches frozen `os.cpu_count()` (its qmake `make --jobs`) to n.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-artifact-manifest-bounds"
OUTPUT = ROOT / "tests/rust-parity/cpp/artifact-manifest-bounds-oracle.json"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD = "[engines.build]\nenabled=true\n"
CMAKE_BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
QMAKE_BASE = "[project]\ntype='cpp'\nsource_dirs=['tests']\n"
DEEP = ["--profile", "deep"]
LIBRARIES = 520
GENERATED = {
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(many LANGUAGES CXX)\n"
        f"foreach(index RANGE 1 {LIBRARIES})\n"
        "  add_library(part${index} STATIC src/part.cpp)\n"
        "endforeach()\n"
        "add_executable(app src/main.cpp)\n"
        "enable_testing()\n"
        "add_executable(test_ok tests/test_ok.cpp)\n"
        "add_test(NAME test_ok COMMAND test_ok)\n"
    ),
    "tests/test_ok.cpp": "int main() { return 0; }\n",
    "src/part.cpp": "int part() { return 1; }\n",
    "src/main.cpp": "int main() { return 0; }\n",
}
# name: (fixture or None for GENERATED, base, command, args, config, patched entry bound)
CASES = {
    "qmake-deep-build": ("test-qmake", QMAKE_BASE, "verify", DEEP, BUILD, None),
    "qmake-deep-build-uncaptured": (
        "test-qmake",
        "[project]\ntype='cpp'\nsource_dirs=['src']\n",
        "verify",
        DEEP,
        BUILD,
        None,
    ),
    "qmake-verify-standard": ("test-qmake", QMAKE_BASE, "verify", [], BUILD, None),
    "cmake-record-limit": (None, CMAKE_BASE, "verify", DEEP, BUILD, None),
    "cmake-entry-limit": ("test-cmake/plain", CMAKE_BASE, "verify", DEEP, BUILD, 40),
    "qmake-entry-limit": ("test-qmake", QMAKE_BASE, "verify", DEEP, BUILD, 3),
    "qmake-entry-limit-uncaptured": (
        "test-qmake",
        "[project]\ntype='cpp'\nsource_dirs=['src']\n",
        "verify",
        DEEP,
        BUILD,
        3,
    ),
    "qmake-entry-limit-analysis": ("test-qmake", QMAKE_BASE, "verify", DEEP, BUILD, 3),
}
# Cases that also keep the analysis view (root ruling 2026-11-14 (g5)).
ANALYSIS = {"qmake-entry-limit-analysis"}
ENGINES = {"build", "test", "sanitize", "thread_sanitize", "binary_compat"}
COUNTERS = [
    "suite_status",
    "passed_count",
    "failed_count",
    "error_count",
    "warned_count",
    "skipped_count",
    "total_count",
]
TARGET_FIELDS = ["target_name", "status", "message", "file_path", "start_line"]
FINDING_FIELDS = ["rule_id", "severity", "confidence", "category", "message", "primary_location"]
# Cases whose release-shadow process transcript is kept.
TRANSCRIPT = {"qmake-deep-build"}
RELEASE_SHADOW = "build/ici-qmake-build"
LAUNCH = (
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "import ici.core.cmake as cmake\n"
    "if os.environ.get('ICI_CAPTURE_CPU_COUNT'):\n"
    "    os.cpu_count = lambda: int(os.environ['ICI_CAPTURE_CPU_COUNT'])\n"
    "bound = sys.argv.pop(1)\n"
    "log = sys.argv.pop(1)\n"
    "if bound != 'none':\n"
    "    cmake._MAX_ARTIFACT_DISCOVERY_ENTRIES = int(bound)\n"
    "if log != 'none':\n"
    "    shadow = (Path.cwd() / sys.argv.pop(1)).resolve()\n"
    "    original = cmake.run_process\n"
    "    def recorded(argv, *args, **kwargs):\n"
    "        result = original(argv, *args, **kwargs)\n"
    "        cwd = kwargs.get('cwd', args[0] if args else None)\n"
    "        if cwd is not None and Path(cwd).resolve() == shadow:\n"
    "            with open(log, 'a', encoding='utf-8') as out:\n"
    "                out.write(json.dumps({'argv': [str(a) for a in argv],\n"
    "                    'cwd': str(cwd), 'exit': result.returncode,\n"
    "                    'stdout': result.stdout, 'stderr': result.stderr,\n"
    "                    'timed_out': result.timed_out, 'truncated': result.truncated}) + '\\n')\n"
    "        return result\n"
    "    cmake.run_process = recorded\n"
    "from ici.__main__ import main\n"
    "sys.argv[0] = 'ici'\n"
    "main()\n"
)
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CMAKE = subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]
QMAKE = subprocess.check_output(["qmake6", "--version"], text=True).splitlines()[0]
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, str) and DIGEST.fullmatch(value):
        return "<sha256>"
    return value


def pick(value: dict, fields: list[str]) -> dict:
    return {field: value.get(field) for field in fields}


def engine_view(engine: dict) -> dict:
    return {
        "status": engine.get("status"),
        "evidence": engine.get("evidence"),
        "summary": engine.get("summary"),
        "targets": [pick(target, TARGET_FIELDS) for target in engine.get("targets", [])],
        "findings": [pick(finding, FINDING_FIELDS) for finding in engine.get("findings", [])],
        "tool_evidence": [item.get("name") for item in engine.get("tool_evidence", [])],
        "artifact_manifests": shape(engine.get("artifact_manifests")),
    }


def run(name, fixture, base, command, args, config, bound) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        if fixture is None:
            for name, text in GENERATED.items():
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text(text, encoding="utf-8")
        else:
            shutil.copytree(ROOT / "parity-fixtures/cpp" / fixture, project)
        (project / "ici.toml").write_text(base + config, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        log = WORK / "transcript.jsonl"
        logged = [str(log), RELEASE_SHADOW] if name in TRANSCRIPT else ["none"]
        result = subprocess.run(
            [PYTHON, "-B", "-c", LAUNCH, str(bound or "none"), *logged, command, *args, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
                "LC_CTYPE": "C.UTF-8",
                # Optional: frozen's qmake `make --jobs=<os.cpu_count()>` on a shared host.
                **(
                    {"ICI_CAPTURE_CPU_COUNT": os.environ["ICI_CAPTURE_CPU_COUNT"]}
                    if os.environ.get("ICI_CAPTURE_CPU_COUNT")
                    else {}
                ),
            },
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        text = (project / f"{command}_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<ROOT>"))
        transcript = (
            [
                json.loads(line.replace(str(project), "<ROOT>"))
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            if name in TRANSCRIPT
            else None
        )
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = report["results"] if command == "verify" else [report]
    context = report.get("analysis_context") or {}
    return {
        "exit_code": result.returncode,
        "counters": pick(report, COUNTERS) if command == "verify" else None,
        "engines": {
            engine["engine_name"]: engine_view(engine)
            for engine in engines
            if engine["engine_name"] in ENGINES
        },
        "context_manifests": shape(context.get("artifact_manifests")),
        **({"transcript": transcript} if transcript is not None else {}),
        **(
            {
                "analysis": {
                    "engines": {
                        engine["engine_name"]: {
                            "status": engine.get("status"),
                            "evidence": engine.get("evidence"),
                            "summary": engine.get("summary"),
                        }
                        for engine in engines
                        if engine["engine_name"] not in ENGINES
                    },
                    "compilation": shape(context.get("compilation")),
                }
            }
            if name in ANALYSIS
            else {}
        ),
    }


only = sys.argv[1:]
cases = json.loads(OUTPUT.read_text(encoding="utf-8"))["cases"] if only and OUTPUT.exists() else {}
for name, (fixture, base, command, args, config, bound) in CASES.items():
    if only and name not in only:
        continue
    cases[name] = {
        "fixture": fixture,
        "base": base,
        "command": command,
        "args": args,
        "config": config,
        "entry_bound": bound,
        "frozen": run(name, fixture, base, command, args, config, bound),
    }
    print(name, cases[name]["frozen"]["exit_code"], flush=True)
cases = {name: cases[name] for name in CASES if name in cases}
OUTPUT.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "cmake": CMAKE,
            "qmake": QMAKE,
            "gxx": GXX,
            "generated": GENERATED,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} artifact manifest bound cases with {CMAKE}, {QMAKE}")
