"""Development-only frozen captures of the invalid ABI floor configuration refusal.

Never called by the Rust product. Frozen `_analysis_config._validate_binary_compat` refuses an
`engines.binary_compat.max_glibc`/`max_glibcxx`/`max_cxxabi` value that is neither empty nor a
dotted ABI version of at most 32 characters before anything runs: `ici verify --profile deep
--report` prints `Configuration error: <key> must be empty or an ABI version such as 2.17`,
exits 2 and saves no report. The replay is
`crates/ici-cli/tests/legacy_binary_compat.rs::invalid_abi_floor_is_refused_with_the_frozen_wording`.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/abi-floor-refusal-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n[engines.binary_compat]\nenabled=true\n"
CASES = {
    "max_glibc-not-a-version": BASE + "max_glibc='2.x'\n",
    "max_glibcxx-without-dot": BASE + "max_glibcxx='3'\n",
    "max_cxxabi-too-long": BASE + "max_cxxabi='" + ".".join(["1"] * 17) + "'\n",
    "max_glibc-trailing-dot": BASE + "max_glibc='2.17.'\n",
}


def run(config: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-abi-floor-capture-"))
    try:
        project = work / "project"
        (project / "src").mkdir(parents=True)
        (project / "src/main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        (project / "ici.toml").write_text(config, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return {
            "config": config,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "report_written": (project / "verify_report.json").exists(),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "cases": {name: run(config) for name, config in CASES.items()}},
        indent=1,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(CASES)} invalid ABI floor cases")
