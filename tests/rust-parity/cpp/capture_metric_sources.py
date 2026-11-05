"""Development-only frozen legacy C++ metric captures over unusable sources; never Rust.

Runs the frozen CLI `ici cognitive --report` and `ici complexity --report` (type='cpp',
source_dirs=['src'], no compilation database) in a fresh copy of each
parity-fixtures/cpp/metric-sources tree with PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8:
`unreadable` has a source that is not UTF-8 beside a measurable one; `unterminated` a function
whose body never closes. The `empty` tree (a type='cpp' project with no source) runs
`ici exception --report` and `ici type --report` instead, the not-applicable C++ scope;
`unreadable` also runs `ici dup --report` and `ici dead --report` (frozen source intake stops
at the bad source, dead's without any compilation database); `unreadable-header` has a header
that is not UTF-8, which only dup and dead read. Generated `intake-nul` (a source with a NUL)
and `intake-too-large` (a source over the frozen 8 MiB bound), the inventory/count bounds and
`intake-database` (a non-UTF-8 source with a compilation database entry) and `excluded-only`
(sources only under excluded vendor/generated directories) run dup and dead; `empty` also runs
dup and dead; `dup-*` trees run the bounded duplicate normalization/comparison stops. Keeps
the exit code, status, evidence, required flag, summary and targets, and for dup/dead the whole
engine extra (the project root written as `<root>`).
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURES = ROOT / "parity-fixtures/cpp/metric-sources"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-metric-sources"
GOLDEN = ROOT / "tests/rust-parity/cpp/metric-sources-oracle.json"
CONFIG = '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
FIELDS = ("target_name", "status", "message", "file_path", "start_line", "end_line")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


OK_SOURCE = b"int ok(int x) {\n  if (x) { return 1; }\n  return 0;\n}\n"
# Trees generated at capture time (never committed): frozen intake faults past UTF-8 decoding.
GENERATED = {
    "intake-nul": {"src/nul.cpp": b"int a;\0\n", "src/ok.cpp": OK_SOURCE},
    "intake-too-large": {
        "src/big.cpp": b"// " + b"x" * (8 * 1024 * 1024) + b"\n",
        "src/ok.cpp": OK_SOURCE,
    },
    # Nine 7.5 MiB sources pass the per-file bound but exceed the 64 MiB inventory.
    "intake-inventory": {
        f"src/part{index}.cpp": b"// " + b"x" * (15 * 512 * 1024) + b"\n" for index in range(9)
    },
    # A recorded compilation database: frozen still reads the inventory before any compiler.
    "intake-database": {
        "ici.toml": CONFIG.encode() + b'compile_database = "compile_commands.json"\n',
        "compile_commands.json": (
            b'[{"directory": ".", "command": "g++ -std=c++17 -c src/ok.cpp -o build/ok.o",'
            b' "file": "src/ok.cpp"},'
            b' {"directory": ".", "command": "g++ -std=c++17 -c src/latin.cpp -o build/latin.o",'
            b' "file": "src/latin.cpp"}]\n'
        ),
        "src/ok.cpp": OK_SOURCE,
        "src/latin.cpp": b"// caf\xe9\nstatic int unused() { return 1; }\n",
    },
    # Every owned-looking source sits under an excluded vendor/generated directory.
    "excluded-only": {
        "src/third_party/v.cpp": OK_SOURCE,
        "src/generated/g.cpp": OK_SOURCE,
    },
    # An excluded vendor source sorting before an owned one.
    "excluded-mixed": {"src/_deps/v.cpp": OK_SOURCE, "src/ok.cpp": OK_SOURCE},
    "intake-too-many-files": {f"src/f{index:04}.cpp": b"int x;\n" for index in range(2049)},
    "intake-too-many-candidates": {
        **{f"src/third_party/v{index:04}.cpp": b"int x;\n" for index in range(8192)},
        "src/ok.cpp": OK_SOURCE,
    },
}


def functions(count: int) -> bytes:
    """`count` identical 14-line C++ function bodies (one shared duplicate window per body)."""

    body = "".join(f"  int v{j} = a * {j} + b - {j};\n" for j in range(12))
    return "".join(
        f"int f{i}(int a, int b) {{\n" + body + "  return v0;\n}\n\n" for i in range(count)
    ).encode()


# Generated `ici dup` trees: frozen bounded normalization/comparison stops for C++ sources.
GENERATED_DUP = {
    # 500,001 declarations exceed the per-file MAX_CPP_DUPLICATE_TOKENS tokenizer bound (dup.py:149).
    "dup-records-limit": {"src/a.cpp": "".join(f"int v{i};\n" for i in range(500_001)).encode()},
    # 600,000 one-token lines over two files exceed MAX_DUPLICATE_INDEXED_RECORDS=500000.
    "dup-indexed-records-limit": {
        "src/a.cpp": b"x\n" * 300_000,
        "src/b.cpp": b"x\n" * 300_000,
    },
    # dup.py:207 DuplicateComparisonLimit stops.
    "dup-window-occurrence-limit": {"src/a.cpp": functions(2_100)},
    "dup-raw-match-limit": {"src/a.cpp": functions(200)},
}
GENERATED.update(GENERATED_DUP)


def run(name: str, command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        if name in GENERATED:
            for relative, payload in GENERATED[name].items():
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_bytes(payload)
        else:
            shutil.copytree(FIXTURES / name, project)
        if not (project / "ici.toml").exists():
            (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (WORK / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        text = (project / f"{command}_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    case = {
        "exit_code": result.returncode,
        "status": report["status"],
        "evidence": report["evidence"],
        "required": report["required"],
        "summary": report["summary"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }
    if command in ("dup", "dead"):
        case["extra"] = report["extra"]
    return case


COMMANDS = {
    "empty": ("exception", "type", "dup", "dead"),
    "unreadable": ("cognitive", "complexity", "dup", "dead"),
    "unreadable-header": ("cognitive", "complexity", "dup", "dead"),
}
cases = {
    f"{command}:{path.name}": run(path.name, command)
    for path in sorted(FIXTURES.iterdir())
    if path.is_dir()
    for command in COMMANDS.get(path.name, ("cognitive", "complexity"))
}
cases.update(
    {
        f"{command}:{name}": run(name, command)
        for name in GENERATED
        for command in (("dup",) if name in GENERATED_DUP else ("dup", "dead"))
    }
)
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "config": CONFIG, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} metric source cases")
