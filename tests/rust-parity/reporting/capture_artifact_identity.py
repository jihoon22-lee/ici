"""Development-only capture: frozen `ici next verify` identity of internal-check findings.

Reuses the `cpp.artifact` contract cases (tests/rust-parity/cpp/artifact-contract-oracle.json)
and records, for every finding, the envelope fingerprint with its analysis unit and variant, and
every check record's unit and variant. Never called by the Rust product.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
CASES = ("absent-directory", "mixed")
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in ("compile", "diagnostics", "tidy", "test", "coverage")
)


def config(case: dict) -> str:
    artifacts = ", ".join(json.dumps(pattern) for pattern in case["artifacts"])
    return (
        'schema_version = 1\n[workspace]\nname = "product"\n'
        '[builds.release]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        f'directory = "{case["directory"]}"\nvariant = "release"\nartifacts = [{artifacts}]\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\nbuild = "release"\n'
        f"{OFF}{case['extra']}"
    )


def materialize(project: Path, layout: list[str]) -> None:
    (project.parent / "outside").write_text("outside\n", encoding="utf-8")
    for entry in layout:
        kind, _, rest = entry.partition(":")
        if kind == "link":
            path, _, target = rest.partition("->")
            (project / path).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, project / path)
        elif kind == "dir":
            (project / rest).mkdir(parents=True, exist_ok=True)
        else:
            (project / rest).parent.mkdir(parents=True, exist_ok=True)
            (project / rest).write_bytes(b"\x7fELF fake")


def run(case: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="ici-artifact-identity-") as temporary:
        work = Path(temporary)
        project = work / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp/artifact-contract", project)
        (project / "ici.toml").write_text(config(case), encoding="utf-8")
        materialize(project, case["layout"])
        subprocess.run(
            [PYTHON, "-m", "ici", "next", "verify"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        with (project / ".ici/next/result.json").open(encoding="utf-8") as handle:
            document = json.load(handle)
    keys = ("rule_id", "fingerprint", "component_id", "analysis_unit_id", "variant", "task_id")
    return {
        "findings": sorted(
            ({key: finding.get(key) for key in keys} for finding in document["findings"]),
            key=lambda item: (item["rule_id"], item["fingerprint"]),
        ),
        "checks": sorted(
            (
                {
                    key: check.get(key)
                    for key in ("task_id", "check_id", "analysis_unit_id", "variant")
                }
                for check in document["checks"]
            ),
            key=lambda item: item["task_id"],
        ),
    }


def main() -> None:
    head = subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True)
    assert head.strip() == EXPECTED
    with (ROOT / "tests/rust-parity/cpp/artifact-contract-oracle.json").open(
        encoding="utf-8"
    ) as handle:
        contract = json.load(handle)["cases"]
    golden = {"oracle": EXPECTED, "cases": {name: run(contract[name]) for name in CASES}}
    (ROOT / "tests/rust-parity/reporting/artifact-identity.json").write_text(
        json.dumps(golden, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
