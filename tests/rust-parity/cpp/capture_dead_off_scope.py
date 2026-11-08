"""Capture frozen v0.13.0 `ici dead --report` under `cpp_unused = 'off'` by C/C++ scope.

    python -I capture_dead_off_scope.py <oracle-root> <work-dir> <output.json>

Development only; never called by the Rust product. Frozen `DeadCodeEngine._analyze_cpp_scope`
adds the disabled-policy target at "." only when C/C++ candidates (headers included) exist,
before source exclusion and independent of other read errors. `_effective_required` makes the
engine optional when only C/C++ candidates remain after the Python sources were read or
excluded (`cpp_only_disabled`). Each project is a legacy
`type='cpp'` project under `<work-dir>` with an empty HOME, a `.hg` marker at the project and
`GIT_CEILING_DIRECTORIES` at the work directory. `legacy_cpp_metrics.rs` compares the status,
exit code, evidence, requiredness and every target field.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
CONFIG = "[project]\ntype='cpp'\nsource_dirs=['src']\n[engines.dead]\ncpp_unused='off'\n"
USED = "def used():\n    return 1\n\n\nused()\n"
CPP = "int main() { return 0; }\n"
CASES = {
    "off-without-cpp": {"src/app.py": USED},
    "off-header-only": {"src/app.py": USED, "src/api.hpp": "int api();\n"},
    "off-vendor-only": {"src/app.py": USED, "src/vendor/lib.cpp": "int lib() { return 1; }\n"},
    "off-unreadable-python": {
        "src/app.py": USED,
        "src/bad.py": b"\xff\xfe\n",
        "src/main.cpp": "int main() { return 0; }\n",
    },
    "off-vendor-python": {"src/main.cpp": CPP, "src/vendor/tool.py": USED},
    "off-generated-python": {"src/main.cpp": CPP, "src/generated/tool.py": USED},
    "off-vendor-python-included": {"src/main.cpp": CPP, "src/vendor/tool.py": USED},
}
# Per-case `[engines.dead]` additions (frozen `include_vendor` selects the vendor Python file).
EXTRA = {"off-vendor-python-included": "include_vendor=true\n"}


def main():
    oracle, work, output = Path(sys.argv[1]), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
    document = {"oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188", "cases": {}}
    for name, tree in CASES.items():
        shutil.rmtree(work, ignore_errors=True)
        project, home = work / "project", work / "home"
        project.mkdir(parents=True)
        home.mkdir()
        (project / ".hg").mkdir()
        (project / "ici.toml").write_text(CONFIG + EXTRA.get(name, ""), encoding="utf-8")
        for path, content in tree.items():
            target = project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "dead", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": str(work),
                "PYTHONHASHSEED": "0",
            },
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        report = json.loads((project / "dead_report.json").read_text(encoding="utf-8"))
        document["cases"][name] = {
            "exit_code": completed.returncode,
            "status": report["status"],
            "evidence": report["evidence"],
            "required": report["required"],
            "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        }
    shutil.rmtree(work, ignore_errors=True)
    output.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(document['cases'])} cases")


main()
