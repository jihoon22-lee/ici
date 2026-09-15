"""The Python pack's checks.

``CheckDefinition`` itself lives in :mod:`ici.languages.checks` so a C++ check
does not import from a module named ``python``; this module keeps the re-export
because existing callers import it from here.

``python.lint`` and ``python.format`` are the two questions Ruff answers, kept
as separate checks because a lint violation and an unformatted file are
different findings with different remedies (#215). ``python.type`` declares
no tool because the checker is the component's own choice — ``mypy`` by
default, ``ty`` only when ``[python] type_provider`` names it — and the
selection is resolved when the plan is gated, not guessed at declaration
time.
"""

from __future__ import annotations

from ici.languages.checks import CheckDefinition

__all__ = [
    "COVERAGE_CHECK",
    "FORMAT_CHECK",
    "LINE_CHECK",
    "LINT_CHECK",
    "PYTHON_CHECKS",
    "TEST_CHECK",
    "TYPE_CHECK",
    "CheckDefinition",
]


LINE_CHECK = CheckDefinition(
    id="python.line",
    title="Line counts",
    language="python",
    tool=None,
)

LINT_CHECK = CheckDefinition(
    id="python.lint",
    title="Ruff lint",
    language="python",
    tool="ruff",
)

FORMAT_CHECK = CheckDefinition(
    id="python.format",
    title="Ruff format check",
    language="python",
    tool="ruff",
)

#: ``tool=None`` is deliberate: the checker comes from the component's
#: ``type_provider`` setting, resolved at plan time — declaring ``mypy`` here
#: would run mypy even for a component that chose ty.
TYPE_CHECK = CheckDefinition(
    id="python.type",
    title="Static type check",
    language="python",
    tool=None,
)

#: ``tool=None`` because the runner is the project's own interpreter — the
#: declared ``[python] executable`` or its ``.venv``, never ici's runtime
#: (#216). The check ``provides`` the test evidence coverage reads.
TEST_CHECK = CheckDefinition(
    id="python.test",
    title="pytest suite",
    language="python",
    tool=None,
    provides=("test-evidence",),
)

#: Coverage shares the pytest execution: when this check is selected the test
#: task is wrapped in ``coverage run`` and this task only reads the data —
#: one run, two readers, ordered through ``test-evidence``.
COVERAGE_CHECK = CheckDefinition(
    id="python.coverage",
    title="coverage.py collection",
    language="python",
    tool=None,
    needs=("test-evidence",),
)

#: ``tool=None`` because ici measures these itself: the AST is exact, and the
#: two checks share one parse through the metrics primitive — selecting both
#: does not scan the file twice (#218).
COMPLEXITY_CHECK = CheckDefinition(
    id="python.complexity",
    title="Cyclomatic complexity",
    language="python",
    tool=None,
)

COGNITIVE_CHECK = CheckDefinition(
    id="python.cognitive",
    title="Cognitive complexity",
    language="python",
    tool=None,
)

#: Module import cycles — read from the component's own module index, no
#: tool involved (#218).
CYCLE_CHECK = CheckDefinition(
    id="python.cycle",
    title="Import cycles",
    language="python",
    tool=None,
)

#: Type-2 clone detection over the component's own files — tokenized,
#: window-matched and clustered in-process (#218).
DUP_CHECK = CheckDefinition(
    id="python.dup",
    title="Duplicate code",
    language="python",
    tool=None,
)

#: ici's own AST rules — secrets, crypto and deserialization risks; and
#: unclosed acquisitions plus mutable defaults. Both run in-process over the
#: component's owned files (#218).
SECURITY_CHECK = CheckDefinition(
    id="python.security",
    title="Security hygiene",
    language="python",
    tool=None,
)

RESOURCE_CHECK = CheckDefinition(
    id="python.resource",
    title="Resource hygiene",
    language="python",
    tool=None,
)

#: Handler anti-patterns — bare except, BaseException, swallowed handlers and
#: traceback-losing re-raise; the same rules the stable ``exception`` engine
#: runs, shared through ``engines._exception_rules`` (#218).
EXCEPTION_CHECK = CheckDefinition(
    id="python.exception",
    title="Exception safety",
    language="python",
    tool=None,
)

#: Cross-file dead-code heuristic — correlates definitions and uses across
#: the component snapshot, the same analysis the stable ``dead`` engine
#: runs. Results stay ESTIMATED: it is a heuristic, not a proof (#218).
DEAD_CHECK = CheckDefinition(
    id="python.dead",
    title="Dead code",
    language="python",
    tool=None,
)

#: Declaration only. Importing this must not look at the machine.
PYTHON_CHECKS: tuple[CheckDefinition, ...] = (
    LINE_CHECK,
    LINT_CHECK,
    FORMAT_CHECK,
    TYPE_CHECK,
    TEST_CHECK,
    COVERAGE_CHECK,
    COMPLEXITY_CHECK,
    COGNITIVE_CHECK,
    CYCLE_CHECK,
    DUP_CHECK,
    SECURITY_CHECK,
    RESOURCE_CHECK,
    EXCEPTION_CHECK,
    DEAD_CHECK,
)
