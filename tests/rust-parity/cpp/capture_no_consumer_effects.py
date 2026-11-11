"""Development-only frozen v0.13.0 capture: what `ici verify` runs and writes when no selected check
consumes the canonical analysis database (`verify-no-consumer:*`), beside the same fixtures with
compile_db selected. Records every real `cmake` argv in order (a PATH wrapper), the files the run
created, changed or deleted (relative paths), the exit code and the line engine's verdict, beside the
case's fixture, config and files so the native replay needs no other oracle. The first line of
`/usr/bin/cmake --version` pins the CMake whose files the lists name.

Run with `python -I -B tests/rust-parity/cpp/capture_no_consumer_effects.py` from any directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_preparation_failures as base

GOLDEN = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else base.ROOT / "tests/rust-parity/cpp/no-consumer-effects-oracle.json"
)
NAMES = (
    "cmake-configured",
    "cmake-configure-failed",
    "cmake-generation-failed",
    "cmake-generated-then-build-failure",
    "cmake-own-database",
    "cmake-cache-missing",
)
WRAPPER = '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$ICI_TRACE_CMAKE_LOG"\nexec /usr/bin/cmake "$@"\n'


def tree(root: Path) -> dict:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def run(fixture: str, config: str, files: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cppval-capture-"))
    try:
        project = work / "project"
        shutil.copytree(base.FIXTURES / fixture, project)
        (project / ".git").mkdir()
        (work / ".hg").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        for relative, text in files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        bin_dir = work / "bin"
        bin_dir.mkdir()
        (bin_dir / "cmake").write_text(WRAPPER, encoding="utf-8")
        (bin_dir / "cmake").chmod(0o755)
        log = work / "cmake.log"
        before = tree(project)
        result = subprocess.run(
            [base.PYTHON, "-m", "ici", "verify", "--report"],
            cwd=project,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(work),
                "GIT_CEILING_DIRECTORIES": str(work),
                "LC_CTYPE": "C.UTF-8",
                "PYTHONPATH": str(base.ORACLE / "src"),
                "ICI_TRACE_CMAKE_LOG": str(log),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        after = tree(project)
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
        line = next(item for item in report["results"] if item["engine_name"] == "line")
        argv = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return {
            "fixture": fixture,
            "config": config,
            "files": files,
            "exit_code": result.returncode,
            "cmake": [entry.replace(str(project), "<root>") for entry in argv],
            "created": sorted(path for path in after if path not in before),
            "changed": sorted(
                path for path in after if path in before and after[path] != before[path]
            ),
            "deleted": sorted(path for path in before if path not in after),
            "line": [
                line["status"],
                line["evidence"],
                line["summary"],
                [[t["target_name"], t["status"], t["file_path"]] for t in line["targets"]],
            ],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = {}
    for name in NAMES:
        fixture, _, files, *_ = base.CASES[f"verify:{name}"]
        cases[f"verify-no-consumer:{name}"] = run(fixture, base.CMAKE_NO_CONSUMER, files)
        # The same fixture with compile_db selected (a consumer) and the line engine on.
        consumer = base.CMAKE_NO_CONSUMER.replace("[engines.compile_db]\nenabled=false\n", "")
        cases[f"verify-consumer:{name}"] = run(fixture, consumer, files)
        print(name, cases[f"verify-no-consumer:{name}"]["cmake"], flush=True)
    cmake = subprocess.check_output(["/usr/bin/cmake", "--version"], text=True).splitlines()[0]
    GOLDEN.write_text(
        json.dumps(
            {"oracle": base.EXPECTED, "cmake": cmake, "cases": cases}, indent=1, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
