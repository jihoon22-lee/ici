"""Development-only capture against the immutable Python source, never a product worker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    oracle = args.oracle.resolve()
    python = args.python.absolute()
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != SOURCE:
        raise SystemExit("oracle must be the immutable v0.13.0 source")
    args.out.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        PYTHONPATH=str(oracle / "src"),
        PATH=str(python.parent) + os.pathsep + os.environ["PATH"],
    )
    summary: dict[str, object] = {
        "source": SOURCE,
        "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="ici-oracle-") as temporary:
        root = Path(temporary)
        (root / "ici.toml").write_text(
            'schema_version=1\n[workspace]\nname="oracle"\nprofile="fast"\n[[components]]\nid="project"\nroot="."\nlanguages=["python"]\n',
            encoding="utf-8",
        )
        (root / "main.py").write_text(
            "def answer(value):\n    if value:\n        return 42\n    return 0\n", encoding="utf-8"
        )
        commands = [
            ("plan", ["analyze", "--plan", "--json"]),
            ("doctor", ["next", "doctor", "--json"]),
            (
                "analyze-cold",
                ["analyze", "--json", "--events", "events.jsonl", "--result", "result.json"],
            ),
            (
                "analyze-warm",
                ["analyze", "--json", "--events", "events.jsonl", "--result", "result.json"],
            ),
            ("report", ["report", "--result", "result.json", "--out", "report.html"]),
            (
                "publish-unconfigured",
                ["next", "publish", "--result", "result.json", "--out", "report.html"],
            ),
        ]
        for name, command in commands:
            clock = root / "time.txt"
            completed = subprocess.run(
                [
                    "/usr/bin/time",
                    "-f",
                    "%e %M",
                    "-o",
                    str(clock),
                    str(python),
                    "-m",
                    "ici",
                    *command,
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
            )
            if completed.returncode not in (0, 1, 2, 3):
                raise RuntimeError(completed.stderr)
            normalized = (
                completed.stdout.replace(str(root), "<PROJECT>")
                .replace(str(oracle), "<ORACLE>")
                .replace(str(python.parent.parent), "<PYTHON_ENV>")
            )
            if name.startswith("analyze-"):
                result = json.loads(normalized)
                result["run_id"] = "<RUN_ID>"
                (args.out / f"{name}.json").write_text(
                    json.dumps(result, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            if name in {"plan", "doctor"}:
                json.loads(normalized)
                (args.out / f"{name}.json").write_text(normalized, encoding="utf-8")
            times = clock.read_text().splitlines()[-1].split()
            summary["runs"].append(
                {
                    "name": name,
                    "argv": command,
                    "exit": completed.returncode,
                    "wall_seconds": float(times[0]),
                    "peak_rss_kib": int(times[1]),
                    "stdout_bytes": len(completed.stdout.encode()),
                    "stderr": completed.stderr.replace(str(root), "<PROJECT>").replace(
                        str(oracle), "<ORACLE>"
                    ),
                }
            )
        publish = root / ".ici/next/publish.json"
        if not publish.exists():
            raise RuntimeError("unconfigured publish did not write its outcome record")
        if publish.exists():
            (args.out / "publish-unconfigured.json").write_text(
                publish.read_text().replace(str(root), "<PROJECT>"), encoding="utf-8"
            )
        if summary["runs"][-1]["exit"] != 2:
            raise RuntimeError("unconfigured publication must exit 2")
        summary["html_bytes"] = (root / "report.html").stat().st_size
        summary["result_bytes"] = (root / "result.json").stat().st_size
    (args.out / "capture.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
