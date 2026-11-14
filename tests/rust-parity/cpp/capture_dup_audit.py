"""Capture the frozen v0.13.0 `ici dup --report` duplicate-engine audit corpus.

    python -I capture_dup_audit.py <oracle-root> <output.json>

Development only; never called by the Rust product. Each case is a small project run in a fresh
directory (empty HOME, `PATH=/usr/bin:/bin`, `PYTHONHASHSEED=0`, VCS discovery bounded by the
project's own `.git`), and the saved engine report is recorded with the project root as `<root>`.

Tree values: a string is the UTF-8 file text; `{"base64": ...}` raw bytes; `{"symlink": ...}` a
symbolic link; `{"repeat": [text, count]}` the text repeated (kept out of the oracle's bytes).
"""

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
CPP = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
HYBRID = "[project]\ntype='hybrid'\nsource_dirs=['src']\n"
BODY = "".join(
    f"    int v{i} = compute{i}(a, b);\n    if (v{i} > {i}) {{ total += v{i}; }}\n"
    for i in range(4)
)


def fn(name, body=BODY):
    return f"int {name}(int a, int b) {{\n    int total = 0;\n{body}    return total;\n}}\n"


PY = (
    "def first(values):\n    total = 0\n    for value in values:\n        if value > 10:\n"
    "            total += value * 2\n        else:\n            total -= value\n    return total\n"
    "\n\ndef second(items):\n    result = 0\n    for item in items:\n        if item > 10:\n"
    "            result += item * 2\n        else:\n            result -= item\n    return result\n"
)
RAW = (
    'const char* s = R"(\n#define X\n)";\nconst char* t = fooR"(\n#define Y\n)";\n'
    "int n = 1'000;\nint m = 0x1p3;\nint q = 1.5e3f;\nauto k = 12_km;\n"
    'auto u = "a"_s;\nauto w = L"x";\nauto w2 = u8\'c\';\n'
)


def cases():
    pair = fn("f") + fn("g")
    yield "basic", {"src/a.cpp": pair, "src/b.cpp": fn("h")}
    yield "no-clone", {"src/a.cpp": "int f(){return 1;}\n"}
    # Windowing: the window is configurable, a shorter one finds what the default does not, a
    # window longer than the file finds nothing, and clones never overlap themselves.
    three = (
        "int f(int a){\n  call_a(a);\n  call_b(a);\n  call_c(a);\n  return 1;\n}\n"
        "int g(int a){\n  call_a(a);\n  call_b(a);\n  call_c(a);\n  return 2;\n}\n"
    )
    yield "window-3", {"src/a.cpp": three}, "[engines.dup]\nmin_window=3\n"
    yield "window-default-misses-3", {"src/a.cpp": three}
    yield "window-20", {"src/a.cpp": pair}, "[engines.dup]\nmin_window=20\n"
    yield "window-100", {"src/a.cpp": pair}, "[engines.dup]\nmin_window=100\n"
    yield (
        "window-1",
        {"src/a.cpp": "call_one(a);\n\ncall_two(a);\ncall_one(a);\n"},
        ("[engines.dup]\nmin_window=1\n"),
    )
    yield (
        "overlap-same-file",
        {"src/a.cpp": "void f(){\n" + "  tick(a, b);\n  tock(a, b);\n" * 12 + "}\n"},
    )
    # Line endings and Python `splitlines` breaks: records count LF/CRLF/CR, snippets and the
    # function scan take every Python line break.
    yield "line-endings-cr", {"src/a.cpp": pair.replace("\n", "\r")}
    yield "line-endings-crlf", {"src/a.cpp": pair.replace("\n", "\r\n")}
    yield (
        "line-breaks-ff-vt",
        {"src/a.cpp": pair.replace("int total = 0;", "int total = 0;\f\n  \x0b ")},
    )
    yield (
        "line-breaks-ls",
        {"src/a.cpp": pair.replace("int total = 0;", "int total = 0; // x\u2028 y\n")},
    )
    yield (
        "control-spaces",
        {"src/a.cpp": pair.replace("int total = 0;", "int \x1c total \x1d = \x1e 0\x1f;")},
    )
    yield "snippet-rstrip", {"src/a.cpp": pair.replace("}\n", "}\x1f\u00a0\n")}
    # Normalization: Python character classes, literals, splices, digraphs, directives.
    yield "ident-mark", {"src/a.cpp": pair.replace("total", "कार")}
    yield "ident-letter-number", {"src/a.cpp": pair.replace("total", "Ⅷx")}
    yield "ident-circled", {"src/a.cpp": pair.replace("total", "xⒶ")}
    yield "ident-superscript", {"src/a.cpp": pair.replace("total", "x²")}
    yield "ident-hangul", {"src/a.cpp": pair.replace("total", "합계")}
    yield "ident-upper", {"src/a.cpp": pair.replace("total", "ÀÉ")}
    yield "nbsp", {"src/a.cpp": pair.replace("int total", "int\u00a0total")}
    yield "bom", {"src/a.cpp": "﻿" + pair}
    yield "literals-and-directive-scan", {"src/a.cpp": RAW + fn("f") + RAW + fn("g")}
    yield (
        "splice",
        {
            "src/a.cpp": "#define M \\\n  1 \\\n  2\n"
            + fn("f").replace("int total = 0;", "int to\\\ntal = 0;")
            + "#define N \\\n  3\n"
            + fn("g").replace("int total = 0;", "int to\\\ntal = 0;")
        },
    )
    yield (
        "directive-barrier",
        {"src/a.cpp": fn("f") + fn("g").replace("    int v2 =", "#if X\n#endif\n    int v2 =")},
    )
    yield "directive-eof-splice", {"src/a.cpp": pair + "#define Z \\"}
    yield (
        "comment-then-directive",
        {"src/a.cpp": "/* a\n b */ #define X\n/* c */ #define Y\n" + pair},
    )
    yield "unterminated-comment", {"src/a.cpp": pair + "/* unterminated\n #define x\n"}
    yield (
        "unterminated-string",
        {"src/a.cpp": fn("f") + 'const char* a = "abc\n' + fn("g") + 'const char* b = "x\\'},
    )
    yield "unterminated-raw", {"src/a.cpp": pair + 'auto r = R"x(abc\n#define q\n'}
    yield (
        "digraphs",
        {
            "src/a.cpp": "%:define X\n"
            + fn("f").replace("{", "<%").replace("}", "%>")
            + "%:%:\n"
            + fn("g").replace("[", "<:")
        },
    )
    yield (
        "qt-anchors",
        {
            "src/a.cpp": "class A : public QObject {\n  Q_OBJECT\n  Q_PROPERTY(int x READ x WRITE setX NOTIFY xChanged)\npublic slots:\n  void a();\nsignals:\n  void b();\n};\n"
            + pair.replace("int total = 0;", "connect(this, SIGNAL(b()), this, SLOT(a()));")
        },
    )
    yield "long-identifier", {"src/a.cpp": pair.replace("compute0", "c" * 200)}
    data = "".join(f"  {i}, {i + 1}, {i + 2},\n" for i in range(24))
    yield (
        "data-table-no-signal",
        {"src/a.cpp": "int t1[] = {\n" + data + "};\nint t2[] = {\n" + data + "};\n"},
    )
    yield (
        "low-signal",
        {
            "src/a.cpp": "".join(
                f"int f{k}() {{\n{{\n}}\n{{\n}}\n{{\n}}\n{{\n}}\n}}\n" for k in range(2)
            )
        },
    )
    # Regions: the fallback function scan and its bounded failure.
    macro = (
        "#define DEF(n) int n(int a, int b)\nDEF(f) {\n    int total = 0;\n"
        + BODY
        + "    return total;\n}\nDEF(g) {\n    int total = 0;\n"
        + BODY
        + "    return total;\n}\n"
    )
    yield "region-macro-function", {"src/a.cpp": macro}
    knr = (
        "int f(a, b)\n int a;\n int b;\n{\n    int total = 0;\n"
        + BODY
        + "    return total;\n}\nint g(a, b)\n int a;\n int b;\n{\n    int total = 0;\n"
        + BODY
        + "    return total;\n}\n"
    )
    yield "region-knr", {"src/a.c": knr}
    yield "region-class-methods", {"src/a.hpp": "class A {\npublic:\n" + pair + "};\n"}
    yield (
        "region-lambda",
        {
            "src/a.cpp": "void h() {\n  auto l1 = [](int a, int b) {\n    int total = 0;\n"
            + BODY
            + "    return total;\n  };\n  auto l2 = [](int a, int b) {\n    int total = 0;\n"
            + BODY
            + "    return total;\n  };\n}\n"
        },
    )
    top = "".join(f"int table{i} = compute{i}(a, b);\nif_like{i}(table{i}, 1);\n" for i in range(6))
    yield "region-top-level", {"src/a.cpp": top + "\nint main(){return 0;}\n" + top}
    yield (
        "region-one-line-functions",
        {
            "src/a.cpp": "".join(f"int a{i}(){{ return n{i}(); }}\n" for i in range(7))
            + "\n"
            + "".join(f"int b{i}(){{ return n{i}(); }}\n" for i in range(7))
        },
    )
    yield (
        "region-ctor-try",
        {
            "src/a.cpp": "struct S { S(); S(int); int a; };\nS::S() : a(0) {\n  int total = 0;\n"
            + BODY
            + "}\nS::S(int) try : a(0) {\n  int total = 0;\n"
            + BODY
            + "} catch (...) {\n}\n"
        },
    )
    yield (
        "region-namespaces",
        {
            "src/a.cpp": "namespace x {\n"
            + fn("f")
            + "}\nnamespace y { namespace z {\n"
            + fn("f")
            + "}}\n"
        },
    )
    yield (
        "region-unbalanced",
        {
            "src/a.cpp": "int f(int a, int b) {\n  int total = 0;\n"
            + BODY
            + "\nint g(int a, int b) {\n  int total = 0;\n"
            + BODY
            + "}\n"
        },
    )
    yield (
        "region-conditional",
        {
            "src/a.cpp": "#if A\nint f(int a) {\n#else\nint f(long a) {\n#endif\n  int total = 0;\n"
            + BODY
            + "  return total;\n}\n"
            + fn("g")
        },
    )
    yield "region-extern-c", {"src/a.cpp": 'extern "C" {\n' + pair + "}\n"}
    yield (
        "region-unmatched-close",
        {"src/a.cpp": "}\n}\nint f() {\n" + BODY + "}\n}\nint g() {\n" + BODY + "}\n"},
    )
    # Thresholds and mode.
    for name, config in {
        "warn": "warn_pct=0.1\nfail_pct=99.0\n",
        "fail": "warn_pct=0.1\nfail_pct=0.2\n",
        "pass": "warn_pct=99.0\nfail_pct=99.0\n",
        "fail-pass-warn": "warn_pct=0.1\nfail_pct=0.2\nmode='pass_warn'\n",
        "fail-pass-fail": "warn_pct=0.1\nfail_pct=0.2\nmode='pass_fail'\n",
        "warn-pass-fail": "warn_pct=0.1\nfail_pct=99.0\nmode='pass_fail'\n",
        "warn-pass-warn-fail": "warn_pct=0.1\nfail_pct=99.0\nmode='pass_warn_fail'\n",
        "integer": "warn_pct=1\nfail_pct=99\n",
        "not-required": "warn_pct=0.1\nfail_pct=0.2\nrequired=false\n",
    }.items():
        yield f"threshold-{name}", {"src/a.cpp": pair}, "[engines.dup]\n" + config
    yield (
        "threshold-equal",
        {"src/a.cpp": fn("f"), "src/b.cpp": fn("g")},
        "[engines.dup]\nwarn_pct=50.0\nfail_pct=50.0\n",
    )
    # Vendor/generated ownership, walked and classified like frozen.
    for directory in (
        "third_party",
        "vendor",
        "external",
        "extern",
        "_deps",
        "deps",
        "node_modules",
        "subprojects",
        "third-party",
        "THIRD_PARTY",
        "generated",
        "autogen",
        "x_autogen",
        "Generated",
    ):
        yield f"owned-dir-{directory}", {f"src/{directory}/a.cpp": fn("f"), "src/b.cpp": fn("g")}
    for name in (
        "moc_a.cpp",
        "qrc_a.cpp",
        "ui_a.h",
        "mocs_compilation.cpp",
        "mocs_compilationx.cpp",
        "MOC_a.cpp",
        "a.moc",
    ):
        yield f"owned-name-{name}", {f"src/{name}": fn("f"), "src/b.cpp": fn("g")}
    yield "owned-none", {"src/vendor/a.cpp": fn("f"), "src/generated/b.cpp": fn("g")}
    yield (
        "owned-both-reasons",
        {
            "src/vendor/generated/a.cpp": fn("f"),
            "src/vendor/moc_a.cpp": fn("g"),
            "src/b.cpp": "int x;\n",
        },
    )
    three_trees = {
        "src/vendor/generated/a.cpp": fn("f"),
        "src/vendor/c.cpp": fn("h"),
        "src/b.cpp": fn("g"),
    }
    yield "include-generated", dict(three_trees), "[engines.dup]\ninclude_generated=true\n"
    yield "include-vendor", dict(three_trees), "[engines.dup]\ninclude_vendor=true\n"
    yield (
        "include-both",
        dict(three_trees),
        "[engines.dup]\ninclude_vendor=true\ninclude_generated=true\n",
    )
    yield (
        "include-moc",
        {"src/a.moc": fn("f"), "src/y.cpp": fn("g")},
        "[engines.dup]\ninclude_generated=true\n",
    )
    # The frozen source walk: which directories and suffixes it reads.
    for directory in (
        "target",
        "dist",
        ".svn",
        ".hg",
        "v1.2",
        "v0.1",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "venv",
        ".venv",
        "__pycache__",
    ):
        yield f"walk-{directory}", {f"src/{directory}/x.cpp": fn("f"), "src/y.cpp": fn("g")}
    yield (
        "walk-nested-repository",
        {"src/sub/.git/x": "", "src/sub/x.cpp": fn("f"), "src/y.cpp": fn("g")},
    )
    yield (
        "walk-root-scope",
        {"dist/x.cpp": fn("f"), "target/x.cpp": fn("g"), "y.cpp": fn("g"), "build/z.cpp": fn("q")},
        "",
        "[project]\ntype='cpp'\nsource_dirs=['.']\n",
    )
    yield (
        "walk-suffixes",
        {
            "src/a.c": fn("f"),
            "src/b.cc": fn("f"),
            "src/c.cxx": fn("f"),
            "src/d.cpp": fn("f"),
            "src/e.h": fn("f"),
            "src/f.hh": fn("f"),
            "src/g.hpp": fn("f"),
            "src/h.hxx": fn("f"),
            "src/i.CPP": fn("f"),
            "src/j.ipp": fn("f"),
            "src/k.ixx": fn("f"),
        },
    )
    # Failure targets.
    yield "fail-nul", {"src/a.cpp": "int a;\0\n", "src/b.cpp": "int b;\n"}
    yield (
        "fail-utf8",
        {
            "src/a.cpp": {"base64": base64.b64encode(b"int a; // \xff\n").decode()},
            "src/b.cpp": "int b;\n",
        },
    )
    yield "fail-symlink-out", {"src/a.cpp": {"symlink": "/etc/hostname"}, "src/b.cpp": "int b;\n"}
    yield "skip-symlink-in", {"src/a.cpp": {"symlink": "b.cpp"}, "src/b.cpp": pair}
    yield "fail-dangling", {"src/a.cpp": {"symlink": "nowhere.cpp"}, "src/b.cpp": "int b;\n"}
    yield "empty-file", {"src/a.cpp": ""}
    yield "no-sources", {"src/a.txt": "x"}
    yield "comments-only", {"src/a.cpp": "// nothing\n/* more */\n"}
    yield "fail-token-limit", {"src/a.cpp": {"repeat": ["x ", 500_001]}, "src/b.cpp": "int b;\n"}
    yield "fail-record-limit", {"src/a.cpp": {"repeat": ["x;\n", 500_001]}, "src/b.cpp": "int b;\n"}
    yield "fail-same-file-seeds", {"src/a.cpp": "void f(){\n" + "  tick(a, b);\n" * 600 + "}\n"}
    yield "fail-window-occurrences", {"src/a.cpp": "void f(){\n" + "  tick(a, b);\n" * 2200 + "}\n"}
    six = "".join(f"  call_{k}(a, b);\n" for k in range(6))
    yield "fail-cross-pairs", {f"src/m{k:03}.cpp": six for k in range(201)}
    yield "fail-raw-matches", {f"src/m{k:03}.cpp": six for k in range(142)}
    many = "".join(f"  tick_{k}(a);\n" for k in range(400))
    yield "fail-cross-seeds", {f"src/m{k:02}.cpp": many for k in range(40)}
    # `MAX_DUPLICATE_NORMALIZED_CHARS`: 25 files of 500,000 empty strings (the per-file token
    # bound) normalize to `STRING_LIT` plus a separator each, 137.5M chars over 128 MiB.
    yield (
        "fail-normalized-chars",
        {f"src/n{k:02}.cpp": {"repeat": ['""', 500_000]} for k in range(25)},
    )
    # `MAX_DUPLICATE_EXTENSION_COMPARISONS`: only windows holding both `tick` lines carry a
    # duplicate signal, so each alignment seeds once and then extends over the identical `;`
    # filler (81M comparisons without the limit; every other bound stays satisfied).
    period = "  tick(a, b);\n  tick(a, b);\n" + ";\n" * 398
    filler = "void f(){\n" + period * 129 + "}\n"
    yield "fail-extension-comparisons", {"src/a.cpp": filler, "src/b.cpp": filler}
    # The one scan over Python and C++.
    yield "mix", {"src/a.py": PY, "src/b.cpp": pair}, "", HYBRID
    yield (
        "mix-semantic-off",
        {"src/a.py": PY, "src/b.cpp": pair},
        "[engines.dup]\npython_semantic='off'\n",
        HYBRID,
    )
    yield (
        "mix-same-text",
        {"src/a.py": "a = b\nc = d\ne = f\n", "src/b.cpp": "a = b\nc = d\ne = f\n"},
        "[engines.dup]\nmin_window=2\n",
        HYBRID,
    )
    yield "mix-python-unparsed-first", {"src/a.py": "def x(:\n", "src/b.cpp": pair}, "", HYBRID
    yield "mix-python-unparsed-last", {"src/z.py": "def x(:\n", "src/b.cpp": pair}, "", HYBRID
    yield (
        "mix-required-semantic-unparsed",
        {"src/a.py": PY + "\ndef x(:\n", "src/b.cpp": pair},
        "[engines.dup]\npython_semantic='required'\n",
        HYBRID,
    )
    yield (
        "mix-python-intake",
        {"src/z.py": {"base64": base64.b64encode(b"x='\xff'\n").decode()}, "src/b.cpp": pair},
        "",
        HYBRID,
    )
    yield (
        "mix-cpp-intake",
        {
            "src/a.py": "x=1\n",
            "src/b.cpp": {"base64": base64.b64encode(b"int a;//\xff\n").decode()},
        },
        "",
        HYBRID,
    )
    yield (
        "mix-both-intake-python-first",
        {
            "src/a.py": {"base64": base64.b64encode(b"x='\xff'\n").decode()},
            "src/b.cpp": {"base64": base64.b64encode(b"int a;//\xff\n").decode()},
        },
        "",
        HYBRID,
    )
    yield (
        "mix-both-intake-cpp-first",
        {
            "src/z.py": {"base64": base64.b64encode(b"x='\xff'\n").decode()},
            "src/b.cpp": {"base64": base64.b64encode(b"int a;//\xff\n").decode()},
        },
        "",
        HYBRID,
    )
    yield (
        "mix-comparison-limit",
        {"src/a.py": "x=1\n", "src/b.cpp": "void f(){\n" + "  tick(a, b);\n" * 2200 + "}\n"},
        "",
        HYBRID,
    )
    yield "mix-cpp-all-excluded", {"src/a.py": "x=1\n", "src/vendor/b.cpp": fn("f")}, "", HYBRID
    yield "mix-python-all-excluded", {"src/vendor/a.py": "x=1\n", "src/b.cpp": fn("f")}, "", HYBRID
    yield "mix-all-excluded", {"src/vendor/a.py": "x=1\n", "src/vendor/b.cpp": fn("f")}, "", HYBRID
    yield (
        "mix-header-only-python-type",
        {"src/a.py": PY, "src/b.h": pair},
        "",
        "[project]\ntype='python'\nsource_dirs=['src']\n",
    )


def materialize(base, tree):
    for name, value in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_bytes(value.encode("utf-8"))
        elif "base64" in value:
            path.write_bytes(base64.b64decode(value["base64"]))
        elif "symlink" in value:
            path.symlink_to(value["symlink"])
        else:
            text, count = value["repeat"]
            path.write_bytes((text * count).encode("utf-8"))


def run_case(oracle, tree, config):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        materialize(base, tree)
        (base / "ici.toml").write_text(config, encoding="utf-8")
        (base / ".git").mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), "dup", "--report"],
            cwd=base,
            capture_output=True,
            text=True,
            env={
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C.UTF-8",
                "GIT_CEILING_DIRECTORIES": temporary,
            },
            check=False,
        )
        report = (base / "dup_report.json").read_text(encoding="utf-8")
        return {
            "exit_code": completed.returncode,
            "report": json.loads(report.replace(str(base), ROOT)),
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "cases": {},
    }
    # Optional third argument: comma-separated case names recaptured into the existing output
    # (development reruns); the recorded oracle is captured in full.
    only = [name for name in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if name]
    if only:
        document["cases"] = json.loads(output.read_text(encoding="utf-8"))["cases"]
    for case in cases():
        name, tree, extra, header = (
            (*case, "", CPP)[:4]
            if len(case) == 2
            else ((*case, CPP)[:4] if len(case) == 3 else case)
        )
        if only and name not in only:
            continue
        config = header + extra
        document["cases"][name] = {
            "tree": {**tree, "ici.toml": config},
            **run_case(oracle, tree, config),
        }
        report = document["cases"][name]["report"]
        print(name, document["cases"][name]["exit_code"], report["status"], report["summary"][:70])
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
