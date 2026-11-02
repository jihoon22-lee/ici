"""Development-only capture: frozen legacy python_compat bounds and process problems (F2).

    python -I capture_legacy_python_compat_bounds.py --oracle <v0.13.0 checkout> \
        --python <venv python> --bare <CPython 3.12>

Frozen `engines/python_compat.py` under `ici python-compat --report` with an explicit
interpreter: the per-file (100,000) and aggregate (1,000,000) AST node bounds, the 128
compileall-target bound, and `_process_problem` for a version probe that times out (10 s), a
compileall run and an import smoke whose output exceeds 128,000 characters. The interpreter is
a wrapper around `--bare` that misbehaves only for the probed step. Records the engine verdict
and targets with the temporary paths as `<root>`/`<bin>`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
APP = "import json\n\n\ndef f():\n    return json.dumps({})\n"
# 4 AST nodes per line (Module counted once): 26,000 lines exceed the per-file bound;
# eleven 23,000-line files stay under it but exceed the aggregate bound.
WIDE = "x = 1\n" * 26_000
TALL = "x = 1\n" * 23_000
FLOOD = "head -c 200000 /dev/zero | tr '\\0' a; exit 0"
WRAPPER = """#!/bin/sh
case "$1 $2 $3" in
  "-VV  "*|"-I -c "*) {version} ;;
  "-B -m compileall") {compile} ;;
  "-I -B -c") {imports} ;;
esac
exec {bare} "$@"
"""
NONE = ":"
SCENARIOS = {
    "ast_file_bound": ({"app.py": APP, "wide.py": WIDE}, ["."], {}, ""),
    "ast_aggregate_bound": (
        {"app.py": APP, **{f"tall{index:02}.py": TALL for index in range(11)}},
        ["."],
        {},
        "",
    ),
    "compile_targets_bound": (
        {f"d{index:03}/m.py": APP for index in range(129)},
        [f"d{index:03}" for index in range(129)],
        {},
        "",
    ),
    "version_timeout": ({"app.py": APP}, ["."], {"version": "sleep 15"}, ""),
    "compile_truncated": ({"app.py": APP}, ["."], {"compile": FLOOD}, ""),
    "imports_truncated": ({"app.py": APP}, ["."], {"imports": FLOOD}, 'imports = ["app"]\n'),
}


def capture(oracle: Path, python: Path, bare: Path, scenario: tuple) -> dict[str, object]:
    files, source_dirs, behaviour, extra = scenario
    with tempfile.TemporaryDirectory(prefix="ici-compat-bounds-") as temporary:
        base = Path(temporary)
        root, bin_dir = base / "project", base / "bin"
        bin_dir.mkdir()
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        wrapper = bin_dir / "python3"
        wrapper.write_text(
            WRAPPER.format(
                version=behaviour.get("version", NONE),
                compile=behaviour.get("compile", NONE),
                imports=behaviour.get("imports", NONE),
                bare=bare,
            ),
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        (root / "ici.toml").write_text(
            '[ici]\nprofile = "standard"\n[project]\ntype = "python"\n'
            f"source_dirs = {json.dumps(source_dirs)}\n"
            "[engines.python_compat]\nenabled = true\n"
            f'interpreters = ["{wrapper}"]\nrequired_interpreters = ["{wrapper}"]\n{extra}',
            encoding="utf-8",
        )
        process = subprocess.run(
            [str(python), "-m", "ici", "python-compat", "--report"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=900,
        )
        with (root / "python_compat_report.json").open(encoding="utf-8") as handle:
            engine = json.load(handle)
    text = json.dumps(
        {
            "exit_code": process.returncode,
            "engine": {key: engine[key] for key in ("status", "summary", "evidence")},
            "targets": [
                {
                    key: target.get(key)
                    for key in ("file_path", "start_line", "target_name", "status", "message")
                }
                for target in engine["targets"]
                if target["target_name"] != "Compatibility:StaticScan"
            ],
            "static_scans": sum(
                target["target_name"] == "Compatibility:StaticScan" for target in engine["targets"]
            ),
            "ast_nodes": engine["extra"]["ast_nodes"],
        }
    )
    return json.loads(text.replace(str(root), "<root>").replace(str(bin_dir), "<bin>"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bare", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    cases = {
        name: capture(args.oracle, args.python, args.bare, scenario)
        for name, scenario in SCENARIOS.items()
    }
    payload = {"oracle": SOURCE, "cases": cases}
    out = Path(__file__).with_name("legacy-python-compat-bounds-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=1, sort_keys=True))


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
