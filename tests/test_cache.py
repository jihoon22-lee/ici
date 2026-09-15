"""The observation cache — #209's honesty rules, tested at the boundary.

What is exercised here is the difference between *stored* and *trusted*: an
entry is reused only when its identity matches this run's inputs, partial or
failed evidence is never promoted, and a corrupt or tampered entry is a miss
with a reason — never a wrong answer and never a crash in the reader.
"""

from __future__ import annotations

import json

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.application.graph import build_graph
from ici.application.identity import task_identity
from ici.application.plan import PlannedCheck
from ici.application.schedule import run_graph
from ici.domain.enums import TaskKind, TaskState
from ici.domain.observation import Measurement, Observation
from ici.domain.tasks import TaskSpec
from ici.execution.cache import ObservationCache
from ici.execution.process import Outcome, TaskOutcome
from ici.languages.checks import CheckDefinition


def _observation(
    task_id: str = "a.lint", state: TaskState = TaskState.SUCCEEDED, **kw
) -> Observation:
    return Observation(task_id=task_id, provider="stub", state=state, **kw)


class _StubProvider:
    name = "stub"

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        return ParsedOutput()


def _spec(task_id: str, **kw) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        kind=kw.pop("kind", TaskKind.ANALYZE),
        provider="stub",
        argv=kw.pop("argv", ("/bin/true", task_id)),
        cwd="/project",
        input_refs=("src/a.py",),
        **kw,
    )


def _finished(spec) -> TaskOutcome:
    return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=0, duration=0.01)


def test_a_stored_observation_is_returned_verbatim(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")
    stored = _observation(measurements=(Measurement(name="n", value=3),))

    cache.write("a" * 64, stored, run_id="run-1")
    read = cache.read("a" * 64)

    assert read.hit
    assert read.source_run == "run-1"
    assert read.observation is not None
    assert read.observation.task_id == "a.lint"
    assert read.observation.measurements[0].value == 3


def test_a_missing_key_is_a_miss_with_a_reason(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")

    read = cache.read("b" * 64)

    assert not read.hit
    assert read.reason


def test_a_failed_observation_is_never_promoted(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")

    cache.write("c" * 64, _observation(state=TaskState.FAILED), run_id="r")
    cache.write("d" * 64, _observation(timed_out=True), run_id="r")
    cache.write("e" * 64, _observation(truncated=True), run_id="r")

    assert not cache.read("c" * 64).hit
    assert not cache.read("d" * 64).hit
    assert not cache.read("e" * 64).hit


def test_a_corrupt_entry_is_a_miss_and_is_removed(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")
    entry = tmp_path / "cache" / ("f" * 64 + ".json")
    entry.write_text("{ not json", encoding="utf-8")

    read = cache.read("f" * 64)

    assert not read.hit
    assert "not readable" in read.reason
    assert not entry.exists()


def test_an_entry_whose_identity_disagrees_is_refused(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")
    cache.write("1" * 64, _observation(), run_id="r")
    entry = tmp_path / "cache" / ("1" * 64 + ".json")
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["identity"] = "0" * 64  # tampered: the file claims other inputs
    entry.write_text(json.dumps(payload), encoding="utf-8")

    read = cache.read("1" * 64)

    assert not read.hit
    assert "identity" in read.reason


def test_eviction_bounds_the_cache_and_skips_symlinks(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache", max_entries=2)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    link = tmp_path / "cache" / "link.json"
    link.symlink_to(outside)

    for index in range(4):
        cache.write(f"{index:064x}", _observation(), run_id="r")

    remaining = [p for p in (tmp_path / "cache").iterdir() if p.name != ".write-lock"]
    assert len([p for p in remaining if p.suffix == ".json" and not p.is_symlink()]) <= 3
    # The symlink itself may be evicted, but what it points at is never touched.
    assert outside.read_text(encoding="utf-8") == "keep me"


def test_identity_refuses_a_task_with_undeclared_inputs() -> None:
    from ici.application.graph import WorkUnit

    check = CheckDefinition(id="a.lint", title="t", language="python", tool="stub")
    task = TaskSpec(
        id="a.lint",
        kind=TaskKind.ANALYZE,
        provider="stub",
        argv=("tool",),
        cwd="/p",
        input_refs=(),
    )
    unit = WorkUnit(
        id="a.lint",
        consumers=("a.lint",),
        source=PlannedCheck(check=check, task=ProviderPlan(task=task)),
    )

    key, reason = task_identity(unit, input_digests=(), tool_digest="sha256:x", policy_digest="p")

    assert key is None
    assert "did not declare" in reason


def test_identity_refuses_test_and_mutating_tasks() -> None:
    from ici.application.graph import WorkUnit

    def unit_for(**kw) -> WorkUnit:
        check = CheckDefinition(id="t", title="t", language="python", tool="stub")
        task = _spec("t", **kw)
        return WorkUnit(
            id="t", consumers=("t",), source=PlannedCheck(check=check, task=ProviderPlan(task=task))
        )

    key, reason = task_identity(
        unit_for(kind=TaskKind.TEST),
        input_digests=(("a", "d"),),
        tool_digest="sha256:x",
        policy_digest="p",
    )
    assert key is None and "test" in reason

    key, reason = task_identity(
        unit_for(kind=TaskKind.PREPARE, cacheable=False),
        input_digests=(("a", "d"),),
        tool_digest="sha256:x",
        policy_digest="p",
    )
    assert key is None and "cacheable" in reason


def test_a_second_run_reuses_the_first_run_answer(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")
    calls: list[str] = []

    def runner(spec):
        calls.append(spec.name)
        return _finished(spec)

    planned = PlannedCheck(
        check=CheckDefinition(id="a.lint", title="t", language="python", tool="stub"),
        task=ProviderPlan(task=_spec("a.lint")),
        task_id="a.lint",
    )
    graph = build_graph((planned,))

    def identify(unit):
        return "a" * 64, ""

    run_graph(
        graph,
        {"stub": _StubProvider()},
        {},
        runner=runner,
        cache=cache,
        identify=identify,
        run_id="first",
    )
    scheduled = run_graph(
        graph,
        {"stub": _StubProvider()},
        {},
        runner=runner,
        cache=cache,
        identify=identify,
        run_id="second",
    )

    assert calls == ["a.lint"]  # ran once, reused the second time
    assert scheduled.executions[0].detail.startswith("cache hit")
    assert scheduled.observations[0].state is TaskState.SUCCEEDED


def test_a_unit_with_no_key_still_runs_and_says_why(tmp_path) -> None:
    cache = ObservationCache(tmp_path / "cache")
    planned = PlannedCheck(
        check=CheckDefinition(id="a.lint", title="t", language="python", tool="stub"),
        task=ProviderPlan(task=_spec("a.lint")),
        task_id="a.lint",
    )

    scheduled = run_graph(
        build_graph((planned,)),
        {"stub": _StubProvider()},
        {},
        runner=_finished,
        cache=cache,
        identify=lambda unit: (None, "the provider did not declare its inputs"),
        run_id="r",
    )

    assert scheduled.observations[0].state is TaskState.SUCCEEDED
    assert "did not declare" in scheduled.executions[0].detail
