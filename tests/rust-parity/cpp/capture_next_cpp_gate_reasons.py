"""Development-only capture: frozen next gate reasons of the C++ non-compilation checks.

    python -I -B capture_next_cpp_gate_reasons.py --oracle <oracle-root> --python <py> --out <json>

Frozen `ici next verify` and `ici analyze` (schema_version = 1, one C++ component `app` at the
workspace root, `[builds.native]` with `prepare = "explicit"`, so nothing is built) with every C++
check off but the one under test, so every gate reason belongs to it:
- `cpp.artifact` (`application/gates.gate_artifact`, `languages/artifacts.verify_artifacts`): no
  linked build, a build without an artifact contract, an absent build directory, a glob with no
  match, a glob matching only a directory, a satisfied contract, and one satisfied beside one
  missing;
- `cpp.test` with `cpp.coverage` (no declared suite, the prerequisite reason).
`analyze` omits tests, runtime checks and build artifact checks; its gate is recorded beside
`next verify`'s. Every run records the exit code, the gate (selected verdict and every reason,
literally), each check's state and evidence completeness, and each finding's rule, message and
location. No compiler or tool runs (PATH=/usr/bin:/bin).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
CHECKS = (
    "compile",
    "diagnostics",
    "tidy",
    "test",
    "coverage",
    "artifact",
    "binary-compat",
    "cycle",
    "line",
    "complexity",
    "cognitive",
    "dup",
    "exception",
    "sanitize",
    "tsan",
)
HEAD = 'schema_version = 1\n[workspace]\nname = "product"\nprofile = "standard"\n'
COMPONENT = '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nsources = ["src/**"]\n'
SOURCES = {"src/app.cpp": "int main() { return 0; }\n"}


def config(
    enabled: tuple[str, ...], build: str | None, linked: bool, required: tuple[str, ...] = ()
) -> str:
    text = HEAD
    if build is not None:
        text += (
            '[builds.native]\nsystem = "cmake"\nproject = "CMakeLists.txt"\ndirectory = "out"\n'
            f'variant = "release"\nprepare = "explicit"\n{build}'
        )
    text += COMPONENT + ('build = "native"\n' if linked else "")
    for check in CHECKS:
        if check not in enabled:
            text += f'[checks."cpp.{check}"]\nenabled = false\n'
        elif check in required:
            text += f'[checks."cpp.{check}"]\nrequired = true\n'
    return text


ARTIFACT = ("artifact",)
REQUIRED = ("artifact",)
WITH_LINE = ("artifact", "line")
# scenario: (enabled checks, [builds.native] extra lines or None, linked, extra files[, required])
SCENARIOS = {
    "artifact-no-build": (ARTIFACT, None, False, {}),
    "artifact-no-contract": (ARTIFACT, "", True, {"out/bin/app": "x\n"}),
    "artifact-build-dir-absent": (ARTIFACT, 'artifacts = ["bin/app"]\n', True, {}),
    "artifact-no-match": (ARTIFACT, 'artifacts = ["bin/app"]\n', True, {"out/other": "x\n"}),
    "artifact-directory-only": (
        ARTIFACT,
        'artifacts = ["bin/*"]\n',
        True,
        {"out/bin/sub/x": "x\n"},
    ),
    "artifact-satisfied": (ARTIFACT, 'artifacts = ["bin/app"]\n', True, {"out/bin/app": "x\n"}),
    "artifact-partly-missing": (
        ARTIFACT,
        'artifacts = ["bin/app", "lib/*.so"]\n',
        True,
        {"out/bin/app": "x\n"},
    ),
    # `required = true`: the same states become gate reasons; with cpp.line, `analyze` has a check.
    "artifact-required-no-build": (WITH_LINE, None, False, {}, REQUIRED),
    "artifact-required-no-contract": (WITH_LINE, "", True, {"out/bin/app": "x\n"}, REQUIRED),
    "artifact-required-no-match": (
        WITH_LINE,
        'artifacts = ["bin/app"]\n',
        True,
        {"out/other": "x\n"},
        REQUIRED,
    ),
    "artifact-required-satisfied": (
        WITH_LINE,
        'artifacts = ["bin/app"]\n',
        True,
        {"out/bin/app": "x\n"},
        REQUIRED,
    ),
    "test-coverage-no-suite": (
        ("test", "coverage"),
        "",
        True,
        {
            "CMakeLists.txt": "project(p CXX)\nadd_executable(app src/app.cpp)\n",
            "out/app.gcno": "x\n",
        },
    ),
}
COMMANDS = (("next", "verify", "--no-cache"), ("analyze", "--no-cache"))


def run(oracle: Path, python: Path, scenario: tuple, argv: tuple) -> dict:
    enabled, build, linked, files, *required = scenario
    with tempfile.TemporaryDirectory(prefix="ici-next-cpp-gates-") as temporary:
        base = Path(temporary).resolve()
        (base / "home").mkdir()
        root = base / "p"
        root.mkdir()
        (root / ".hg").mkdir()
        for name, text in {**SOURCES, **files}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        (root / "ici.toml").write_text(config(enabled, build, linked, *required), encoding="utf-8")
        done = subprocess.run(
            [str(python), "-B", "-m", "ici", *argv],
            cwd=root,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(base / "home"),
                "PYTHONPATH": str(oracle / "src"),
                "LC_CTYPE": "C.UTF-8",
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        result_path = root / ".ici/next/result.json"
        result = json.loads(result_path.read_text("utf-8")) if result_path.exists() else None
    view: dict[str, object] = {"exit_code": done.returncode}
    if result is not None:
        view["gate"] = {key: result["gate"][key] for key in ("selected", "reasons")}
        view["checks"] = {
            check["check_id"]: [check["state"], check["evidence_complete"]]
            for check in result["checks"]
        }
        view["findings"] = sorted(
            [
                finding["rule_id"],
                finding["message"],
                finding["primary_location"]["path"],
                finding["severity"],
            ]
            for finding in result["findings"]
        )
    else:
        view["console"] = (done.stdout + done.stderr).splitlines()[-20:]
    return json.loads(json.dumps(view).replace(str(root), "<ROOT>"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "runs": {
            name: {
                "config": config(*scenario[:3], *scenario[4:]),
                "files": {**SOURCES, **scenario[3]},
                **{
                    " ".join(argv[:-1]): run(args.oracle, args.python, scenario, argv)
                    for argv in COMMANDS
                },
            }
            for name, scenario in SCENARIOS.items()
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
