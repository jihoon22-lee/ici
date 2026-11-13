"""Development-only frozen capture: a C++-only project never runs the deep test-quality pass.

    python -B capture_quality_cpp_deep.py

Frozen `TestQualityMixin._run_deep_test_quality` (engines/test_quality.py:324-334, oracle
6fe3e405) returns `empty_quality_info()` with only the profile filled in unless the analysis
profile is deep AND the project type is python/hybrid, so a legacy `type='cpp'` project with
every `[engines.test.quality]` key set (enabled, mode, repeat_runs, timeout, slow_test_threshold,
max_slow_tests, and a mutation table with enabled, tool and a marker-writing command; the
`repeat`/`slow_threshold` aliases are refused at load next to the default canonical keys, see
tests/rust-parity/config/legacy-schema-oracle.json) repeats no test, measures no slow test and runs no mutation command.
Each case copies parity-fixtures/cpp/test-direct/mixed (one passing and one failing direct C++
test, the real /usr/bin/g++), enables only the test engine and runs the frozen CLI's
`ici verify [--profile deep] --report`. Records the exit code, the test engine's status, every
target, the grouped and flat quality counters and whether the mutation marker exists.
`legacy_cpp_quality_deep.rs` replays every case natively. Never called by the Rust product.
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
FIXTURE = ROOT / "parity-fixtures/cpp/test-direct/mixed"
GOLDEN = ROOT / "tests/rust-parity/cpp/quality-cpp-deep-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
QUALITY_KEYS = (
    "test_quality",
    "quality_enabled",
    "repeat_runs",
    "repeat_reruns",
    "repeat_cases",
    "repeat_unavailable",
    "repeat_timeouts",
    "flaky_tests",
    "slow_tests",
    "slow_tests_observed",
    "slow_test_inventory",
    "flaky_test_inventory",
    "mutation_probes",
    "mutation_available",
    "mutation_unavailable",
    "mutation_status",
)
OFF_ENGINES = (
    "lint",
    "line",
    "type",
    "python_compat",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "build",
    "binary_compat",
    "integration",
    "cycle",
    "compile_db",
)
HEAD = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
OFF = "".join(f"[engines.{name}]\nenabled=false\n" for name in OFF_ENGINES)
MARKER = "mutation-ran"
MUTATION = (
    "[engines.test.quality.mutation]\nenabled=true\ntool='mutmut'\n"
    f"command=['touch', '{MARKER}']\n"
)
QUALITY = (
    "[engines.test.quality]\nenabled=true\nmode='warn'\nrepeat_runs=3\ntimeout=30\n"
    "slow_test_threshold=0.0001\nmax_slow_tests=2\n" + MUTATION
)
DISABLED = "[engines.test.quality]\nenabled=false\n"
# name: (argv after `ici`, quality table)
CASES = {
    "deep-quality-mutation": (["verify", "--profile", "deep", "--report"], QUALITY),
    "deep-quality-disabled": (["verify", "--profile", "deep", "--report"], DISABLED),
    "standard-quality-mutation": (["verify", "--report"], QUALITY),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def run(argv: list, quality: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-quality-cpp-deep-")).resolve()
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(HEAD + quality + OFF, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", *argv],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "LC_ALL": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if not (project / "verify_report.json").exists():
            raise SystemExit(result.stdout[-1500:] + result.stderr[-1500:])
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
        marker = (project / MARKER).exists()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    engines = {item["engine_name"]: item for item in report["results"]}
    test = engines["test"]
    return {
        "argv": argv,
        "quality": quality,
        "exit_code": result.returncode,
        "status": test["status"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in test["targets"]],
        "quality_extra": {key: test["extra"].get(key) for key in QUALITY_KEYS},
        "mutation_marker": marker,
    }


def main() -> None:
    cases = {}
    for name, (argv, quality) in CASES.items():
        cases[name] = run(argv, quality)
        print(name, cases[name]["exit_code"], cases[name]["status"], cases[name]["mutation_marker"])
    document = {
        "oracle": EXPECTED,
        "gxx": GXX,
        "head": HEAD,
        "off": OFF,
        "marker": MARKER,
        "cases": cases,
    }
    GOLDEN.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} quality cases")


if __name__ == "__main__":
    main()
