"""Development-only frozen legacy binary_compat captures for an artifact that disappears mid-run.

Never called by the Rust product. `ici verify --profile deep --report` over the package-make fixture
with `build` and `binary_compat` enabled. The readelf replacement deletes the other declared
artifact while inspecting the first one, so frozen's next `path.open` fails (binary_compat.py:135).
Four cases keep the rule narrow: the vanished artifact, two stable artifacts, a readelf that fails
on every artifact, and one whose first artifact succeeds before the second fails.
"""

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
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
BASE = (
    "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n"
    "build_argv=['make','all']\n[engines.build]\nenabled=true\n"
    "[engines.binary_compat]\nenabled=true\n"
)
TWO_MAKE = (
    "all:\n\tmkdir -p build/ici-make\n\tg++ src/main.cpp -o build/ici-make/app\n"
    "\tg++ src/main.cpp -o build/ici-make/tool\n"
)
READELF_STABLE = '#!/bin/sh\nexec /usr/bin/readelf "$@"\n'
# The replacement for the first artifact deletes `tool` beside it, then inspects as usual.
READELF_VANISH = (
    '#!/bin/bash\nfor last in "$@"; do :; done\n'
    'if [ -f "$(dirname "$last")/tool" ] && [ "$(basename "$last")" != tool ]; then\n'
    '  rm -f "$(dirname "$last")/tool"\nfi\nexec /usr/bin/readelf "$@"\n'
)
READELF_FAIL = '#!/bin/sh\n/usr/bin/readelf "$@"\nexit 1\n'
# The first artifact is inspected in full; only `tool`, the second, fails.
READELF_SECOND_FAILS = (
    '#!/bin/bash\nfor last in "$@"; do :; done\n/usr/bin/readelf "$@"\n'
    'if [ "$(basename "$last")" = tool ]; then exit 1; fi\n'
)
CASES = {
    "vanished": READELF_VANISH,
    "stable-two-artifacts": READELF_STABLE,
    "readelf-tool-error": READELF_FAIL,
    "readelf-second-fails": READELF_SECOND_FAILS,
}


def run(script: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-bc-vanish-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / "package-make", project)
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        (project / "Makefile").write_text(TWO_MAKE, encoding="utf-8")
        bin_dir = work / "bin"
        bin_dir.mkdir()
        (bin_dir / "readelf").write_text(script, encoding="utf-8")
        (bin_dir / "readelf").chmod(0o755)
        for parent in ("/usr/bin", "/bin"):
            for entry in sorted(os.listdir(parent)):
                link = bin_dir / entry
                if not link.exists() and not link.is_symlink():
                    link.symlink_to(Path(parent) / entry)
        completed = subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={
                "PATH": str(bin_dir),
                "HOME": str(work),
                "LC_CTYPE": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
        engine = next(e for e in report["results"] if e["engine_name"] == "binary_compat")
        text = json.dumps(engine).replace(str(project), "<root>")
        return {"exit_code": completed.returncode, "report": json.loads(text)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


cases = {name: run(script) | {"readelf": script} for name, script in CASES.items()}
(ROOT / "tests/rust-parity/cpp/binary-compat-vanished-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "base": BASE, "makefile": TWO_MAKE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
for name, case in sorted(cases.items()):
    print(name, case["exit_code"], case["report"]["status"], case["report"]["summary"][:100])
