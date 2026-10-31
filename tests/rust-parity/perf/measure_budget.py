"""Development-only R8 budget measurement: frozen Python oracle versus the native binary.

Both products run the same fixed fixture (the one in ``capture_cli_oracle.py``) on the same
host with the same PATH. Each cold run uses a fresh project copy and fresh XDG cache/config
roots; each warm run repeats the analysis in the project its cold run left behind. Budgets
come from ``docs/design/rust-replacement/decisions.md``:

- cold and warm median wall time <= max(oracle x 2, 2 s)
- peak RSS median <= max(oracle x 2, 128 MiB)
- report size (HTML and result) <= max(oracle x 2, 64 KiB)
- after SIGINT, the native process and its descendants are gone within 2 s (exit 130)

The measurement records raw samples; it never normalises findings or exit codes away.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
RUNS = 5
CONFIG = (
    'schema_version=1\n[workspace]\nname="oracle"\nprofile="fast"\n'
    '[[components]]\nid="project"\nroot="."\nlanguages=["python"]\n'
)
SOURCE_TEXT = "def answer(value):\n    if value:\n        return 42\n    return 0\n"
ANALYZE = ["analyze", "--json", "--events", "events.jsonl", "--result", "result.json"]
REPORT = ["report", "--result", "result.json", "--out", "report.html"]


def fixture(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "ici.toml").write_text(CONFIG, encoding="utf-8")
    (root / "main.py").write_text(SOURCE_TEXT, encoding="utf-8")


def timed(argv: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    clock = cwd.parent / f"{cwd.name}.time"
    completed = subprocess.run(
        ["/usr/bin/time", "-f", "%e %M", "-o", str(clock), *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=300,
        check=False,
    )
    wall, rss = clock.read_text(encoding="utf-8").splitlines()[-1].split()
    clock.unlink()
    return {
        "exit": completed.returncode,
        "wall_seconds": float(wall),
        "peak_rss_kib": int(rss),
        "stdout_bytes": len(completed.stdout),
        "stderr_tail": completed.stderr.decode("utf-8", "replace")[-400:],
    }


def product(name: str, command: list[str], base: Path, env: dict[str, str]) -> dict[str, object]:
    samples: dict[str, list[dict[str, object]]] = {"cold": [], "warm": [], "report": []}
    sizes: dict[str, int] = {}
    for index in range(RUNS):
        run = base / f"{name}-{index}"
        project = run / "project"
        fixture(project)
        isolated = dict(
            env,
            XDG_CACHE_HOME=str(run / "cache"),
            XDG_CONFIG_HOME=str(run / "config"),
            XDG_DATA_HOME=str(run / "data"),
        )
        samples["cold"].append(timed([*command, *ANALYZE], project, isolated))
        samples["warm"].append(timed([*command, *ANALYZE], project, isolated))
        samples["report"].append(timed([*command, *REPORT], project, isolated))
        for artifact in ("result.json", "report.html"):
            path = project / artifact
            if path.exists():
                sizes[artifact] = path.stat().st_size
        shutil.rmtree(run)
    summary: dict[str, object] = {"samples": samples, "sizes": sizes}
    for phase in ("cold", "warm"):
        summary[f"{phase}_median_seconds"] = statistics.median(
            float(sample["wall_seconds"]) for sample in samples[phase]
        )
        summary[f"{phase}_median_rss_kib"] = statistics.median(
            int(sample["peak_rss_kib"]) for sample in samples[phase]
        )
        summary[f"{phase}_exits"] = sorted({int(sample["exit"]) for sample in samples[phase]})
    return summary


def descendants(pid: int) -> set[int]:
    found: set[int] = set()
    pending = [pid]
    while pending:
        parent = pending.pop()
        values: list[str] = []
        # Children started from worker threads are listed under that thread's task entry.
        for children in Path(f"/proc/{parent}/task").glob("*/children"):
            try:
                values.extend(children.read_text(encoding="utf-8").split())
            except OSError:
                continue
        for value in values:
            child = int(value)
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def cancellation(native: Path, base: Path, env: dict[str, str]) -> dict[str, object]:
    project = base / "cancel" / "project"
    fixture(project)
    (project / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8"
    )
    process = subprocess.Popen(
        [str(native), "next", "verify", "--profile", "deep", "--result", "result.json"],
        cwd=project,
        env=dict(env, XDG_CACHE_HOME=str(base / "cancel" / "cache")),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not descendants(process.pid):
        time.sleep(0.05)
    children = descendants(process.pid)
    sent = time.monotonic()
    process.send_signal(signal.SIGINT)
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        code = process.wait()
    exited = time.monotonic() - sent
    while time.monotonic() - sent < 10 and any(Path(f"/proc/{pid}").exists() for pid in children):
        time.sleep(0.02)
    survivors = sorted(pid for pid in children if Path(f"/proc/{pid}").exists())
    cleaned = time.monotonic() - sent
    shutil.rmtree(base / "cancel")
    return {
        "observed_descendants_before_signal": len(children),
        "exit": code,
        "exit_seconds": round(exited, 3),
        "descendants_gone_seconds": round(cleaned, 3),
        "survivors": survivors,
        "stderr_tail": (process.stderr.read() if process.stderr else b"")[-400:].decode(
            "utf-8", "replace"
        ),
    }


def verdicts(oracle: dict[str, object], native: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for phase in ("cold", "warm"):
        seconds = float(oracle[f"{phase}_median_seconds"])
        rss = float(oracle[f"{phase}_median_rss_kib"])
        result[f"{phase}_time"] = {
            "budget_seconds": max(seconds * 2, 2.0),
            "native_seconds": native[f"{phase}_median_seconds"],
            "pass": float(native[f"{phase}_median_seconds"]) <= max(seconds * 2, 2.0),
        }
        result[f"{phase}_rss"] = {
            "budget_kib": max(rss * 2, 128 * 1024),
            "native_kib": native[f"{phase}_median_rss_kib"],
            "pass": float(native[f"{phase}_median_rss_kib"]) <= max(rss * 2, 128 * 1024),
        }
    for artifact in ("result.json", "report.html"):
        reference = dict(oracle["sizes"]).get(artifact)  # type: ignore[arg-type]
        actual = dict(native["sizes"]).get(artifact)  # type: ignore[arg-type]
        budget = max((reference or 0) * 2, 64 * 1024)
        result[f"size_{artifact}"] = {
            "oracle_bytes": reference,
            "native_bytes": actual,
            "budget_bytes": budget,
            "pass": actual is not None and actual <= budget,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    oracle = args.oracle.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != SOURCE:
        raise SystemExit("oracle must be the immutable v0.13.0 source")
    python = args.python.absolute()
    native = args.native.resolve()
    path = os.pathsep.join([str(python.parent), "/usr/bin", "/bin"])
    common = {"PATH": path, "HOME": os.environ.get("HOME", "/nonexistent"), "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="ici-budget-") as temporary:
        base = Path(temporary)
        measured_oracle = product(
            "oracle",
            [str(python), "-m", "ici"],
            base,
            dict(common, PYTHONPATH=str(oracle / "src")),
        )
        measured_native = product("native", [str(native)], base, common)
        cancel = cancellation(native, base, common)
    summary = {
        "source": SOURCE,
        "native_sha256": subprocess.check_output(["sha256sum", str(native)], text=True).split()[0],
        "host": {
            "kernel": os.uname().release,
            "machine": os.uname().machine,
            "cpus": os.cpu_count(),
        },
        "oracle": measured_oracle,
        "native": measured_native,
        "cancellation": cancel,
        "verdicts": verdicts(measured_oracle, measured_native),
    }
    summary["verdicts"]["cancellation"] = {  # type: ignore[index]
        "pass": cancel["exit"] == 130
        and int(cancel["observed_descendants_before_signal"]) > 0
        and not cancel["survivors"]
        and float(cancel["descendants_gone_seconds"]) <= 2.0
    }
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary["verdicts"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
