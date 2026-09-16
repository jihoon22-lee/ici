#!/usr/bin/env python3
"""Fixed-fixture benchmark for ``ici next`` (WP28 #226 item 3).

What is measured, on a generated workspace of ``--files`` Python modules:

- cold ``next verify`` wall time and peak RSS (first run, empty ``.ici``);
- warm ``next verify`` wall time (same run, observation cache populated);
- provider/task counts and the ``reused`` count the second run reports;
- ``result.json`` / ``result.html`` / observation-cache on-disk sizes;
- cancel latency: SIGINT sent the moment ``run.started`` lands in the
  ``--events`` stream, then time to process exit and ``run.completed``.

The record is a trend artifact, not a gate — the same rule as
``benchmark_report.py``. Numbers land in
``docs/design/ici-next/inventory/wp28-measurements.md`` with the machine
they came from; nothing here is asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SRC = _ROOT / "src"

BENCHMARK_SCHEMA = "ici.benchmark.next/v1"

# Each module gets index-shaped names and branch constants — a corpus of
# identical files is a duplicate-detector stress case, not a realistic one.
_MODULE = '''"""Benchmark module {index}."""


def handler_{index}(value_{index}, scale, flag):
    """A few branches so the metric checks have something to see."""
    if value_{index} > {index}:
        if scale > 0:
            return value_{index} + scale + {index}
        return value_{index} - scale
    if flag:
        return value_{index} * scale + {index} * 10 + 1
    return {index}
'''

_CONFIG = """schema_version = 1
[workspace]
name = "bench"
[[components]]
id = "app"
root = "."
languages = ["python"]
[checks."python.test"]
enabled = false
[checks."python.coverage"]
enabled = false
[checks."python.type"]
enabled = false
[checks."python.format"]
enabled = false
[checks."python.compat-runtime"]
enabled = false
# The fixture modules share one shape; a few hundred near-clones is a dup
# stress case, not the run this benchmark is about.
[checks."python.dup"]
enabled = false
"""


def _fixture(directory: Path, files: int) -> None:
    """A fixed-shape workspace: N modules, one seeded unused import."""
    (directory / "src").mkdir(parents=True)
    for index in range(files):
        (directory / "src" / f"mod_{index:04d}.py").write_text(
            _MODULE.format(index=index), encoding="utf-8"
        )
    (directory / "src" / "mod_0000.py").write_text(
        "import os\n" + _MODULE.format(index=0), encoding="utf-8"
    )
    (directory / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (directory / "ici.toml").write_text(_CONFIG, encoding="utf-8")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _peak_rss(pid: int, stop: list[bool]) -> int:
    """Poll /proc for VmHWM while the child runs; KiB, Linux-only."""
    peak = 0
    status = Path(f"/proc/{pid}/status")
    while not stop[0]:
        try:
            for line in status.read_text().splitlines():
                if line.startswith("VmHWM:"):
                    peak = max(peak, int(line.split()[1]))
        except OSError:
            break
        time.sleep(0.02)
    return peak


def _verify(workspace: Path, *extra: str) -> tuple[subprocess.Popen, float, int]:
    """Run ``next verify``; returns (completed proc, wall seconds, peak KiB)."""
    import threading

    argv = [sys.executable, "-m", "ici", "next", "verify", *extra]
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        cwd=workspace,
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    stop = [False]
    peak = [0]

    def watch() -> None:
        peak[0] = _peak_rss(proc.pid, stop)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    output, _ = proc.communicate()
    stop[0] = True
    watcher.join(timeout=1)
    if proc.returncode not in (0, 1):
        sys.stderr.write(f"verify exited {proc.returncode}:\n{output}\n")
    return proc, time.perf_counter() - started, peak[0]


def _dir_bytes(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())


def _cancel_latency(workspace: Path, events: Path) -> dict:
    """SIGINT at ``run.started``; time to exit and a closed stream."""
    argv = [sys.executable, "-m", "ici", "next", "verify", "--events", str(events)]
    proc = subprocess.Popen(
        argv,
        cwd=workspace,
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if events.is_file() and '"run.started"' in events.read_text(encoding="utf-8"):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.01)
    sent = time.perf_counter()
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=30)
    latency = time.perf_counter() - sent
    text = events.read_text(encoding="utf-8") if events.is_file() else ""
    return {
        "sigint_to_exit_s": round(latency, 3),
        "exit_code": proc.returncode,
        "stream_closed": '"run.completed"' in text,
    }


def measure(workspace: Path, files: int) -> dict:
    _fixture(workspace, files)
    record: dict = {"schema_id": BENCHMARK_SCHEMA, "files": files}

    cold, cold_seconds, cold_rss = _verify(workspace, "--result", "result.json")
    record["cold"] = {
        "exit_code": cold.returncode,
        "wall_s": round(cold_seconds, 3),
        "peak_rss_mib": round(cold_rss / 1024, 1),
    }

    result_doc = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    execution = result_doc["execution"]
    record["tasks"] = {
        "blocked": len(execution["blocked_task_ids"]),
        "failed": len(execution["failed_task_ids"]),
        "reused": len(execution["reused_task_ids"]),
        "findings": len(result_doc["findings"]),
    }

    warm, warm_seconds, warm_rss = _verify(workspace, "--result", "result.json")
    warm_doc = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    record["warm"] = {
        "exit_code": warm.returncode,
        "wall_s": round(warm_seconds, 3),
        "peak_rss_mib": round(warm_rss / 1024, 1),
        "reused": len(warm_doc["execution"]["reused_task_ids"]),
    }

    report = subprocess.run(
        [
            sys.executable,
            "-m",
            "ici",
            "next",
            "report",
            "--result",
            "result.json",
            "--out",
            "result.html",
        ],
        cwd=workspace,
        env=_env(),
        capture_output=True,
    )
    record["artifacts"] = {
        "result_json_bytes": (workspace / "result.json").stat().st_size,
        "result_html_bytes": (
            (workspace / "result.html").stat().st_size
            if (workspace / "result.html").is_file()
            else 0
        ),
        "cache_bytes": _dir_bytes(workspace / ".ici" / "cache"),
        "report_exit": report.returncode,
    }

    events = workspace / "events.jsonl"
    record["cancel"] = _cancel_latency(workspace, events)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=int, default=200)
    parser.add_argument("--output", type=Path, help="Write the JSON record here")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Reuse this directory instead of a temporary one (kept afterwards)",
    )
    args = parser.parse_args()

    directory = args.workspace or Path(tempfile.mkdtemp(prefix="ici-bench-"))
    directory.mkdir(parents=True, exist_ok=True)
    record = measure(directory, args.files)
    record["machine"] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    text = json.dumps(record, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
