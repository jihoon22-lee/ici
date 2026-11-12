"""Development-only capture: frozen `[build.artifacts]` (engines/build.py:185-227) per schema family.

    python -I -B capture_build_declared_artifacts.py --oracle <oracle-root> --python <py> --out <json>

Legacy (`type='python'`): `_publish_declared_artifacts` resolves each declared glob to project
files and records one `DeclaredArtifacts` PASS 'Recorded <n> declared artifact(s)' at '.' with a
shared analysis context (deep `ici verify --report`), else one `BuildError` ERROR. The frozen
`ici build` command has no analysis context, so every declaration there is the blanket
'declared artifacts require a shared analysis context'. Both commands are captured: `ici build`'s
exit code and console, and the verify build engine's verdict, targets and manifests.
Schema 1 (`schema_version = 1`): the next schema has no `build` table (`build: unknown key (did
you mean builds?)`, exit 2, under `next plan`, `next verify` and `analyze`) and the stable `ici
build` refuses any `[workspace]` config, exit 2.
Every run records the project files it created.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
# Every legacy engine but build (and integration) is disabled, as in capture_build_python_errors.
DISABLED = (
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "python_compat",
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
)

PYPROJECT = "[project]\nname = 'pkg'\nversion = '2.3.4'\n"
APP = {"src/app.py": "def main():\n    return 0\n"}
REPORT = "[build.artifacts]\nreport = ['reports/*.txt']\n"
WHEEL = "[build.artifacts]\npython_wheel = ['dist/*.whl']\n"
BOTH = "[build.artifacts]\npython_wheel = ['dist/*.whl']\nreport = ['reports/*.txt']\n"
# legacy scenario: (files, symlinks {link: target}, build lines)
LEGACY = {
    "report": ({**APP, "reports/summary.txt": "ok\n"}, {}, REPORT),
    "wheel": ({**APP, "dist/pkg-2.3.4-py3-none-any.whl": "PK\n"}, {}, WHEEL),
    "both": (
        {
            **APP,
            "dist/pkg-2.3.4-py3-none-any.whl": "PK\n",
            "reports/b.txt": "b\n",
            "reports/a.txt": "a\n",
        },
        {},
        BOTH,
    ),
    "missing": (APP, {}, REPORT),
    "one-missing": ({**APP, "reports/summary.txt": "ok\n"}, {}, BOTH),
    "external": (APP, {"reports/summary.txt": "../../outside.txt"}, REPORT),
    "directory-only": ({**APP, "reports/sub.txt/inner": "x\n"}, {}, REPORT),
    "none": (APP, {}, ""),
    # Declared outputs the build itself writes (frozen records them after packaging).
    "output-tree": (
        APP,
        {},
        "[build.artifacts]\nreport = ['v2.3.4/x86_64/lib/*.py', 'v2.3.4/x86_64/env.sh']\n",
    ),
}
SCHEMA1 = (
    "schema_version = 1\n[workspace]\nname = 'w'\n[[components]]\nid = 'app'\nroot = '.'\n"
    "languages = ['python']\n"
)
# schema 1 scenario: (files, ici.toml)
NEXT = {
    "schema1-artifacts": ({**APP, "reports/summary.txt": "ok\n"}, SCHEMA1 + REPORT),
    "schema1-plain": ({**APP, "reports/summary.txt": "ok\n"}, SCHEMA1),
}


def legacy_config(build: str) -> str:
    text = "[project]\ntype = 'python'\nsource_dirs = ['src']\n[ici]\nprofile = 'deep'\n"
    for engine in (*DISABLED, "integration"):
        text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = true\n" + build


def run(python: Path, oracle: Path, root: Path, home: Path, *argv: str):
    return subprocess.run(
        [str(python), "-B", "-m", "ici", *argv],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONPATH": str(oracle / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def populate(root: Path, files: dict[str, str], links: dict[str, str], config: str) -> set[str]:
    root.mkdir(parents=True)
    (root / "ici.toml").write_text(config, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    for name, target in links.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to(target)
    return listing(root)


def listing(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def legacy(oracle: Path, python: Path, scenario: tuple) -> dict[str, object]:
    files, links, build = scenario
    config = legacy_config(build)
    view: dict[str, object] = {"config": config, "files": files, "links": links}
    with tempfile.TemporaryDirectory(prefix="ici-declared-artifacts-") as temporary:
        base = Path(temporary).resolve()
        (base / "home").mkdir()
        (base / "outside.txt").write_text("outside\n", encoding="utf-8")
        root = base / "p"
        before = populate(root, files, links, config)
        built = run(python, oracle, root, base / "home", "build")
        view["build"] = {
            "exit_code": built.returncode,
            "stdout": built.stdout.strip(),
            "created": sorted(listing(root) - before),
        }
    with tempfile.TemporaryDirectory(prefix="ici-declared-artifacts-") as temporary:
        base = Path(temporary).resolve()
        (base / "home").mkdir()
        (base / "outside.txt").write_text("outside\n", encoding="utf-8")
        root = base / "p"
        populate(root, files, links, config)
        verified = run(python, oracle, root, base / "home", "verify", "--report", "--no-cache")
        report = json.loads((root / "verify_report.json").read_text(encoding="utf-8"))
        engine = next(item for item in report["results"] if item["engine_name"] == "build")
        view["verify"] = {
            "exit_code": verified.returncode,
            "suite_status": report["suite_status"],
            "build": {
                key: engine[key] for key in ("status", "summary", "evidence", "required", "targets")
            },
            # The whole frozen manifest of every `config.artifacts` record set, as reported.
            "manifests": [
                manifest
                for manifest in engine.get("artifact_manifests", [])
                if any(
                    record.get("producer") == "config.artifacts"
                    for record in manifest.get("artifacts", [])
                )
            ],
        }
    for manifest in view["verify"]["manifests"]:
        for record in manifest["artifacts"]:
            # Frozen `ArtifactRecord.to_dict` names the identity `id` (never `artifact_id`).
            assert record.get("id"), record
            assert "artifact_id" not in record, record
    return json.loads(json.dumps(view).replace(str(root), "<ROOT>"))


def schema1(oracle: Path, python: Path, scenario: tuple) -> dict[str, object]:
    files, config = scenario
    view: dict[str, object] = {"config": config, "files": files}
    for argv in (("build",), ("next", "plan"), ("next", "verify"), ("analyze",)):
        with tempfile.TemporaryDirectory(prefix="ici-declared-artifacts-") as temporary:
            base = Path(temporary).resolve()
            (base / "home").mkdir()
            root = base / "p"
            before = populate(root, files, {}, config)
            done = run(python, oracle, root, base / "home", *argv)
            created = sorted(path for path in listing(root) - before)
            view[" ".join(argv)] = {
                "exit_code": done.returncode,
                "stdout": done.stdout.replace(str(root), "<ROOT>").strip(),
                "stderr": done.stderr.replace(str(root), "<ROOT>").strip(),
                "created": created,
            }
    return view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "legacy": {name: legacy(args.oracle, args.python, item) for name, item in LEGACY.items()},
        "schema1": {name: schema1(args.oracle, args.python, item) for name, item in NEXT.items()},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
