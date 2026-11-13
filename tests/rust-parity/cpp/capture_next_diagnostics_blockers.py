"""Capture frozen v0.13.0 `ici next verify --json` and `ici next plan --json` for every
`application/gates.py` reason a `cpp.diagnostics` translation unit is refused before replay.

    python -I capture_next_diagnostics_blockers.py <oracle-root> <output.json>

Development only; never called by the Rust product. A `[workspace]` config with one C++ component
and a `compile_commands.json` per case (`{root}` is the project root), under an empty HOME with
VCS discovery bounded by a `.hg` marker beside the project. `next_cpp_diagnostics_blockers.rs`
writes the same trees and compares through the real native CLI.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CONFIG = (
    "schema_version=1\n[workspace]\nname='p'\n[[components]]\nid='app'\nroot='.'\n"
    "languages=['cpp']\nsources=['src/**/*.cpp']\n"
    "[checks.'cpp.tidy']\nenabled=false\n"
)
GOOD = "int a() { return 0; }\n"
WARNING = "int w() { int unused = 0; return 1; }\n"
ERROR = "int e() { return undefined_name; }\n"


def row(source, *driver, directory="{root}", **extra):
    return {
        "directory": directory,
        "arguments": [*driver, "-Wall", "-c", source],
        "file": source,
        **extra,
    }


GPP = "/usr/bin/g++"
# A GCC wrapper beside the project (`../tools/g++`, made executable): version probes and every
# source not named below run the real g++; the named sources end the replay the frozen
# `CompilerDiagnosticsProvider.parse`/`COMPILER_CONTRACT` way.
FAKE = (
    "#!/bin/sh\n"
    'case "$*" in\n'
    '  *--version*|*-dump*) exec /usr/bin/g++ "$@" ;;\n'
    "  *exit3.cpp*) exit 3 ;;\n"
    "  *silent1.cpp*) exit 1 ;;\n"
    "  *garbage.cpp*) echo 'this is not a compiler diagnostic' >&2; exit 1 ;;\n"
    "  *signal.cpp*) kill -KILL $$ ;;\n"
    "esac\n"
    'exec /usr/bin/g++ "$@"\n'
)
FAKE_GPP = "{root}/../tools/g++"
OUTSIDE = "inline int outside() { int unused = 0; return 1; }\n"
CASES = {
    # Positive: every unit replays.
    "all-replay": (
        {"src/a.cpp": GOOD, "src/b.cpp": WARNING},
        [row("src/a.cpp", GPP), row("src/b.cpp", GPP)],
    ),
    "relative-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "g++")]),
    # Failure: one reason per branch, alone.
    "unsupported-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "/usr/bin/true")]),
    "prefixed-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "x86_64-linux-gnu-g++")]),
    "missing-absolute-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "/nonexistent/g++")]),
    "unknown-relative-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "no-such-g++")]),
    "family-driver-not-on-path": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "g++-99")]),
    "clang-not-on-path": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "clang++-99")]),
    "gone-directory": (
        {"src/a.cpp": GOOD},
        [row("{root}/src/a.cpp", GPP, directory="{root}/gone")],
    ),
    "no-unit-in-scope": ({"src/a.cpp": GOOD, "lib/o.cpp": GOOD}, [row("lib/o.cpp", GPP)]),
    "empty-database": ({"src/a.cpp": GOOD}, []),
    "unparsable-command": (
        {"src/a.cpp": GOOD},
        [{"directory": "{root}", "command": "g++ 'unclosed -c src/a.cpp", "file": "src/a.cpp"}],
    ),
    "command-without-words": (
        {"src/a.cpp": GOOD},
        [{"directory": "{root}", "command": "   ", "file": "src/a.cpp"}],
    ),
    "neither-arguments-nor-command": (
        {"src/a.cpp": GOOD},
        [{"directory": "{root}", "file": "src/a.cpp"}],
    ),
    "missing-response-file": (
        {"src/a.cpp": GOOD},
        [row("src/a.cpp", GPP, "@none.rsp")],
    ),
    "unreadable-response-tokens": (
        {"src/a.cpp": GOOD, "bad.rsp": "'unclosed"},
        [row("src/a.cpp", GPP, "@bad.rsp")],
    ),
    "nested-response-file": (
        {"src/a.cpp": GOOD, "rsp/outer.rsp": "@inner.rsp", "rsp/inner.rsp": "-DNESTED"},
        [row("src/a.cpp", GPP, "@rsp/outer.rsp")],
    ),
    "bare-response-token": ({"src/a.cpp": GOOD}, [row("src/a.cpp", GPP, "@")]),
    "partial-coverage": (
        {"src/a.cpp": GOOD, "src/b.cpp": GOOD},
        [row("src/a.cpp", GPP)],
    ),
    "refused-row-beside-good": (
        {"src/a.cpp": GOOD},
        [row("src/a.cpp", GPP), {"directory": "{root}", "file": "src/gone.cpp"}],
    ),
    "unity-bundle-outside-scope": (
        {"src/a.cpp": GOOD, "src/b.cpp": GOOD, "unity_bundle.cpp": '#include "src/a.cpp"\n'},
        [row("unity_bundle.cpp", GPP)],
    ),
    "unity-bundle-in-scope": (
        {"src/a.cpp": GOOD, "src/unity_bundle.cpp": '#include "a.cpp"\n'},
        [row("src/unity_bundle.cpp", GPP)],
    ),
    "response-file-replayed": (
        {"src/a.cpp": GOOD, "ok.rsp": "-Wall"},
        [row("src/a.cpp", GPP, "@ok.rsp")],
    ),
    "empty-arguments": (
        {"src/a.cpp": GOOD},
        [{"directory": "{root}", "arguments": [], "file": "src/a.cpp"}],
    ),
    "launcher-before-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "ccache", GPP)]),
    "launcher-before-refused": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "ccache", "/usr/bin/true")]),
    "relative-path-driver": ({"src/a.cpp": GOOD}, [row("src/a.cpp", "./bin/g++")]),
    "malformed-database": ({"src/a.cpp": GOOD}, "[{"),
    "not-an-array": ({"src/a.cpp": GOOD}, "{}"),
    # Negative: a refused unit stays one step beside units that run.
    "refused-beside-clean": (
        {"src/a.cpp": GOOD, "src/z.cpp": GOOD},
        [row("src/a.cpp", GPP), row("src/z.cpp", "/usr/bin/true")],
    ),
    "refused-beside-warning": (
        {"src/a.cpp": GOOD, "src/w.cpp": WARNING},
        [row("src/a.cpp", "/nonexistent/g++"), row("src/w.cpp", GPP)],
    ),
    "refused-beside-error": (
        {"src/a.cpp": GOOD, "src/e.cpp": ERROR},
        [row("src/a.cpp", "no-such-g++"), row("src/e.cpp", GPP)],
    ),
    "two-refused-one-clean": (
        {"src/a.cpp": GOOD, "src/b.cpp": GOOD, "src/c.cpp": GOOD},
        [
            row("src/a.cpp", "/usr/bin/true"),
            row("src/b.cpp", GPP),
            row("src/c.cpp", "/nonexistent/g++"),
        ],
    ),
    # All refused under distinct names: the step numbers pin frozen's `_compile_variant` order,
    # including the workspace relocation of joined, `=` and separate path arguments and output.
    "configurations-keep-frozen-order": (
        {"src/a.cpp": GOOD},
        [
            row("src/a.cpp", "tcc1", "-DX"),
            row("src/a.cpp", "tcc2", "-I{root}/inc"),
            row("src/a.cpp", "tcc3", "--sysroot={root}/sys"),
            row("src/a.cpp", "tcc4", "-include", "{root}/inc/p.h"),
            row("src/a.cpp", "tcc5", "-o", "out/a.o"),
            row("src/a.cpp", "tcc6", "-DY='{root}/inc'"),
        ],
    ),
    # Replay outcomes after a unit ran: a contract failure (exit 3), exit 1 without a diagnostic,
    # unparseable output, a killed compiler, and a warning in a header outside the workspace
    # (frozen keeps only workspace-relative findings), each beside a clean unit.
    "replay-exit-three": (
        {"src/exit3.cpp": GOOD, "src/a.cpp": GOOD, "../tools/g++": FAKE},
        [row("src/exit3.cpp", FAKE_GPP), row("src/a.cpp", GPP)],
    ),
    "replay-exit-one-silent": (
        {"src/silent1.cpp": GOOD, "src/a.cpp": GOOD, "../tools/g++": FAKE},
        [row("src/silent1.cpp", FAKE_GPP), row("src/a.cpp", GPP)],
    ),
    "replay-unparseable": (
        {"src/garbage.cpp": GOOD, "src/a.cpp": GOOD, "../tools/g++": FAKE},
        [row("src/garbage.cpp", FAKE_GPP), row("src/a.cpp", GPP)],
    ),
    "replay-signal": (
        {"src/signal.cpp": GOOD, "src/a.cpp": GOOD, "../tools/g++": FAKE},
        [row("src/signal.cpp", FAKE_GPP), row("src/a.cpp", GPP)],
    ),
    "outside-header-warning": (
        {
            "src/a.cpp": '#include "outside.h"\nint a() { return outside(); }\n',
            "../ext/outside.h": OUTSIDE,
        },
        [row("src/a.cpp", GPP, "-I{root}/../ext")],
    ),
    "two-configurations-one-refused": (
        {"src/a.cpp": GOOD},
        [row("src/a.cpp", GPP), row("src/a.cpp", "/usr/bin/true", "-DX")],
    ),
}


def run(oracle, base, command, home):
    completed = subprocess.run(
        [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "next", command, "--json"],
        cwd=base,
        capture_output=True,
        text=True,
        env={
            "HOME": home,
            "XDG_CONFIG_HOME": home,
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CEILING_DIRECTORIES": str(base.parent),
        },
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout.replace(str(base), "{root}"))


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "config": CONFIG,
        "cases": {},
    }
    # Optional third argument: comma-separated case names recaptured into the existing output.
    only = [name for name in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if name]
    if only:
        document["cases"] = json.loads(output.read_text(encoding="utf-8"))["cases"]
    for name, (files, database) in CASES.items():
        if only and name not in only:
            continue
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "project"
            base.mkdir()
            (base.parent / ".hg").mkdir()
            (base / "ici.toml").write_text(CONFIG, encoding="utf-8")
            for path, text in files.items():
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                if path.startswith("../tools/"):
                    target.chmod(0o755)
            (base / "compile_commands.json").write_text(
                (database if isinstance(database, str) else json.dumps(database)).replace(
                    "{root}", str(base)
                ),
                encoding="utf-8",
            )
            plan_exit, plan = run(oracle, base, "plan", home)
            verify_exit, verify = run(oracle, base, "verify", home)
        document["cases"][name] = {
            "files": files,
            "database": database,
            "plan_exit": plan_exit,
            "plan": plan,
            "exit_code": verify_exit,
            "verify": verify,
        }
        print(name, verify_exit, verify["gate"]["selected"], verify["gate"]["reasons"][:3])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
