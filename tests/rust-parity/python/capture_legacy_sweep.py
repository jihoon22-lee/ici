"""Development-only frozen captures for the closure sweep; never called by the Rust product.

Runs `python -m ici security --report` (and `ici verify --report`, keeping its security entry)
from the immutable v0.13.0 checkout over projects that exercise `engines.security.scan_tests`
(frozen engines/security.py `_test_python_files`), plus legacy resource syntax counters, and
records the engine status, summary, evidence, targets, findings, counters and exit code.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
EXTRA = ("files_checked", "calls_checked", "secret_literals_checked", "excluded_source_counts")
OUT = ROOT / "tests/rust-parity/python/legacy-sweep-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

BASE = "[project]\ntype='python'\nsource_dirs=['src']\n"
ON = BASE + "[engines.security]\nscan_tests=true\n"
CLEAN = "def f():\n    return 1\n"
RISKY = (
    'import subprocess\n\n\ndef test_x():\n    subprocess.run("ls", shell=True)\n    eval("1")\n'
)
PICKLE = "import pickle\n\n\ndef load(data):\n    return pickle.loads(data)\n"

# name: (configuration text or None for no ici.toml, files); a file value {"symlink": target}
# creates a symbolic link to the project-relative (or absolute) target.
CASES = {
    "scan-tests-on": (ON, {"src/a.py": CLEAN, "tests/test_a.py": RISKY}),
    "scan-tests-default-off": (BASE, {"src/a.py": CLEAN, "tests/test_a.py": RISKY}),
    "scan-tests-explicit-false": (
        BASE + "[engines.security]\nscan_tests=false\n",
        {"src/a.py": CLEAN, "tests/test_a.py": RISKY},
    ),
    "scan-tests-only-tests": (ON, {"src/README.md": "x\n", "tests/test_a.py": RISKY}),
    "scan-tests-no-tests-dir": (ON, {"src/a.py": PICKLE}),
    "scan-tests-nothing": (ON, {"src/README.md": "x\n", "tests/README.md": "y\n"}),
    "scan-tests-tests-is-file": (ON, {"src/a.py": CLEAN, "tests": "not a directory\n"}),
    "scan-tests-ignored-parts": (
        ON,
        {
            "src/a.py": CLEAN,
            "tests/sub/deep/test_deep.py": PICKLE,
            "tests/__pycache__/cached.py": RISKY,
            "tests/venv/lib.py": RISKY,
            "tests/.venv/lib.py": RISKY,
            "tests/build/gen.py": RISKY,
            "tests/.git/hook.py": RISKY,
            "tests/.pytest_cache/c.py": RISKY,
            "tests/.ruff_cache/c.py": RISKY,
            "tests/v1.2/old.py": RISKY,
            "tests/v0.9/old.py": RISKY,
            "tests/v1.helper.py": RISKY,
            "tests/vendor/third.py": RISKY,
            "tests/notes.txt": "eval('1')\n",
            "tests/upper.PY": RISKY,
        },
    ),
    "scan-tests-symlinks": (
        ON,
        {
            "src/a.py": CLEAN,
            "outside/evil.py": RISKY,
            "tests/test_ok.py": CLEAN,
            "tests/link.py": {"symlink": "outside/evil.py"},
            "tests/linkdir": {"symlink": "outside"},
        },
    ),
    "scan-tests-dir-symlink-inside": (
        ON,
        {"src/a.py": CLEAN, "suite/test_s.py": RISKY, "tests": {"symlink": "suite"}},
    ),
    "scan-tests-dir-symlink-dangling": (
        ON,
        {"src/a.py": CLEAN, "tests": {"symlink": "/nonexistent-ici-sweep-target"}},
    ),
    "scan-tests-syntax": (
        ON,
        {"src/a.py": CLEAN, "tests/test_bad.py": "def f(:\n    pass\n"},
    ),
    "scan-tests-overlap": (
        "[project]\ntype='python'\nsource_dirs=['src', 'tests']\n"
        "[engines.security]\nscan_tests=true\n",
        {"src/a.py": CLEAN, "tests/test_a.py": RISKY},
    ),
    "scan-tests-allowlist": (
        ON + "secret_name_allowlist=['API_TOKEN']\n",
        {
            "src/a.py": CLEAN,
            "tests/test_secret.py": "API_TOKEN = 'abcdefghijklmnop'\nPASSWORD = 'zyxwvutsrq'\n",
        },
    ),
}


# Other commands: name -> (command, configuration, files). Verify keeps only its security entry.
OTHER = {
    "verify/scan-tests-on": (
        "verify",
        ON,
        {"src/a.py": CLEAN, "tests/test_a.py": PICKLE},
    ),
    "resource/syntax-files-checked": (
        "resource",
        BASE,
        {"src/a.py": CLEAN, "src/b.py": "def f(:\n    pass\n", "src/c.py": "x = (1,\n"},
    ),
    "security/syntax-files-checked": (
        "security",
        BASE,
        {"src/a.py": CLEAN, "src/b.py": "def f(:\n    pass\n", "src/c.py": "x = (1,\n"},
    ),
}


def write(project: Path, files: dict) -> None:
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            os.symlink(project / content["symlink"], path)
        else:
            path.write_text(content, encoding="utf-8")


def run(command: str, config: str | None, files: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-legacy-sweep-"))
    try:
        project = work / "project"
        project.mkdir()
        if config is not None:
            (project / "ici.toml").write_text(config, encoding="utf-8")
        write(project, files)
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report_path = project / f"{command}_report.json"
        report = (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        )
        if command == "verify" and report is not None:
            report = next(item for item in report["results"] if item["engine_name"] == "security")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if report is None:
        return {"exit_code": result.returncode, "crash": result.stderr.strip().splitlines()[-1]}
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "extra": {key: report["extra"].get(key) for key in EXTRA},
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
        "findings": [
            {key: finding.get(key) for key in ("rule_id", "fingerprint", "message", "tool_rule_id")}
            for finding in report["findings"]
        ],
    }


def main() -> None:
    cases = {
        f"security/{name}": {
            "command": "security",
            "config": config,
            "files": files,
            "frozen": run("security", config, files),
        }
        for name, (config, files) in CASES.items()
    }
    for name, (command, config, files) in OTHER.items():
        cases[name] = {
            "command": command,
            "config": config,
            "files": files,
            "frozen": run(command, config, files),
        }
    python = subprocess.check_output([PYTHON, "--version"], text=True).strip()
    OUT.write_text(
        json.dumps(
            {"oracle": EXPECTED, "python": python, "cases": cases},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} legacy sweep cases with {python}", file=sys.stderr)


if __name__ == "__main__":
    main()
