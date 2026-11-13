"""Development-only frozen capture: the legacy Python-family and source engine commands on C++ trees.

    python -I -B capture_engine_commands.py --oracle <oracle-root> --out <json>

`ici python-compat`, `ici resource`, `ici security`, `ici type`, `ici exception` and `ici dup`
(frozen `__main__._ENGINE_COMMANDS`; each has only `--report`) on more C++ trees than the
verify-matrix fixture: the legacy-scope fixture, a C++ tree with clone groups and exception
violations under default and configured engine keys (dup `warn_pct`/`fail_pct`/`min_window`/`mode`,
exception `mode`, `required` flips, a disabled engine that the typed command re-enables), a
C-only tree, a C++ source that is not UTF-8 (the source-input failure path), and a hybrid tree
with Python resource/security findings. Each command runs with `--report` (the saved engine
report, its frozen `extra` included) and without it (the console line; frozen prints its one-line verdict, wrapped by Rich at
80 columns). PATH is /usr/bin:/bin (no mypy, Ruff or clang tools); HOME is private. Frozen runs
from the capture runtime `--python`, whose `sys.executable` is also the interpreter frozen's
python-compat probes; the native replay names it as ICI_PYTHON. The document records that
runtime's path (`capture_python`, cppval17) so the replay can assert it is the interpreter frozen
ran; its version is the frozen `PythonRuntime:Verified` message of the hybrid tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ("python-compat", "resource", "security", "type", "exception", "dup")
TARGET_FIELDS = ("file_path", "start_line", "end_line", "target_name", "status", "message")
FINDING_FIELDS = ("rule_id", "message", "severity", "confidence", "category", "fingerprint")

CLONE_BODY = """    int total = 0;
    for (int index = 0; index < limit; ++index) {
        if (index % 3 == 0) {
            total += index * 2;
        } else if (index % 3 == 1) {
            total -= index;
        } else {
            total += 7;
        }
        if (total > 1000) {
            total = total / 2;
        }
    }
    return total + offset;
"""
CLONE_A = "int first(int limit, int offset) {\n" + CLONE_BODY + "}\n"
CLONE_B = (
    "#include <stdexcept>\n\nint second(int limit, int offset) {\n"
    + CLONE_BODY
    + '}\n\nstruct Holder {\n    ~Holder() { throw std::runtime_error("x"); }\n};\n\n'
    "void swallow() {\n    try {\n        second(1, 2);\n    } catch (...) {\n    }\n}\n"
)
C_SOURCE = '#include "api.h"\n\nint api_add(int a, int b) { return a + b; }\n'
C_HEADER = "int api_add(int a, int b);\n"
PY_LEAK = (
    "import subprocess\n\n\ndef read(path):\n    handle = open(path)\n    return handle.read()\n\n\n"
    "def run(command):\n    return subprocess.call(command, shell=True)\n\n\n"
    "def collect(items=[]):\n    items.append(1)\n    return items\n"
)
CPP_TYPE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
HYBRID_TYPE = "[project]\ntype='hybrid'\nsource_dirs=['src']\n"

# name: (files, ici.toml); a `None` file copies the legacy-scope fixture.
TREES = {
    "scope": (None, CPP_TYPE),
    "clones": ({"src/a.cpp": CLONE_A, "src/b.cpp": CLONE_B}, CPP_TYPE),
    "clones-configured": (
        {"src/a.cpp": CLONE_A, "src/b.cpp": CLONE_B},
        CPP_TYPE
        + "[engines.dup]\nwarn_pct=1.0\nfail_pct=2.0\nmin_window=4\nmode='pass_warn_fail'\n"
        "[engines.exception]\nmode='pass_warn'\nrequired=false\n"
        "[engines.resource]\nenabled=false\n[engines.security]\nrequired=true\n"
        "[engines.type]\nrequired=false\n[engines.python_compat]\nrequired=true\n",
    ),
    "clones-wide-window": (
        {"src/a.cpp": CLONE_A, "src/b.cpp": CLONE_B},
        CPP_TYPE + "[engines.dup]\nmin_window=200\n",
    ),
    "c-only": ({"src/api.c": C_SOURCE, "src/api.h": C_HEADER}, CPP_TYPE),
    "not-utf8": (
        {"src/a.cpp": CLONE_A, "src/bad.cpp": b"int bad() { return 0; } // \xff\xfe\n"},
        CPP_TYPE,
    ),
    "hybrid": (
        {"src/a.cpp": CLONE_A, "src/b.cpp": CLONE_B, "src/tool.py": PY_LEAK},
        HYBRID_TYPE,
    ),
}


def report_view(report: dict) -> dict:
    return {
        "status": report.get("status"),
        "evidence": report.get("evidence"),
        "summary": report.get("summary"),
        "score": report.get("score"),
        "required": report.get("required"),
        "extra": report.get("extra"),
        "targets": [
            {key: target.get(key) for key in TARGET_FIELDS} for target in report["targets"]
        ],
        "findings": sorted(
            (
                {
                    **{key: finding.get(key) for key in FINDING_FIELDS},
                    "path": finding["primary_location"]["path"],
                    "start_line": finding["primary_location"].get("start_line"),
                }
                for finding in report["findings"]
            ),
            key=lambda finding: finding["fingerprint"],
        ),
    }


def capture(oracle: Path, python: Path, tree: str, command: str, report: bool) -> dict:
    files, config = TREES[tree]
    with tempfile.TemporaryDirectory(prefix="ici-engine-commands-") as temporary:
        root = Path(temporary).resolve() / "project"
        if files is None:
            shutil.copytree(ROOT / "parity-fixtures/cpp/legacy-scope-fixture", root)
        else:
            root.mkdir()
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(text, bytes):
                    path.write_bytes(text)
                else:
                    path.write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config, encoding="utf-8")
        process = subprocess.run(
            [str(python), "-B", "-m", "ici", command, *(["--report"] if report else [])],
            cwd=root,
            env={
                **{key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")},
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(oracle / "src"),
                "PYTHONHASHSEED": "0",
                "LC_CTYPE": "C.UTF-8",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        view: dict[str, object] = {"exit_code": process.returncode}
        if report:
            path = root / f"{command.replace('-', '_')}_report.json"
            view["report"] = (
                report_view(json.loads(path.read_text("utf-8"))) if path.exists() else None
            )
        else:
            view["stdout"] = process.stdout
    text = json.dumps(view).replace(str(root), "<ROOT>").replace(str(python), "<PYTHON>")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document: dict[str, object] = {
        "source": SOURCE,
        "capture_python": str(args.python),
        "trees": {
            name: {
                "config": config,
                "files": None
                if files is None
                else {
                    path: (text.hex() if isinstance(text, bytes) else text)
                    for path, text in files.items()
                },
                "binary": sorted(
                    path for path, text in (files or {}).items() if isinstance(text, bytes)
                ),
            }
            for name, (files, config) in TREES.items()
        },
        "runs": {
            f"{command}:{tree}:{'report' if report else 'console'}": capture(
                args.oracle, args.python, tree, command, report
            )
            for tree in TREES
            for command in COMMANDS
            for report in (True, False)
        },
    }
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _private_home():
    """The shared private HOME/XDG context (tests/rust-parity/private_home.py), loaded by path."""
    path = Path(__file__).resolve().parents[1] / "private_home.py"
    spec = importlib.util.spec_from_file_location("rust_parity_private_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.private_home()


if __name__ == "__main__":
    with _private_home():
        main()
