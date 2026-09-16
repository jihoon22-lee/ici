"""The integration domain's check — component-owned process contracts (#220).

Integration is not a language, so this check is not selected by
``checks_for``: a component sees it only by *declaring* cases under
``[[components.integrations]]``, which is the opt-in #220 item 6 asks for.
Deep profile only, for the same reason the sanitizer checks are: an
integration case runs a real process against real artifacts and possibly
declared external services — the offline default run must never invoke one.

``tool=None`` because there is no tool to locate: a case's executable is a
typed placeholder the plan resolves — ``{python:NAME}`` against the
component's declared interpreters, ``{artifact:BUILD/PATH}`` against a
linked build's declared artifact contract. A placeholder that cannot be
resolved leaves the case blocked, never silently skipped.
"""

from __future__ import annotations

from ici.domain.enums import Profile
from ici.languages.checks import CheckDefinition

__all__ = ["INTEGRATION_CASES_CHECK"]

INTEGRATION_CASES_CHECK = CheckDefinition(
    id="integration",
    title="Integration cases",
    language="integration",
    tool=None,
    profiles=frozenset({Profile.DEEP}),
)
