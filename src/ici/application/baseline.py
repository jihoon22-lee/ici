"""Comparing a run against a stored baseline (#221).

A baseline is a previous ``ici.next.run`` result. Comparing against it is
only meaningful when the two runs measured the same thing, so the first
check is identity: same policy digest, same toolchain digest, same schema.
A baseline that fails that check is ``INCOMPATIBLE`` — the comparison
refuses rather than guess, because a delta computed across a policy or
provider change would mark findings resolved that nobody re-checked.

When the identities match, the delta is computed on fingerprints — the
identity a finding already carries. The one rule that matters most is
scope honesty: a finding whose component this run never selected is
``carried``, not ``resolved``. Only a finding from a component that ran
and no longer reports it is resolved — a partial run must not be able to
"fix" code it never looked at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ici.domain._codec import require_mapping
from ici.domain.enums import BaselineState
from ici.domain.finding import Finding
from ici.domain.result import SCHEMA_ID, BaselineComparison

__all__ = ["BaselineDocument", "BaselineError", "compare", "load_baseline"]


class BaselineError(ValueError):
    """Why a baseline cannot be read or trusted — named, never silent."""


@dataclass(frozen=True)
class BaselineDocument:
    """What a stored baseline offers: its identities and its findings."""

    path: str
    policy_digest: str
    toolchain_digest: str
    #: fingerprint -> component the finding belonged to.
    findings: tuple[tuple[str, str], ...]


def load_baseline(path: Path) -> BaselineDocument:
    """Read a baseline result, naming what was actually found when it is not one.

    A v3 result, an event stream or a truncated file are all refused by
    name — reading them as an empty baseline would resolve everything.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BaselineError(f"baseline {path} cannot be read: {error}") from error
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise BaselineError(f"baseline {path} is not JSON: {error}") from error
    data = require_mapping(payload, "baseline document")
    schema_id = data.get("schema_id")
    if schema_id != SCHEMA_ID:
        raise BaselineError(
            f"baseline {path} is a {schema_id!r} document — a baseline must be "
            f"a stored {SCHEMA_ID} result"
        )
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise BaselineError(f"baseline {path} carries no identity — cannot verify what it measured")
    findings: list[tuple[str, str]] = []
    for index, item in enumerate(data.get("findings") or ()):
        if not isinstance(item, dict) or not item.get("fingerprint"):
            raise BaselineError(f"baseline {path} finding #{index} has no fingerprint")
        component = str(item.get("component_id") or "")
        findings.append((str(item["fingerprint"]), component))
    return BaselineDocument(
        path=str(path),
        policy_digest=str(identity.get("policy_digest") or ""),
        toolchain_digest=str(identity.get("toolchain_digest") or ""),
        findings=tuple(findings),
    )


def compare(
    findings: tuple[Finding, ...],
    baseline: BaselineDocument,
    *,
    policy_digest: str,
    toolchain_digest: str,
    selected_components: tuple[str, ...],
) -> BaselineComparison:
    """The fingerprint delta, or a refusal that says why it cannot be a delta."""

    if baseline.policy_digest != policy_digest:
        return BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin=baseline.path,
            reason=(
                "policy changed since the baseline — accept the current run as "
                "the new baseline instead of comparing across the change"
            ),
        )
    if baseline.toolchain_digest != toolchain_digest:
        return BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin=baseline.path,
            reason=(
                "the provider toolchain changed since the baseline — findings "
                "from different tool versions are not the same identity"
            ),
        )

    selected = set(selected_components)
    current = {finding.fingerprint for finding in findings}
    baseline_in_scope = {
        fingerprint
        for fingerprint, component in baseline.findings
        if not component or component in selected
    }
    carried = {
        fingerprint
        for fingerprint, component in baseline.findings
        if component and component not in selected
    }
    return BaselineComparison(
        state=BaselineState.COMPARABLE,
        origin=baseline.path,
        new=tuple(sorted(current - {fp for fp, _ in baseline.findings})),
        unchanged=tuple(sorted(current & baseline_in_scope)),
        resolved=tuple(sorted(baseline_in_scope - current)),
        carried=tuple(sorted(carried)),
    )
