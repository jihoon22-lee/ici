#!/usr/bin/env python3
"""Measure what a browser actually pays to open a large ici HTML report.

`scripts/benchmark_report.py` measures the cost of *generating* a report and
never opens a browser, so a 33 MB HTML has a known write cost and an unknown
read cost. This opens one and records startup wall-clock and peak resident
memory across the whole browser process tree.

It is deliberately local-only. Putting a browser in the PR gate would add a
large dependency and several minutes to every run for a number that moves
slowly; CI instead holds the payload that decides this cost — row count, inline
JSON size, HTML bytes — through `benchmark_report.py`. Re-run this by hand when
the HTML reporter changes shape, and record the numbers in
docs/ci-integration.md.

Everything it creates lives in a temporary directory and is removed, and the
browser is terminated in a `finally` so an exception cannot leave one running.
The run ends by checking that no descendant survived and says so either way.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

sys.path.insert(0, str(_ROOT / "scripts"))

from benchmark_report import build_suite, measure_browser_payload  # noqa: E402

from ici.reporters.html import generate_html_report  # noqa: E402

BROWSER_SCHEMA = "ici.benchmark.browser/v1"
DEFAULT_FINDINGS = 100_000
_CANDIDATES = ("google-chrome", "chromium", "chromium-browser", "chrome")
_POLL_SECONDS = 0.05
_KILL_GRACE_SECONDS = 5.0


def find_browser(explicit: str | None) -> str:
    """Locate a Chrome-family binary, failing loudly rather than guessing."""

    if explicit:
        resolved = shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
        if resolved is None:
            raise SystemExit(f"browser not found: {explicit}")
        return resolved
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(f"no browser found; tried {', '.join(_CANDIDATES)}")


def _tree_rss_kib(root_pid: int) -> tuple[int, int, set[int]]:
    """Sample the browser tree's memory, and say which number means what.

    Returns the largest single process, the sum over the tree, and the pids
    seen. The headline is the largest process — Chrome splits into a browser, a
    zygote and a renderer, and each one's RSS counts the shared mappings they
    have in common, so summing them over-reports by roughly the shared set. The
    renderer holds the DOM, so the largest process is the honest answer to "how
    much did this page cost"; the sum is kept only as a labelled upper bound.
    """

    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,rss="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0, 0, set()
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for row in listing.stdout.splitlines():
        parts = row.split()
        if len(parts) != 3:
            continue
        try:
            pid, ppid, size = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
        rss[pid] = size
    total = 0
    largest = 0
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        size = rss.get(pid, 0)
        total += size
        largest = max(largest, size)
        stack.extend(children.get(pid, ()))
    return largest, total, seen


def _still_alive(pids: set[int]) -> list[int]:
    """Return which of exactly these pids survived.

    Checking a set captured while the browser ran, rather than walking the
    process table again, is what keeps this from blaming the machine's own
    Chrome — or, as the first version did, a transient `ps` child of this very
    script — for a leak that is not there.
    """

    alive = []
    for pid in sorted(pids):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            # Someone else's process reused the pid; not ours to report.
            continue
        alive.append(pid)
    return alive


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """End the browser and its group, escalating only if it ignores the first ask."""

    if process.poll() is not None:
        return
    # The browser is started in its own session, so the group signal reaches the
    # renderer and zygote too. Signalling only the process we spawned would
    # leave those behind, which is the exact leak this guards against.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_GRACE_SECONDS)
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_GRACE_SECONDS)


def open_once(browser: str, html_path: Path, profile: Path, timeout: float) -> dict[str, Any]:
    """Open the report once and record how long it took and what it cost."""

    argv = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        # A throwaway profile in the temp tree. Without it the measurement would
        # depend on whatever profile, extensions and cache the machine's own
        # browser happens to have, and would not be comparable between runs.
        f"--user-data-dir={profile}",
        # Forces a complete parse and layout rather than an early return.
        "--dump-dom",
        html_path.resolve().as_uri(),
    ]
    peak_process_kib = 0
    peak_tree_kib = 0
    observed: set[int] = set()
    started = time.perf_counter()
    process = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        while process.poll() is None:
            largest, total, pids = _tree_rss_kib(process.pid)
            peak_process_kib = max(peak_process_kib, largest)
            peak_tree_kib = max(peak_tree_kib, total)
            observed |= pids
            if time.perf_counter() - started > timeout:
                raise SystemExit(f"browser did not finish within {timeout:.0f}s")
            time.sleep(_POLL_SECONDS)
        elapsed = time.perf_counter() - started
        return {
            "seconds": round(elapsed, 3),
            "peak_process_rss_mib": round(peak_process_kib / 1024, 1),
            "tree_rss_upper_bound_mib": round(peak_tree_kib / 1024, 1),
            "exit_code": process.returncode,
            "observed_pids": sorted(observed),
        }
    finally:
        # Reached on success, on timeout and on Ctrl-C alike: a benchmark must
        # not be able to leave a browser running.
        _terminate(process)


def run(findings: int, browser: str, timeout: float, runs: int) -> dict[str, Any]:
    """Generate one report, open it `runs` times, and report the best run."""

    suite = build_suite(findings, 10, 2_000)
    with tempfile.TemporaryDirectory(prefix="ici-browser-benchmark-") as raw_dir:
        base = Path(raw_dir)
        html_path = base / "benchmark.html"
        profile = base / "profile"
        generate_html_report(suite, html_path, "benchmark", base)
        payload = measure_browser_payload(html_path)
        observations = [open_once(browser, html_path, profile, timeout) for _ in range(runs)]
        spawned = {pid for item in observations for pid in item["observed_pids"]}
        leaked = _still_alive(spawned)
    best = min(observations, key=lambda item: item["seconds"])
    for item in observations:
        del item["observed_pids"]
    return {
        "schema": BROWSER_SCHEMA,
        "finding_count": findings,
        "browser": Path(browser).name,
        "runs": observations,
        "best": best,
        "browser_payload": payload,
        "leaked_processes": leaked,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=int, default=DEFAULT_FINDINGS)
    parser.add_argument("--browser", default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    record = run(args.findings, find_browser(args.browser), args.timeout, args.runs)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    if record["leaked_processes"]:
        sys.stderr.write(f"leaked browser processes: {record['leaked_processes']}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
