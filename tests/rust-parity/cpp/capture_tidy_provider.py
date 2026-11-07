"""Development-only frozen `ici next` ClangTidyProvider.parse captures; never called by Rust.

Runs the real clang-tidy (ICI_TEST_CLANG_TIDY) with `-p build` over parity-fixtures/cpp/lint-tidy
in a fresh temporary project and feeds the transcript to the frozen provider parse. Extra cases
reuse that transcript: a clean file (no findings), a run that did not finish (timed out), a
transcript with a null byte (unparseable), one whose only diagnostic is outside the project
(dropped) and the stdout alone (without the stderr progress lines the frozen parser rejects). Transcripts store the project root as <ROOT>.
"""

import json
import os
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

from ici.adapters.providers.tidy import ClangTidyProvider  # noqa: E402
from ici.execution.process import Outcome, ProcessSpec, TaskOutcome  # noqa: E402

TIDY = os.environ["ICI_TEST_CLANG_TIDY"]
VERSION = subprocess.check_output([TIDY, "--version"], text=True).strip()


def parse(project: Path, argv: tuple[str, ...], outcome: Outcome, stdout: str, stderr: str):
    task = TaskOutcome(
        spec=ProcessSpec(argv=argv, name="app.cpp.tidy", cwd=project),
        outcome=outcome,
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
    )
    parsed = ClangTidyProvider().parse(task)
    return {
        "failed_to_parse": parsed.failed_to_parse,
        "findings": [
            {
                "fingerprint": finding.fingerprint,
                "rule_id": finding.rule_id,
                "native_rule_id": finding.native_rule_id,
                "message": finding.message,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "path": finding.primary_location.path,
                "start_line": finding.primary_location.start_line,
                "start_column": finding.primary_location.start_column,
                "provider": finding.provider,
                "task_id": finding.task_id,
                "evidence": finding.evidence.value,
            }
            for finding in parsed.findings
        ],
    }


work = Path(tempfile.mkdtemp(prefix="ici-tidy-provider-"))
try:
    project = work / "project"
    shutil.copytree(ROOT / "parity-fixtures/cpp/lint-tidy", project)
    sources = sorted(f"src/{path.name}" for path in (project / "src").iterdir())
    (project / "build").mkdir()
    (project / "build/compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(project),
                    "file": source,
                    "arguments": ["g++", "-std=c++17", "-Iinclude", "-c", source],
                }
                for source in sources
            ]
        ),
        encoding="utf-8",
    )
    argv = (TIDY, "-p", "build", *sources)
    result = subprocess.run(
        list(argv),
        cwd=project,
        env={"PATH": "/usr/bin:/bin", "HOME": str(work), "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    stdout, stderr = result.stdout, result.stderr
    outside = "/usr/include/outside.h:3:5: warning: outside the project [misc-outside]\n"
    transcripts = {
        # clang-tidy 21 prints `[n/N] Processing file` progress lines on stderr.
        "real": (Outcome.FINISHED, stdout, stderr),
        "real-stdout": (Outcome.FINISHED, stdout, ""),
        "clean": (Outcome.FINISHED, "", ""),
        "timed-out": (Outcome.TIMED_OUT, stdout, ""),
        "null-byte": (Outcome.FINISHED, stdout + "\0", ""),
        "outside-only": (Outcome.FINISHED, outside, ""),
    }
    cases = {
        name: {
            "outcome": outcome.value,
            "stdout": out.replace(str(project), "<ROOT>"),
            "stderr": err.replace(str(project), "<ROOT>"),
            "frozen": json.loads(
                json.dumps(parse(project, argv, outcome, out, err)).replace(str(project), "<ROOT>")
            ),
        }
        for name, (outcome, out, err) in transcripts.items()
    }
    exit_code = result.returncode
finally:
    shutil.rmtree(work, ignore_errors=True)
(ROOT / "tests/rust-parity/cpp/tidy-provider-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "clang_tidy": VERSION,
            "argv_tail": ["-p", "build", *sources],
            "exit_code": exit_code,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} clang-tidy provider cases with {VERSION.splitlines()[0]}")
