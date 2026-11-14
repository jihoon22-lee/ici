"""Development-only capture: frozen `[build.artifacts]` beside a C++ adapter release build.

    python -I -B capture_build_declared_cpp.py --oracle <oracle-root> --python <py> --out <json>

Frozen deep `ici verify` BuildEngine: the adapter release session appends its linked-output
manifest (engines/build.py:736), then `_publish_declared_artifacts` appends the
`config.artifacts` manifest (build.py:227) or records the BuildError of a glob that matched
nothing. The suite's `analysis_context` carries every engine's manifests (engines/verify.py:321).
Cases use parity-fixtures/cpp/test-cmake/plain (CMake) and package-make (configured Make) in a
git checkout; every engine's manifests are kept whole with digest values replaced by their shape
(they hash the frozen run identity and compiler output). Tools come from PATH=/usr/bin:/bin.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
FIXTURES = Path(__file__).resolve().parents[3] / "parity-fixtures/cpp"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n[engines.build]\nenabled=true\n"
MAKE = "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
REPORT = "[build.artifacts]\nreport=['reports/*.txt']\n"
MISSING = "[build.artifacts]\nreport=['missing/*.txt']\n"
FILES = {"reports/summary.txt": "ok\n"}
# name: (fixture, configuration, extra files)
CASES = {
    "cmake-declared": ("test-cmake/plain", BASE + REPORT, FILES),
    "cmake-declared-missing": ("test-cmake/plain", BASE + MISSING, FILES),
    "cmake-plain": ("test-cmake/plain", BASE, FILES),
    "make-declared": ("package-make", BASE + MAKE + REPORT, FILES),
    "make-declared-missing": ("package-make", BASE + MAKE + MISSING, FILES),
}


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, str) and DIGEST.fullmatch(value):
        return "<sha256>"
    return value


def run(oracle: Path, python: Path, fixture: str, config: str, files: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="ici-declared-cpp-") as temporary:
        base = Path(temporary).resolve()
        project = base / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / "ici.toml").write_text(config, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        result = subprocess.run(
            [str(python), "-B", "-m", "ici", "verify", "--profile", "deep", "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(base), "PYTHONPATH": str(oracle / "src")},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        report = json.loads((project / "verify_report.json").read_text(encoding="utf-8"))
    build = next(engine for engine in report["results"] if engine["engine_name"] == "build")
    return {
        "fixture": fixture,
        "config": config,
        "files": files,
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "build": {
            "status": build["status"],
            "summary": build["summary"],
            "targets": [
                {"target_name": target["target_name"], "status": target["status"]}
                for target in build["targets"]
            ],
            "declared_message": [
                target["message"]
                for target in build["targets"]
                if target["target_name"] in {"DeclaredArtifacts", "BuildError"}
            ],
        },
        "engines": {
            engine["engine_name"]: shape(engine.get("artifact_manifests"))
            for engine in report["results"]
            if engine.get("artifact_manifests")
        },
        "analysis_context": shape(report["analysis_context"]["artifact_manifests"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(
        ["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head == SOURCE, head
    document = {
        "source": SOURCE,
        "cmake": subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0],
        "gxx": subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0],
        "cases": {name: run(args.oracle, args.python, *case) for name, case in CASES.items()},
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
