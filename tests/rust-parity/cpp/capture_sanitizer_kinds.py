"""Development-only frozen next `SanitizeProvider.parse` over UBSan, LSan and unlocated reports.

Compiles tiny programs with the real g++ sanitizers in a scratch build directory (argv[1]),
runs them with the frozen provider's option overlay (`_env`: ASAN_OPTIONS=detect_leaks=1,
UBSAN_OPTIONS=halt_on_error=1, TSAN_OPTIONS=halt_on_error=0) and records the transcript and the
frozen findings and measurements. Extends cpptest-parser-oracle.json, which holds only heap
use-after-free and a plain non-zero exit. Never called by the Rust product.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.adapters.providers.sanitize import SanitizeProvider  # noqa: E402
from ici.execution.process import Outcome, ProcessSpec, TaskOutcome  # noqa: E402

BASE = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}
OVERLAY = {
    "sanitize": {"ASAN_OPTIONS": "detect_leaks=1", "UBSAN_OPTIONS": "halt_on_error=1"},
    "thread-sanitize": {"TSAN_OPTIONS": "halt_on_error=0"},
}
FLAGS = {
    "sanitize": ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"],
    "thread-sanitize": ["-fsanitize=thread"],
}
UBSAN = """#include <climits>
int bump(int value) { return value + 1; }
int main(int argc, char**) { return bump(INT_MAX - 1 + argc) == 0; }
"""
LEAK = """#include <cstdlib>
void* keep() { return std::malloc(32); }
int main() { void* p = keep(); p = nullptr; return p != nullptr; }
"""
UAF = """#include <cstdlib>
int main() { int* p = (int*)std::malloc(4); std::free(p); return p[0]; }
"""
# (case, variant, source path, source text, extra compiler flags)
CASES = (
    ("sanitize-ubsan-overflow", "sanitize", "tests/test_overflow.cpp", UBSAN, ["-g", "-O0"]),
    ("sanitize-lsan-leak", "sanitize", "tests/test_leak.cpp", LEAK, ["-g", "-O0"]),
    # Without debug information no frame names a file, so no project location is found.
    ("sanitize-unlocated-uaf", "sanitize", "tests/test_nodebug.cpp", UAF, ["-g0", "-O0"]),
)


def finding(item) -> dict:
    return {
        "rule_id": item.rule_id,
        "message": item.message,
        "native_rule_id": item.native_rule_id,
        "path": item.primary_location.path,
        "start_line": item.primary_location.start_line,
        "start_column": item.primary_location.start_column,
        "label": item.primary_location.label,
        "related": [[span.path, span.start_line, span.label] for span in item.related_locations],
        "severity": item.severity,
        "confidence": item.confidence,
        "limitations": list(item.limitations),
    }


def main() -> None:
    project = Path(sys.argv[1]).resolve()
    shutil.rmtree(project, ignore_errors=True)
    (project / "tests").mkdir(parents=True)
    (project / "build").mkdir()
    cases = {}
    try:
        for name, variant, source, text, extra in CASES:
            (project / source).write_text(text, encoding="utf-8")
            binary = project / "build" / name
            subprocess.run(
                ["g++", *extra, *FLAGS[variant], source, "-o", str(binary)],
                cwd=project,
                env=BASE,
                check=True,
                timeout=600,
            )
            argv = (str(binary),)
            result = subprocess.run(
                list(argv),
                cwd=project,
                env={**BASE, **OVERLAY[variant]},
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            outcome = TaskOutcome(
                spec=ProcessSpec(argv=argv, name="app.cpp.sanitize.unit", cwd=project),
                outcome=Outcome.FINISHED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            parsed = SanitizeProvider(variant, project_root=project).parse(outcome)
            cases[name] = {
                "variant": variant,
                "source": source,
                "source_text": text,
                "exit_code": result.returncode,
                "stdout": result.stdout.replace(str(project), "<ROOT>"),
                "stderr": result.stderr.replace(str(project), "<ROOT>"),
                "frozen": {
                    "failed_to_parse": parsed.failed_to_parse,
                    "findings": [finding(item) for item in parsed.findings],
                    "measurements": [
                        {"name": item.name, "value": item.value} for item in parsed.measurements
                    ],
                    "limitations": list(parsed.limitations),
                },
            }
    finally:
        shutil.rmtree(project, ignore_errors=True)
    (ROOT / "tests/rust-parity/cpp/sanitizer-kinds-oracle.json").write_text(
        json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} sanitizer kind cases")


if __name__ == "__main__":
    main()
