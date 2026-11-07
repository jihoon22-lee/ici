"""Development-only frozen captures of next command options; never called by the Rust product.

    python -I capture_cli_options.py

Each case runs the immutable v0.13.0 CLI once in a fresh copy of a small two-component next
workspace (empty HOME, PATH=/usr/bin:/bin, analysis tools disabled) and records comparable
facts: the exit code, the class prefix of the first refusal line on stderr, the files the run
wrote outside `.ici/`, and for a JSON document (stdout or a written result) the selected scope:
doctor's profile and per-component non-omitted checks, a result's check ids and gate. Options:
`next doctor` --component/--python/--cpp/--profile/--config/--local-config, `next verify`
--component/--python/--cpp/--json/--require-full/--result/--config/--local-config, `next
report` --result/--page/--auxiliary, `next publish` --config/--local-config, `next diff`
--result, the top-level `ici init` and `ici diff` spellings, historical `ici publish`
--html/--json/--report-dir (outside and inside a stubbed Actions environment whose API URL is a
closed loopback port), and `ici analyze` / global refusals.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
OUT = ROOT / "tests/rust-parity/config/cli-options-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)

OFF = ("lint", "format", "type", "test", "coverage", "compat-runtime")


def workspace(components: tuple[str, ...] = ("app", "lib"), extra: str = "") -> str:
    text = 'schema_version = 1\n[workspace]\nname = "demo"\nprofile = "fast"\n'
    for name in components:
        language = "python" if name == "app" else "cpp"
        text += f'[[components]]\nid = "{name}"\nroot = "{name}"\nlanguages = ["{language}"]\n'
    text += "".join(f'[checks."python.{check}"]\nenabled = false\n' for check in OFF)
    return text + extra


SOURCES = {"app/main.py": "def f():\n    return 1\n", "lib/a.cpp": "int f() { return 1; }\n"}
TREE = {"ici.toml": workspace(), **SOURCES}
PY_TREE = {"ici.toml": workspace(("app",)), "app/main.py": SOURCES["app/main.py"]}
ALT = {**TREE, "alt.toml": workspace(("app",))}
LOCAL = {**TREE, "local.toml": 'components.app.python.executable = "/usr/bin/python3"\n'}
LOCAL_BAD = {**TREE, "local.toml": "workspace.name = 1\n"}
PUBLISH = {
    **PY_TREE,
    # token_env names PATH (always set) and the plain-HTTP api_url is refused before any request.
    "pub.toml": workspace(
        ("app",),
        '[publish]\nrepo = "o/r"\nbranch = "gh-pages"\napi_url = "http://example.invalid"\n'
        'token_env = "PATH"\n',
    ),
    "local.toml": 'output = "out"\n',
}
# Without `limitations`, which the published ici-auxiliary-v1 schema requires and frozen
# `load_auxiliary` never checked (the `auxiliary-without-limitations` case).
AUXILIARY_BARE = (
    '{"schema_id": "ici.auxiliary", "schema_version": 1, "gate": "NOT_EVALUATED", '
    '"evidence": "imported", "records": [], "format": "sarif", "scope": "app", '
    '"source": {"path": "x.sarif", "sha256": "' + "0" * 64 + '"}, '
    '"imported_at": "2026-01-01T00:00:00Z", "observed_at": null}\n'
)
AUXILIARY = AUXILIARY_BARE.replace('"records"', '"limitations": [], "records"')
LEGACY_BARE = {"src/a.py": "x = 1\n"}
PAGE = "<html><body>report</body></html>\n"
SUITE = (
    json.dumps(
        {
            "schema_version": "ici.result/v3",
            "suite_status": "PASS",
            "tem_score": None,
            "max_tem_score": 5.0,
            "results": [{"engine_name": "line", "status": "PASS", "summary": "ok"}],
            "baseline_comparison": None,
        }
    )
    + "\n"
)
# The default verify_report.json is present: without a readable suite frozen `cmd_publish` passed
# None to `redact_suite` and crashed (AttributeError, exit 1; the `*-without-json` cases).
LEGACY = {**LEGACY_BARE, "verify_report.json": SUITE}
ACTIONS = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "team/repo",
    "GITHUB_TOKEN": "t",
    "GITHUB_API_URL": "https://127.0.0.1:9",
}
# name: (files, argv after `ici`, steps before the case run (each an argv)[, extra environment])
VERIFY = ["next", "verify", "--python", "--result", "out/base.json"]
REPORT = ["next", "report", "--result", "out/base.json", "--out", "out/page.html"]
PUBLISH_ARGS = ["--result", "out/base.json", "--out", "out/page.html"]
CASES = {
    "doctor/default": (TREE, ["next", "doctor", "--json"], []),
    "doctor/python": (TREE, ["next", "doctor", "--json", "--python"], []),
    "doctor/cpp": (TREE, ["next", "doctor", "--json", "--cpp"], []),
    "doctor/component": (TREE, ["next", "doctor", "--json", "--component", "lib"], []),
    "doctor/component-unknown": (TREE, ["next", "doctor", "--json", "--component", "x"], []),
    "doctor/profile-deep": (TREE, ["next", "doctor", "--json", "--profile", "deep"], []),
    "doctor/profile-invalid": (TREE, ["next", "doctor", "--json", "--profile", "huge"], []),
    "doctor/config": (ALT, ["next", "doctor", "--json", "--config", "alt.toml"], []),
    "doctor/config-missing": (TREE, ["next", "doctor", "--json", "--config", "no.toml"], []),
    "doctor/local-config": (
        LOCAL,
        ["next", "doctor", "--json", "--local-config", "local.toml"],
        [],
    ),
    "doctor/local-config-invalid": (
        LOCAL_BAD,
        ["next", "doctor", "--json", "--local-config", "local.toml"],
        [],
    ),
    "doctor/local-config-missing": (
        TREE,
        ["next", "doctor", "--json", "--local-config", "no.toml"],
        [],
    ),
    "verify/json-python": (TREE, ["next", "verify", "--python", "--json"], []),
    "verify/text-python": (TREE, ["next", "verify", "--python"], []),
    "verify/result": (TREE, VERIFY, []),
    "verify/component": (TREE, ["next", "verify", "--json", "--component", "app"], []),
    "verify/component-unknown": (TREE, ["next", "verify", "--json", "--component", "x"], []),
    "verify/cpp-without-cpp": (PY_TREE, ["next", "verify", "--json", "--cpp"], []),
    "verify/require-full-partial": (TREE, ["next", "verify", "--json", "--require-full"], []),
    "verify/require-full-selection": (
        TREE,
        ["next", "verify", "--json", "--python", "--require-full"],
        [],
    ),
    "verify/config": (ALT, ["next", "verify", "--json", "--config", "alt.toml"], []),
    "verify/config-missing": (TREE, ["next", "verify", "--json", "--config", "no.toml"], []),
    "verify/local-config": (
        LOCAL,
        ["next", "verify", "--json", "--python", "--local-config", "local.toml"],
        [],
    ),
    "verify/local-config-invalid": (
        LOCAL_BAD,
        ["next", "verify", "--json", "--local-config", "local.toml"],
        [],
    ),
    "report/result-page": (
        TREE,
        ["next", "report", "--result", "out/base.json", "--out", "out/page.html"],
        [VERIFY],
    ),
    "report/result-missing": (TREE, ["next", "report", "--result", "out/none.json"], []),
    "report/result-unreadable": (
        {**TREE, "out/bad.json": "{}\n"},
        ["next", "report", "--result", "out/bad.json", "--out", "out/page.html"],
        [],
    ),
    "report/auxiliary": (
        {**TREE, "aux.json": AUXILIARY},
        [
            "next",
            "report",
            "--result",
            "out/base.json",
            "--out",
            "out/page.html",
            "--auxiliary",
            "aux.json",
        ],
        [VERIFY],
    ),
    "report/auxiliary-without-limitations": (
        {**TREE, "aux.json": AUXILIARY_BARE},
        [
            "next",
            "report",
            "--result",
            "out/base.json",
            "--out",
            "out/page.html",
            "--auxiliary",
            "aux.json",
        ],
        [VERIFY],
    ),
    "report/auxiliary-invalid": (
        {**TREE, "aux.json": "{}\n"},
        [
            "next",
            "report",
            "--result",
            "out/base.json",
            "--out",
            "out/page.html",
            "--auxiliary",
            "aux.json",
        ],
        [VERIFY],
    ),
    "report/auxiliary-missing": (
        TREE,
        [
            "next",
            "report",
            "--result",
            "out/base.json",
            "--out",
            "out/page.html",
            "--auxiliary",
            "out/none.json",
        ],
        [VERIFY],
    ),
    "report/auxiliary-with-sarif": (
        TREE,
        [
            "next",
            "report",
            "--result",
            "out/base.json",
            "--sarif",
            "out/r.sarif",
            "--auxiliary",
            "x.json",
        ],
        [VERIFY],
    ),
    "publish/config-not-configured": (
        {**PY_TREE, "alt.toml": workspace(("app",))},
        ["next", "publish", "--config", "alt.toml", "--result", "out/base.json"],
        [VERIFY],
    ),
    "publish/config-missing": (
        PY_TREE,
        ["next", "publish", "--config", "no.toml", "--result", "out/base.json"],
        [VERIFY],
    ),
    "publish/local-config-missing": (
        PY_TREE,
        ["next", "publish", "--local-config", "no.toml", "--result", "out/base.json"],
        [VERIFY],
    ),
    "publish/config-unconfigured-with-page": (
        {**PY_TREE, "alt.toml": workspace(("app",))},
        ["next", "publish", "--config", "alt.toml", *PUBLISH_ARGS],
        [VERIFY, REPORT],
    ),
    "publish/config-configured-with-page": (
        PUBLISH,
        ["next", "publish", "--config", "pub.toml", *PUBLISH_ARGS],
        [VERIFY, REPORT],
    ),
    "publish/local-config-with-page": (
        PUBLISH,
        ["next", "publish", "--config", "pub.toml", "--local-config", "local.toml", *PUBLISH_ARGS],
        [VERIFY, REPORT],
    ),
    "publish/config-without-page": (
        PUBLISH,
        [
            "next",
            "publish",
            "--config",
            "pub.toml",
            "--result",
            "out/base.json",
            "--out",
            "out/none.html",
        ],
        [VERIFY],
    ),
    "publish/configured-without-page": (
        PUBLISH,
        [
            "next",
            "publish",
            "--config",
            "pub.toml",
            "--local-config",
            "local.toml",
            "--result",
            "out/base.json",
            "--out",
            "out/none.html",
        ],
        [VERIFY],
    ),
    # schema_version = 1 [publish] value types and a local overlay naming a publish key.
    **{
        f"publish/v1-invalid-{key.replace('_', '-')}-type": (
            {**PY_TREE, "bad.toml": workspace(("app",), f"[publish]\n{key} = 1\n")},
            ["next", "publish", "--config", "bad.toml", "--result", "out/base.json"],
            [VERIFY],
        )
        for key in ("repo", "api_url", "server_url", "branch", "token_env")
    },
    "publish/v1-publish-not-a-table": (
        {
            **PY_TREE,
            "bad.toml": workspace(("app",)).replace(
                "schema_version = 1\n", "schema_version = 1\npublish = 1\n", 1
            ),
        },
        ["next", "publish", "--config", "bad.toml", "--result", "out/base.json"],
        [VERIFY],
    ),
    "publish/local-overlay-publish-key": (
        {**PY_TREE, "local.toml": 'publish.repo = "x/y"\n'},
        ["next", "publish", "--local-config", "local.toml", "--result", "out/base.json"],
        [VERIFY],
    ),
    "diff/next-spelling": (
        TREE,
        ["next", "diff", "out/base.json", "--result", "out/base.json"],
        [VERIFY],
    ),
    "diff/next-missing-baseline": (TREE, ["next", "diff", "out/none.json"], []),
    "diff/next-missing-current": (
        TREE,
        ["next", "diff", "out/base.json", "--result", "out/none.json"],
        [VERIFY],
    ),
    "init/top-level": (SOURCES, ["init"], []),
    "init/top-level-preview": (SOURCES, ["init", "--preview"], []),
    "init/top-level-existing": (TREE, ["init"], []),
    # Historical `ici publish` (`__main__.cmd_publish`) on a configless legacy tree. Every Actions
    # case points GITHUB_API_URL at a closed loopback port, so nothing leaves the machine.
    "legacy-publish/html-outside-actions": (
        {**LEGACY, "custom.html": PAGE},
        ["publish", "--html", "custom.html"],
        [],
        {},
    ),
    "legacy-publish/html-missing": (LEGACY, ["publish", "--html", "custom.html"], [], ACTIONS),
    "legacy-publish/html-default-missing": (LEGACY, ["publish"], [], ACTIONS),
    "legacy-publish/html-upload-refused": (
        {**LEGACY, "custom.html": PAGE},
        ["publish", "--html", "custom.html"],
        [],
        ACTIONS,
    ),
    "legacy-publish/html-without-json": (
        {**LEGACY_BARE, "custom.html": PAGE},
        ["publish", "--html", "custom.html"],
        [],
        {},
    ),
    "legacy-publish/json-valid-outside-actions": (
        {**LEGACY, "r.json": SUITE},
        ["publish", "--json", "r.json"],
        [],
        {},
    ),
    "legacy-publish/json-invalid": (
        {**LEGACY_BARE, "r.json": "{\n"},
        ["publish", "--json", "r.json"],
        [],
        {},
    ),
    "legacy-publish/report-dir-outside-actions": (
        {**LEGACY, "d/verify_report.html": PAGE, "d/verify_report.json": SUITE},
        ["publish", "--report-dir", "d"],
        [],
        {},
    ),
    "legacy-publish/report-dir-missing-html": (
        {**LEGACY, "d/verify_report.json": SUITE},
        ["publish", "--report-dir", "d"],
        [],
        ACTIONS,
    ),
    "legacy-publish/report-dir-invalid-json": (
        {**LEGACY, "d/verify_report.html": PAGE, "d/verify_report.json": "{\n"},
        ["publish", "--report-dir", "x=d"],
        [],
        {},
    ),
    "legacy-publish/report-dir-upload-refused": (
        {**LEGACY, "d/verify_report.html": PAGE, "d/verify_report.json": SUITE},
        ["publish", "--report-dir", "x=d"],
        [],
        ACTIONS,
    ),
    "analyze/component-unknown": (TREE, ["analyze", "--component", "nope"], []),
    "analyze/profile-invalid": (TREE, ["analyze", "--profile", "huge"], []),
    "analyze/config-invalid": (
        {**TREE, "bad.toml": "workspace = 1\n"},
        ["analyze", "--config", "bad.toml"],
        [],
    ),
    "analyze/baseline-missing": (
        TREE,
        ["analyze", "--python", "--baseline", "out/none.json"],
        [],
    ),
    "diff/next-unreadable-baseline": (
        {**TREE, "out/bad.json": "{}\n"},
        ["next", "diff", "out/bad.json", "--result", "out/base.json"],
        [VERIFY],
    ),
    "diff/top-spelling": (TREE, ["diff", "out/base.json", "--result", "out/base.json"], [VERIFY]),
    "diff/top-missing-baseline": (TREE, ["diff", "out/none.json"], []),
    "diff/top-unreadable-baseline": (
        {**TREE, "out/bad.json": "{}\n"},
        ["diff", "out/bad.json", "--result", "out/base.json"],
        [VERIFY],
    ),
    "global/unknown-command": (TREE, ["bogus"], []),
    "global/unknown-option": (TREE, ["--bogus"], []),
    "global/version": (TREE, ["--version"], []),
    "global/missing-command": (TREE, [], []),
}
CLASS = re.compile(r"^([a-z][a-z-]*): ")


def scope_facts(document: object) -> object:
    if not isinstance(document, dict):
        return None
    if isinstance(document.get("components"), list) and "profile" in document:
        return {
            "profile": document["profile"],
            "components": {
                item["id"]: sorted(
                    check["check"] for check in item["checks"] if check["status"] != "omitted"
                )
                for item in document["components"]
            },
        }
    if isinstance(document.get("checks"), list) and "gate" in document:
        return {
            "checks": sorted(check["check_id"] for check in document["checks"]),
            "gate": document["gate"]["selected"],
        }
    return None


def run(files: dict, argv: list, steps: list, extra: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cli-options-"))
    try:
        project = work / "project"
        project.mkdir()
        # The project is its own VCS boundary (frozen and native both stop at `.hg`).
        (project / ".hg").mkdir()
        (work / "home").mkdir()
        for name, text in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work / "home"),
            "GIT_CEILING_DIRECTORIES": str(work),
            "PYTHONPATH": str(ORACLE / "src"),
            **extra,
        }

        def call(arguments: list) -> subprocess.CompletedProcess:
            return subprocess.run(
                [PYTHON, "-m", "ici", *arguments],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )

        for step in steps:
            call(step)
        before = {path for path in project.rglob("*") if path.is_file()}
        completed = call(argv)
        written = sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file()
            and path not in before
            and ".ici" not in path.relative_to(project).parts
        )
        try:
            stdout_facts = scope_facts(json.loads(completed.stdout))
        except json.JSONDecodeError:
            stdout_facts = None
        result_facts = None
        if "--result" in argv and argv[:2] == ["next", "verify"]:
            target = project / argv[argv.index("--result") + 1]
            if target.is_file():
                result_facts = scope_facts(json.loads(target.read_text(encoding="utf-8")))
        line = next(
            (
                line
                for line in completed.stderr.splitlines()
                if (match := CLASS.match(line)) and match.group(1) not in ("root", "scope")
            ),
            None,
        )
        refusal = CLASS.match(line).group(1) if line else None
        outcome = next(
            (
                line.replace(str(project), "<root>")
                for line in completed.stdout.splitlines()
                if line.startswith(("publish: ", "incompatible: ", "[publish] "))
                or " new, " in line
            ),
            None,
        )
        message = line.replace(str(project), "<root>") if line else None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "exit_code": completed.returncode,
        "refusal": refusal,
        "message": message,
        "outcome": outcome,
        "written": written,
        "stdout": stdout_facts,
        "result": result_facts,
    }


def main() -> None:
    cases = {}
    for name, (files, argv, steps, *rest) in CASES.items():
        extra = rest[0] if rest else {}
        cases[name] = {
            "files": files,
            "argv": argv,
            "steps": steps,
            "env": extra,
            "frozen": run(files, argv, steps, extra),
        }
    python = subprocess.check_output([PYTHON, "--version"], text=True).strip()
    OUT.write_text(
        json.dumps(
            {"oracle": EXPECTED, "python": python, "cases": cases},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} frozen next option cases with {python}", file=sys.stderr)


if __name__ == "__main__":
    main()
