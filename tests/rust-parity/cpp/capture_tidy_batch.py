"""Development-only frozen `ici next verify` cpp.tidy batch captures; never called by Rust.

Each case is a fresh workspace whose component selects only `cpp.compile` and `cpp.tidy`. The
real clang-tidy (ICI_TEST_CLANG_TIDY, libraries from ICI_TEST_CLANG_LIBRARY_PATH) is reached
through a PATH wrapper that keeps, per call, the working directory, every argv element, the exit
status and the raw stdout and stderr. The frozen result (exit, cpp.tidy state, findings with
their fingerprints, limitations, gate) is stored next to the transcript. The workspace root is
stored as <root> and the directory above it as <base>.

    ICI_TEST_CLANG_TIDY=... ICI_TEST_CLANG_LIBRARY_PATH=... python -B capture_tidy_batch.py
"""

import json
import os
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
TIDY = os.environ["ICI_TEST_CLANG_TIDY"]
LIBRARIES = os.environ["ICI_TEST_CLANG_LIBRARY_PATH"]
CPP_CHECKS = (
    "cpp.artifact",
    "cpp.binary-compat",
    "cpp.cognitive",
    "cpp.compile",
    "cpp.complexity",
    "cpp.coverage",
    "cpp.cycle",
    "cpp.diagnostics",
    "cpp.dup",
    "cpp.exception",
    "cpp.line",
    "cpp.sanitize",
    "cpp.test",
    "cpp.tidy",
    "cpp.tsan",
)
CONFIG = (
    "schema_version = 1\n[workspace]\nname = 'fixture'\n"
    "[[components]]\nid = 'app'\nroot = 'app'\nlanguages = ['cpp']\n"
    + "".join(
        f'[checks."{check}"]\nenabled = {"true" if check in ("cpp.compile", "cpp.tidy") else "false"}\n'
        for check in CPP_CHECKS
    )
)
# `required = true` makes a failed or blocked cpp.tidy an incomplete gate and its findings
# violations (frozen `checks.py`: advisory unless the component opts in).
REQUIRED = CONFIG.replace(
    '[checks."cpp.tidy"]\nenabled = true\n',
    '[checks."cpp.tidy"]\nenabled = true\nrequired = true\n',
)
HEADER = "inline int div0(int x) { int z = 0; return x / z; }\ninline void g() { int unused; }\n"
DIVIDE = "int {name}(int x) {{ int z = 0; return x / z; }}\n"
BRACES = "Checks: '-*,readability-braces-around-statements'\n"
IF_RETURN = "int a(int x) { if (x) return 1; return 0; }\n"
MODES = "#ifdef MODE_A\nint fa(int x) { int z = 0; return x / z; }\n#endif\n"
# files: path -> text; links: path -> symlink target. `rows`: (directory relative to <root>, file,
# arguments[, extra keys]); by default one `/usr/bin/g++ -Wall -c <source>` row per .cpp file at
# <root>.
CASES = {
    "clean": {"files": {"app/a.cpp": "int main() { return 0; }\n"}},
    "default": {
        "files": {"app/a.cpp": DIVIDE.format(name="a") + "int u() { int unused; return 0; }\n"}
    },
    "config": {"files": {"app/.clang-tidy": BRACES, "app/a.cpp": IF_RETURN}},
    "nested-config": {
        "files": {
            "app/.clang-tidy": BRACES,
            "app/sub/.clang-tidy": "Checks: '-*,clang-analyzer-*'\n",
            "app/a.cpp": IF_RETURN,
            "app/sub/b.cpp": "int b(int x) { if (x) return 1; int z = 0; return x / z; }\n",
        }
    },
    "shared-header": {
        "files": {
            "app/h.h": HEADER,
            "app/a.cpp": '#include "h.h"\nint a() { return div0(1); }\n',
            "app/b.cpp": '#include "h.h"\nint b() { return div0(2); }\n',
        }
    },
    "distinct": {
        "files": {
            "app/a.cpp": DIVIDE.format(name="a"),
            "app/b.cpp": "\n" + DIVIDE.format(name="b"),
            "app/c.cpp": "int c() { return 0; }\n",
        }
    },
    "repeat-after-clean": {
        "files": {
            "app/a.cpp": "int a() { return 0; }\n",
            "app/b.cpp": DIVIDE.format(name="b"),
            "app/c.cpp": "int c() { return 0; }\n",
        }
    },
    "broken": {
        "files": {"app/a.cpp": DIVIDE.format(name="a"), "app/b.cpp": "int b( { return 0; }\n"}
    },
    "stdlib": {
        "files": {
            "app/a.cpp": "#include <vector>\n#include <string>\n#include <map>\n"
            'int a() { std::vector<std::string> v{"x"}; std::map<int,int> m; '
            "return (int)v.size() + (int)m.size(); }\n",
            "app/b.cpp": "#include <iostream>\n#include <memory>\n"
            "int b() { auto p = std::make_unique<int>(1); std::cout << *p; return 0; }\n",
        }
    },
    # Each warning exists only under its own row's macro and include directory, and the rows
    # have different working directories: `-p` applied each file's recorded invocation.
    "flags-cwd": {
        "files": {
            "app/a.cpp": MODES,
            "app/sub/b.cpp": '#include "local.h"\n#ifdef MODE_B\n'
            "int fb(int x) { return x % ZERO; }\n#endif\n",
            "app/sub/inc/local.h": "#define ZERO 0\n",
        },
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-DMODE_A", "-c", "app/a.cpp"]),
            ("app/sub", "b.cpp", ["/usr/bin/g++", "-DMODE_B", "-Iinc", "-c", "b.cpp"]),
        ],
    },
    # One source with two rows: frozen passes it once per row.
    "two-rows": {
        "files": {
            "app/m.cpp": MODES
            + "#ifdef MODE_B\nint fb(int x) { int z = 0; return x % z; }\n#endif\n"
        },
        "rows": [
            (".", "app/m.cpp", ["/usr/bin/g++", "-DMODE_A", "-c", "app/m.cpp"]),
            (".", "app/m.cpp", ["/usr/bin/g++", "-DMODE_B", "-c", "app/m.cpp"]),
        ],
    },
    # One source recorded in two directories: clang-tidy prints the same diagnostic once per
    # spelling (relative and absolute), which is one primary in the workspace.
    "two-spellings": {
        "files": {"app/a.cpp": DIVIDE.format(name="a"), "app/sub/keep.h": "\n"},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-DX1", "-c", "app/a.cpp"]),
            ("app/sub", "{root}/app/a.cpp", ["/usr/bin/g++", "-DX2", "-c", "{root}/app/a.cpp"]),
        ],
    },
    # Inputs clang-tidy reads that frozen never checks (native refuses them).
    # A second row for the same source whose directory is outside the workspace: the context
    # loader refuses that row, `-p` still replays it from the database.
    "refused-row": {
        "files": {"app/a.cpp": DIVIDE.format(name="a"), "../elsewhere/keep.h": "\n"},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            (
                "../elsewhere",
                "{root}/app/a.cpp",
                ["/usr/bin/g++", "-DOUT", "-c", "{root}/app/a.cpp"],
            ),
        ],
    },
    # A refused row of a source outside the component: `-p` never enters it for app/a.cpp.
    "refused-other": {
        "files": {
            "app/a.cpp": DIVIDE.format(name="a"),
            "other/b.cpp": DIVIDE.format(name="b"),
            "../elsewhere/keep.h": "\n",
        },
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            ("../elsewhere", "{root}/other/b.cpp", ["/usr/bin/g++", "-c", "{root}/other/b.cpp"]),
        ],
    },
    # A covered source whose only row is refused: frozen leaves it out of the batch.
    "refused-selected": {
        "files": {
            "app/a.cpp": DIVIDE.format(name="a"),
            "app/b.cpp": DIVIDE.format(name="b"),
            "../elsewhere/keep.h": "\n",
        },
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            ("../elsewhere", "{root}/app/b.cpp", ["/usr/bin/g++", "-c", "{root}/app/b.cpp"]),
        ],
    },
    # A refused row spelling the selected source with `.` and `..`: clang-tidy indexes it under
    # the same path and replays it.
    "refused-dots": {
        "files": {"app/a.cpp": DIVIDE.format(name="a"), "../elsewhere/keep.h": "\n"},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            (
                "../elsewhere",
                "{root}/app/../app/./a.cpp",
                ["/usr/bin/g++", "-DOUT", "-c", "{root}/app/a.cpp"],
            ),
        ],
    },
    # A refused row naming another link to the selected source.
    "refused-alias": {
        "files": {"app/a.cpp": DIVIDE.format(name="a"), "../elsewhere/keep.h": "\n"},
        "links": {"other/alias.cpp": "../app/a.cpp"},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            (
                "../elsewhere",
                "{root}/other/alias.cpp",
                ["/usr/bin/g++", "-c", "{root}/other/alias.cpp"],
            ),
        ],
    },
    # A key LLVM's JSONCompilationDatabase does not know: clang-tidy refuses the whole database
    # and runs every source without flags.
    "unknown-key": {
        "files": {"app/a.cpp": MODES},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-DMODE_A", "-c", "app/a.cpp"], {"note": "x"}),
        ],
    },
    # Relative locations of a batch whose commands do not run at the workspace root.
    "single-dir": {
        "files": {
            "app/sub/b.cpp": '#include "local.h"\n#ifdef MODE_B\n'
            "int fb(int x) { return x % ZERO; }\n#endif\n",
            "app/sub/inc/local.h": "#define ZERO 0\n",
        },
        "rows": [("app/sub", "b.cpp", ["/usr/bin/g++", "-DMODE_B", "-Iinc", "-c", "b.cpp"])],
    },
    # The same header name under both compile directories: clang-tidy prints `./h.h` (it also
    # serves the second command the first one's h.h by name, so both hold the same text).
    "two-candidates": {
        "files": {
            "app/x/h.h": "inline int hx(int x) { int z = 0; return x / z; }\n",
            "app/y/h.h": "inline int hx(int x) { int z = 0; return x / z; }\n",
            "app/x/a.cpp": '#include "h.h"\nint a() { return hx(1); }\n',
            "app/y/b.cpp": '#include "h.h"\nint b() { return hx(2); }\n',
        },
        "rows": [
            ("app/x", "a.cpp", ["/usr/bin/g++", "-c", "a.cpp"]),
            ("app/y", "b.cpp", ["/usr/bin/g++", "-c", "b.cpp"]),
        ],
    },
    "zero-candidates": {
        "files": {
            "app/a.cpp": "int a() { return 0; }\n",
            "app/sub/b.cpp": '#include "../../../elsewhere/z.h"\nint b() { return zdiv(1); }\n',
            "../elsewhere/z.h": "inline int zdiv(int x) { int z = 0; return x / z; }\n",
        },
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            ("app/sub", "b.cpp", ["/usr/bin/g++", "-c", "b.cpp"]),
        ],
    },
    # Two byte-identical entries for one source (ruling 2): frozen passes the source twice.
    "identical-rows": {
        "files": {"app/a.cpp": DIVIDE.format(name="a")},
        "rows": [
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
            (".", "app/a.cpp", ["/usr/bin/g++", "-c", "app/a.cpp"]),
        ],
    },
    "parent-config": {"files": {"../.clang-tidy": BRACES, "app/a.cpp": IF_RETURN}},
    "flags-txt": {"files": {"compile_flags.txt": "-DMODE_A\n", "app/a.cpp": MODES}},
    "unsafe-config": {
        "files": {"app/.clang-tidy": BRACES + "ExtraArgs: ['-DX']\n", "app/a.cpp": IF_RETURN}
    },
}
for _name in ("broken", "default", "shared-header"):
    CASES[f"required-{_name}"] = {**CASES[_name], "config": REQUIRED}
WRAPPER = """#!/bin/sh
d=$(mktemp -d "{log}/call-XXXXXXXX") || exit 125
date +%s%N > "$d/start"
pwd > "$d/cwd"
for a in "$0" "$@"; do printf '%s\\0' "$a"; done > "$d/argv"
LD_LIBRARY_PATH="{libraries}" "{tidy}" "$@" >"$d/stdout" 2>"$d/stderr"
rc=$?
echo "$rc" > "$d/exit"
cat "$d/stdout"
cat "$d/stderr" >&2
exit "$rc"
"""


def run(case):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "w"
        base.mkdir()
        (base / ".hg").write_text("")
        bins = base / "bin"
        bins.mkdir()
        for parent in ("/usr/bin", "/bin"):
            for entry in sorted(os.listdir(parent)):
                link = bins / entry
                if not entry.startswith(("clang", "clazy")) and not os.path.lexists(link):
                    link.symlink_to(Path(parent) / entry)
        log = base / "calls"
        log.mkdir()
        wrapper = bins / "clang-tidy"
        wrapper.write_text(WRAPPER.format(log=log, libraries=LIBRARIES, tidy=TIDY))
        wrapper.chmod(0o755)
        root = base / "project"
        root.mkdir()
        for name, text in case["files"].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for name, target in case.get("links", {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        rows = case.get("rows") or [
            (".", name, ["/usr/bin/g++", "-Wall", "-c", name])
            for name in sorted(case["files"])
            if name.endswith(".cpp")
        ]
        (root / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str((root / row[0]).resolve()),
                        "file": row[1].replace("{root}", str(root)),
                        "arguments": [item.replace("{root}", str(root)) for item in row[2]],
                        **(row[3] if len(row) > 3 else {}),
                    }
                    for row in rows
                ]
            )
        )
        config = case.get("config", CONFIG)
        (root / "ici.toml").write_text(config)
        process = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "next", "verify", "--result", "now.json"],
            cwd=root,
            env={
                "HOME": str(base),
                "PATH": str(bins),
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": str(base),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            timeout=900,
            check=False,
        )

        def norm(text):
            return text.replace(str(root), "<root>").replace(str(base), "<base>")

        calls = []
        for call in sorted(log.iterdir(), key=lambda path: int((path / "start").read_text())):
            calls.append(
                {
                    "cwd": norm((call / "cwd").read_text().strip()),
                    "argv": [
                        norm(item.decode())
                        for item in (call / "argv").read_bytes().split(b"\0")[:-1]
                    ],
                    "exit": int((call / "exit").read_text()),
                    "stdout": norm((call / "stdout").read_bytes().decode()),
                    "stderr": norm((call / "stderr").read_bytes().decode()),
                }
            )
        document = json.loads(norm((root / "now.json").read_text()))
        tidy = next(check for check in document["checks"] if check["check_id"] == "cpp.tidy")
        findings = [
            finding
            for finding in document["findings"]
            if finding["rule_id"].startswith("clang_tidy.")
        ]
        return {
            "config": config,
            "files": case["files"],
            "links": case.get("links", {}),
            "rows": [list(row) for row in rows],
            "calls": calls,
            "frozen": {
                "exit": process.returncode,
                "tidy": {key: tidy.get(key) for key in ("task_id", "state", "evidence_complete")},
                "findings": findings,
                "limitations": document["limitations"],
                "gate": document["gate"],
            },
        }


def main():
    version = subprocess.run(
        [TIDY, "--version"],
        env={"LD_LIBRARY_PATH": LIBRARIES},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    oracle = {
        "oracle": EXPECTED,
        "clang_tidy_version": version,
        "cases": {name: run(case) for name, case in CASES.items()},
    }
    target = ROOT / "tests/rust-parity/cpp/tidy-batch-oracle.json"
    target.write_text(json.dumps(oracle, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
