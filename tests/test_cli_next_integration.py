"""WP22-D — declared integration cases as next-path tasks (#220 item 6).

The promises under test:

- a case is **declared**, never discovered: a component with no
  ``[[components.integrations]]`` entries does not see the check, and the
  check is deep-profile only — the offline default run never invokes a
  real process against real artifacts or declared external services
- ``argv`` is typed, not a shell string: ``{python:NAME}`` resolves against
  declared interpreters, ``{artifact:BUILD/PATH}`` only against a path a
  linked build's artifact contract claims — anything else is *blocked with
  the reason named*, not skipped and never guessed
- the outcome distinguishes what the issue asks it to: a finished run that
  broke its contract is a measured finding; a timed-out, cancelled or
  never-started run is an incomplete check, not a pass and not a finding;
  a declared ``requires`` is shown in the plan and echoed as a limitation
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from ici.__main__ import app
from ici.adapters.providers.integration import IntegrationCaseProvider
from ici.config.composition import compose
from ici.config.errors import NextConfigError
from ici.config.schema import read_component, read_root
from ici.execution.process import Outcome, TaskOutcome, TaskSpec

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

COMPONENT = (
    '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
    'sources = ["src/**/*.py"]\n'
    '[components.python]\nexecutable = ".venv/bin/python3"\n'
)

CASE = (
    '[[components.integrations]]\nname = "cli-help"\n'
    'argv = ["{python:declared}", "-c", "print(1)"]\n'
    'expected_exit = 0\nstdout_contains = ["1"]\n'
)


def _workspace(
    root: Path,
    *,
    component: str = COMPONENT,
    cases: str = CASE,
    header: str = HEADER,
    interpreter: bool = True,
) -> None:
    (root / "app" / "src").mkdir(parents=True)
    (root / "app" / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    if interpreter:
        venv = root / "app" / ".venv" / "bin"
        venv.mkdir(parents=True)
        link = venv / "python3"
        if not link.exists():
            link.symlink_to("/usr/bin/python3")
    (root / "ici.toml").write_text(header + component + cases, encoding="utf-8")


def _outcome(
    env: dict[str, str],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(
        name="app.integration.case",
        argv=("/bin/true",),
        cwd=Path.cwd(),
        environment=env,
    )
    return TaskOutcome(
        spec=spec, outcome=outcome, exit_code=exit_code, stdout=stdout, stderr=stderr
    )


CONTRACT = {
    "ICI_CASE": "cli-help",
    "ICI_CASE_SOURCE": "ici.toml",
    "ICI_CASE_REQUIRED": "1",
    "ICI_EXPECTED_EXIT": "0",
    "ICI_STDOUT_MUST": '["ready"]',
    "ICI_STDERR_MUST": "[]",
    "ICI_STDOUT_NEVER": '["traceback"]',
    "ICI_STDERR_NEVER": "[]",
    "ICI_OUTPUT_ASSERTS": "[]",
    "ICI_OUT_NOT_BEFORE": "0",
    "ICI_REQUIRES": "[]",
}


# --- schema and composition -------------------------------------------------


class TestTheDeclarationIsChecked:
    """A malformed case is a config error, not a silent skip."""

    def _problems(self, text: str) -> str:
        try:
            read_root(text, path="root.toml")
        except NextConfigError as error:
            return str(error)
        return ""

    def test_a_valid_case_parses(self) -> None:
        document = read_root(HEADER + COMPONENT + CASE, path="root.toml")
        (component,) = document.components
        assert component.integrations is not None
        (case,) = component.integrations
        assert case.name is not None and case.name.value == "cli-help"
        assert case.argv is not None and case.argv.value[0] == "{python:declared}"

    def test_an_unknown_key_is_refused(self) -> None:
        case = CASE + "bogus = true\n"
        assert "bogus" in self._problems(HEADER + COMPONENT + case)

    def test_a_duplicate_name_is_refused(self) -> None:
        cases = CASE + CASE
        assert "unique" in self._problems(HEADER + COMPONENT + cases)

    def test_argv0_must_be_a_typed_placeholder(self) -> None:
        case = CASE.replace('"{python:declared}"', '"pytest"')
        assert "typed placeholder" in self._problems(HEADER + COMPONENT + case)

    def test_a_partial_placeholder_is_refused(self) -> None:
        case = CASE.replace('"print(1)"', '"{python:"')
        assert "placeholder" in self._problems(HEADER + COMPONENT + case)

    def test_an_env_name_may_not_carry_the_contract_prefix(self) -> None:
        case = CASE + '[components.integrations.env]\nICI_CASE = "x"\n'
        assert "ICI_" in self._problems(HEADER + COMPONENT + case)

    def test_an_output_path_may_not_escape_the_workspace(self) -> None:
        case = CASE + '[[components.integrations.output_artifacts]]\npath = "../escape.txt"\n'
        assert "contained" in self._problems(HEADER + COMPONENT + case)

    def test_a_case_cap_is_enforced(self) -> None:
        cases = "".join(
            f'[[components.integrations]]\nname = "case-{n}"\nargv = ["{{python:declared}}"]\n'
            for n in range(33)
        )
        assert "at most" in self._problems(HEADER + COMPONENT + cases)


class TestCompositionAnchorsTheCase:
    """Paths in a case resolve like every other declared path (#203)."""

    def test_output_artifacts_anchor_to_the_declaring_file(self) -> None:
        child = (
            'schema_version = 1\n[component]\nroot = "."\n'
            'languages = ["python"]\nsources = ["src/**/*.py"]\n'
            '[[component.integrations]]\nname = "writer"\n'
            'argv = ["{python:declared}"]\n'
            '[[component.integrations.output_artifacts]]\npath = "out/result.txt"\n'
        )
        root = HEADER + '[[components]]\nid = "app"\nconfig = "app/ici.toml"\n'
        config = compose(
            read_root(root, path="root.toml"),
            {"app": read_component(child, path="app/ici.toml")},
        )
        effective = config.component("app")
        assert effective is not None
        (case,) = effective.integrations
        (output,) = case.output_artifacts
        assert output.path == "app/out/result.txt"
        assert case.declared_in == "app/ici.toml"

    def test_a_python_target_path_anchors_bare_names_do_not(self) -> None:
        case = (
            '[[components.integrations]]\nname = "targets"\n'
            'argv = ["{python:declared}"]\n'
            "[components.integrations.python_targets]\n"
            'runner = ".venv/bin/py"\nfrozen = "python3"\n'
        )
        config = compose(read_root(HEADER + COMPONENT + case, path="root.toml"))
        effective = config.component("app")
        assert effective is not None
        targets = dict(effective.integrations[0].python_targets)
        assert targets["frozen"] == "python3"  # a bare name stays a PATH lookup
        # a path anchors to its declaring file — here the workspace root
        assert targets["runner"] == ".venv/bin/py"


# --- planning ---------------------------------------------------------------


class TestPlanningIsOptInAndDeclared:
    def test_a_component_without_cases_never_sees_the_check(self, tmp_path, monkeypatch) -> None:
        _workspace(tmp_path, cases="")
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert plan.exit_code == 0, plan.output
        assert "integration" not in plan.output

    def test_cases_expand_into_one_task_each(self, tmp_path, monkeypatch) -> None:
        cases = CASE + (
            '[[components.integrations]]\nname = "second case"\n'
            'argv = ["{python:declared}", "-V"]\n'
        )
        _workspace(tmp_path, cases=cases)
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert plan.exit_code == 0, plan.output
        assert "app.integration.cli-help:" in plan.output
        assert "app.integration.second-case:" in plan.output

    def test_standard_and_fast_do_not_ask(self, tmp_path, monkeypatch) -> None:
        _workspace(tmp_path)
        monkeypatch.chdir(tmp_path)

        for profile in ("standard", "fast"):
            plan = runner.invoke(app, ["next", "plan", "--profile", profile])
            assert plan.exit_code == 0, plan.output
            assert "integration" not in plan.output

    def test_a_language_filter_excludes_the_domain_check(self, tmp_path, monkeypatch) -> None:
        _workspace(tmp_path)
        monkeypatch.chdir(tmp_path)

        # ``--cpp`` on a python-only component selects nothing at all — the
        # declared case does not sneak in through the language filter.
        plan = runner.invoke(app, ["next", "plan", "--profile", "deep", "--cpp"])

        assert plan.exit_code == 2
        assert "no component selected a check" in plan.output

    def test_requires_is_shown_in_the_plan(self, tmp_path, monkeypatch) -> None:
        case = CASE + 'requires = ["network", "service:license-server"]\n'
        _workspace(tmp_path, cases=case)
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert plan.exit_code == 0, plan.output
        assert "requires network, service:license-server" in plan.output

    def test_requires_is_in_the_plan_document(self, tmp_path, monkeypatch) -> None:
        case = CASE + 'requires = ["network"]\n'
        _workspace(tmp_path, cases=case)
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep", "--json"])

        assert plan.exit_code == 0, plan.output
        document = json.loads(plan.output.strip().splitlines()[-1])
        checks = {item["task_id"]: item for entry in document["plans"] for item in entry["checks"]}
        assert checks["app.integration.cli-help"]["requires"] == ["network"]


class TestPlaceholdersResolveOrBlock:
    """A case may only invoke what the workspace's contract already names."""

    def _build_workspace(self, root: Path, argv0: str, *, required: bool = True) -> None:
        (root / "build" / "native").mkdir(parents=True)
        binary = root / "build" / "native" / "app"
        binary.write_bytes(b"\x7fELFfake")
        binary.chmod(0o755)
        component = (
            '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
            'sources = ["src/**/*.py"]\nbuild = "native"\n'
            '[components.python]\nexecutable = ".venv/bin/python3"\n'
        )
        build = (
            '[builds.native]\nsystem = "cmake"\ndirectory = "build/native"\n'
            'artifacts = ["app", "lib*.so"]\n'
        )
        case = f'[[components.integrations]]\nname = "uses"\nargv = ["{argv0}"]\n' + (
            "" if required else "required = false\n"
        )
        _workspace(root, component=build + component, cases=case)

    def test_a_contracted_artifact_resolves(self, tmp_path, monkeypatch) -> None:
        self._build_workspace(tmp_path, "{artifact:native/app}")
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert plan.exit_code == 0, plan.output
        assert f"{tmp_path}/build/native/app" in plan.output

    def test_an_uncontracted_path_is_blocked(self, tmp_path, monkeypatch) -> None:
        self._build_workspace(tmp_path, "{artifact:native/other.so}")
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert plan.exit_code == 0, plan.output
        assert "not covered by build 'native'" in plan.output

    def test_an_unbuilt_artifact_is_blocked_not_guessed(self, tmp_path, monkeypatch) -> None:
        self._build_workspace(tmp_path, "{artifact:native/app}")
        (tmp_path / "build" / "native" / "app").unlink()
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert "blocked" in plan.output
        assert "not covered" in plan.output

    def test_a_build_the_component_does_not_link_is_blocked(self, tmp_path, monkeypatch) -> None:
        self._build_workspace(tmp_path, "{artifact:elsewhere/app}")
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert "not linked" in plan.output

    def test_an_unknown_python_target_is_blocked(self, tmp_path, monkeypatch) -> None:
        _workspace(tmp_path, cases=CASE.replace("{python:declared}", "{python:nope}"))
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert "unknown python target 'nope'" in plan.output

    def test_a_component_without_an_interpreter_is_blocked(self, tmp_path, monkeypatch) -> None:
        bare = (
            '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
            'sources = ["src/**/*.py"]\n'
        )
        _workspace(tmp_path, component=bare, interpreter=False)
        monkeypatch.chdir(tmp_path)

        plan = runner.invoke(app, ["next", "plan", "--profile", "deep"])

        assert "no declared Python interpreter" in plan.output


# --- the provider judges the outcome -----------------------------------------


class TestTheOutcomeIsJudged:
    provider = IntegrationCaseProvider()

    def test_a_clean_run_is_no_findings(self) -> None:
        parsed = self.provider.parse(_outcome(CONTRACT, stdout="ready now"))
        assert parsed.findings == ()
        assert not parsed.failed_to_parse
        (measurement,) = parsed.measurements
        assert measurement.name == "integration.assertions"
        assert measurement.denominator == 3  # exit + must + never

    def test_a_wrong_exit_is_a_measured_finding(self) -> None:
        parsed = self.provider.parse(_outcome(CONTRACT, exit_code=3, stdout="ready"))
        rules = {finding.rule_id for finding in parsed.findings}
        assert "integration.exit" in rules
        assert all(f.evidence.name == "MEASURED" for f in parsed.findings)

    def test_stream_assertions_are_measured_findings(self) -> None:
        parsed = self.provider.parse(_outcome(CONTRACT, stdout="traceback oops"))
        rules = {finding.rule_id for finding in parsed.findings}
        assert "integration.stdout" in rules

    def test_an_advisory_case_keeps_its_findings_advisory(self) -> None:
        env = {**CONTRACT, "ICI_CASE_REQUIRED": "0"}
        parsed = self.provider.parse(_outcome(env, exit_code=9, stdout="ready"))
        assert all(f.severity == "medium" for f in parsed.findings)

    def test_an_unfinished_run_is_not_an_answer(self) -> None:
        parsed = self.provider.parse(_outcome(CONTRACT, outcome=Outcome.TIMED_OUT))
        assert parsed.failed_to_parse is not None
        assert parsed.findings == ()

    def test_a_declared_requirement_is_recorded_as_a_limitation(self) -> None:
        env = {**CONTRACT, "ICI_REQUIRES": '["network"]'}
        parsed = self.provider.parse(_outcome(env, stdout="ready"))
        assert any("network" in item for item in parsed.limitations)

    def test_a_stale_output_fails_the_assertion(self, tmp_path) -> None:
        stale = tmp_path / "result.txt"
        stale.write_text("old", encoding="utf-8")
        past = time.time() + 60  # the run has not started yet
        env = {
            **CONTRACT,
            "ICI_OUTPUT_ASSERTS": json.dumps([{"path": str(stale), "min_size": 1}]),
            "ICI_OUT_NOT_BEFORE": repr(past),
        }
        parsed = self.provider.parse(_outcome(env, stdout="ready"))
        assert any("predates" in f.message for f in parsed.findings)

    def test_a_missing_output_fails_the_assertion(self, tmp_path) -> None:
        env = {
            **CONTRACT,
            "ICI_OUTPUT_ASSERTS": json.dumps(
                [{"path": str(tmp_path / "absent.txt"), "min_size": 1}]
            ),
        }
        parsed = self.provider.parse(_outcome(env, stdout="ready"))
        assert any("was not produced" in f.message for f in parsed.findings)


# --- end to end --------------------------------------------------------------


class TestTheRunEndToEnd:
    def test_a_passing_case_leaves_no_findings(self, tmp_path, monkeypatch) -> None:
        _workspace(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["next", "verify", "--profile", "deep"])

        payload = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
        integration_findings = [
            f for f in payload["findings"] if f["rule_id"].startswith("integration.")
        ]
        assert integration_findings == [], result.output

    def test_a_failing_case_is_measured_not_hidden(self, tmp_path, monkeypatch) -> None:
        case = (
            '[[components.integrations]]\nname = "boom"\n'
            'argv = ["{python:declared}", "-c", "import sys; sys.exit(3)"]\n'
        )
        _workspace(tmp_path, cases=case)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["next", "verify", "--profile", "deep"])

        payload = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
        assert any(
            f["rule_id"] == "integration.exit" and "boom" in f["message"]
            for f in payload["findings"]
        ), result.output
