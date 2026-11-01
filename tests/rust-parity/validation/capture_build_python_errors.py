"""Development-only capture: frozen deep `ici verify` BuildEngine `_package_python` error branches.

Each scenario is one legacy `type='python'` project: a symlinked source, two source roots that
map a module to the same artifact path, and launcher entrypoints that are malformed, name an
unsafe launcher, or name a missing module. `verify --report` records the whole build engine;
`ici build` (the build engine required) records only its exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE

PYPROJECT = "[project]\nname = 'pkg'\nversion = '2.3.4'\n"
# scenario: (source_dirs, files, symlinks {link: target}, extra pyproject text, build lines)
SCENARIOS = {
    "symlinked_source": (["src"], {"src/real.py": "X = 1\n"}, {"src/link.py": "real.py"}, "", ""),
    "collision": (["a", "b"], {"a/mod.py": "X = 1\n", "b/mod.py": "Y = 2\n"}, {}, "", ""),
    "invalid_entrypoint": (
        ["src"],
        {"src/app.py": "def main():\n    return 0\n"},
        {},
        "",
        "[build.python]\nentrypoint = 'not a valid entrypoint'\n",
    ),
    "unsafe_launcher": (
        ["src"],
        {"src/app.py": "def main():\n    return 0\n"},
        {},
        "[project.scripts]\n'../evil' = 'app:main'\n",
        "",
    ),
    "missing_module": (
        ["src"],
        {"src/app.py": "def main():\n    return 0\n"},
        {},
        "",
        "[build.python]\nentrypoint = 'nothere:main'\n",
    ),
    "launcher_ok": (
        ["src"],
        {"src/app.py": "def main():\n    return 0\n"},
        {},
        "",
        "[build.python]\nentrypoint = 'app:main'\n",
    ),
}


def config(source_dirs: list[str], build: str) -> str:
    text = (
        f"[project]\ntype = 'python'\nsource_dirs = {json.dumps(source_dirs)}\n"
        "[ici]\nprofile = 'deep'\n"
    )
    for engine in (*DISABLED, "integration"):
        text += f"[engines.{engine}]\nenabled = false\n"
    return text + "[engines.build]\nenabled = true\n" + build


def populate(root: Path, scenario: tuple) -> None:
    source_dirs, files, links, pyproject, build = scenario
    (root / "ici.toml").write_text(config(source_dirs, build), encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT + pyproject, encoding="utf-8")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    for name, target in links.items():
        (root / name).symlink_to(target)


def build_exit(oracle: Path, python: Path, scenario: tuple) -> int:
    with tempfile.TemporaryDirectory(prefix="ici-build-errors-") as temporary:
        root = Path(temporary).resolve()
        populate(root, scenario)
        return subprocess.run(
            [str(python), "-m", "ici", "build"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            check=False,
            timeout=600,
        ).returncode


def capture(oracle: Path, python: Path, scenario: tuple) -> dict[str, object]:
    source_dirs, files, links, pyproject, build = scenario
    with tempfile.TemporaryDirectory(prefix="ici-build-errors-") as temporary:
        root = Path(temporary).resolve()
        populate(root, scenario)
        completed = subprocess.run(
            [str(python), "-m", "ici", "verify", "--report", "--no-cache"],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(oracle / "src")),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        with (root / "verify_report.json").open(encoding="utf-8") as handle:
            report = json.load(handle)
        outputs = sorted(
            str(path.relative_to(root))
            for path in (root / "v2.3.4").rglob("*")
            if (root / "v2.3.4").exists()
        )
    engine = next(item for item in report["results"] if item["engine_name"] == "build")
    view = {
        "config": config(source_dirs, build),
        "pyproject": PYPROJECT + pyproject,
        "files": files,
        "links": links,
        "exit_code": completed.returncode,
        "build_exit_code": build_exit(oracle, python, scenario),
        "suite_status": report["suite_status"],
        "outputs": outputs,
        "build": {
            key: engine[key]
            for key in ("status", "summary", "evidence", "required", "targets", "findings")
        },
    }
    return json.loads(json.dumps(view).replace(str(root), "<ROOT>"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "runs": {name: capture(args.oracle, args.python, item) for name, item in SCENARIOS.items()},
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
