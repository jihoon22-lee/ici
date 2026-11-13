"""Development-only frozen capture of legacy `ici cycle` counts and truncation.

Never called by the Rust product. Frozen Tarjan walks components in Python set order, so a
`max_reported` below the cycle count picks a seed-dependent subset (root ruling 2026-11-14 (b1)).
What must still be equal is captured here: the summary, the cycle and truncation counts in the
engine `extra`, and the full reported set whenever `max_reported` covers every cycle.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
EXTRA = (
    "total_cycles",
    "py_cycles",
    "cpp_cycles",
    "cpp_include_resolution",
    "resolved_cpp_includes",
    "ambiguous_cpp_includes",
    "unresolved_cpp_includes",
    "cpp_include_diagnostics_truncated",
)
# Two hash seeds; the test compares the reported subset as a set either way.
SEEDS = ("0", "1")
CONFIGS = {
    "default": "",
    "limit-one": "[engines.cycle]\nmax_reported=1\n",
    "limit-two": "[engines.cycle]\nmax_reported=2\n",
    "limit-three": "[engines.cycle]\nmax_reported=3\n",
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(work: Path, config: str, seed: str) -> dict:
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        project = work / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp/cycle-fixture", project)
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n" + config, encoding="utf-8"
        )
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "cycle", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "PYTHONHASHSEED": seed,
            },
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        report = json.loads((project / "cycle_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "extra": {key: report["extra"][key] for key in EXTRA},
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


def main() -> None:
    work = Path(sys.argv[1])
    cases = {
        f"{name}:seed{seed}": run(work, config, seed)
        for name, config in CONFIGS.items()
        for seed in SEEDS
    }
    (ROOT / "tests/rust-parity/cpp/cycle-counts-oracle.json").write_text(
        json.dumps(
            {"oracle": EXPECTED, "configs": CONFIGS, "cases": cases}, indent=1, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} cycle count cases")


if __name__ == "__main__":
    main()
