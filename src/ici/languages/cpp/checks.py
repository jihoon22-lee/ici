"""The C++ pack's checks.

Only ``cpp.line`` can be planned today — the line counter is ici's own and the
algorithm already reads ``//`` and block comments, so it is as honest on a
``.cpp`` file as on a ``.py`` one. The C++ providers the disposition table maps
(lint, type, compile_db, test, coverage) are #214's work: they are not declared
here yet because a declared check is a promise the run can be asked for, and a
check with no provider would make every C++ workspace permanently INCOMPLETE on
a tool requirement nobody can satisfy yet.
"""

from __future__ import annotations

from ici.languages.checks import CheckDefinition

__all__ = ["CPP_CHECKS", "CPP_LINE_CHECK"]

CPP_LINE_CHECK = CheckDefinition(
    id="cpp.line",
    title="Line counts",
    language="cpp",
    tool=None,
)

#: Declaration only. Importing this must not look at the machine.
CPP_CHECKS: tuple[CheckDefinition, ...] = (CPP_LINE_CHECK,)
