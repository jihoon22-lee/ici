"""Development-only capture: the frozen pyz launcher rule as the legacy project interpreter.

Frozen legacy engines fall back to ``sys.executable`` (``engines/test_interpreter.py``,
``analysis/_sanitize_python_scope.py``) or use it directly (``engines/python_compat.py`` with no
``interpreters``; ``engines/integration.py`` ``{python:current}``, symlink-resolved). Under the
released ``dist/ici.pyz`` that interpreter is the one ``scripts/launcher.sh`` chose: the first of
``$ICI_PYTHON``, python3.14 … python3.10, python3 that ``command -v`` finds on PATH and that passes
a Python >= 3.10 check. ``python -m ici`` from the source tree cannot reproduce this, so each
scenario runs a private copy of the released pyz. The copy's sha256 is
9e5f5751df033a2d51e9d09457bb35b7e81b05bf7cb750e93e31e62e61bef44f and all 283 bundled ``ici/``
sources are byte-identical to the frozen source tree (both checked before any scenario runs).

``--python`` is a virtual-environment interpreter with pytest and coverage; its ``bin`` holds
``python3.10``. ``--bare`` is a CPython 3.12 outside any environment (no pytest). A fake
``python3.14``/``python3`` (``exit 1``) proves the launcher's version check skips a candidate.
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

from capture_integration_legacy import DISABLED, SOURCE

PYZ_SHA256 = "9e5f5751df033a2d51e9d09457bb35b7e81b05bf7cb750e93e31e62e61bef44f"
APP = "def f(x):\n    return x + 1\n"
TEST = "import app\n\n\ndef test_f():\n    assert app.f(1) == 2\n"
CONTRACT = "import sys\n\nprint('contract ok')\n"
FAKE = "#!/bin/sh\nexit 1\n"


def wrapper(target: Path) -> str:
    return f'#!/bin/sh\nexec {target} "$@"\n'


def legacy(engine: str, extra: str = "") -> str:
    text = '[ici]\nprofile = "standard"\n[project]\ntype = "python"\nsource_dirs = ["."]\n'
    text += f"[engines]\n[engines.{engine}]\nenabled = true\n{extra}"
    for name in DISABLED:
        if name != engine:
            text += f"[engines.{name}]\nenabled = false\n"
    return text


def integration_config() -> str:
    text = (
        '[ici]\nprofile = "deep"\n[project]\ntype = "python"\nsource_dirs = ["."]\n[engines]\n'
        "[engines.integration]\nenabled = true\nrequired = true\n"
        '[[engines.integration.cases]]\nname = "current"\n'
        'argv = ["{python:current}", "contract.py"]\nstdout_contains = ["contract ok"]\n'
    )
    for name in DISABLED:
        text += f"[engines.{name}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = true\n"


# scenario: (command, config, PATH dirs, ICI_PYTHON, .venv wrapper)
SCENARIOS = {
    "test_path": ("test", legacy("test"), ["<VENV_BIN>"], None, False),
    "test_ici_python": ("test", legacy("test"), ["<BARE_BIN>"], "<VENV_BIN>/python", False),
    "test_version_skip": ("test", legacy("test"), ["<FAKE>", "<VENV_BIN>"], None, False),
    "test_venv_first": ("test", legacy("test"), ["<BARE_BIN>"], None, True),
    "test_declared_bare": (
        "test",
        legacy("test", 'python = "python3"\n'),
        ["<VENV_BIN>"],
        None,
        False,
    ),
    "test_none": ("test", legacy("test"), ["<FAKE>"], None, False),
    "sanitize_path": ("sanitize", legacy("sanitize"), ["<VENV_BIN>"], None, False),
    "compat_path": ("python-compat", legacy("python_compat"), ["<BARE_BIN>"], None, True),
    "integration_current": (
        "verify",
        integration_config(),
        ["<VENV_BIN>"],
        None,
        False,
    ),
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


class Paths:
    def __init__(self, work: Path, python: Path, bare: Path) -> None:
        self.fake = work / "fake"
        self.fake.mkdir()
        # The pyz (shiv) extracts itself under HOME; keep that outside every project root.
        self.home = work / "home"
        self.home.mkdir()
        for name in ("python3.14", "python3"):
            (self.fake / name).write_text(FAKE, encoding="utf-8")
            (self.fake / name).chmod(0o755)
        self.values = {
            "<VENV_BIN>": str(python.parent),
            "<BARE_BIN>": str(bare.parent),
            "<FAKE>": str(self.fake),
        }
        self.real = {str(python.resolve()): "<VENV_REAL>", str(bare.resolve()): "<BARE_REAL>"}

    def expand(self, text: str) -> str:
        for key, value in self.values.items():
            text = text.replace(key, value)
        return text

    def normal(self, text: str, root: Path) -> str:
        text = text.replace(str(root), "<ROOT>")
        for value, key in self.real.items():
            text = text.replace(value, key)
        for key, value in sorted(self.values.items(), key=lambda item: -len(item[1])):
            text = text.replace(value, key)
        return text


def engine_view(engine: dict, paths: Paths, root: Path) -> dict[str, object]:
    def normal(value: object) -> object:
        return json.loads(paths.normal(json.dumps(value), root))

    view = {key: engine[key] for key in ("status", "summary", "evidence", "required")}
    view["targets"] = [
        {key: target[key] for key in ("file_path", "start_line", "target_name", "status")}
        | {"message": target["message"]}
        for target in engine["targets"]
    ]
    view["interpreters"] = sorted({item["argv"][0] for item in engine.get("tool_evidence", [])})
    if engine["engine_name"] == "integration":
        view["cases"] = [
            {key: case[key] for key in ("name", "assertions", "returncode", "status")}
            for case in engine["extra"]["integration"]["cases"]
        ]
    return normal(view)


def capture(pyz: Path, paths: Paths, scenario: tuple) -> dict[str, object]:
    command, config, path_dirs, ici_python, venv = scenario
    with tempfile.TemporaryDirectory(prefix="ici-launcher-") as temporary:
        root = Path(temporary).resolve()
        (root / "ici.toml").write_text(config, encoding="utf-8")
        (root / "app.py").write_text(APP, encoding="utf-8")
        (root / "contract.py").write_text(CONTRACT, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/test_app.py").write_text(TEST, encoding="utf-8")
        if venv:
            (root / ".venv/bin").mkdir(parents=True)
            target = Path(paths.values["<VENV_BIN>"]) / "python"
            (root / ".venv/bin/python").write_text(wrapper(target), encoding="utf-8")
            (root / ".venv/bin/python").chmod(0o755)
        environment = {
            "HOME": str(paths.home),
            "PATH": ":".join(paths.expand(d) for d in path_dirs),
        }
        if ici_python is not None:
            environment["ICI_PYTHON"] = paths.expand(ici_python)
        argv = [str(pyz), command, "--report"]
        if command == "verify":
            argv += ["--profile", "deep", "--no-cache"]
        completed = subprocess.run(
            argv,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        name = {"python-compat": "python_compat_report.json"}.get(command, f"{command}_report.json")
        run: dict[str, object] = {"exit_code": completed.returncode}
        report_path = root / name
        if report_path.exists():
            with report_path.open(encoding="utf-8") as handle:
                report = json.load(handle)
            if command == "verify":
                run["suite_status"] = report["suite_status"]
                report = next(e for e in report["results"] if e["engine_name"] == "integration")
            run["engine"] = engine_view(report, paths, root)
        else:
            run["stderr"] = paths.normal(completed.stderr.strip(), root)
        return run | {
            "command": command,
            "config": config,
            "path": path_dirs,
            "ici_python": ici_python,
            "venv": venv,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--pyz", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bare", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-launcher-work-") as temporary:
        work = Path(temporary)
        pyz = work / "ici.pyz"
        shutil.copyfile(args.pyz, pyz)
        pyz.chmod(0o755)
        checked_pyz(pyz, args.oracle)
        paths = Paths(work, args.python, args.bare)
        runs = {name: capture(pyz, paths, scenario) for name, scenario in SCENARIOS.items()}
        listing = {
            key: sorted(p.name for p in Path(value).iterdir() if p.name.startswith("python"))
            for key, value in paths.values.items()
        }
    document = {
        "source": SOURCE,
        "pyz_sha256": PYZ_SHA256,
        "app": APP,
        "test": TEST,
        "contract": CONTRACT,
        "fake": FAKE,
        "path_listing": listing,
        "runs": runs,
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    os.umask(0o022)
    main()
