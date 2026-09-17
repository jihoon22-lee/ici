"""What the user asked a run to cover, before selection turns it into plans.

#210 item 1: the CLI flags — ``--component``, ``--python``/``--cpp``,
``--profile``, ``--require-full`` — are one request object here, so every
command interprets them the same way and no command re-reads the model to
guess what a flag meant. Three properties of that interpretation are the ones
the spec calls out:

**Language flags filter checks, never the model.** ``--python`` on a hybrid
component selects its Python checks; it does not re-declare the component a
Python project (SPEC-01 section 5: *구성요소 모델을 유지하고 해당 언어 검사만
필터링*). Two language flags are a union, and ``--component`` intersects with
them — a component is in scope when it was named, and a check runs when its
language was asked for.

**An empty selection is a configuration error, not a clean run.** The CLI
spelling that selects nothing gets exit 2 with the reason, because a run that
checked nothing and exited 0 claimed the code was fine on the strength of
never looking.

**``--require-full`` is a coverage gate, not a verdict override.** A run whose
requested scope leaves required components or languages out cannot claim the
workspace passed — the gate reads INCOMPLETE with the missing scope named,
which is SPEC-04's exit 3. A complete-coverage run that found violations still
reports FAIL: coverage was satisfied, the code was not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ici.application.verify import Verification
from ici.domain.enums import GateVerdict, Profile
from ici.domain.result import GateOutcome
from ici.domain.workspace import Workspace
from ici.languages.checks import CheckDefinition

__all__ = ["RunRequest", "apply_require_full", "profile_for", "scope_of", "wanted"]


@dataclass(frozen=True)
class RunRequest:
    """The selection a command line asked for, in model terms.

    Empty ``components``/``languages`` mean *everything declared* — a flag the
    user did not pass must not read as a flag set to nothing.
    """

    components: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    profile: Profile | None = None
    require_full: bool = False


def scope_of(model: Workspace, request: RunRequest) -> Workspace:
    """The workspace narrowed to the named components, or why that fails.

    An unknown id is a selection error: the message names it and lists what
    was declared, because "no component ghost" and "no components at all"
    have different fixes.
    """

    if not request.components:
        return model
    known = {component.id for component in model.components}
    unknown = [item for item in request.components if item not in known]
    if unknown:
        declared = ", ".join(sorted(known)) or "none"
        raise ValueError(f"no component {unknown[0]!r}; declared: {declared}")
    return model.scoped(request.components)


def wanted(request: RunRequest, check: CheckDefinition) -> bool:
    """Whether the request's language filter keeps this check."""

    return not request.languages or check.language in request.languages


def requested_languages(request: RunRequest, scope: Workspace) -> tuple[str, ...]:
    """The languages the run covers — request ∩ what the scope declares."""

    declared = {language for item in scope.components for language in item.languages}
    if request.languages:
        return tuple(sorted(language for language in request.languages if language in declared))
    return tuple(sorted(declared))


def uncovered_scope(model: Workspace, request: RunRequest, scope: Workspace) -> tuple[str, ...]:
    """What the request left out that ``--require-full`` cannot pass over.

    Each entry is a human reason naming the missing required coverage — a
    required component not selected, or a language a required component needed
    that the request filtered away. An empty tuple means coverage is complete
    and the gate may keep its own verdict.
    """

    reasons: list[str] = []
    selected = {item.id for item in scope.components}
    required = set(model.required_component_ids)
    for component_id in sorted(required - selected):
        reasons.append(
            f"--require-full: required component {component_id!r} was not in the selected scope"
        )
    if request.languages:
        wanted_set = set(request.languages)
        for component in model.components:
            if component.id not in required:
                continue
            missing = sorted(set(component.languages) - wanted_set)
            if missing:
                reasons.append(
                    f"--require-full: {component.id} declares {', '.join(missing)} "
                    f"but the request selected only {', '.join(request.languages)}"
                )
    return tuple(reasons)


def profile_for(configured: str | None, request: RunRequest) -> Profile:
    """The run's cost profile: the flag, then the root's, then standard.

    A profile the model or the flag names but the domain does not know is a
    configuration error — silently treating ``deepp`` as ``deep`` would run a
    different cost contract than the file asked for.
    """

    value = request.profile.value if request.profile is not None else configured
    if value is None:
        return Profile.STANDARD
    try:
        return Profile(value)
    except ValueError:
        known = ", ".join(item.value for item in Profile)
        raise ValueError(f"unknown profile {value!r}; declared profiles: {known}") from None


def apply_require_full(verification: Verification, coverage_gaps: Sequence[str]) -> Verification:
    """Demote the gate when required coverage was left out of the request.

    The run's own verdict is not rewritten — a subset that passed is still
    recorded as having passed *what ran*. What changes is the final gate the
    caller asked to be judged on: SPEC-04 exit 3, with each gap named. A gate
    already INCOMPLETE gains the coverage reasons on top of its own.
    """

    if not coverage_gaps:
        return verification
    gate = verification.gate
    reasons = tuple(coverage_gaps) + (
        gate.reasons if gate.selected is GateVerdict.INCOMPLETE else ()
    )
    return replace(
        verification,
        gate=GateOutcome(
            selected=GateVerdict.INCOMPLETE,
            workspace=GateVerdict.INCOMPLETE,
            has_violations=gate.has_violations,
            reasons=reasons,
        ),
    )
