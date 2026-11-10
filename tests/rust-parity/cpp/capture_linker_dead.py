"""Development-only frozen legacy GNU ELF linker dead-function captures; never Rust.

Runs the frozen CLI `ici dead --report` in a fresh copy of each parity-fixtures/cpp/linker-dead
tree (its ici.toml sets cpp_unused/cpp_linker; 'off'/'required' unless named below) with
PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8: `discarded` relinks two CMake executables sharing
one object, where GNU ld discards one hidden function in both links (one
`ici.dead.gnu-elf-discarded-function`); `clean` relinks one executable that uses every hidden
function; `broken` fails its CMake configure; `build-failure` configures and then fails its
shadow build. Exclusions: `archive` has a static library target (a two-line link.txt);
`archive-auto` is the same tree under cpp_linker='auto'; `shared` skips a shared library target
and relinks the executable linking it; `lto` links its executable with -flto. `unused-and-linker`
runs cpp_unused='required' beside cpp_linker='required' (both scopes merged). `verify-auto` and
`verify-required` run `ici verify --report` (every engine but dead disabled; frozen probes every
tool there) over the `discarded` tree with cpp_linker='auto' / 'required' and keep its dead result. Linker stops:
`unity` sets CMAKE_UNITY_BUILD; `coverage-required` compiles one executable's object without
-ffunction-sections under a required policy, `coverage-doctor` the same under 'auto' with
`doctor.required_tools` naming cmake/readelf/addr2line, `doctor-required` that promotion alone;
the `unprobed-driver` trees configure with CXX naming a GCC wrapper called g++ outside PATH, `symlinked-driver` has PATH g++/c++ links to a real GCC under another file name, `verify-coverage-auto` is the coverage exclusion under auto, `no-gcc` runs with a PATH of only cmake/readelf/addr2line, `gnu-ld` behind a PATH `g++`/`c++`
wrapper whose `-Wl,--version` banner is LLD's, `source-drift` behind a PATH `addr2line` wrapper
that appends a line to src/common.cpp before each lookup, `non-exec` behind a PATH `g++`/`c++`
wrapper that replaces the output of the `-Wl,--print-gc-sections` relink with a copy of the PIE
/usr/bin/true (ELF DYN) and `relink-failure` behind one that fails that relink with exit 1 (the
links that create their output run unchanged; the
wrappers live in a `tools` directory beside the project, written as `<tools>`). Keeps the exit
code, status, evidence, required flag, summary, targets, findings, the whole engine extra and the
tool evidence names and return codes, with the project root written as `<root>`.
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
FIXTURES = ROOT / "parity-fixtures/cpp/linker-dead"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-linker-dead"
GOLDEN = ROOT / "tests/rust-parity/cpp/linker-dead-oracle.json"
TARGET_FIELDS = ("target_name", "status", "message", "file_path", "start_line", "metrics")
FINDING_FIELDS = (
    "rule_id",
    "severity",
    "confidence",
    "message",
    "explanation",
    "remediation",
    "tool_rule_id",
    "tool_name",
    "tool_version",
    "primary_location",
    "metrics",
)

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


LLD = '#!/bin/sh\ncase "$*" in *-Wl,--version*) echo \'LLD 18.1.3 (compatible with GNU linkers)\'; exit 0;; esac\nexec /usr/bin/g++ "$@"\n'
# Every other driver call (compile, preprocess, identity) runs the real g++, and so does every
# `-Wl,--print-gc-sections` link whose `-o` output does not exist yet (CMake's try-compile and
# shadow build links); a link over an existing output is a relink.
RELINK = (
    "#!/bin/sh\n"
    'case " $* " in *" -c "*|*" -E "*|*" --version "*|*" -Wl,--version "*) exec /usr/bin/g++ "$@";; esac\n'
    'case " $* " in *" -Wl,--print-gc-sections "*) ;; *) exec /usr/bin/g++ "$@";; esac\n'
    'out=; prev=; for a in "$@"; do [ "$prev" = -o ] && out=$a; prev=$a; done\n'
    '[ -e "$out" ] || exec /usr/bin/g++ "$@"\n'
)
NON_EXEC = RELINK + '/usr/bin/g++ "$@" || exit $?\ncp /usr/bin/true "$out"\n'
RELINK_FAILURE = RELINK + "echo 'relink refused' >&2\nexit 1\n"
DRIFT = "#!/bin/sh\ncase \"$*\" in *--version*) ;; *) echo '// drift' >> '{source}';; esac\nexec /usr/bin/addr2line \"$@\"\n"


def tools_path(name: str, tools: Path, project: Path) -> str:
    """The case's PATH; wrapper cases write their tools beside the project."""

    def script(tool: str, text: str) -> None:
        (tools / tool).write_text(text, encoding="utf-8")
        (tools / tool).chmod(0o755)

    if "unprobed-driver" in name:
        # Named exactly like a GCC driver, so only the probe set can exclude it.
        tools.mkdir()
        script("g++", '#!/bin/sh\nexec /usr/bin/g++ "$@"\n')
        return "/usr/bin:/bin"
    if "unused-replay-fails" in name:
        # The project builds, but the compiler scope's own -Wunused-function replay exits 1.
        tools.mkdir()
        script(
            "g++",
            '#!/bin/sh\ncase " $* " in *" -Wunused-function "*) exit 1;; esac\nexec /usr/bin/g++ "$@"\n',
        )
        (tools / "c++").symlink_to("g++")
        return f"{tools}:/usr/bin:/bin"
    if name == "symlinked-driver":
        # The PATH drivers resolve to a real GCC whose own file name is not a GCC driver name.
        (tools / "real").mkdir(parents=True)
        script("real/my-compiler", '#!/bin/sh\nexec /usr/bin/g++ "$@"\n')
        (tools / "g++").symlink_to("real/my-compiler")
        (tools / "c++").symlink_to("real/my-compiler")
        return f"{tools}:/usr/bin:/bin"
    if name == "no-gcc":
        tools.mkdir()
        for tool in ("cmake", "readelf", "addr2line"):
            (tools / tool).symlink_to(f"/usr/bin/{tool}")
        return str(tools)
    if name.endswith("gnu-ld"):
        tools.mkdir()
        script("g++", LLD)
        (tools / "c++").symlink_to("g++")
        return f"{tools}:/usr/bin:/bin"
    if name in ("non-exec", "relink-failure"):
        tools.mkdir()
        script("g++", NON_EXEC if name == "non-exec" else RELINK_FAILURE)
        (tools / "c++").symlink_to("g++")
        return f"{tools}:/usr/bin:/bin"
    if name == "source-drift":
        tools.mkdir()
        script("addr2line", DRIFT.format(source=project / "src/common.cpp"))
        return f"{tools}:/usr/bin:/bin"
    return "/usr/bin:/bin"


def run(name: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / name, project)
        (WORK / "home").mkdir()
        path = tools_path(name, WORK / "tools", project)
        command = "verify" if name.startswith("verify-") else "dead"
        # CMake takes this compiler, so the link driver is a real GCC no capability probe named.
        driver = {"CXX": str(WORK / "tools/g++")} if "unprobed-driver" in name else {}
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                **driver,
                "PATH": path,
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
        report = json.loads(
            text.replace(str(project), "<root>").replace(str(WORK / "tools"), "<tools>")
        )
        if command == "verify":
            (report,) = [item for item in report["results"] if item["engine_name"] == "dead"]
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    findings = [{key: finding.get(key) for key in FINDING_FIELDS} for finding in report["findings"]]
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "evidence": report["evidence"],
        "required": report["required"],
        "summary": report["summary"],
        "targets": [
            {key: target.get(key) for key in TARGET_FIELDS} for target in report["targets"]
        ],
        "findings": sorted(findings, key=lambda item: json.dumps(item, sort_keys=True)),
        "extra": report["extra"],
        "tool_evidence": [
            {"name": item["name"], "returncode": item["returncode"]}
            for item in report["tool_evidence"]
        ],
    }


def banner(executable: str) -> str:
    output = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return output.splitlines()[0]


cases = {path.name: run(path.name) for path in sorted(FIXTURES.iterdir()) if path.is_dir()}
GOLDEN.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": banner("/usr/bin/g++"),
            "cmake": banner("/usr/bin/cmake"),
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} linker dead cases")
