"""Development-only frozen `ici lint` captures with a Ruff whose formatter has no JSON output.

Frozen `LintEngine._run_ruff_format` first runs `ruff format --help` ("ruff format capability")
and, when the help does not offer `--output-format`, runs `ruff format --check <paths>` and
parses the text dialect (`Would reformat: <path>` lines and one tally) into `Format:Style`
targets (engines/lint.py:651). Ruff 0.5.7 is such a Ruff (`ruff check` still accepts every flag
frozen passes). The binary was installed with `python -m venv` + `pip install --no-cache-dir
ruff==0.5.7` and copied to RUFF; the capture records its version and digest.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
RUFF = "/tmp/ici-rust-replacement-20261028/w-py3-tools/ruff-0.5.7/ruff"
OUT = ROOT / "tests/rust-parity/python/legacy-ruff-text-format-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

CASES = {
    "clean": {"src/a.py": "x = 1\n"},
    "format-needed": {"src/a.py": "x=1\n"},
    "two-files-one-format": {"src/a.py": "x=1\n", "src/b.py": "y = 2\n"},
    "two-files-both-format": {"src/a.py": "x=1\n", "src/pkg/b.py": "y=2\n"},
    "lint-and-format": {"src/a.py": "import os\nx=1\n"},
    "syntax": {"src/a.py": "x = 1\n", "src/b.py": "def f(:\n    pass\n"},
}


def run(files: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-ruff-text-"))
    try:
        bin_dir = work / "bin"
        bin_dir.mkdir()
        (bin_dir / "ruff").symlink_to(RUFF)
        project = work / "project"
        project.mkdir()
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        for name, text in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", "lint", "--report"],
            cwd=project,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / "lint_report.json").read_text(encoding="utf-8"))
        text = json.dumps(report["tool_evidence"]).replace(str(bin_dir), "<tools>")
        return {
            "exit_code": result.returncode,
            "status": report["status"],
            "summary": report["summary"],
            "evidence": report["evidence"],
            "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
            "tool_evidence": json.loads(text.replace(str(project), "<project>")),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {name: {"files": files, "frozen": run(files)} for name, files in CASES.items()}
    with open(RUFF, "rb") as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
    OUT.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "python": subprocess.check_output([PYTHON, "--version"], text=True).strip(),
                "base": BASE,
                "ruff_version": subprocess.check_output([RUFF, "--version"], text=True).strip(),
                "ruff_sha256": digest,
                "cases": cases,
            },
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} text-dialect lint cases", file=sys.stderr)


if __name__ == "__main__":
    main()
