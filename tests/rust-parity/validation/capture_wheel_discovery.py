"""Development-only capture: frozen legacy `ici python-compat --report` wheel discovery.

    python -I capture_wheel_discovery.py <v0.13.0 checkout> <python>

Frozen `analysis/_python_packaging._wheel_paths` expands `[engines.python_compat] wheel_globs`
against the canonical project root. Each case places one pure wheel (stored as `wheel_base64`) (or a symlink, dangling
link, directory, linked `dist` or extra files in its place), runs the frozen CLI in a fresh
HOME with PATH=/usr/bin:/bin and records the exit code, engine status/summary, every target
(the project root as `<ROOT>`) and the finding rule ids. Written to
`wheel-discovery-oracle.json` beside this script.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
NAME = "demo-1.2.3-py3-none-any.whl"
DISABLED = [
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
]
PYPROJECT = '[project]\nname = "demo"\nversion = "1.2.3"\nrequires-python = ">=3.8"\n'
CASES = {
    # case: (wheel_globs, max_wheels or None, layout)
    "regular": (["dist/*.whl"], None, "regular"),
    "non-wheel-skipped": (["dist/*"], None, "with-text"),
    "symlink-outside": (["dist/*.whl"], None, "symlink-outside"),
    "symlink-inside": (["dist/*.whl"], None, "symlink-inside"),
    "dangling": (["dist/*.whl"], None, "dangling"),
    "directory": (["dist/*.whl"], None, "directory"),
    "dist-symlink-outside": (["dist/*.whl"], None, "dist-symlink-outside"),
    "count-bound": (["dist/*.whl"], 1, "two-wheels"),
}


def config(globs: list[str], max_wheels: int | None) -> str:
    text = '[ici]\nprofile = "standard"\n[project]\ntype = "python"\nsource_dirs = ["."]\n'
    text += "[engines]\n[engines.python_compat]\nenabled = true\n"
    text += f"wheel_globs = {json.dumps(globs)}\n"
    if max_wheels is not None:
        text += f"max_wheels = {max_wheels}\n"
    return text + "".join(f"[engines.{name}]\nenabled = false\n" for name in DISABLED)


def wheel() -> bytes:
    members = {
        "demo/__init__.py": b"VALUE = 1\n",
        "demo-1.2.3.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: x\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "demo-1.2.3.dist-info/METADATA": b"Metadata-Version: 2.1\nName: demo\nVersion: 1.2.3\n",
    }
    rows = []
    for member, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        rows.append(f"{member},sha256={digest.decode()},{len(payload)}")
    rows.append("demo-1.2.3.dist-info/RECORD,,")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
        archive.writestr("demo-1.2.3.dist-info/RECORD", "\n".join(rows) + "\n")
    return buffer.getvalue()


def layout(root: Path, outside: Path, kind: str) -> None:
    (root / "demo").mkdir(parents=True)
    (root / "demo/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    dist = root / "dist"
    dist.mkdir()
    payload = wheel()
    if kind in {"regular", "with-text", "two-wheels"}:
        (dist / NAME).write_bytes(payload)
    if kind == "with-text":
        (dist / "notes.txt").write_text("notes\n", encoding="utf-8")
    if kind == "two-wheels":
        (dist / "demo-1.2.3-py2.py3-none-any.whl").write_bytes(payload)
    if kind == "symlink-outside":
        (outside / NAME).write_bytes(payload)
        (dist / NAME).symlink_to(outside / NAME)
    if kind == "symlink-inside":
        (root / "keep").mkdir()
        (root / "keep" / NAME).write_bytes(payload)
        (dist / NAME).symlink_to(root / "keep" / NAME)
    if kind == "dangling":
        (dist / NAME).symlink_to(root / "nowhere.whl")
    if kind == "directory":
        (dist / NAME).mkdir()
    if kind == "dist-symlink-outside":
        dist.rmdir()
        (outside / "d").mkdir()
        (outside / "d" / NAME).write_bytes(payload)
        dist.symlink_to(outside / "d")


def capture(oracle: Path, python: Path, name: str) -> dict[str, object]:
    globs, max_wheels, kind = CASES[name]
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        root, outside, home = base / "project", base / "outside", base / "home"
        outside.mkdir()
        home.mkdir()
        layout(root, outside, kind)
        (root / "ici.toml").write_text(config(globs, max_wheels), encoding="utf-8")
        process = subprocess.run(
            [str(python), "-I", "-c", ENTRY, str(oracle / "src"), "python-compat", "--report"],
            cwd=root,
            env={"HOME": str(home), "XDG_CONFIG_HOME": str(home), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        report = json.loads(
            (root / "python_compat_report.json")
            .read_text(encoding="utf-8")
            .replace(str(root), "<ROOT>")
        )
    return {
        "layout": kind,
        "wheel_globs": globs,
        "max_wheels": max_wheels,
        "exit_code": process.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "targets": [
            {key: target.get(key) for key in ("file_path", "target_name", "status", "message")}
            for target in report["targets"]
        ],
        "finding_rules": [finding["rule_id"] for finding in report["findings"]],
    }


def main() -> None:
    oracle, python = Path(sys.argv[1]), Path(sys.argv[2])
    head = subprocess.check_output(["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE, head
    payload = {
        "oracle": SOURCE,
        "pyproject": PYPROJECT,
        "wheel_base64": base64.b64encode(wheel()).decode(),
        "disabled_engines": DISABLED,
        "cases": {name: capture(oracle, python, name) for name in CASES},
    }
    out = Path(__file__).with_name("wheel-discovery-oracle.json")
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, case in payload["cases"].items():
        print(name, case["exit_code"], case["status"], case["summary"])


if __name__ == "__main__":
    main()
