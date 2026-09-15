"""The Python pack's checks.

``CheckDefinition`` itself lives in :mod:`ici.languages.checks` so a C++ check
does not import from a module named ``python``; this module keeps the re-export
because existing callers import it from here.
"""

from __future__ import annotations

from ici.languages.checks import CheckDefinition

__all__ = ["LINE_CHECK", "LINT_CHECK", "PYTHON_CHECKS", "CheckDefinition"]


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

#: Declaration only. Importing this must not look at the machine.
PYTHON_CHECKS: tuple[CheckDefinition, ...] = (LINE_CHECK, LINT_CHECK)
