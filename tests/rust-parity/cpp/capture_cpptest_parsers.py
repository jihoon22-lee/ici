"""Development-only frozen ctest/QTest transcript parsing (`ici next` cpptest provider).

Builds parity-fixtures/cpp/test-cmake/suite (CMake) and test-qmake (qmake, out-of-source) in a
fresh temporary directory, runs their real test executables, and feeds each transcript to the
frozen `_parse_ctest` / `_parse_qtest`. The golden keeps the transcript (project root replaced
by <ROOT>) and the frozen findings, measurements, limitations, parse failure or exception.
A second QTest transcript rewrites the out-of-source `Loc: [../../tests/...]` paths to the
in-root `tests/...` a source-tree build prints. Never called by the Rust product.
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
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.adapters.providers.cpptest import _parse_ctest, _parse_qtest  # noqa: E402
from ici.adapters.providers.sanitize import SanitizeProvider  # noqa: E402
from ici.execution.process import Outcome, ProcessSpec, TaskOutcome  # noqa: E402

FIXTURES = ROOT / "parity-fixtures/cpp"
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}


def run(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(
        argv, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=600, check=False
    )
    return result.stdout + result.stderr


def parsed(parser, text: str, project: Path, argv: tuple[str, ...]) -> dict:
    spec = ProcessSpec(argv=argv, name="app.cpp.test", cwd=project)
    try:
        output = parser(text, spec, project_root=project)
    except ValueError as error:
        return {"exception": f"ValueError: {error}"}
    return {
        "failed_to_parse": output.failed_to_parse,
        "findings": [
            {
                "rule_id": finding.rule_id,
                "message": finding.message,
                "path": finding.primary_location.path,
                "start_line": finding.primary_location.start_line,
                "severity": finding.severity,
                "confidence": finding.confidence,
            }
            for finding in output.findings
        ],
        "measurements": [
            {
                "name": item.name,
                "value": item.value,
                "numerator": item.numerator,
                "denominator": item.denominator,
            }
            for item in output.measurements
        ],
        "limitations": list(output.limitations),
    }


work = Path(tempfile.mkdtemp(prefix="ici-cpptest-capture-"))
cases = {}
try:
    project = work / "cmake"
    shutil.copytree(FIXTURES / "test-cmake/suite", project)
    run(["cmake", "-S", ".", "-B", "build"], project)
    run(["cmake", "--build", "build"], project)
    argv = ("ctest", "--test-dir", str(project / "build"), "--output-on-failure")
    text = run(list(argv), project)
    cases["ctest-suite"] = {
        "fixture": "test-cmake/suite",
        "transcript": text.replace(str(project), "<ROOT>"),
        "frozen": parsed(_parse_ctest, text, project, argv),
    }
    project = work / "qmake"
    shutil.copytree(FIXTURES / "test-qmake", project)
    (project / "build").mkdir()
    run(["qmake", "../project.pro"], project / "build")
    run(["make"], project / "build")
    argv = (str(project / "build/test_unit"),)
    text = run(list(argv), project)
    cases["qtest-out-of-source"] = {
        "fixture": "test-qmake",
        "transcript": text.replace(str(project), "<ROOT>"),
        "frozen": parsed(_parse_qtest, text, project, argv),
    }
    text = text.replace("Loc: [../../tests/", "Loc: [tests/")
    cases["qtest-in-root-locations"] = {
        "fixture": "test-qmake",
        "transcript": text.replace(str(project), "<ROOT>"),
        "frozen": parsed(_parse_qtest, text, project, argv),
    }
    # Frozen next SanitizeProvider.parse over real instrumented runs (stderr transcript).
    project = work / "sanitize"
    shutil.copytree(FIXTURES / "test-cmake/sanitize", project)
    (project / "build").mkdir()
    flags = {
        "sanitize": ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"],
        "thread-sanitize": ["-fsanitize=thread"],
    }
    for variant, sanitizer in flags.items():
        for test, source in (("uaf", "tests/test_uaf.cpp"), ("exit", "tests/test_exit.cpp")):
            if test == "exit":
                (project / source).write_text("int main() { return 3; }\n", encoding="utf-8")
            binary = project / "build" / f"{variant}-{test}"
            run(["g++", "-g", "-O0", *sanitizer, source, "-o", str(binary)], project)
            argv = (str(binary),)
            result = subprocess.run(
                list(argv), cwd=project, env=ENV, capture_output=True, text=True, check=False
            )
            outcome = TaskOutcome(
                spec=ProcessSpec(argv=argv, name="app.cpp.sanitize.unit", cwd=project),
                outcome=Outcome.FINISHED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            parsed_output = SanitizeProvider(variant, project_root=project).parse(outcome)
            cases[f"{variant}-{test}"] = {
                "fixture": "test-cmake/sanitize",
                "source": source,
                "exit_code": result.returncode,
                "stdout": result.stdout.replace(str(project), "<ROOT>"),
                "stderr": result.stderr.replace(str(project), "<ROOT>"),
                "frozen": {
                    "failed_to_parse": parsed_output.failed_to_parse,
                    "findings": [
                        {
                            "rule_id": item.rule_id,
                            "message": item.message,
                            "native_rule_id": item.native_rule_id,
                            "path": item.primary_location.path,
                            "start_line": item.primary_location.start_line,
                            "start_column": item.primary_location.start_column,
                            "label": item.primary_location.label,
                            "related": [
                                [span.path, span.start_line, span.label]
                                for span in item.related_locations
                            ],
                            "severity": item.severity,
                            "confidence": item.confidence,
                            "limitations": list(item.limitations),
                        }
                        for item in parsed_output.findings
                    ],
                    "measurements": [
                        {"name": item.name, "value": item.value}
                        for item in parsed_output.measurements
                    ],
                },
            }
finally:
    shutil.rmtree(work, ignore_errors=True)
(ROOT / "tests/rust-parity/cpp/cpptest-parser-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} cpptest parser cases")
