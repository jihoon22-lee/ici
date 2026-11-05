"""Development-only frozen `ici next plan --json`/`next verify --json` captures of next-path
C++ binary compatibility.

Never called by the Rust product. Frozen `application/testing.expand_cpp_binary_compat`
turns every ELF file the linked build's `artifacts` globs match into its own readelf task
`<task>.<build>-<n>` (numbered in glob order across patterns); a component whose contract
matches no ELF file is one blocked task ("the artifact contract matched no ELF binaries — …"),
and without `readelf` on PATH the check is blocked ("readelf is not available"). Each finished
readelf run is parsed by `adapters/providers/binarycompat.BinaryCompatProvider` with the fixed
stable default policy (absolute loader entries forbidden) and reports the measured ELF facts
as a limitation.

`readelf` is a fake tool written by the case itself (a shell script in the pinned PATH
directory `tools/` inside the project) that prints a fixed transcript per artifact, and the
artifacts are files that start with the ELF magic, so the same bytes run under the frozen
oracle here and under the native replay (`crates/ici-cli/tests/next_binary_compat.rs`). Only
the project and work directories are normalized (`{root}`, `{work}`); whole JSON documents
are kept.
"""

import json
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/next-binary-compat-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-binary-compat-capture"
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in (
        "compile",
        "diagnostics",
        "tidy",
        "test",
        "coverage",
        "cycle",
        "line",
        "complexity",
        "cognitive",
        "dup",
        "exception",
    )
)
HEAD = 'schema_version = 1\n[workspace]\nname = "product"\n'
COMPONENT = (
    '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nbuild = "native"\n'
    'sources = ["src/**"]\n'
)


def config(artifacts: list[str], *, required: bool = False) -> str:
    return (
        HEAD + '[builds.native]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "out"\nvariant = "release"\nprepare = "explicit"\n'
        f"artifacts = {json.dumps(artifacts)}\n"
        + COMPONENT
        + OFF
        + ('[checks."cpp.binary-compat"]\nrequired = true\n' if required else "")
    )


ELF = "\x7fELF\x02\x01\x01\x00fake object\n"
FILES = {
    "CMakeLists.txt": "project(p CXX)\nadd_executable(app src/main.cpp)\n",
    "src/main.cpp": "int main() { return 0; }\n",
}
HEADER = [
    "ELF Header:",
    "  Class:                             ELF64",
    "  Data:                              2's complement, little endian",
    "  Type:                              DYN (Position-Independent Executable file)",
    "  Machine:                           Advanced Micro Devices X86-64",
    "",
    "Section Headers:",
    "  [Nr] Name              Type            Address          Off    Size   ES Flg Lk Inf Al",
    "  [ 1] .interp           PROGBITS        0000000000000318 000318 00001c 00   A  0   0  1",
    "  [27] .symtab           SYMTAB          0000000000000000 003040 000360 18     28  18  8",
    "",
    "Dynamic section at offset 0x2dc8 contains 2 entries:",
    "  Tag        Type                         Name/Value",
    " 0x0000000000000001 (NEEDED)             Shared library: [libstdc++.so.6]",
    " 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]",
]
VERSIONS = [
    "",
    "Version needs section '.gnu.version_r' contains 2 entries:",
    "  000000: Version: 1  File: libstdc++.so.6  Cnt: 2",
    "  0x0010:   Name: GLIBCXX_3.4.21  Flags: none  Version: 4",
    "  0x0020:   Name: CXXABI_1.3.9  Flags: none  Version: 3",
    "  0x0030: Version: 1  File: libc.so.6  Cnt: 2",
    "  0x0040:   Name: GLIBC_2.34  Flags: none  Version: 2",
    "  0x0050:   Name: GLIBC_2.2.5  Flags: none  Version: 5",
]
CLEAN = HEADER + VERSIONS
RPATH = [
    *HEADER,
    " 0x000000000000001d (RUNPATH)            Library runpath: [/opt/vendor/lib:$ORIGIN/../lib]",
    " 0x000000000000000f (RPATH)              Library rpath: [{root}/out/lib]",
    "",
    "Version needs section '.gnu.version_r' contains 1 entry:",
    "  000000: Version: 1  File: libc.so.6  Cnt: 1",
    "  0x0010:   Name: GLIBC_2.17  Flags: none  Version: 2",
]
# Loader entries under the project root but outside the build directory `out`.
ROOT_RPATH = [
    *HEADER,
    " 0x000000000000001d (RUNPATH)            Library runpath: [{root}/vendor/lib]",
]
STATIC = [
    "ELF Header:",
    "  Class:                             ELF32",
    "  Type:                              EXEC (Executable file)",
    "  Machine:                           Intel 80386",
    "",
    "There is no dynamic section in this file.",
]


def readelf(arms: dict[str, tuple[list[str], list[str], int]], raw: dict[str, str] = {}) -> str:  # noqa: B006
    """A fake readelf answering per artifact basename: (stdout lines, stderr lines, exit), or
    a raw shell body per basename."""

    body = ['#!/bin/sh\nfor last; do :; done\ncase "${last##*/}" in\n']
    for name, text in raw.items():
        body.append(f"{name})\n{text}\n;;\n")
    for name, (out, err, code) in arms.items():
        body.append(f"{name})\n")
        body.extend(f"printf '%s\\n' {shlex.quote(line)}\n" for line in out)
        body.extend(f"printf '%s\\n' {shlex.quote(line)} >&2\n" for line in err)
        body.append(f"exit {code}\n;;\n")
    body.append("esac\necho unexpected >&2\nexit 99\n")
    return "".join(body)


# Each case: config, project files, ELF-looking artifact files, fake tools in the PATH dir.
CASES = {
    "readelf-missing": {
        "config": config(["bin/*"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF},
        "tools": {},
    },
    "no-elf": {
        "config": config(["bin/*"]),
        "files": {**FILES, "out/bin/notes.txt": "plain text\n"},
        "artifacts": {},
        "tools": {"readelf": readelf({})},
    },
    "no-match": {
        "config": config(["bin/none*"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF},
        "tools": {"readelf": readelf({})},
    },
    "one-clean": {
        "config": config(["bin/app"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF},
        "tools": {"readelf": readelf({"app": (CLEAN, [], 0)})},
    },
    "several": {
        # Two patterns: numbering runs across both in glob order; a non-ELF file and a
        # directory are skipped without a number.
        "config": config(["bin/*", "lib/*.so"]),
        "files": {**FILES, "out/bin/readme.txt": "text\n", "out/bin/sub/keep": "x\n"},
        "artifacts": {
            "out/bin/app": ELF,
            "out/bin/tool": ELF,
            "out/lib/libplug.so": ELF,
        },
        "tools": {
            "readelf": readelf(
                {
                    "app": (CLEAN, [], 0),
                    "tool": (STATIC, [], 0),
                    "libplug.so": (RPATH, [], 0),
                }
            )
        },
    },
    "readelf-fails": {
        "config": config(["bin/*"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF, "out/bin/next": ELF},
        "tools": {
            "readelf": readelf(
                {
                    "app": ([], ["readelf: Error: Not an ELF file - it has the wrong magic"], 1),
                    "next": (["garbage only"], [], 0),
                }
            )
        },
    },
    "required-fails": {
        # The same two failing artifacts with the check required: each failing step is a
        # gate reason under its own task id.
        "config": config(["bin/*"], required=True),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF, "out/bin/next": ELF},
        "tools": {
            "readelf": readelf(
                {
                    "app": ([], ["readelf: Error: Not an ELF file - it has the wrong magic"], 1),
                    "next": (["garbage only"], [], 0),
                }
            )
        },
    },
    "required-clean": {
        "config": config(["bin/app"], required=True),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF},
        "tools": {"readelf": readelf({"app": (ROOT_RPATH, [], 0)})},
    },
    "unfinished": {
        # Output past frozen's 1,000,000-byte default bound, and a readelf killed by a signal:
        # neither run is parsed.
        "config": config(["bin/*"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF, "out/bin/next": ELF},
        "tools": {
            "readelf": readelf(
                {},
                {
                    # Shell builtins only: PATH holds just the fake tools.
                    "app": "i=0\nwhile [ $i -lt 1100 ]; do printf '%01000d' 0; i=$((i+1)); done\nexit 0",
                    "next": "kill -9 $$",
                },
            )
        },
    },
    "overlap": {
        # The same file matched by two patterns is inspected (and numbered) twice.
        "config": config(["bin/*", "bin/app"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF},
        "tools": {"readelf": readelf({"app": (CLEAN, [], 0)})},
    },
    "conflicting-output": {
        # Corrupted transcripts frozen `_elf.parse_readelf` accepts: a repeated ELF header with
        # another value (frozen keeps the last) and a version component past u32 (Python int).
        "config": config(["bin/*"]),
        "files": FILES,
        "artifacts": {"out/bin/app": ELF, "out/bin/wide": ELF},
        "tools": {
            "readelf": readelf(
                {
                    "app": (
                        [*CLEAN, "  Class:                             ELF32"],
                        [],
                        0,
                    ),
                    "wide": (
                        [*HEADER, "  0x0010:   Name: GLIBC_2.4294967296  Flags: none  Version: 2"],
                        [],
                        0,
                    ),
                }
            )
        },
    },
}


def write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        bin_dir = project / "tools"
        bin_dir.mkdir(parents=True)
        for name, text in {**case["files"], **case["artifacts"]}.items():
            write(project / name, text)
        for name, text in case["tools"].items():
            write(bin_dir / name, text.replace("{root}", str(project)), executable=True)
        write(project / "ici.toml", case["config"])
        captured = {}
        for command in ("plan", "verify"):
            result = subprocess.run(
                [PYTHON, "-m", "ici", "next", command, "--json"],
                cwd=project,
                env={
                    "PATH": str(bin_dir),
                    "HOME": str(WORK),
                    "XDG_CACHE_HOME": str(WORK / "cache"),
                    # Git discovery stops above the project, as in the native replay.
                    "GIT_CEILING_DIRECTORIES": str(project.parent),
                    "PYTHONPATH": str(ORACLE / "src"),
                    "PYTHONHASHSEED": "0",
                },
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            text = result.stdout.replace(str(project), "{root}").replace(str(WORK), "{work}")
            captured[command] = {"exit_code": result.returncode, "document": json.loads(text)}
        return captured
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


oracle = {
    "oracle": EXPECTED,
    "cases": {name: {**case, "frozen": run(case)} for name, case in CASES.items()},
}
GOLDEN.write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} next C++ binary-compat cases")
