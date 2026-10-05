"""Python providers — #215's lint/format/type contracts.

The claims under test: ruff lint and format-check are separate argv shapes
over the same read-only guarantee, mypy and ty each parse their own output
shape, a checker the project did not choose is never substituted for the one
it did, and output the parser cannot read is a parse failure rather than an
empty PASS.
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.mypy import MypyProvider, parse_mypy_output
from ici.adapters.providers.ruff import (
    RuffProvider,
    RuffRequest,
    parse_ruff_format,
)
from ici.adapters.providers.ty import TyProvider, parse_ty_output
from ici.execution.process import Outcome, TaskOutcome, TaskSpec


def _outcome(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    outcome: Outcome = Outcome.FINISHED,
) -> TaskOutcome:
    spec = TaskSpec(argv=argv, name="app.python.type", cwd=cwd)
    return TaskOutcome(
        spec=spec,
        outcome=outcome,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _request(mode: str = "lint") -> RuffRequest:
    return RuffRequest(
        executable="ruff",
        project_root=Path("/work"),
        targets=("app/one.py",),
        mode=mode,
    )


def test_ruff_format_plan_is_a_read_only_check() -> None:
    plan = RuffProvider().plan(_request(mode="format"))

    assert plan.task.argv == (
        "ruff",
        "format",
        "--check",
        "--no-cache",
        "app/one.py",
    )
    # --check never writes: the verifier reports what would change.
    assert "--fix" not in plan.task.argv and "--diff" not in plan.task.argv


def test_ruff_format_parse_finds_unformatted_files(tmp_path) -> None:
    (tmp_path / "bad.py").write_text("x=1\n", encoding="utf-8")
    text = (
        "unformatted: File would be reformatted\n"
        f" --> {tmp_path}/bad.py:1:2\n"
        "  |\n"
        "  - x=1\n"
        "1 + x = 1\n"
        "  |\n"
        "\n"
        "1 file would be reformatted, 1 file already formatted\n"
    )

    parsed = parse_ruff_format(text, tmp_path)

    assert not parsed.failed_to_parse
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.rule_id == "ruff.format"
    assert finding.primary_location.path == "bad.py"
    assert finding.fingerprint


def test_ruff_format_parse_accepts_the_legacy_line(tmp_path) -> None:
    (tmp_path / "bad.py").write_text("x=1\n", encoding="utf-8")

    parsed = parse_ruff_format(f"Would reformat: {tmp_path}/bad.py\n", tmp_path)

    assert len(parsed.findings) == 1
    assert parsed.findings[0].primary_location.path == "bad.py"


def test_ruff_format_parse_refuses_unknown_lines(tmp_path) -> None:
    parsed = parse_ruff_format("something unexpected happened\n", tmp_path)

    assert parsed.failed_to_parse
    assert parsed.findings == ()


def test_ruff_parse_reads_the_argv_to_pick_the_parser(tmp_path) -> None:
    (tmp_path / "bad.py").write_text("x=1\n", encoding="utf-8")
    outcome = _outcome(
        ("ruff", "format", "--check", "bad.py"),
        tmp_path,
        exit_code=1,
        stdout=f"Would reformat: {tmp_path}/bad.py\n",
    )

    parsed = RuffProvider().parse(outcome)

    assert len(parsed.findings) == 1
    assert parsed.findings[0].rule_id == "ruff.format"


def test_mypy_plan_asks_for_parseable_output() -> None:
    plan = MypyProvider().plan(
        "mypy",
        targets=("app/one.py",),
        cwd="/work",
        task_id="app.python.type",
        analysis_unit_id="app",
        input_refs=("app/one.py",),
    )

    assert plan.task.provider == "mypy"
    assert plan.task.argv[:2] == ("mypy", "--show-error-codes")
    assert "--no-color-output" in plan.task.argv
    assert plan.task.argv[-1] == "app/one.py"


def test_mypy_parse_normalizes_errors_and_notes(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x: int = 's'\n", encoding="utf-8")
    text = (
        "app/one.py:1:11: error: Incompatible types [assignment]\n"
        "app/one.py:1: note: See https://mypy.readthedocs.io/\n"
        "Found 1 error in 1 file (checked 1 source file)\n"
    )

    parsed = parse_mypy_output(text, tmp_path, task_id="app.python.type")

    assert not parsed.failed_to_parse
    assert len(parsed.findings) == 2
    error, note = parsed.findings
    assert error.rule_id == "mypy.assignment" and error.severity == "high"
    assert note.severity == "low"
    assert error.primary_location.start_column == 11


def test_mypy_parse_refuses_unreadable_output(tmp_path) -> None:
    parsed = parse_mypy_output("mypy: internal error\n", tmp_path)

    assert parsed.failed_to_parse
    assert parsed.findings == ()


def test_mypy_parse_reports_an_unfinished_run(tmp_path) -> None:
    parsed = MypyProvider().parse(_outcome(("mypy",), tmp_path, outcome=Outcome.TIMED_OUT))
    assert parsed.failed_to_parse


def test_ty_plan_uses_the_concise_stream() -> None:
    plan = TyProvider().plan(
        "ty",
        targets=("app/one.py",),
        cwd="/work",
        task_id="app.python.type",
        input_refs=("app/one.py",),
    )

    assert plan.task.provider == "ty"
    assert plan.task.argv == (
        "ty",
        "check",
        "--output-format",
        "concise",
        "app/one.py",
    )


def test_ty_parse_normalizes_concise_diagnostics(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x: int = 's'\n", encoding="utf-8")
    text = (
        "app/one.py:1:10: error[invalid-assignment] "
        'Object of type `Literal["s"]` is not assignable to `int`\n'
        "Found 1 diagnostic\n"
    )

    parsed = parse_ty_output(text, tmp_path, task_id="app.python.type")

    assert not parsed.failed_to_parse
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.rule_id == "ty.invalid-assignment"
    assert finding.severity == "high"
    assert finding.primary_location.start_column == 10


def test_ty_parse_refuses_unreadable_output(tmp_path) -> None:
    parsed = parse_ty_output("ty crashed badly\n", tmp_path)

    assert parsed.failed_to_parse
