"""Development-only capture: which mypy the frozen legacy type engine ran.

Frozen ``engines/type_check.py`` ``_find_mypy_cmd``: a standalone ``ici type`` has no analysis
context, so it runs ``shutil.which("mypy")`` and then the project ``.venv`` mypy
(``core/env.find_project_executable``). ``ici verify`` builds an analysis context, whose
``core/toolchain`` probe for mypy is ``python_module="mypy"``: ``<interpreter> -m mypy`` with
the project ``.venv`` python or else ``sys.executable`` (``_resolve_python_module_interpreter``,
toolchain.py:449, type_check.py:176). Under the released ``dist/ici.pyz`` ``sys.executable`` is
the interpreter ``scripts/launcher.sh`` chose, so each scenario runs a private copy of the pyz
(sha256 and the 283 bundled ``ici/`` sources are checked against the frozen tree first).

    python capture_legacy_type_interpreter.py --oracle <oracle> --pyz <dist/ici.pyz>
        --python <venv python with mypy> --bare <CPython without mypy> --out <json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYZ_SHA256 = "9e5f5751df033a2d51e9d09457bb35b7e81b05bf7cb750e93e31e62e61bef44f"
ENGINES = (
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "cognitive",
    "resource",
    "security",
    "cycle",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "binary_compat",
    "python_compat",
)
APP = 'def f(x: int) -> int:\n    return x + "1"\n'
CONFIG = (
    '[ici]\nprofile = "standard"\n[project]\ntype = "python"\nsource_dirs = ["."]\n[engines]\n'
    + "".join(
        f"[engines.{name}]\nenabled = {'true' if name == 'type' else 'false'}\n" for name in ENGINES
    )
)
# scenario: (command, PATH dirs, project .venv entries)
SCENARIOS = {
    "type_path_mypy": ("type", ["<VENV_BIN>"], ()),
    "type_venv_mypy": ("type", ["<BARE_BIN>"], ("mypy",)),
    "type_path_before_venv": ("type", ["<VENV_BIN>"], ("mypy-broken",)),
    "type_no_mypy": ("type", ["<BARE_BIN>"], ()),
    "verify_launcher_module": ("verify", ["<VENV_BIN>"], ()),
    "verify_launcher_without_mypy": ("verify", ["<BARE_BIN>"], ()),
    "verify_venv_python_module": ("verify", ["<BARE_BIN>"], ("python",)),
}


def checked_pyz(pyz: Path, oracle: Path) -> None:
    digest = hashlib.sha256(pyz.read_bytes()).hexdigest()
    if digest != PYZ_SHA256:
        raise SystemExit(f"unexpected pyz digest {digest}")
    with zipfile.ZipFile(pyz) as archive:
        prefix = "site-packages/"
        names = [
            name
            for name in archive.namelist()
            if name.startswith(prefix + "ici/") and name.endswith(".py")
        ]
        changed = [
            name
            for name in names
            if archive.read(name) != (oracle / "src" / name[len(prefix) :]).read_bytes()
        ]
    if len(names) != 283 or changed:
        raise SystemExit(f"pyz sources differ from the frozen tree: {len(names)} {changed}")


def wrapper(target: Path) -> str:
    return f'#!/bin/sh\nexec {target} "$@"\n'


def capture(pyz: Path, values: dict[str, str], home: Path, scenario: tuple) -> dict:
    command, path_dirs, venv = scenario
    venv_bin = Path(values["<VENV_BIN>"])
    with tempfile.TemporaryDirectory(prefix="ici-type-") as temporary:
        root = Path(temporary).resolve()
        (root / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (root / "app.py").write_text(APP, encoding="utf-8")
        for entry in venv:
            target = root / ".venv/bin" / entry.replace("-broken", "")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = "#!/bin/sh\nexit 3\n" if entry.endswith("-broken") else wrapper(venv_bin / entry)
            target.write_text(text, encoding="utf-8")
            target.chmod(0o755)
        environment = {"HOME": str(home), "PATH": ":".join(values[d] for d in path_dirs)}
        argv = [str(pyz), command, "--report"]
        if command == "verify":
            argv.append("--no-cache")
        completed = subprocess.run(
            argv, cwd=root, env=environment, capture_output=True, text=True, check=False
        )

        def normal(text: str) -> str:
            text = text.replace(str(root), "<ROOT>")
            for key, value in sorted(values.items(), key=lambda item: -len(item[1])):
                text = text.replace(value, key)
            return text

        report = json.loads((root / f"{command}_report.json").read_text(encoding="utf-8"))
        engine = (
            next(e for e in report["results"] if e["engine_name"] == "type")
            if command == "verify"
            else report
        )
        view = {key: engine[key] for key in ("status", "summary", "evidence")}
        view["tool_evidence"] = [
            {key: item.get(key) for key in ("name", "path", "argv", "error")}
            for item in engine.get("tool_evidence", [])
            if item.get("name") == "mypy"
        ]
        view["targets"] = sorted({target["target_name"] for target in engine["targets"]})
        return {
            "command": command,
            "path": path_dirs,
            "venv": list(venv),
            "exit_code": completed.returncode,
            "engine": json.loads(normal(json.dumps(view))),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("--oracle", "--pyz", "--python", "--bare", "--out"):
        parser.add_argument(option, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-type-work-") as temporary:
        work = Path(temporary)
        pyz = work / "ici.pyz"
        shutil.copyfile(args.pyz, pyz)
        pyz.chmod(0o755)
        checked_pyz(pyz, args.oracle)
        home = work / "home"
        home.mkdir()
        values = {"<VENV_BIN>": str(args.python.parent), "<BARE_BIN>": str(args.bare.parent)}
        runs = {name: capture(pyz, values, home, scenario) for name, scenario in SCENARIOS.items()}
    document = {
        "source": SOURCE,
        "pyz_sha256": PYZ_SHA256,
        "config": CONFIG,
        "app": APP,
        "runs": runs,
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    os.umask(0o022)
    main()
