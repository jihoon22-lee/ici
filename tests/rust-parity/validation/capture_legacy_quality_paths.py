"""Development-only capture: frozen legacy deep `ici verify` test-quality repeat/mutation paths.

A conftest counts pytest sessions in a state file next to it, so the second session (the first
quality repeat) can time out, exit outside 0/1, be killed by a signal, or print no per-test
verdict (passing or failing), a third session can time out after a bounded second one, and the
first session (the base suite) can print no per-test verdict (passing or failing). Mutation capability is probed with no
tool on PATH, a succeeding command and a failing command.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

CONFTEST = (
    "import os\nimport pathlib\nimport time\n\n"
    "STATE = pathlib.Path(__file__).with_name('run.state')\n"
    "MODE = pathlib.Path(__file__).with_name('mode.txt').read_text().strip()\n\n\n"
    "def pytest_configure(config):\n"
    "    run = int(STATE.read_text()) + 1 if STATE.exists() else 1\n"
    "    STATE.write_text(str(run))\n"
    "    config.ici_run = run\n"
    "    if (MODE in ('quiet_repeat', 'quiet_failing_repeat') and run == 2) or (\n"
    "        MODE in ('quiet_base', 'quiet_failing_base') and run == 1\n"
    "    ):\n"
    "        config.option.verbose = -1\n\n\n"
    "def pytest_runtest_call(item):\n"
    "    run = item.config.ici_run\n"
    "    if (MODE == 'timeout' and run == 2) or (MODE == 'timeout_third' and run == 3):\n"
    "        time.sleep(30)\n"
    "    if MODE == 'exit' and run == 2:\n"
    "        os._exit(7)\n"
    "    if MODE == 'signal' and run == 2:\n"
    "        os.kill(os.getpid(), 9)\n"
    "    if MODE == 'quiet_failing_repeat' and run == 2:\n"
    "        raise AssertionError('repeat failure')\n"
    "    if MODE == 'quiet_failing_base' and run == 1:\n"
    "        raise AssertionError('base failure')\n"
)
TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
# scenario: (conftest mode, extra [engines.test.quality] lines)
SCENARIOS = {
    "timeout": ("timeout", "repeat_runs = 2\ntimeout = 2\n"),
    "exit": ("exit", "repeat_runs = 2\n"),
    "quiet_repeat": ("quiet_repeat", "repeat_runs = 2\n"),
    "quiet_base": ("quiet_base", "repeat_runs = 2\n"),
    # The quiet base suite fails: its summary count is the failing reading.
    "quiet_failing_base": ("quiet_failing_base", "repeat_runs = 2\n"),
    # Distinct failure modes: a later repeat times out after a bounded one, a repeat is
    # killed by a signal (negative return code), a quiet repeat fails (exit 1).
    "timeout_third": ("timeout_third", "repeat_runs = 3\ntimeout = 2\n"),
    "signal": ("signal", "repeat_runs = 2\n"),
    "quiet_failing_repeat": ("quiet_failing_repeat", "repeat_runs = 2\n"),
    "mutation_missing": ("none", "[engines.test.quality.mutation]\nenabled = true\n"),
    "mutation_available": (
        "none",
        "[engines.test.quality.mutation]\nenabled = true\ncommand = ['/bin/true']\n",
    ),
    "mutation_command_missing": (
        "none",
        "[engines.test.quality.mutation]\nenabled = true\ncommand = ['/nonexistent/mutation']\n",
    ),
    "mutation_failing": (
        "none",
        "[engines.test.quality.mutation]\nenabled = true\ncommand = ['/bin/false']\n",
    ),
}


def config(python: str, extra: str) -> str:
    text = (
        '[ici]\nprofile = "deep"\n[project]\nsource_dirs = ["src"]\n[engines]\n'
        f"[engines.test]\nenabled = true\nrequired = true\npython = {json.dumps(python)}\n"
        "[engines.test.quality]\nenabled = true\nmode = 'report'\n" + extra
    )
    for engine in DISABLED:
        if engine != "test":
            text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = false\n[engines.integration]\nenabled = false\n"


def capture(oracle: Path, python: Path, mode: str, extra: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-quality-paths-") as temporary:
        root = Path(temporary)
        (root / "src").mkdir()
        (root / "src/calc.py").write_text("X = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/conftest.py").write_text(CONFTEST, encoding="utf-8")
        (root / "tests/mode.txt").write_text(mode + "\n", encoding="utf-8")
        (root / "tests/test_calc.py").write_text(TEST, encoding="utf-8")
        (root / "ici.toml").write_text(config(str(python), extra), encoding="utf-8")
        completed = subprocess.run(
            [str(python), "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src"), PATH="/usr/bin:/bin"),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        with (root / "verify_report.json").open(encoding="utf-8") as handle:
            report = json.load(handle)
    engine = next(item for item in report["results"] if item["engine_name"] == "test")
    return {
        "mode": mode,
        "config": config("<PYTHON>", extra),
        "exit_code": completed.returncode,
        "status": engine["status"],
        "summary": engine["summary"],
        "targets": [
            {key: target[key] for key in ("file_path", "start_line", "target_name", "status")}
            | {"message": target["message"], "metrics": target["metrics"]}
            for target in engine["targets"]
        ],
        "quality_findings": [
            {key: finding[key] for key in ("rule_id", "message", "fingerprint")}
            for finding in engine["findings"]
            if finding["rule_id"].startswith("ici.test.")
            or "[test-quality]" in finding["primary_location"]["label"]
        ],
        "test_quality": engine["extra"].get("test_quality"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {
        name: capture(args.oracle, args.python, mode, extra)
        for name, (mode, extra) in SCENARIOS.items()
    }
    document = {"source": SOURCE, "conftest": CONFTEST, "test": TEST, "runs": runs}
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _private_home():
    """The shared private HOME/XDG context (tests/rust-parity/private_home.py), loaded by path."""
    path = Path(__file__).resolve().parents[1] / "private_home.py"
    spec = importlib.util.spec_from_file_location("rust_parity_private_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.private_home()


if __name__ == "__main__":
    with _private_home():
        main()
