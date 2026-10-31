"""Development-only v1 oracle capture; never called by the Rust product."""

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))
from ici.core.compilation_export import compilation_export_payload  # noqa: E402
from ici.core.context import (  # noqa: E402
    CompilationContext,
    CompilationDefine,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
)

base = dict(
    source="main.cpp",
    directory=".",
    argv=("clang++", "-std=c++20", "-c", "main.cpp", "-o", "main.o"),
    compiler="clang++",
    language="c++",
    standard="c++20",
    output="main.o",
    target="app",
)
cases = [
    ("standard", {}),
    ("opaque-flags", dict(argv=("clang++", "-O2", "-fno-exceptions", "-c", "main.cpp"))),
    (
        "ordered-undefines",
        dict(argv=("g++-14", "-U", "FIRST", "-USECOND", "-c", "main.cpp"), compiler="g++-14"),
    ),
    ("wrapped", dict(argv=("env", "LANG=C", "ccache", "clang++", "-std=c++20", "-c", "main.cpp"))),
    ("unknown-compiler", dict(argv=("vendor-c++", "-c", "main.cpp"), compiler="vendor-c++")),
    (
        "msvc",
        dict(
            argv=("cl.exe", "/std:c++20", "/UFIRST", "/U", "SECOND", "/c", "main.cpp"),
            compiler="cl.exe",
        ),
    ),
    (
        "target-triple",
        dict(argv=("clang++", "--target=x86_64-unknown-linux-gnu", "-c", "main.cpp")),
    ),
    (
        "secret-define",
        dict(
            argv=("clang++", "-DPASSWORD=hidden", "-DCOUNT=2", "-c", "main.cpp"),
            defines=(CompilationDefine("PASSWORD", "hidden"), CompilationDefine("COUNT", "2")),
        ),
    ),
    (
        "external-include",
        dict(
            argv=("clang++", "-isystem", "/external/sdk", "-c", "main.cpp"),
            include_paths=(CompilationSearchPath("/external/sdk", "system", "external", True),),
        ),
    ),
    (
        "external-sysroot",
        dict(
            argv=("clang++", "--sysroot=/external/sysroot", "-c", "main.cpp"),
            sysroot="/external/sysroot",
            sysroot_scope="external",
        ),
    ),
]
rows = []
for name, changed in cases:
    kwargs = base | changed
    unit = CompilationUnit(**kwargs)
    context = CompilationContext(
        database_path="compile_commands.json",
        database_digest="sha256:" + "a" * 64,
        origin="configured",
        generator="Ninja",
        units=(unit,),
        unity_build=False,
    )
    project = ProjectModel(
        root=Path("/fixture/project"),
        name="sample",
        version="v1.2.3",
        project_type="cpp",
        backend="cmake",
    )
    expected = compilation_export_payload(project, context)
    native = asdict(unit)
    native["original_argv"] = list(unit.argv)
    native["variant"] = "release"
    native["configuration"] = "sha256:" + "b" * 64
    native["response_files"] = []
    native["defines"] = [dict(asdict(value), undefine=False) for value in unit.defines]
    native["include_paths"] = [
        dict(path=value.path, kind=value.kind, scope=value.scope, present=value.exists)
        for value in unit.include_paths
    ]
    rows.append(dict(name=name, unit=native, expected=expected))
(ROOT / "tests/rust-parity/cpp/compilation-export-oracle.json").write_text(
    json.dumps(dict(oracle_commit=EXPECTED, cases=rows), indent=2, sort_keys=True) + "\n"
)
