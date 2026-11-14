"""Development-only frozen `ici build` package-tree capture; never called by the Rust product."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-package-capture"
TOOLS = sorted({str(Path(shutil.which(name)).parent) for name in ("make", "g++", "cmake")})
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def tree(release: Path) -> dict:
    entries = {}
    for path in sorted([release, *release.rglob("*")]):
        relative = path.relative_to(release.parent).as_posix()
        mode = oct(path.stat().st_mode & 0o777)
        if path.is_dir():
            entries[relative] = {"type": "dir", "mode": mode}
            continue
        data = path.read_bytes()
        digest = "elf" if data[:4] == b"\x7fELF" else hashlib.sha256(data).hexdigest()
        entries[relative] = {"type": "file", "mode": mode, "sha256": digest}
    return entries


def run(name: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / name, project)
        result = subprocess.run(
            [PYTHON, "-m", "ici", "build"],
            cwd=project,
            env={
                "PATH": os.pathsep.join([*TOOLS, "/usr/bin", "/bin"]),
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return {"exit_code": result.returncode, "tree": tree(project / "v2.3.4")}
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


old = os.umask(0o022)
try:
    cases = {name: run(f"package-{name}") for name in ("make", "direct", "cmake")}
finally:
    os.umask(old)
(ROOT / "tests/rust-parity/cpp/package-tree-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "umask": "0o022", "cases": cases}, indent=1, sort_keys=True)
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} package cases")
