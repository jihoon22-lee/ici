"""Capture frozen legacy publication outcomes: `ici publish` and `ici verify --publish`.

    PYTHONPATH=<oracle>/src python capture_legacy_publish.py <out.json>

The frozen CLI runs in-process (typer CliRunner) with a scrubbed environment. The GitHub
calls of engines/publish.ReportPublisher are replaced by recorded stubs, so each case fixes
which stage fails: the Actions guard, target resolution, the HTML page, the upload (branch),
or the sticky comment. Every case records stdout, stderr and the exit code. `ici next
publish` with no destination is captured on a `[workspace]` root for the modern contract.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from typer.testing import CliRunner

import ici.engines.publish as publishing
from ici.__main__ import app

SCRUB = ("GITHUB_", "ICI_PUBLISH_", "RUNNER_", "CI")
SUITE = {
    "schema_version": "ici.result/v3",
    "suite_status": "PASS",
    "tem_score": None,
    "max_tem_score": 5.0,
    "results": [{"engine_name": "line", "status": "PASS", "summary": "ok"}],
    "baseline_comparison": None,
}
ACTIONS = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "team/repo", "GITHUB_TOKEN": "t"}
# name -> (environment, branch ok, comment ok, pages, write html)
CASES = {
    "outside_actions": ({"GITHUB_REPOSITORY": "team/repo", "GITHUB_TOKEN": "t"}, 1, 1, 0, 1),
    "no_repository": ({"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "t"}, 1, 1, 0, 1),
    "no_token": ({"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "team/repo"}, 1, 1, 0, 1),
    "html_missing": (ACTIONS, 1, 1, 0, 0),
    "upload_failure": (ACTIONS, 0, 1, 0, 1),
    "comment_failure": ({**ACTIONS, "PR": "7"}, 1, 0, 0, 1),
    "pushed_without_pages": (ACTIONS, 1, 1, 0, 1),
    "published_with_comment": ({**ACTIONS, "PR": "7"}, 1, 1, 1, 1),
}


def stub(branch_ok, comment_ok, pages):
    publishing.ReportPublisher._ensure_branch = lambda *_: bool(branch_ok)
    publishing.ReportPublisher._put_file = lambda *_: True
    publishing.ReportPublisher._check_pages = lambda *_: (
        (True, "https://team.github.io/repo") if pages else (False, None)
    )
    publishing.ReportPublisher._upsert_comment = lambda *_: (
        "https://github.com/team/repo/pull/7#issuecomment-1" if comment_ok else None
    )


def environment(base, values):
    env = {key: None for key in os.environ if key.startswith(SCRUB)}
    env.update({key: value for key, value in values.items() if key != "PR"})
    env["HOME"] = str(base / "home")
    env["XDG_CACHE_HOME"] = str(base / "home" / ".cache")
    if "PR" in values:
        event = base / "event.json"
        event.write_text(json.dumps({"pull_request": {"number": int(values["PR"])}}))
        env["GITHUB_EVENT_PATH"] = str(event)
    return env


def invoke(base, argv, env):
    previous = Path.cwd()
    os.chdir(base)
    try:
        outcome = CliRunner().invoke(app, argv, env=env, catch_exceptions=False)
    finally:
        os.chdir(previous)
    return {
        "argv": argv,
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout.replace(str(base), "<root>"),
        "stderr": outcome.stderr.replace(str(base), "<root>"),
    }


def legacy_project(base, write_html):
    (base / ".git").mkdir()
    (base / "main.py").write_text("def hello():\n    return 1\n")
    (base / "verify_report.json").write_text(json.dumps(SUITE))
    if write_html:
        (base / "verify_report.html").write_text("<!doctype html>saved")


def main():
    out = {"publish": {}, "verify": {}, "next_publish": {}}
    for name, (values, branch_ok, comment_ok, pages, write_html) in CASES.items():
        stub(branch_ok, comment_ok, pages)
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch).resolve()
            legacy_project(base, write_html)
            env = environment(base, values)
            out["publish"][name] = invoke(base, ["publish"], env)
            if name != "html_missing":
                # verify writes its own page; the run exit is captured without --publish too.
                plain = invoke(base, ["verify", "--html", "verify_report.html"], env)
                case = invoke(base, ["verify", "--html", "verify_report.html", "--publish"], env)
                case["run_exit_code"] = plain["exit_code"]
                case["publish_lines"] = [
                    line for line in case["stdout"].splitlines() if line.startswith("[publish]")
                ]
                del case["stdout"]
                out["verify"][name] = case
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch).resolve()
        (base / ".git").mkdir()
        (base / "main.py").write_text("def hello():\n    return 1\n")
        (base / "ici.toml").write_text(
            "schema_version = 1\n[workspace]\nname = 'demo'\n"
            "[[components]]\nid = 'app'\nroot = '.'\nlanguages = ['python']\n"
        )
        env = environment(base, {})
        run = invoke(base, ["next", "verify"], env)
        report = invoke(base, ["next", "report"], env)
        case = invoke(base, ["next", "publish"], env)
        case["setup_exit_codes"] = [run["exit_code"], report["exit_code"]]
        record = base / ".ici" / "next" / "publish.json"
        case["record"] = json.loads(record.read_text()) if record.exists() else None
        out["next_publish"]["not_configured"] = case
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
