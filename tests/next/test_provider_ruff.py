"""Ruff as a provider, and the two things it must not be able to do.

#206 item 2 is one sentence with two refusals in it — *"기존 pyproject/ruff.toml을
존중하고 자동 format/fix를 실행하지 않는다."* Both are tested over the whole space
of requests the builder accepts rather than over one example argv, because an
option added later is exactly the way a fixed command stops being fixed.

The third thing here is the one this series keeps coming back to: output that
cannot be read is not a clean file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.adapters.providers.base import STRICT, observe
from ici.adapters.providers.ruff import (
    FIXED_HEAD,
    FORBIDDEN_OPTIONS,
    RUFF_CONTRACT,
    RuffProvider,
    RuffRequest,
    parse_ruff_json,
)
from ici.domain.enums import TaskState
from ici.execution.process import Outcome, TaskOutcome
from ici.execution.process import TaskSpec as ExecutionTaskSpec


def _request(tmp_path: Path, **kwargs: object) -> RuffRequest:
    defaults: dict[str, object] = {
        "executable": "/bundle/bin/ruff",
        "project_root": tmp_path,
        "targets": ("src",),
    }
    defaults.update(kwargs)
    return RuffRequest(**defaults)  # type: ignore[arg-type]


def _outcome(
    stdout: str, exit_code: int = 0, cwd: Path | None = None, **kwargs: object
) -> TaskOutcome:
    return TaskOutcome(
        spec=ExecutionTaskSpec(argv=("ruff", "check"), name="python.lint.ruff", cwd=cwd),
        outcome=kwargs.pop("outcome", Outcome.FINISHED),  # type: ignore[arg-type]
        exit_code=exit_code,
        stdout=stdout,
        **kwargs,  # type: ignore[arg-type]
    )


# --- it cannot be made to change the code --------------------------------


@pytest.mark.parametrize(
    "targets",
    [
        ("src",),
        ("src", "tests"),
        ("a b/c.py",),
        ("--fix",),
        ("src", "--fix-only"),
        ("format",),
    ],
)
def test_no_request_can_produce_an_argument_that_edits_the_tree(
    tmp_path: Path, targets: tuple[str, ...]
) -> None:
    # "--fix" and "--fix-only" are the attack: an option smuggled in where a
    # path goes. "format" is the opposite case and the reason the first version
    # of this was wrong -- it is a perfectly ordinary directory name, and
    # refusing to lint a directory for what it is called would be a bug of its
    # own. What protects against `ruff format` is the fixed head, not the
    # target list.
    try:
        plan = RuffProvider().plan(_request(tmp_path, targets=targets))
    except ValueError:
        return
    argv = plan.task.argv
    assert argv[1 : 1 + len(FIXED_HEAD)] == FIXED_HEAD, "the fixed head was displaced"
    for forbidden in FORBIDDEN_OPTIONS:
        assert forbidden not in argv, f"{forbidden} reached the command line"


def test_an_option_disguised_as_a_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is an option"):
        _request(tmp_path, targets=("--fix",))


def test_the_command_is_a_check_and_never_a_format(tmp_path: Path) -> None:
    argv = RuffProvider().plan(_request(tmp_path)).task.argv

    assert argv[1] == "check"


def test_a_directory_named_format_is_linted_not_refused(tmp_path: Path) -> None:
    # The subcommand lives in position 1 and is always "check", so a path can
    # be called anything. Refusing this would be a bug dressed as caution.
    argv = RuffProvider().plan(_request(tmp_path, targets=("format",))).task.argv

    assert argv[1] == "check"
    assert argv[-1] == "format"


def test_nothing_overrides_the_projects_own_rules(tmp_path: Path) -> None:
    # A linter run against rules other than the project's own answers a
    # question nobody asked.
    argv = RuffProvider().plan(_request(tmp_path)).task.argv

    assert not any(item.startswith("--select") for item in argv)
    assert not any(item.startswith("--ignore") for item in argv)
    assert not any(item.startswith("--config") for item in argv)


def test_the_output_format_is_the_one_the_parser_reads(tmp_path: Path) -> None:
    argv = RuffProvider().plan(_request(tmp_path)).task.argv

    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"


def test_a_request_with_nothing_to_lint_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name something to lint"):
        _request(tmp_path, targets=())


# --- exit 1 is an answer --------------------------------------------------


def test_violations_are_an_answer_not_a_broken_tool(tmp_path: Path) -> None:
    assert RuffProvider().plan(_request(tmp_path)).contract == RUFF_CONTRACT
    assert 1 in RUFF_CONTRACT.findings
    assert 1 not in STRICT.findings, "the default contract must not assume ruff's convention"


# --- reading the output ---------------------------------------------------


def _violation(root: Path, relative: str = "src/app.py", message: str = "`os` unused") -> str:
    # Absolute, because that is what ruff emits. The first version of these
    # fixtures used relative paths, every test passed, and the first run
    # against the real tool failed on every finding.
    return json.dumps(
        [
            {
                "code": "F401",
                "message": message,
                "filename": str(root / relative),
                "location": {"row": 3, "column": 8},
                "end_location": {"row": 3, "column": 10},
            }
        ]
    )


def test_a_violation_becomes_a_finding(tmp_path: Path) -> None:
    parsed = parse_ruff_json(_violation(tmp_path), root=tmp_path)

    assert parsed.is_readable
    (finding,) = parsed.findings
    assert finding.rule_id == "ruff.F401"
    assert finding.native_rule_id == "F401"
    assert finding.primary_location.path == "src/app.py"
    assert finding.primary_location.start_line == 3


def test_an_empty_array_is_a_real_clean_answer(tmp_path: Path) -> None:
    parsed = parse_ruff_json("[]", root=tmp_path)

    assert parsed.is_readable
    assert parsed.findings == ()


def test_no_output_at_all_is_a_real_clean_answer(tmp_path: Path) -> None:
    # --quiet means a clean run can say nothing.
    assert parse_ruff_json("", root=tmp_path).is_readable


@pytest.mark.parametrize(
    "broken",
    [
        "not json at all",
        '{"code": "F401"}',
        "[1, 2, 3]",
        '[{"code": "F401", "message": "x"}]',
        # A file inside the component whose position is nonsense. It has to be
        # inside, or the path check rejects it first and this stops testing
        # what it says it tests.
        '[{"filename": "{root}/a.py", "location": "everywhere"}]',
    ],
)
def test_output_that_cannot_be_read_is_not_a_clean_file(broken: str, tmp_path: Path) -> None:
    # The case that otherwise passes for clean: the tool ran, the exit code
    # said "no violations", and the parser understood none of it.
    parsed = parse_ruff_json(broken.replace("{root}", str(tmp_path)), root=tmp_path)

    assert not parsed.is_readable
    assert parsed.findings == ()
    assert parsed.failed_to_parse


def test_two_violations_of_one_rule_in_one_file_stay_distinct(tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {
                "code": "F401",
                "message": "`os` imported but unused",
                "filename": str(tmp_path / "src/app.py"),
                "location": {"row": 3, "column": 8},
            },
            {
                "code": "F401",
                "message": "`sys` imported but unused",
                "filename": str(tmp_path / "src/app.py"),
                "location": {"row": 4, "column": 8},
            },
        ]
    )

    fingerprints = {f.fingerprint for f in parse_ruff_json(payload, root=tmp_path).findings}

    assert len(fingerprints) == 2


def test_an_absolute_path_becomes_a_path_inside_the_component(tmp_path: Path) -> None:
    (finding,) = parse_ruff_json(_violation(tmp_path), root=tmp_path).findings

    assert finding.primary_location.path == "src/app.py"


def test_a_violation_outside_the_component_is_named_rather_than_dropped(tmp_path: Path) -> None:
    # Perfectly readable output about a file this component does not own. Not a
    # parse failure, and not something to discard without saying so.
    elsewhere = json.dumps(
        [
            {
                "code": "F401",
                "message": "unused",
                "filename": "/somewhere/else/app.py",
                "location": {"row": 1, "column": 1},
            }
        ]
    )

    parsed = parse_ruff_json(elsewhere, root=tmp_path)

    assert parsed.is_readable
    assert parsed.findings == ()
    assert parsed.limitations and "/somewhere/else/app.py" in parsed.limitations[0]


# --- what an observation is allowed to say --------------------------------


def test_a_run_that_did_not_finish_is_never_parsed(tmp_path: Path) -> None:
    # A parser handed a truncated stream produces findings that look exactly
    # like real ones and are missing however much was cut off. This asserts the
    # parser is not even called.
    plan = RuffProvider().plan(_request(tmp_path))

    class Exploding(RuffProvider):
        def parse(self, outcome: TaskOutcome):  # pragma: no cover - must not run
            raise AssertionError("a truncated run was handed to the parser")

    outcome = _outcome(
        _violation(tmp_path), exit_code=0, outcome=Outcome.OUTPUT_TRUNCATED, truncated=True
    )
    observation = observe(Exploding(), plan, outcome)

    assert observation.state is TaskState.FAILED
    assert observation.findings == ()
    assert observation.truncated


def test_a_finished_run_with_unreadable_output_has_no_evidence(tmp_path: Path) -> None:
    plan = RuffProvider().plan(_request(tmp_path))

    observation = observe(RuffProvider(), plan, _outcome("not json", exit_code=0, cwd=tmp_path))

    assert observation.state is TaskState.FAILED
    assert observation.findings == ()
    assert observation.limitations, "it did not say why there is nothing"


def test_a_clean_run_is_an_observation_with_no_findings(tmp_path: Path) -> None:
    plan = RuffProvider().plan(_request(tmp_path))

    observation = observe(RuffProvider(), plan, _outcome("[]", exit_code=0, cwd=tmp_path))

    assert observation.state is TaskState.SUCCEEDED
    assert observation.findings == ()
    assert observation.evidence_is_complete


def test_violations_come_through_as_a_completed_observation(tmp_path: Path) -> None:
    plan = RuffProvider().plan(_request(tmp_path))

    observation = observe(
        RuffProvider(), plan, _outcome(_violation(tmp_path), exit_code=1, cwd=tmp_path)
    )

    assert observation.state is TaskState.SUCCEEDED, "findings were read as a failed run"
    assert len(observation.findings) == 1


def test_a_tool_failure_is_not_a_clean_run(tmp_path: Path) -> None:
    plan = RuffProvider().plan(_request(tmp_path))

    observation = observe(RuffProvider(), plan, _outcome("", exit_code=2, cwd=tmp_path))

    assert observation.state is TaskState.FAILED
    assert observation.findings == ()
    assert "exit code 2" in observation.limitations[0]


# --- where the cache goes -------------------------------------------------


def test_by_default_ruff_is_told_not_to_cache(tmp_path: Path) -> None:
    # Left to itself Ruff writes .ruff_cache/ into the tree it is checking, and
    # a verification tool that writes into what it verifies cannot run against
    # a read-only checkout.
    argv = RuffProvider().plan(_request(tmp_path)).task.argv

    assert "--no-cache" in argv
    assert "--cache-dir" not in argv


def test_a_caller_that_names_a_cache_directory_gets_it(tmp_path: Path) -> None:
    elsewhere = tmp_path / "run" / "cache"

    argv = RuffProvider().plan(_request(tmp_path, cache_dir=elsewhere)).task.argv

    assert "--no-cache" not in argv
    assert argv[argv.index("--cache-dir") + 1] == str(elsewhere)
