"""Capture frozen v0.13.0 historical commands run below a `schema_version = 2` root.

    PYTHONPATH= /home/jihoon/projects/ici/.venv/bin/python -I -B \
        tests/rust-parity/config/capture_v2_subdirectory.py <oracle-root> <output.json>

Development-only (never called by Rust); root ruling 2026-11-14 (f1). Frozen v0.13.0 has no
schema_version = 2: its stable commands read `load_config(base=cwd)`. Each case writes a project
whose root `ici.toml` is a native v2 file (plain, with a `[workspace]` table, or with an unknown
key), and runs `line`, `verify`, `doctor`, `export-compilation-context` or `publish` from `sub/`,
with no `sub/ici.toml`, a valid legacy one and an invalid legacy one. The valid file names
`source_dirs = ["lib"]` and `[doctor] required_tools = ["ruff"]`, so the configuration in use
shows in the line report's file inventory (`extra.all`, `extra.files_data`) and the doctor
policy. The root has a `.git` directory; HOME and XDG_CONFIG_HOME are empty temporary
directories and PATH is os.defpath. Output scrubs the project root as <root> and HOME as <home>,
as capture_cli_gaps.py does, and names frozen's HOME analysis cache entries by <digest>.

Recorded in reduced form: a saved line_report.json keeps `extra.all`/`extra.files_data`, a saved
verify_report.json keeps `suite_status` and each engine's (name, status, evidence, required);
`doctor --json` keeps required_tools, effective_required_tools and analysis_profile; a completed
verify's suite frame is omitted; a traceback keeps its last line.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
HOME = "<home>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
V2_ROOTS = {
    "v2": 'schema_version = 2\n[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n',
    "v2_workspace": 'schema_version = 2\n[workspace]\nname = "demo"\n',
    "v2_invalid": "schema_version = 2\nbogus = 1\n",
}
SOURCES = {
    "src/root.py": "".join(f"x{index} = {index}\n" for index in range(40)),
    "sub/src/inner.py": "def inner():\n    return 1\n",
    "sub/lib/lib.py": "def first():\n    return 1\n\n\ndef second():\n    return 2\n",
}
CWD_CONFIGS = {
    "no_cwd_config": None,
    "legacy_cwd": (
        '[project]\ntype = "python"\nsource_dirs = ["lib"]\n[doctor]\nrequired_tools = ["ruff"]\n'
    ),
    "invalid_cwd": '[project]\ntype = "python"\n[engines.line]\nwarn_limit = "many"\n',
}
DOCTOR_KEYS = ("required_tools", "effective_required_tools", "analysis_profile")
COMMANDS = {
    "line": ["line"],
    "line_report": ["line", "--report"],
    "verify_report": ["verify", "--report"],
    "doctor_json": ["doctor", "--json"],
    "export": ["export-compilation-context"],
    "publish": ["publish"],
}


def cases():
    found = {}
    for root_name, root_text in V2_ROOTS.items():
        for cwd_name, cwd_text in CWD_CONFIGS.items():
            tree = {"ici.toml": root_text, **SOURCES}
            if cwd_text is not None:
                tree["sub/ici.toml"] = cwd_text
            for command, argv in COMMANDS.items():
                found[f"{root_name}_{cwd_name}_{command}"] = (tree, "sub", argv)
    return found


def _scrub(text, base, home):
    return text.replace(str(base), ROOT).replace(str(home), HOME)


def _files(base, skip=()):
    if not base.exists():
        return []
    return sorted(
        str(path.relative_to(base))
        for path in base.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(base).parts
        and str(path.relative_to(base)) not in skip
    )


def _read_json(path, base, home):
    if not path.is_file():
        return None
    return json.loads(_scrub(path.read_text(encoding="utf-8"), base, home))


def _run(oracle, tree, cwd, argv):
    with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as temporary:
        home = Path(home_dir).resolve()
        base = Path(temporary).resolve() / "project"
        for file, text in tree.items():
            (base / file).parent.mkdir(parents=True, exist_ok=True)
            (base / file).write_text(text, encoding="utf-8")
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
            cwd=base / cwd,
            capture_output=True,
            text=True,
            env={"HOME": str(home), "XDG_CONFIG_HOME": str(home), "PATH": os.defpath},
            check=False,
            timeout=600,
        )
        reports = {}
        line = _read_json(base / cwd / "line_report.json", base, home)
        if line is not None:
            reports["line_report.json"] = {
                "all": line["extra"]["all"],
                "files_data": line["extra"]["files_data"],
            }
        verify = _read_json(base / cwd / "verify_report.json", base, home)
        if verify is not None:
            reports["verify_report.json"] = {
                "suite_status": verify["suite_status"],
                "engines": [
                    [item["engine_name"], item["status"], item["evidence"], item.get("required")]
                    for item in verify["results"]
                ],
            }
        stdout = _scrub(completed.stdout, base, home)
        stderr = _scrub(completed.stderr, base, home)
        recorded = {}
        if argv[0] == "doctor" and completed.returncode == 0:
            diagnosis = json.loads(stdout)
            recorded["doctor"] = {key: diagnosis[key] for key in DOCTOR_KEYS}
            stdout = "<doctor --json: the keys under doctor>"
        if argv[0] == "verify" and verify is not None:
            stdout = "<verify suite frame omitted>"
        if "Traceback (most recent call last)" in stderr:
            prefix = stderr.split("╭", 1)[0]
            stderr = prefix + "<traceback> " + stderr.rstrip("\n").splitlines()[-1] + "\n"
        return {
            "argv": argv,
            "cwd": cwd,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            **recorded,
            "written": _files(base, set(tree)),
            # Frozen's analysis cache entries are named by a digest over temporary paths.
            "home_written": sorted(
                {
                    re.sub(r"entries-v1/[0-9a-f]{64}\.json$", "entries-v1/<digest>.json", path)
                    for path in _files(home)
                }
            ),
            "reports": reports,
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "cases": {
            name: {"tree": tree, **_run(oracle, tree, cwd, argv)}
            for name, (tree, cwd, argv) in cases().items()
        },
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
