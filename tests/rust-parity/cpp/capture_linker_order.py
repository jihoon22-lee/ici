"""Capture the real cmake and g++ invocation order of frozen v0.13.0 `ici dead`.

Wrappers named cmake, g++ and c++ first on PATH append each invocation to a marker log. Keeps the
ordered CONFIGURE/BUILD/UNUSED/compile/link events, the exit code, the dead engine's verdict and
targets and whether a project custom command ran. `verify-*` trees run `ici verify`. `*replay-fails*` trees fail the compiler scope's -Wunused-function replay.
"""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/linker-order-oracle.json"
FIXTURES = ROOT / "parity-fixtures/cpp"
CASES = {
    "unused-broken-with-linker": "linker-dead",
    "unused-link-fails-with-linker": "linker-dead",
    "coverage-required-with-unused": "linker-dead",
    "unused-replay-fails-with-linker": "linker-dead",
    "unused-and-linker": "linker-dead",
    "broken-unused-required": "linker-dead",
    "broken-unused-auto": "linker-dead",
    "custom-command-control": "linker-order",
    "custom-command-broken": "linker-order",
    "verify-replay-fails": "linker-order",
    "verify-custom-command-broken": "linker-order",
    "verify-custom-command-control": "linker-order",
    "verify-control": "linker-order",
    "verify-broken-required": "linker-order",
    "verify-broken-auto": "linker-order",
    "verify-deep-control": "linker-order",
    "verify-deep-broken": "linker-order",
    "verify-deep-build-disabled": "linker-order",
    "verify-linker-only-control": "linker-order",
    "verify-linker-only-broken": "linker-order",
}

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def label(line: str) -> str | None:
    tool, _, rest = line.rstrip("\n").partition("\t")
    cwd, _, args = rest.partition("\t")
    tokens = args.split()
    role = "shadow" if "link-reachability" in cwd or "link-reachability" in args else "release"
    if tool == "cmake":
        if "--build" in tokens:
            return f"BUILD:{role}"
        if "--version" in tokens or "-E" in tokens:
            return None
        return f"CONFIGURE:{role}" if any(t.startswith(("-S", "-B")) for t in tokens) else None
    if any(flag in args for flag in ("--version", "-dumpversion", "-dumpmachine")):
        return None
    if "-Wunused-function" in tokens:
        return "UNUSED:" + next(t for t in reversed(tokens) if t.endswith(".cpp")).split("/")[-1]
    if any(mark in args for mark in ("CMakeCXXCompiler", "CMakeTmp", "CMakeScratch", "TryCompile")):
        return None
    match = re.search(r"(?:^| )-c (\S+\.cpp)", args)
    if match:
        return "compile:" + match.group(1).split("/")[-1] + f":{role}"
    return f"link:{role}" if ".o" in args and "-o" in tokens else None


def custom_runs(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def output_files(project: Path) -> list:
    """The published release tree (`v<version>/`): relative path and content digest."""
    return sorted(
        [str(path.relative_to(project)), hashlib.sha256(path.read_bytes()).hexdigest()]
        for top in project.glob("v[0-9]*")
        for path in top.rglob("*")
        if path.is_file()
    )


def engine_digest(engine: dict, work: Path) -> list:
    """Temporary roots differ in length, which moves where a long message is truncated."""

    def message(text: str) -> str:
        return text.replace(str(work), "<WORK>")[:120]

    return [
        engine["engine_name"],
        engine["status"],
        engine["evidence"],
        engine["summary"],
        [[item["target_name"], item["status"]] for item in engine["targets"]],
        sorted(
            [
                item["rule_id"],
                message(item["message"]),
                item["primary_location"]["path"],
                item["primary_location"]["start_line"],
            ]
            for item in engine["findings"]
        ),
        sorted(item["name"] for item in engine["tool_evidence"]),
        sorted(
            [artifact["id"], artifact["kind"], artifact["target"]]
            for manifest in engine["artifact_manifests"]
            for artifact in manifest["artifacts"]
        ),
    ]


def run(name: str, family: str) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary).resolve()
        project, tools, marker = work / "project", work / "tools", work / "marker.log"
        shutil.copytree(FIXTURES / family / name, project)
        tools.mkdir()
        (work / "home").mkdir()
        fail = (
            'case " $* " in *" -Wunused-function "*) exit 1;; esac\n'
            if "replay-fails" in name
            else ""
        )
        for tool, extra in (("cmake", ""), ("g++", fail)):
            path = tools / tool
            path.write_text(
                f"#!/bin/sh\nprintf '%s\\t%s\\t%s\\n' {tool} \"$PWD\" \"$*\" >> '{marker}'\n"
                f'{extra}exec /usr/bin/{tool} "$@"\n',
                encoding="utf-8",
            )
            path.chmod(0o755)
        (tools / "c++").symlink_to("g++")
        command = "verify" if name.startswith("verify-") else "dead"
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": f"{tools}:/usr/bin:/bin",
                "HOME": str(work / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
        engine = (
            next(item for item in report["results"] if item["engine_name"] == "dead")
            if command == "verify"
            else report
        )
        return {
            "exit_code": result.returncode,
            "status": engine["status"],
            "evidence": engine["evidence"],
            "required": engine["required"],
            "summary": engine["summary"],
            "targets": [[item["target_name"], item["status"]] for item in engine["targets"]],
            "tool_names": sorted(item["name"] for item in engine["tool_evidence"]),
            "findings": sorted(
                [
                    item["rule_id"],
                    item["message"],
                    item["primary_location"]["path"],
                    item["primary_location"]["start_line"],
                ]
                for item in engine["findings"]
            ),
            "other_engines": [
                engine_digest(item, work)
                for item in report.get("results", [])
                if item["engine_name"] != "dead"
            ],
            "events": [event for event in map(label, lines) if event],
            "custom_runs": custom_runs(work / "custom-ran"),
            "output_files": output_files(project),
        }


GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "cases": {name: run(name, family) for name, family in CASES.items()}},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(CASES)} linker order cases")
