"""Development-only capture: frozen legacy `ici python-compat --report` targets and findings.

Runs a private copy of the released pyz (checked as in capture_legacy_launcher_interpreter.py),
so an empty `interpreters` list checks the launcher's interpreter exactly as frozen did. Each
scenario exercises one `engines/python_compat.py` branch: Verified, optional/required
Unavailable, VersionUnavailable, Incompatible (requires-python and import smoke),
SyntaxUnavailable, AnalysisUnavailable and NotApplicable, plus a refused target_version.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from capture_integration_legacy import DISABLED, SOURCE
from capture_legacy_launcher_interpreter import PYZ_SHA256, Paths, checked_pyz

APP = "import json\n\n\ndef f():\n    return json.dumps({})\n"
SILENT = "#!/bin/sh\nexit 0\n"


def config(extra: str = "") -> str:
    text = '[ici]\nprofile = "standard"\n[project]\ntype = "python"\nsource_dirs = ["."]\n'
    text += f"[engines]\n[engines.python_compat]\nenabled = true\n{extra}"
    for name in DISABLED:
        if name != "python_compat":
            text += f"[engines.{name}]\nenabled = false\n"
    return text


PYPROJECT_313 = '[project]\nname = "demo"\nversion = "1.0"\nrequires-python = ">=3.13"\n'
# scenario: (extra [engines.python_compat] lines, files)
SCENARIOS = {
    "verified_launcher": ("", {"app.py": APP}),
    "matrix_optional_unavailable": (
        'interpreters = ["<BARE>", "/nonexistent/python3.99"]\nrequired_interpreters = ["<BARE>"]\n',
        {"app.py": APP},
    ),
    "required_unavailable": (
        'interpreters = ["/nonexistent/python3.99"]\n'
        'required_interpreters = ["/nonexistent/python3.99"]\n',
        {"app.py": APP},
    ),
    "version_unavailable": (
        'interpreters = ["<SILENT>"]\nrequired_interpreters = ["<SILENT>"]\n',
        {"app.py": APP},
    ),
    "requires_python_incompatible": (
        'interpreters = ["<BARE>"]\nrequired_interpreters = ["<BARE>"]\n',
        {"app.py": APP, "pyproject.toml": PYPROJECT_313},
    ),
    "import_smoke_failure": (
        'interpreters = ["<BARE>"]\nrequired_interpreters = ["<BARE>"]\n'
        'imports = ["app", "missing_module"]\n',
        {"app.py": APP},
    ),
    "syntax_unavailable": ("", {"app.py": APP, "broken.py": "def broken(:\n    pass\n"}),
    "target_version_config_error": ('target_version = "bogus"\n', {"app.py": APP}),
    "analysis_unavailable_toml": ("", {"app.py": APP, "pyproject.toml": "[project\n"}),
    "analysis_unavailable_requires": (
        "",
        {"app.py": APP, "pyproject.toml": '[project]\nname = "demo"\nrequires-python = 3\n'},
    ),
    "not_applicable": ("", {"README.md": "docs only\n"}),
}


def capture(pyz: Path, paths: Paths, silent: Path, extra: str, files: dict) -> dict:
    bare = str(Path(paths.values["<BARE_BIN>"]) / "python3.12")
    text = config(extra).replace("<BARE>", bare).replace("<SILENT>", str(silent))
    with tempfile.TemporaryDirectory(prefix="ici-compat-") as temporary:
        root = Path(temporary).resolve()
        (root / "ici.toml").write_text(text, encoding="utf-8")
        for name, body in files.items():
            (root / name).write_text(body, encoding="utf-8")
        completed = subprocess.run(
            [str(pyz), "python-compat", "--report"],
            cwd=root,
            env={"HOME": str(paths.home), "PATH": paths.values["<BARE_BIN>"]},
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        report_path = root / "python_compat_report.json"
        if not report_path.exists():
            stderr = completed.stdout + completed.stderr
            return {"exit_code": completed.returncode, "config": config(extra), "files": files} | {
                "output": paths.normal(stderr.strip(), root)
            }
        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
    for evidence in report.get("tool_evidence", []):
        evidence.pop("duration_seconds", None)
    view = {
        "exit_code": completed.returncode,
        "config": config(extra),
        "files": files,
    } | {
        key: report[key]
        for key in (
            "status",
            "summary",
            "evidence",
            "required",
            "targets",
            "findings",
            "extra",
            "tool_evidence",
        )
    }
    text = json.dumps(view).replace(str(silent), "<SILENT>").replace(bare, "<BARE>")
    return json.loads(paths.normal(text, root))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--pyz", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bare", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ici-compat-work-") as temporary:
        work = Path(temporary)
        pyz = work / "ici.pyz"
        shutil.copyfile(args.pyz, pyz)
        pyz.chmod(0o755)
        checked_pyz(pyz, args.oracle)
        paths = Paths(work, args.python, args.bare)
        silent = work / "silent-python"
        silent.write_text(SILENT, encoding="utf-8")
        silent.chmod(0o755)
        runs = {
            name: capture(pyz, paths, silent, extra, files)
            for name, (extra, files) in SCENARIOS.items()
        }
    document = {"source": SOURCE, "pyz_sha256": PYZ_SHA256, "silent": SILENT, "runs": runs}
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
