"""Development-only frozen capture: a direct g++ sanitizer test that outlives its bound.

    python -I -B capture_sanitizer_timeout.py --out <json>

Never called by Rust. Frozen SanitizeEngine runs each direct instrumented test with
`run_process(..., max_output_chars=(MAX_SANITIZER_OUTPUT_BYTES - 1) // 2)` under run_process's
300 s default timeout (core/runner.py:478); ThreadSanitizeEngine inherits that run. A test still
running then is `_process_incomplete` (engines/sanitize.py:335-340) and `_incomplete_message`
"Sanitizer execution timed out: <snippet>" (:895-897). Waiting 300 s per capture is avoided the
way capture_lint_budget.py replaces its budget: the frozen CLI starts with the
`ici.engines.sanitize.run_process` name wrapped so that only the execution call (the one
passing that `max_output_chars`) gets `timeout=BOUND`; the compiles keep frozen's default. The
bound is recorded in the document. Root ruling 2026-11-14.

Each case writes a direct project (src/twice.cpp, tests/test_slow.cpp that prints
"test   started" to stderr and sleeps 30 s) and runs `ici sanitize --report` or
`ici thread-sanitize --report` with PATH=/usr/bin:/bin; the saved report has the project root
as <root>.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
BOUND = 3.0
LAUNCH = (
    "import sys\n"
    "import ici.engines.sanitize as sanitize\n"
    "from ici.analysis._sanitizer_diagnostics import MAX_SANITIZER_OUTPUT_BYTES\n"
    "bound = float(sys.argv.pop(1))\n"
    "original = sanitize.run_process\n"
    "def bounded(command, *args, **kwargs):\n"
    "    if kwargs.get('max_output_chars') == (MAX_SANITIZER_OUTPUT_BYTES - 1) // 2:\n"
    "        kwargs['timeout'] = bound\n"
    "    return original(command, *args, **kwargs)\n"
    "sanitize.run_process = bounded\n"
    "from ici.__main__ import main\n"
    "sys.argv[0] = 'ici'\n"
    "main()\n"
)
FILES = {
    "ici.toml": "[project]\ntype='cpp'\nsource_dirs=['src']\n",
    "src/twice.cpp": "int twice(int value) { return value * 2; }\n",
    "tests/test_slow.cpp": (
        "#include <cstdio>\n#include <unistd.h>\nint twice(int value);\n"
        'int main() { std::fprintf(stderr, "test   started\\n"); std::fflush(stderr);\n'
        "  sleep(30); return twice(2) == 4 ? 0 : 1; }\n"
    ),
}
COMMANDS = ("sanitize", "thread-sanitize")


def run(command: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-sanitizer-timeout-"))
    try:
        project = work / "project"
        for relative, text in FILES.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        (project / ".git").mkdir()
        result = subprocess.run(
            [PYTHON, "-B", "-c", LAUNCH, str(BOUND), command, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "LC_CTYPE": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        report = project / (command.replace("-", "_") + "_report.json")
        text = report.read_text(encoding="utf-8") if report.exists() else "null"
        return {
            "command": command,
            "files": FILES,
            "exit_code": result.returncode,
            "stdout": result.stdout.replace(str(project), "<root>"),
            "report": json.loads(text.replace(str(project), "<root>")),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True)
    assert head.strip() == EXPECTED
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    document = {
        "oracle": EXPECTED,
        "gxx": gxx,
        "patched_execution_timeout_seconds": BOUND,
        "frozen_default_timeout_seconds": 300.0,
        "cases": {command: run(command) for command in COMMANDS},
    }
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
