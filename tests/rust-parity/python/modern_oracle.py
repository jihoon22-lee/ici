"""Capture or verify frozen modern Python check results end to end (development only).

Each case is a small project analyzed by the immutable v0.13.0 `ici analyze --json`
command. The recorded result keeps check states, findings (rule identity, message,
severity, confidence, category, evidence, location and attributed fingerprint),
measurements, limitations, the gate and the exit code. The native Rust test
`crates/ici-cli/tests/python_modern_parity.rs` replays the same projects and
compares them; explicit corrections live beside the original observations and are
never produced by regenerating this file.

    python3 -I -B modern_oracle.py FROZEN_CHECKOUT --python FROZEN_PYTHON [--write]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
FIXTURE = Path(__file__).with_name("modern-findings.json")
ALL_CHECKS = [
    "line",
    "lint",
    "format",
    "type",
    "test",
    "coverage",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
    "compat-runtime",
]
STATIC = [
    "line",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
]

HYGIENE = """import hashlib
import os
import pickle
import random
import subprocess

PASSWORD = "hunter2hunter2xx"
API_TOKEN = "Zx9Qw8Er7Ty6Ui5Op4As3Df2"  # nosec
KEY = "-----BEGIN RSA PRIVATE KEY-----"


def digest(data):
    weak = hashlib.md5(data)
    other = hashlib.sha1(data)
    fine = hashlib.md5(data, usedforsecurity=False)
    return weak, other, fine


def run(command, payload):
    os.system(command)
    subprocess.run(command, shell=True)
    subprocess.run([command], shell=False)
    eval(command)
    token = random.choice("abc")
    return pickle.loads(payload), token


def shadowed(eval, text):
    return eval(text)


def handlers(value):
    try:
        return int(value)
    except:
        pass
    try:
        return float(value)
    except BaseException:
        return None


def rethrow(value):
    try:
        return value()
    except ValueError as error:
        raise RuntimeError("failed")


def resources(path, items=[]):
    handle = open(path)
    data = handle.read()
    with open(path) as managed:
        managed.read()
    return data, items
"""

METRICS = (
    "def busy(a, b, c, d):\n"
    + "".join(
        f"    if a > {i}:\n        if b:\n            if c:\n                if d:\n"
        f"                    if a and b or c and d:\n                        a += {i}\n"
        for i in range(6)
    )
    + "    return a\n\n\ndef calm(x):\n    return x\n"
)

DUPLICATE_BODY = """    total = 0
    for item in values:
        if item > limit:
            total += item * 2
        else:
            total -= item
        print(item, total, limit)
    return total
"""

LONG = "".join(f"value_{i} = {i}\n" for i in range(520))
HUGE = "".join(f"value_{i} = {i}\n" for i in range(1010))

CASES: list[dict] = [
    {"id": "hygiene-rules", "files": {"main.py": HYGIENE}, "checks": STATIC},
    {
        "id": "invalid-syntax-and-valid-file",
        "files": {"main.py": "def ok():\n    return 1\n", "bad.py": "def broken(:\n    pass\n"},
        "checks": STATIC,
    },
    {
        "id": "generated-and-vendor-exclusions",
        "files": {
            "main.py": "import pickle\n\n\ndef load(x):\n    return pickle.loads(x)\n",
            "vendor/lib.py": "import pickle\n\n\ndef load(x):\n    return pickle.loads(x)\n",
            "generated/api.py": "def _hidden():\n    return 1\n",
            "ui_form.py": "PASSWORD = 'hunter2hunter2xx'\n",
        },
        "checks": STATIC,
    },
    {
        "id": "all-sources-excluded",
        "files": {
            "vendor/lib.py": "import pickle\n\n\ndef load(x):\n    return pickle.loads(x)\n",
            "generated/api.py": "def _hidden():\n    return 1\n",
        },
        "checks": STATIC,
    },
    {"id": "metric-thresholds", "files": {"main.py": METRICS}, "checks": STATIC},
    {
        "id": "import-cycle",
        "files": {
            "a.py": "import b\n\n\ndef fa():\n    return b.fb\n",
            "b.py": "import a\n\n\ndef fb():\n    return a.fa\n",
            "pkg/__init__.py": "from . import mod\n",
            "pkg/mod.py": "from pkg import other\n",
            "pkg/other.py": "from . import mod\n",
        },
        "checks": ["cycle", "dead", "line"],
    },
    {
        "id": "duplicate-functions",
        "files": {
            "one.py": "def first(values, limit):\n" + DUPLICATE_BODY,
            "two.py": "def second(values, limit):\n" + DUPLICATE_BODY,
        },
        "checks": ["dup", "line"],
    },
    {
        "id": "semantic-shape-only-clone",
        "files": {
            "left.py": "def left(value):\n"
            + "".join(f"    step_{i} = (\n        value\n        + {i}\n    )\n" for i in range(8))
            + "    return step_0 * step_7\n",
            "right.py": "def right(amount):\n"
            + "".join(f"    part_{i} = amount + {i}\n" for i in range(8))
            + "    return part_0 * part_7\n",
        },
        "checks": ["dup"],
    },
    {
        "id": "dead-private-and-unreachable",
        "files": {
            "main.py": (
                "def _unused():\n    return 1\n\n\ndef _used():\n    return 2\n\n\n"
                "def public():\n    return _used()\n    print('never')\n"
            ),
        },
        "checks": ["dead"],
    },
    {
        "id": "compat-floor",
        "files": {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.8"\n',
            "main.py": (
                "import tomllib\n\n\ndef parse(value):\n    if (n := len(value)) > 1:\n"
                "        match n:\n            case 2:\n                return tomllib\n"
                "    return value.removeprefix('x')\n"
            ),
        },
        "checks": ["compat"],
    },
    {
        "id": "compat-without-floor",
        "files": {"main.py": "def f(x):\n    match x:\n        case 1:\n            return 1\n"},
        "checks": ["compat"],
    },
    {
        "id": "line-file-size",
        "files": {"long.py": LONG, "huge.py": HUGE, "small.py": "x = 1\n"},
        "checks": ["line"],
    },
    {
        "id": "documentation-only-tree",
        "files": {"README.md": "# demo\n\ntext\n", "docs/guide.md": "guide\n"},
        "checks": STATIC,
    },
    {
        "id": "non-ascii-columns",
        "files": {
            "main.py": (
                "import pickle\n\n\ndef 읽기(값):\n    결과 = pickle.loads(값)\n"
                "    try:\n        return 결과\n    except:\n        pass\n"
            )
        },
        "checks": ["security", "exception", "dead", "line"],
    },
    {
        "id": "latin1-cookie-source",
        "files": {"main.py": "# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n"},
        "encoding": "latin-1",
        "checks": STATIC,
    },
    {
        "id": "empty-and-comment-only-files",
        "files": {"__init__.py": "", "notes.py": "# only a comment\n\n"},
        "checks": STATIC,
    },
]


def config(checks: list[str]) -> str:
    lines = [
        "schema_version=1",
        "[workspace]",
        'name="oracle"',
        'profile="fast"',
        "[[components]]",
        'id="project"',
        'root="."',
        'languages=["python"]',
    ]
    # Only disabled checks get a table: an explicit root check table makes the
    # old composition default `required` to false, which is a separate config
    # contract and would hide the selected checks' gate effect here.
    for check in ALL_CHECKS:
        if check not in checks:
            lines += [f'[checks."python.{check}"]', "enabled=false"]
    return "\n".join(lines) + "\n"


def materialize(root: Path, case: dict) -> None:
    (root / "ici.toml").write_text(config(case["checks"]), encoding="utf-8")
    for relative, text in case["files"].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(case.get("encoding", "utf-8")))


def normalize(document: dict) -> dict:
    return {
        "checks": [
            {key: check.get(key) for key in ("check_id", "state", "evidence_complete")}
            for check in document["checks"]
        ],
        "findings": [
            {key: value for key, value in finding.items() if key != "task_id"}
            for finding in document["findings"]
        ],
        "metrics": document["metrics"],
        "limitations": document["limitations"],
        "gate": document["gate"],
    }


def run_case(oracle: Path, python: Path, case: dict) -> dict:
    env = dict(os.environ, PYTHONPATH=str(oracle / "src"), PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory(prefix="ici-modern-") as temporary:
        root = Path(temporary)
        materialize(root, case)
        completed = subprocess.run(
            [str(python), "-m", "ici", "analyze", "--json"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        document = json.loads(completed.stdout)
    return {"exit_code": completed.returncode, **normalize(document)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("oracle", type=Path)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    oracle = args.oracle.resolve()
    hashes = {
        relative: hashlib.sha256((oracle / relative).read_bytes()).hexdigest()
        for relative in (
            "src/ici/languages/hygiene.py",
            "src/ici/languages/metrics.py",
            "src/ici/languages/deadcode.py",
            "src/ici/languages/cycles.py",
            "src/ici/languages/duplicates.py",
            "src/ici/languages/compat.py",
            "src/ici/languages/python/lines.py",
        )
    }
    observed = {case["id"]: run_case(oracle, args.python, case) for case in CASES}
    if args.write:
        previous = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
        cases = []
        for case in CASES:
            prior = next(
                (item for item in previous.get("cases", []) if item["id"] == case["id"]), {}
            )
            cases.append(
                {
                    **{key: case[key] for key in ("id", "files", "checks")},
                    "encoding": case.get("encoding", "utf-8"),
                    "oracle": observed[case["id"]],
                    "corrections": prior.get("corrections", {}),
                }
            )
        FIXTURE.write_text(
            json.dumps(
                {
                    "source": SOURCE,
                    "oracle_hashes": hashes,
                    "out_of_scope_limitations": previous.get("out_of_scope_limitations", []),
                    "cases": cases,
                },
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["source"] == SOURCE
    assert fixture["oracle_hashes"] == hashes
    for case in fixture["cases"]:
        definition = next(item for item in CASES if item["id"] == case["id"])
        assert case["files"] == definition["files"], case["id"]
        assert case["oracle"] == observed[case["id"]], case["id"]
    print(f"{len(fixture['cases'])} frozen modern Python projects match the recorded oracle")


if __name__ == "__main__":
    main()
