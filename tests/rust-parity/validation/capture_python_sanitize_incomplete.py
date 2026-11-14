"""Development-only frozen capture: the Python ResourceWarning scope's incomplete pytest runs.

    python -I -B capture_python_sanitize_incomplete.py --out <json>

Never called by Rust; root ruling 2026-11-14 (f2). Frozen `_check_python_resource_warnings`
(analysis/_sanitize_python_scope.py:114-127) runs `<python> -W error::ResourceWarning -m pytest
-o addopts= tests` through `run_process(command, cwd=root, env=env)`, under run_process's
defaults (core/runner.py:478-480: 300 s, 1,000,000 characters per stream). A run that raises is
'Pytest could not execute: <exc>'; a timed-out, truncated or signalled run is
`_incomplete_message` (engines/sanitize.py:895-900) with the 300-character `_snippet` of stderr,
else stdout. Each becomes the always-ERROR `PythonResourceWarnings` target at tests:1.

As capture_sanitizer_timeout.py does, the frozen CLI starts with the module's `run_process`
name wrapped, and only the pytest call is touched: `timeout` gets a BOUND-second deadline instead
of waiting out 300 s, and `raise` makes the call raise RuntimeError, the only way to reach that
branch (run_process itself returns a result for a spawn failure). `truncated` and `signal` run
unpatched: a failing test that prints 1,100,000 characters (pytest repeats its captured output),
and a test that SIGKILLs the pytest process. The wrapper also writes what run_process returned
(returncode, timed_out, truncated, the stream lengths and their first 4096 characters, which hold
every character the snippet reads) for the native replay.

Each case writes a legacy Python project (src/calc.py, tests/test_case.py) and runs
`ici sanitize --report` with PATH=/usr/bin:/bin and an empty HOME; the saved report has the
project root as <root>.
"""

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
PREFIX = 4096
LAUNCH = (
    "import json, sys\n"
    "import ici.analysis._sanitize_python_scope as scope\n"
    "mode = sys.argv.pop(1)\n"
    "bound = float(sys.argv.pop(1))\n"
    "record = sys.argv.pop(1)\n"
    "original = scope.run_process\n"
    "def wrapped(command, *args, **kwargs):\n"
    "    if '-m' in command and 'pytest' in command:\n"
    "        if mode == 'raise':\n"
    "            raise RuntimeError('injected runner failure')\n"
    "        if mode == 'timeout':\n"
    "            kwargs['timeout'] = bound\n"
    "        result = original(command, *args, **kwargs)\n"
    "        with open(record, 'w', encoding='utf-8') as stream:\n"
    "            json.dump({\n"
    "                'returncode': result.returncode,\n"
    "                'timed_out': result.timed_out,\n"
    "                'truncated': result.truncated,\n"
    "                'stdout_length': len(result.stdout),\n"
    "                'stderr_length': len(result.stderr),\n"
    f"                'stdout_prefix': result.stdout[:{PREFIX}],\n"
    f"                'stderr_prefix': result.stderr[:{PREFIX}],\n"
    "            }, stream)\n"
    "        return result\n"
    "    return original(command, *args, **kwargs)\n"
    "scope.run_process = wrapped\n"
    "from ici.__main__ import main\n"
    "sys.argv[0] = 'ici'\n"
    "main()\n"
)
CONFIG = '[project]\ntype = "python"\nsource_dirs = ["src"]\n'
SOURCE = "def add(a, b):\n    return a + b\n"
TESTS = {
    "timeout": "import time\n\n\ndef test_slow():\n    time.sleep(30)\n",
    "truncated": (
        "def test_loud():\n    for _ in range(11000):\n        print('x' * 99)\n    assert False\n"
    ),
    "signal": (
        "import os\nimport signal\n\n\ndef test_killed():\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
    ),
    "raise": "def test_ok():\n    assert True\n",
}


def run(mode: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-python-sanitize-incomplete-"))
    try:
        project = work / "project"
        files = {
            "ici.toml": CONFIG,
            "src/calc.py": SOURCE,
            "tests/test_case.py": TESTS[mode],
        }
        for relative, text in files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        (project / ".git").mkdir()
        record = work / "record.json"
        result = subprocess.run(
            [PYTHON, "-B", "-c", LAUNCH, mode, str(BOUND), str(record), "sanitize", "--report"],
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
        report = project / "sanitize_report.json"
        text = report.read_text(encoding="utf-8") if report.exists() else "null"
        process = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        if process is not None:
            for key in ("stdout_prefix", "stderr_prefix"):
                process[key] = process[key].replace(str(project), "<root>")
        return {
            "files": files,
            "exit_code": result.returncode,
            "process": process,
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
    document = {
        "oracle": EXPECTED,
        "patched_timeout_seconds": BOUND,
        "frozen_default_timeout_seconds": 300.0,
        "frozen_default_output_chars": 1_000_000,
        "prefix_characters": PREFIX,
        "cases": {mode: run(mode) for mode in TESTS},
    }
    args.out.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
