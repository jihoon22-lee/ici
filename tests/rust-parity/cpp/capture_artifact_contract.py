"""Development-only frozen `cpp.artifact` contract captures; never called by the Rust product."""

import hashlib
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
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-artifact-capture"
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in ("compile", "diagnostics", "tidy", "test", "coverage")
)
# Layout entries: "dir:<path>", "file:<path>", "link:<path>-><target>" under the project;
# "outside" is a regular file next to the project, outside the workspace.
CASES = {
    "mixed": {
        "directory": "build",
        "artifacts": ["bin/app", "lib/*.a", "dirs/*", "ok/*.so"],
        "layout": [
            "dir:build/bin",
            "link:build/lib/dangling.a->../../missing/app",
            "link:build/lib/outside.a->../../../outside",
            "dir:build/dirs/sub",
            "file:build/ok/a.so",
            "file:build/ok/b.so",
        ],
        "extra": "",
    },
    "absent-directory": {
        "directory": "build",
        "artifacts": ["bin/app"],
        "layout": [],
        "extra": "",
    },
    "satisfied": {
        "directory": "out/release",
        "artifacts": ["bin/app"],
        "layout": ["file:out/release/bin/app"],
        "extra": "",
    },
    "required-broken": {
        "directory": "build",
        "artifacts": ["bin/app"],
        "layout": ["dir:build"],
        "extra": '[checks."cpp.artifact"]\nrequired = true\n',
    },
    "root-directory": {
        "directory": ".",
        "artifacts": ["bin/app"],
        "layout": [],
        "extra": "",
    },
}


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


def unit_fingerprint(finding: dict, raw: str) -> str:
    """The frozen attribution payload with analysis unit `app.cpp` (Rust attributes one)."""
    span = finding["primary_location"]
    payload = (
        "ici.next.fingerprint.v3",
        finding["component_id"],
        "app.cpp",
        None,
        finding["provider"],
        finding["rule_id"],
        raw,
        finding["message"],
        span["path"],
        span["start_line"],
        span.get("end_line"),
        span.get("start_column"),
        span.get("end_column"),
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return "sha256:" + digest


def raw_fingerprint(finding: dict, case: dict, project: Path) -> str:
    """Recover the frozen raw `artifact-<sha1[:16]>` id by matching the envelope id."""
    build = "release"
    identities = [f"missing:{build}:{pattern}" for pattern in case["artifacts"]]
    identities += [
        f"invalid:{build}:{entry.partition(':')[2].partition('->')[0]}"
        for entry in case["layout"]
        if entry.startswith("link:")
    ]
    for identity in identities:
        raw = "artifact-" + hashlib.sha1(identity.encode(), usedforsecurity=False).hexdigest()[:16]
        span = finding["primary_location"]
        payload = (
            "ici.next.fingerprint.v3",
            finding["component_id"],
            None,
            None,
            finding["provider"],
            finding["rule_id"],
            raw,
            finding["message"],
            span["path"],
            span["start_line"],
            span.get("end_line"),
            span.get("start_column"),
            span.get("end_column"),
        )
        digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
        if finding["fingerprint"] == "sha256:" + digest:
            return raw
    raise AssertionError(f"unrecognized frozen fingerprint in {project}: {finding}")


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp/artifact-contract", project)
        (project / "ici.toml").write_text(config(case), encoding="utf-8")
        materialize(project, case["layout"])
        result = subprocess.run(
            [PYTHON, "-m", "ici", "next", "verify"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        document_path = project / ".ici/next/result.json"
        if not document_path.exists():
            return {
                "exit_code": result.returncode,
                "output": (result.stdout + result.stderr).strip(),
            }
        document = json.loads(document_path.read_text(encoding="utf-8"))
        findings = []
        for finding in document["findings"]:
            if not finding["rule_id"].startswith("artifact."):
                continue
            raw = raw_fingerprint(finding, case, project)
            findings.append(
                {
                    **{
                        key: finding.get(key)
                        for key in (
                            "rule_id",
                            "message",
                            "primary_location",
                            "severity",
                            "confidence",
                            "evidence",
                            "provider",
                            "component_id",
                            "task_id",
                        )
                    },
                    "raw_fingerprint": raw,
                    "fingerprint_with_unit": unit_fingerprint(finding, raw),
                }
            )
        metrics = [
            {key: metric.get(key) for key in ("name", "value", "numerator", "denominator")}
            for metric in document["metrics"]
            if metric["name"].startswith("artifacts.")
        ]
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "gate": document["gate"]["selected"],
        "findings": findings,
        "metrics": metrics,
    }


oracle = {
    "oracle": EXPECTED,
    "cases": {name: {**case, "frozen": run(case)} for name, case in CASES.items()},
}
(ROOT / "tests/rust-parity/cpp/artifact-contract-oracle.json").write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} artifact contract cases")
