"""The bundle smoke harness reports what it measured (#202 PR C).

scripts/bundle/smoke.sh is the only thing standing between a broken release
artifact and a user, so the question these tests ask is not "does it pass" but
"would it notice". They run it against stub bundles whose behaviour is known:
one that behaves, and several broken in one specific way each.

A stub bundle needs no python-build-standalone download, so these run
everywhere. What they cannot check is a real runtime — that is the smoke's own
job, run against a built bundle in the bundle-smoke workflow.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "bundle" / "smoke.sh"

sys.path.insert(0, str(ROOT / "scripts" / "bundle"))
from summarize_smoke import render  # noqa: E402

# Answers the four commands runs_here asks for, plus the three the next-path
# case walks through, and nothing else.
#
# Faking `next` is not a claim that the next path works: a stub proving itself
# would be worthless. It stands in for ici so the case's own shell logic — does
# it notice a page that reaches outside, does it refuse to call an unshipped
# analyzer a pass — can be exercised on any host. Whether the real next path
# runs from a real bundle is the bundle-smoke workflow's answer, not this file's.
GOOD_STUB = """#!/usr/bin/env bash
case "${1-}" in
  --version|-v) echo "ici 0.0.0-stub" ;;
  --help)       echo "Usage: ici [OPTIONS] COMMAND [ARGS]..." ;;
  doctor)       echo "Infra Root   Resolved   $PWD" ;;
  line)         echo "Total Volume: 1 Lines" ;;
  next)
    case "${2-}" in
      plan)   echo "1 check planned" ;;
      verify) mkdir -p .ici/next
              echo '{"schema": "stub"}' > .ici/next/result.json
              echo "PASS: 0 finding(s)" ;;
      report) mkdir -p .ici/next
              echo '<!doctype html><style>p{color:#111}</style><p>ok</p>' \
                > .ici/next/result.html
              echo "wrote .ici/next/result.html" ;;
      *)      echo "unknown: ${2-}" >&2; exit 2 ;;
    esac ;;
  *)            echo "unknown command: ${1-}" >&2; exit 2 ;;
esac
"""


def _arm(subcommand: str) -> str:
    """The `next <subcommand>) ... ;;` arm of GOOD_STUB, verbatim.

    Sliced out rather than repeated, because a copy that drifts from the stub by
    one space silently substitutes nothing and hands a negative test the good
    stub — which then passes while measuring the wrong bundle.
    """

    start = GOOD_STUB.index(f"      {subcommand})")
    return GOOD_STUB[start : GOOD_STUB.index(";;", start) + 2]


#: The three `next` arms of GOOD_STUB, each replaceable to break one of them.
NEXT_PLAN = _arm("plan")
NEXT_VERIFY = _arm("verify")
NEXT_REPORT = _arm("report")


def _replace(old: str, new: str, stub: str = GOOD_STUB) -> str:
    """Swap one arm of a stub, refusing a pattern that no longer matches.

    A substitution that quietly matches nothing leaves the good stub behind, and
    a negative test handed the good stub passes for the wrong reason.
    """

    assert old in stub, f"stub no longer contains:\n{old}"
    return stub.replace(old, new)


def _coloured(stub: str, program: str = "ici") -> str:
    """A stub whose help is coloured the way rich colours it on a CI runner.

    The escapes fall between "Usage:" and the program name, so a check matching
    the raw bytes for "Usage: ici" sees nothing — which is how seven of ten
    cases failed on CI while the bundle was perfectly fine.
    """

    coloured = (
        "\\033[1;33mUsage: \\033[0m\\033[1m" + program + " [OPTIONS] COMMAND [ARGS]...\\033[0m"
    )
    return stub.replace(
        '  --help)       echo "Usage: ici [OPTIONS] COMMAND [ARGS]..." ;;',
        f"  --help)       printf '{coloured}\\n' ;;",
    )


def _make_bundle(root: Path, stub: str = GOOD_STUB) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app" / "placeholder").write_text("x", encoding="utf-8")
    (root / "manifest.json").write_text('{"schema_id": "stub"}\n', encoding="utf-8")
    launcher = root / "bin" / "ici"
    launcher.write_text(stub, encoding="utf-8")
    launcher.chmod(0o755)
    return root


def _run(bundle: Path, work: Path, report: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SMOKE), str(bundle), str(work), "--json", str(report)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
    )


def _cases(report: Path) -> dict[str, str]:
    payload = json.loads(report.read_text(encoding="utf-8"))
    return {case["case"]: case["status"] for case in payload["cases"]}


def _detail(report: Path, case: str) -> str:
    payload = json.loads(report.read_text(encoding="utf-8"))
    return next(entry["detail"] for entry in payload["cases"] if entry["case"] == case)


@pytest.fixture
def report(tmp_path: Path) -> Path:
    return tmp_path / "smoke.json"


class TestItPassesAWorkingBundle:
    @pytest.fixture
    def result(self, tmp_path: Path, report: Path) -> subprocess.CompletedProcess[str]:
        bundle = _make_bundle(tmp_path / "bundle")
        return _run(bundle, tmp_path / "work", report)

    def test_it_succeeds(self, result: subprocess.CompletedProcess[str], report: Path) -> None:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in _cases(report).values()

    def test_it_reports_every_case_it_ran(
        self, result: subprocess.CompletedProcess[str], report: Path
    ) -> None:
        del result
        # Named individually so that dropping one is a test failure rather than a
        # quieter summary. Each is an acceptance criterion of #202.
        assert set(_cases(report)) == {
            "in-place",
            "relocated",
            "symlink",
            "clean-home",
            "read-only",
            "offline",
            "combined",
            "no-writes",
            "no-installs",
            "side-by-side",
            "next-path",
        }

    def test_the_report_is_a_schema_tagged_document(
        self, result: subprocess.CompletedProcess[str], report: Path
    ) -> None:
        del result
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["schema"] == "ici.next.bundle-smoke/v1"
        assert payload["pass"] + payload["fail"] + payload["blocked"] == len(payload["cases"])

    def test_a_case_it_could_not_measure_is_blocked_not_passed(
        self, result: subprocess.CompletedProcess[str], report: Path
    ) -> None:
        del result
        # The whole point of the third status. Whether any case is blocked here
        # depends on the host's namespace privileges, so the assertion is that
        # no case can report anything outside the three.
        assert set(_cases(report).values()) <= {"PASS", "FAIL", "BLOCKED"}


class TestItNoticesABrokenBundle:
    def test_a_launcher_that_does_not_run_fails_every_run_case(
        self, tmp_path: Path, report: Path
    ) -> None:
        bundle = _make_bundle(tmp_path / "bundle", stub="#!/usr/bin/env bash\nexit 1\n")
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1
        assert _cases(report)["in-place"] == "FAIL"
        assert _cases(report)["relocated"] == "FAIL"

    def test_a_launcher_that_does_not_run_cannot_pass_the_negative_cases(
        self, tmp_path: Path, report: Path
    ) -> None:
        # no-writes and no-installs both assert an absence, and a launcher that
        # dies on startup produces that absence for the wrong reason. Both cases
        # were written without this guard, and no-installs was silently reporting
        # PASS while env rejected its own argument order and ici never ran.
        bundle = _make_bundle(tmp_path / "bundle", stub="#!/usr/bin/env bash\nexit 1\n")
        _run(bundle, tmp_path / "work", report)
        cases = _cases(report)
        assert cases["no-writes"] == "FAIL"
        assert cases["no-installs"] in {"FAIL", "BLOCKED"}

    def test_a_launcher_that_only_answers_in_place_fails_relocation(
        self, tmp_path: Path, report: Path
    ) -> None:
        bundle = _make_bundle(tmp_path / "bundle")
        pinned = bundle / "bin" / "ici"
        # The defect a bundle with a build-time absolute path baked in would have.
        pinned.write_text(
            f'#!/usr/bin/env bash\n[ "$(cd "$(dirname "$0")/.." && pwd)" = "{bundle}" ] || exit 3\n'
            + GOOD_STUB.split("\n", 1)[1],
            encoding="utf-8",
        )
        pinned.chmod(0o755)
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1
        assert _cases(report)["in-place"] == "PASS"
        assert _cases(report)["relocated"] == "FAIL"

    def test_help_that_does_not_name_the_program_fails_under_colour_too(
        self, tmp_path: Path, report: Path
    ) -> None:
        # Stripping the escapes must not turn the check into one that passes on
        # anything: a coloured "-c" is still the defect.
        bundle = _make_bundle(tmp_path / "bundle", stub=_coloured(GOOD_STUB, program="-c"))
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1
        assert _cases(report)["in-place"] == "FAIL"

    def test_help_that_does_not_name_the_program_fails(self, tmp_path: Path, report: Path) -> None:
        # python -c leaves argv[0] as "-c"; this is the output that produced.
        bundle = _make_bundle(
            tmp_path / "bundle", stub=GOOD_STUB.replace("Usage: ici ", "Usage: -c ")
        )
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1
        assert _cases(report)["in-place"] == "FAIL"

    def test_writing_into_the_install_directory_fails(self, tmp_path: Path, report: Path) -> None:
        # What an unprecompiled bundle does on its first run: writes __pycache__.
        bundle = _make_bundle(
            tmp_path / "bundle",
            stub=GOOD_STUB.replace(
                '  line)         echo "Total Volume: 1 Lines" ;;',
                '  line)         mkdir -p "$(dirname "$0")/../app/__pycache__"\n'
                '                : > "$(dirname "$0")/../app/__pycache__/x.pyc"\n'
                '                echo "Total Volume: 1 Lines" ;;',
            ),
        )
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1
        assert _cases(report)["no-writes"] == "FAIL"

    def test_a_failure_anywhere_fails_the_run(self, tmp_path: Path, report: Path) -> None:
        bundle = _make_bundle(
            tmp_path / "bundle", stub=GOOD_STUB.replace("ici 0.0.0-stub", "0.0.0")
        )
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 1


class TestTheNextPathCaseNoticesItsOwnDefects:
    """#206 item 6: what the end-to-end case would let through.

    Every assertion here is that some plausible bundle defect comes back as
    something other than PASS. The stub's own `next` is a fake, so a passing
    run proves nothing about the next path — these prove the case is not a
    formality that reports PASS whatever it is handed.
    """

    def test_a_bundle_that_shipped_no_analyzer_is_blocked_not_passed(
        self, tmp_path: Path, report: Path
    ) -> None:
        # The whole reason this is a three-way case. `next verify` exits 3 when
        # a required check could not run, and a bundle missing its analyzer must
        # not read the same as a bundle that linted cleanly.
        stub = _replace(
            NEXT_VERIFY,
            # Saving the result before exiting, the way the real command does:
            # a stub that skipped that would make the case fail for the wrong
            # reason and this test pass without proving anything.
            """      verify) mkdir -p .ici/next
              echo '{"schema": "stub"}' > .ici/next/result.json
              echo "INCOMPLETE: 0 finding(s)"
              exit 3 ;;""",
        )
        bundle = _make_bundle(tmp_path / "bundle", stub=stub)
        result = _run(bundle, tmp_path / "work", report)
        assert _cases(report)["next-path"] == "BLOCKED", result.stdout
        assert result.returncode == 0, result.stdout

    def test_a_report_that_reaches_outside_fails(self, tmp_path: Path, report: Path) -> None:
        # An offline report that fetches a stylesheet renders differently on the
        # machine it was made for — and looks perfect on the machine that built it.
        stub = _replace(
            NEXT_REPORT,
            """      report) mkdir -p .ici/next
              printf '<!doctype html><link href=\"https://cdn.example.com/x.css\">\\n' \\
                > .ici/next/result.html ;;""",
        )
        bundle = _make_bundle(tmp_path / "bundle", stub=stub)
        result = _run(bundle, tmp_path / "work", report)
        assert _cases(report)["next-path"] == "FAIL", result.stdout
        assert result.returncode == 1
        assert "cdn.example.com" in _detail(report, "next-path")

    def test_a_plan_that_writes_into_the_project_fails(self, tmp_path: Path, report: Path) -> None:
        # #206 item 3: planning reads the project, it does not build in it.
        stub = _replace(
            NEXT_PLAN,
            '      plan)   mkdir -p .venv; : > .venv/pyvenv.cfg; echo "1 check planned" ;;',
        )
        bundle = _make_bundle(tmp_path / "bundle", stub=stub)
        result = _run(bundle, tmp_path / "work", report)
        assert _cases(report)["next-path"] == "FAIL", result.stdout
        assert result.returncode == 1

    def test_a_report_command_that_writes_no_page_fails(self, tmp_path: Path, report: Path) -> None:
        # Exit 0 is not the artifact. `ici next report` succeeding while the page
        # it promised is absent is exactly the reading this milestone keeps
        # finding: a command that did not do its job and said nothing about it.
        stub = _replace(NEXT_REPORT, '      report) echo "wrote nothing" ;;')
        bundle = _make_bundle(tmp_path / "bundle", stub=stub)
        result = _run(bundle, tmp_path / "work", report)
        assert _cases(report)["next-path"] == "FAIL", result.stdout
        assert result.returncode == 1


class TestStrictModeRefusesAnUnmeasuredCase:
    """Blocking is forced rather than waited for.

    Whether a host can create a namespace decides which cases the smoke can
    measure, so skipping when this one can would leave strict mode — the part
    that decides whether CI accepts an unmeasured case — never exercised. A
    stub unshare that refuses puts any host on the blocked path.
    """

    @pytest.fixture
    def without_namespaces(self, tmp_path: Path) -> dict[str, str]:
        shim = tmp_path / "shim"
        shim.mkdir()
        refuse = shim / "unshare"
        refuse.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        refuse.chmod(0o755)
        return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}

    def test_blocked_cases_fail_the_run_under_strict(
        self, tmp_path: Path, report: Path, without_namespaces: dict[str, str]
    ) -> None:
        bundle = _make_bundle(tmp_path / "bundle")
        result = _run(bundle, tmp_path / "work", report, ICI_SMOKE_STRICT="1", **without_namespaces)
        assert result.returncode == 2, result.stdout
        blocked = {case for case, status in _cases(report).items() if status == "BLOCKED"}
        assert blocked == {"read-only", "offline", "combined", "no-installs"}

    def test_blocked_cases_do_not_fail_the_run_by_default(
        self, tmp_path: Path, report: Path, without_namespaces: dict[str, str]
    ) -> None:
        # Default is lenient on purpose: a developer laptop without the
        # privilege should still get the eight cases it can measure.
        bundle = _make_bundle(tmp_path / "bundle")
        result = _run(bundle, tmp_path / "work", report, **without_namespaces)
        assert result.returncode == 0, result.stdout
        assert "BLOCKED" in set(_cases(report).values())

    def test_a_real_failure_still_outranks_a_blocked_case(
        self, tmp_path: Path, report: Path, without_namespaces: dict[str, str]
    ) -> None:
        bundle = _make_bundle(tmp_path / "bundle", stub="#!/usr/bin/env bash\nexit 1\n")
        result = _run(bundle, tmp_path / "work", report, ICI_SMOKE_STRICT="1", **without_namespaces)
        assert result.returncode == 1, result.stdout


class TestTheSummaryNamesWhatWasNotMeasured:
    """A table of eight passes reads exactly like a table of ten."""

    def _report(self, *statuses: str) -> dict:
        cases = [
            {"case": f"case-{index}", "status": status, "detail": "detail"}
            for index, status in enumerate(statuses)
        ]
        return {
            "schema": "ici.next.bundle-smoke/v1",
            "pass": statuses.count("PASS"),
            "fail": statuses.count("FAIL"),
            "blocked": statuses.count("BLOCKED"),
            "cases": cases,
        }

    def test_a_blocked_case_is_named_outside_the_table(self) -> None:
        summary = render(self._report("PASS", "BLOCKED"))
        body = summary.split("|---|---|---|", 1)[1].split("\n\n", 1)[1]
        assert "case-1" in body
        assert "does not support" in body

    def test_an_all_pass_run_adds_no_caveat(self) -> None:
        summary = render(self._report("PASS", "PASS"))
        assert "does not support" not in summary

    def test_every_case_reaches_the_table(self) -> None:
        summary = render(self._report("PASS", "FAIL", "BLOCKED"))
        assert all(f"|case-{index}|" in summary for index in range(3))

    def test_a_pipe_in_a_detail_does_not_break_the_table(self) -> None:
        report = self._report("FAIL")
        report["cases"][0]["detail"] = "ici line | head failed"
        row = next(line for line in render(report).splitlines() if line.startswith("|case-0|"))
        # Escaped, so the renderer still produces three cells rather than four.
        cells = re.split(r"(?<!\\)\|", row)[1:-1]
        assert cells == ["case-0", "FAIL", "ici line \\| head failed"], row

    def test_a_report_with_no_cases_says_so(self) -> None:
        assert "nothing was measured" in render({"schema": "x", "cases": []})


class TestColourIsNotContent:
    """A runner that forces colour must not change what the smoke concludes."""

    def test_a_coloured_help_still_names_the_program(self, tmp_path: Path, report: Path) -> None:
        bundle = _make_bundle(tmp_path / "bundle", stub=_coloured(GOOD_STUB))
        result = _run(bundle, tmp_path / "work", report)
        assert result.returncode == 0, result.stdout
        assert _cases(report)["in-place"] == "PASS"

    def test_the_namespace_cases_see_the_same_help(self, tmp_path: Path, report: Path) -> None:
        # runs_here travels into the namespace sub-shells by value; a helper it
        # calls that does not travel with it made --version look like it failed
        # in exactly these four cases.
        bundle = _make_bundle(tmp_path / "bundle", stub=_coloured(GOOD_STUB))
        _run(bundle, tmp_path / "work", report)
        for case in ("read-only", "offline", "combined"):
            assert _cases(report)[case] in {"PASS", "BLOCKED"}, _cases(report)

    def test_a_failure_message_quotes_what_it_saw(self, tmp_path: Path, report: Path) -> None:
        # Having to guess what a check saw is what made this take an extra CI
        # cycle to diagnose.
        bundle = _make_bundle(tmp_path / "bundle", stub=_coloured(GOOD_STUB, program="-c"))
        _run(bundle, tmp_path / "work", report)
        detail = json.loads(report.read_text(encoding="utf-8"))["cases"][0]["detail"]
        assert "Usage: -c" in detail, detail
