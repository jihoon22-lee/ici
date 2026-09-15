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
    "FORMAT_CHECK",
    "LINE_CHECK",
    "LINT_CHECK",
    "PYTHON_CHECKS",
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

#: Declaration only. Importing this must not look at the machine.
PYTHON_CHECKS: tuple[CheckDefinition, ...] = (
    LINE_CHECK,
    LINT_CHECK,
    FORMAT_CHECK,
    TYPE_CHECK,
)
