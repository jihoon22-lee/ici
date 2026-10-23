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
``carried``, not ``resolved``. Only a finding whose check completed again over the same input scope
and no longer reports it is resolved — a partial run must not be able to
"fix" code it never looked at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ici.domain._codec import require_mapping
from ici.domain.check_record import CheckRecord
from ici.domain.enums import BaselineState
from ici.domain.finding import Finding
from ici.domain.result import FINGERPRINT_VERSION, SCHEMA_ID, BaselineComparison
from ici.domain.serialization import run_result_from_dict

__all__ = ["BaselineDocument", "BaselineError", "compare", "load_baseline"]


class BaselineError(ValueError):
    """Why a baseline cannot be read or trusted — named, never silent."""


@dataclass(frozen=True)
class BaselineDocument:
    """What a stored baseline offers: its identities and its findings."""

    path: str
    policy_digest: str
    toolchain_digest: str
    #: The fingerprint normalization version the baseline was written under —
    #: empty for results that predate its recording, which cannot be a
    #: baseline because their fingerprints are not known to be comparable.
    fingerprint_version: str
    findings: tuple[Finding, ...]
    checks: tuple[CheckRecord, ...] | None


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
    try:
        data = require_mapping(payload, "baseline document")
    except ValueError as error:
        raise BaselineError(f"baseline {path} is not a result: {error}") from error
    schema_id = data.get("schema_id")
    if schema_id != SCHEMA_ID:
        raise BaselineError(
            f"baseline {path} is a {schema_id!r} document — a baseline must be "
            f"a stored {SCHEMA_ID} result"
        )
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise BaselineError(f"baseline {path} carries no identity — cannot verify what it measured")
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        raise BaselineError(f"baseline {path} findings must be a JSON array")
    for index, item in enumerate(findings):
        if not isinstance(item, dict) or not item.get("fingerprint"):
            raise BaselineError(f"baseline {path} finding #{index} has no fingerprint")
    try:
        result = run_result_from_dict(data)
    except (ValueError, TypeError) as error:
        raise BaselineError(f"baseline {path} is not a valid result: {error}") from error
    return BaselineDocument(
        path=str(path),
        policy_digest=result.identity.policy_digest,
        toolchain_digest=result.identity.toolchain_digest,
        fingerprint_version=result.identity.fingerprint_version,
        findings=result.findings,
        checks=result.checks,
    )


def compare(
    findings: tuple[Finding, ...],
    baseline: BaselineDocument,
    *,
    policy_digest: str,
    toolchain_digest: str,
    checks: tuple[CheckRecord, ...] | None = None,
) -> BaselineComparison:
    """The fingerprint delta, or a refusal that says why it cannot be a delta."""

    if baseline.fingerprint_version != FINGERPRINT_VERSION:
        return BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin=baseline.path,
            reason=(
                "the baseline was written under a different fingerprint "
                "normalization — accept the current run as the new baseline "
                "instead of comparing across the change"
            ),
        )
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

    if baseline.checks is None or checks is None:
        return BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin=baseline.path,
            reason=(
                "completed check scope is unknown — accept a new v2 result as the baseline; "
                "older results remain readable but cannot prove resolutions"
            ),
        )
    old_checks = {item.task_id: item for item in baseline.checks}
    completed = {item.comparison_key for item in checks if item.evidence_complete}
    current = {finding.fingerprint for finding in findings}
    previous = {finding.fingerprint for finding in baseline.findings}
    resolved = set()
    for finding in baseline.findings:
        if finding.fingerprint in current:
            continue
        record = old_checks.get(finding.task_id)
        if (
            record is not None
            and record.component_id == finding.component_id
            and record.comparison_key in completed
        ):
            resolved.add(finding.fingerprint)
    return BaselineComparison(
        state=BaselineState.COMPARABLE,
        origin=baseline.path,
        new=tuple(sorted(current - previous)),
        unchanged=tuple(sorted(current & previous)),
        resolved=tuple(sorted(resolved)),
        carried=tuple(sorted(previous - current - resolved)),
    )
