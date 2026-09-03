"""Canonical identities for overlapping Python diagnostics.

This module is deliberately a *projection* over the finding contract.  It
does not rewrite a :class:`~ici.core.models.Finding`, update an
``EngineResult``, or participate in baseline comparison.  Callers can use the
returned identity to build a display-only projection while keeping the
engine's complete finding inventory intact.

The registry is intentionally conservative.  A rule is eligible for
cross-producer merging only when its meaning is unambiguous and both findings
have a precise, overlapping source region.  A line number without both start
and end columns is not enough: two calls on one line are distinct occurrences.
Likewise, a broad third-party rule is not silently equated with a narrower
AST rule unless a semantic context explicitly proves that they describe the
same operation (for example, Ruff ``S324`` plus ``md5`` context).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ici.core.findings import canonical_project_path
from ici.core.models import Finding, SourceLocation
from ici.core.python_rule_registry import PYTHON_RULE_MAPPINGS, PythonRuleMapping


@dataclass(frozen=True)
class PythonRuleProvenance:
    """Original producer information retained by a projected identity."""

    engine_name: str = ""
    rule_id: str = ""
    tool_rule_id: str = ""
    tool_name: str = ""
    tool_version: str = ""
    target_label: str = ""

    @property
    def source_rule_id(self) -> str:
        """Return the most specific original rule name available."""

        return self.tool_rule_id.strip() or self.target_label.strip() or self.rule_id.strip()


@dataclass(frozen=True)
class PythonRuleIdentity:
    """Canonical rule identity plus its lossless source provenance.

    ``merge_group`` is populated only for rules whose cross-producer alias is
    safe under the supplied context.  It is intentionally separate from
    ``canonical_rule_id`` so an ambiguous diagnostic can still be named in a
    stable way without becoming eligible for a false merge.
    """

    canonical_rule_id: str
    provenance: PythonRuleProvenance
    merge_group: str | None = None
    semantic_context: str = ""

    @property
    def mergeable(self) -> bool:
        """Whether this identity may participate in cross-producer merging."""

        return self.merge_group is not None

    @property
    def source_rule_id(self) -> str:
        """Convenience access to the original rule identifier."""

        return self.provenance.source_rule_id


@dataclass(frozen=True)
class PythonFindingProjection:
    """A finding and its read-only canonical identity.

    The original finding object is retained by reference and is never
    modified.  This makes the projection suitable for reporters without
    changing the source inventory used by JSON output or baselines.
    """

    finding: Finding
    identity: PythonRuleIdentity


_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_LEGACY_ENGINE_RE = re.compile(r"^ici\.legacy\.([a-z0-9-]+)\.target$")
_CANONICAL_RULE_RE = re.compile(r"^ici\.[a-z0-9][a-z0-9.-]*$")


def _normalise(value: str) -> str:
    return _TOKEN_RE.sub("-", value.strip().casefold()).strip("-")


def _context(value: str | None) -> str:
    return _normalise(value or "")


def _has_rule_suffix(value: str, suffix: str) -> bool:
    """Match an internal ``Engine:Rule`` token without matching Ruff codes."""

    normalized = _normalise(value)
    return normalized == suffix or normalized.endswith("-" + suffix)


def _mapping_by_alias() -> dict[str, PythonRuleMapping]:
    aliases: dict[str, PythonRuleMapping] = {}
    for mapping in PYTHON_RULE_MAPPINGS:
        for alias in mapping.aliases:
            aliases[_normalise(alias)] = mapping
    return aliases


_MAPPING_BY_ALIAS = _mapping_by_alias()
_MAPPING_BY_CANONICAL = {mapping.canonical_rule_id: mapping for mapping in PYTHON_RULE_MAPPINGS}
_GENERIC_CONTEXT_RULES = frozenset(
    {
        "ici.python.security.weak-hash",
        "ici.python.security.dynamic-execution",
    }
)
_CONTEXT_REMAPS = {
    "ici.python.security.weak-hash": {
        "md5": "ici.python.security.weak-md5",
        "weak-md5": "ici.python.security.weak-md5",
        "sha1": "ici.python.security.weak-sha1",
        "weak-sha1": "ici.python.security.weak-sha1",
    },
    "ici.python.security.dynamic-execution": {
        "eval": "ici.python.security.eval",
        "exec": "ici.python.security.exec",
    },
}
_CONTEXT_MATCHES = {
    "ici.python.exception.base-exception": ("baseexception", frozenset({"baseexception"})),
    "ici.python.resource.open-without-context": (
        "openwithoutwith",
        frozenset({"confirmed-leak"}),
    ),
    "ici.python.security.shell-true": ("shelltrue", frozenset({"subprocess"})),
    "ici.python.security.command-processor": (
        "commandprocessor",
        frozenset({"os-system", "os-popen"}),
    ),
    "ici.python.complexity.cyclomatic": ("", frozenset({"cyclomatic"})),
}


def _legacy_engine(rule_id: str) -> str:
    match = _LEGACY_ENGINE_RE.fullmatch(rule_id.strip().casefold())
    return match.group(1) if match else ""


def _source_candidates(
    rule_id: str,
    tool_rule_id: str,
    target_label: str,
) -> tuple[str, ...]:
    """Return specific-to-general source tokens without mutating inputs."""

    candidates: list[str] = []
    for value in (tool_rule_id, target_label, rule_id):
        stripped = value.strip()
        if not stripped or stripped in candidates:
            continue
        candidates.append(stripped)
        # ``mypy`` diagnostics commonly carry ``[name-defined]`` in the
        # message/rule field.  The bracket-free token is the registry key.
        bracket = re.search(r"\[([A-Za-z0-9_-]+)\]", stripped)
        if bracket and bracket.group(1) not in candidates:
            candidates.append(bracket.group(1))
        if ":" in stripped:
            suffix = stripped.rsplit(":", 1)[1].strip()
            if suffix and suffix not in candidates:
                candidates.append(suffix)
    return tuple(candidates)


def _contextual_mapping(
    mapping: PythonRuleMapping,
    source_token: str,
    semantic_context: str,
) -> tuple[str, bool]:
    """Resolve a mapping and whether its alias is proven safe to merge."""

    context = _context(semantic_context)
    # A producer that has already emitted the canonical rule ID has made the
    # semantic classification itself.  The two intentionally generic
    # identities below still require context because they are not actionable
    # rules on their own (``weak-hash`` and ``dynamic-execution``).
    canonical = mapping.canonical_rule_id
    if source_token.strip() == canonical and canonical not in _GENERIC_CONTEXT_RULES:
        return mapping.canonical_rule_id, True
    # Some broad tool rules become safe only after the caller has supplied the
    # AST-derived operation.  Keep their canonical name stable even while
    # refusing a cross-producer merge without that proof.
    remapped = _CONTEXT_REMAPS.get(canonical, {}).get(context)
    if remapped:
        return remapped, True
    if canonical in _CONTEXT_REMAPS:
        return canonical, False
    suffix, accepted_contexts = _CONTEXT_MATCHES.get(canonical, ("", frozenset()))
    if canonical in _CONTEXT_MATCHES:
        source_proves = bool(suffix) and _has_rule_suffix(source_token, suffix)
        return canonical, source_proves or context in accepted_contexts
    if mapping.always_mergeable:
        return canonical, True
    return canonical, False


def _rule_from_canonical(
    rule_id: str,
    *,
    semantic_context: str,
) -> tuple[str, bool] | None:
    mapping = _MAPPING_BY_CANONICAL.get(rule_id.strip())
    if mapping is None:
        return None
    return _contextual_mapping(mapping, rule_id, semantic_context)


def _identity(
    canonical_id: str,
    provenance: PythonRuleProvenance,
    mergeable: bool,
    context: str,
) -> PythonRuleIdentity:
    return PythonRuleIdentity(
        canonical_id,
        provenance,
        canonical_id if mergeable else None,
        context,
    )


def _selected_mapping(
    candidates: tuple[str, ...],
) -> tuple[PythonRuleMapping, str] | None:
    return next(
        (
            (mapping, candidate)
            for candidate in candidates
            if (mapping := _MAPPING_BY_ALIAS.get(_normalise(candidate))) is not None
        ),
        None,
    )


def _mapped_identity(
    mapping: PythonRuleMapping,
    source_token: str,
    engine: str,
    provenance: PythonRuleProvenance,
    context: str,
) -> PythonRuleIdentity:
    canonical_id, mergeable = _contextual_mapping(mapping, source_token, context)
    if mapping.canonical_rule_id != "ici.python.complexity.cyclomatic":
        return _identity(canonical_id, provenance, mergeable, context)
    if engine not in {"", "complexity", "lint", "ruff"}:
        return _identity(
            "ici.python.external." + _normalise(source_token), provenance, False, context
        )
    if engine == "complexity" and not context:
        mergeable = True
    return _identity(canonical_id, provenance, mergeable, context)


def _engine_identity(
    engine: str,
    target_label: str,
    provenance: PythonRuleProvenance,
    context: str,
) -> PythonRuleIdentity | None:
    identities = {
        "complexity": "ici.python.complexity.cyclomatic",
        "cognitive": "ici.python.cognitive.complexity",
    }
    canonical_id = identities.get(engine)
    if engine == "dead" and _normalise(target_label) == "unreachablecode":
        canonical_id = "ici.python.dead.unreachable-statement"
    return _identity(canonical_id, provenance, True, context) if canonical_id else None


def canonical_python_rule_identity(
    rule_id: str,
    *,
    tool_rule_id: str = "",
    tool_name: str = "",
    tool_version: str = "",
    engine_name: str = "",
    target_label: str = "",
    semantic_context: str | None = None,
) -> PythonRuleIdentity:
    """Return a canonical identity without changing the source finding.

    ``semantic_context`` is intentionally caller-supplied.  It should come
    from bounded AST/tool metadata, not an arbitrary user message.  Supported
    contextual values are ``baseexception``, ``confirmed-leak``,
    ``subprocess``, ``os.system``, ``os.popen``, and ``cyclomatic``.
    """

    provenance = PythonRuleProvenance(
        engine_name=engine_name.strip(),
        rule_id=rule_id,
        tool_rule_id=tool_rule_id,
        tool_name=tool_name,
        tool_version=tool_version,
        target_label=target_label,
    )
    context = _context(semantic_context)

    # A producer that already emitted a known ici.python identity is
    # authoritative.  Unknown ici namespaces remain distinct and are not
    # promoted into a cross-tool alias by accident.
    canonical = rule_id.strip()
    canonical_result = _rule_from_canonical(canonical, semantic_context=context)
    if canonical_result is not None:
        canonical_id, mergeable = canonical_result
        return _identity(canonical_id, provenance, mergeable, context)
    if canonical.startswith("ici.python.") and _CANONICAL_RULE_RE.fullmatch(canonical):
        return _identity(canonical, provenance, True, context)

    engine = _normalise(engine_name)
    candidates = _source_candidates(rule_id, tool_rule_id, target_label)
    selected = _selected_mapping(candidates)

    # Ruff C901 is only comparable to the cyclomatic engine.  The cognitive
    # engine deliberately has a separate identity even though both mention
    # complexity in their user-facing messages.
    if selected is not None:
        mapping, source_token = selected
        return _mapped_identity(mapping, source_token, engine, provenance, context)

    # Engine-specific native/legacy fallbacks are intentionally narrow.  A
    # function name from the dead engine is not an unused-import rule, and a
    # cognitive target is not cyclomatic complexity.
    engine_identity = _engine_identity(engine, target_label, provenance, context)
    if engine_identity is not None:
        return engine_identity

    # Preserve a pre-existing external canonical ID, but don't infer that two
    # arbitrary tool codes are equivalent.  A legacy engine target is named
    # under a stable Python namespace solely for display/provenance purposes.
    if canonical and _CANONICAL_RULE_RE.fullmatch(canonical):
        canonical_id = canonical
    else:
        token = _normalise(next(iter(candidates), canonical)) or "unknown"
        canonical_id = f"ici.python.external.{token}"
    return _identity(canonical_id, provenance, False, context)


def python_rule_identity(
    finding: Finding,
    *,
    engine_name: str = "",
    semantic_context: str | None = None,
) -> PythonRuleIdentity:
    """Project one :class:`Finding` into a canonical identity.

    Existing legacy adapters store the target name in
    ``primary_location.label``; using that label here lets the projection
    recognize old engine output without rewriting it.
    """

    inferred_engine = engine_name.strip() or _legacy_engine(finding.rule_id)
    return canonical_python_rule_identity(
        finding.rule_id,
        tool_rule_id=finding.tool_rule_id,
        tool_name=finding.tool_name,
        tool_version=finding.tool_version,
        engine_name=inferred_engine,
        target_label=finding.primary_location.label,
        semantic_context=semantic_context,
    )


def project_python_finding(
    finding: Finding,
    *,
    engine_name: str = "",
    semantic_context: str | None = None,
) -> PythonFindingProjection:
    """Return a read-only finding projection with provenance intact."""

    return PythonFindingProjection(
        finding=finding,
        identity=python_rule_identity(
            finding,
            engine_name=engine_name,
            semantic_context=semantic_context,
        ),
    )


def _precise_bounds(location: SourceLocation) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Return validated inclusive coordinates, or ``None`` for line-only data."""

    values = (
        location.start_line,
        location.end_line,
        location.start_column,
        location.end_column,
    )
    if location.end_line is None or location.start_column is None or location.end_column is None:
        return None
    if any(type(value) is not int or value < 1 for value in values):
        return None
    start = (location.start_line, location.start_column)
    end = (location.end_line, location.end_column)
    if end < start:
        return None
    return start, end


def strict_location_overlap(
    left: SourceLocation,
    right: SourceLocation,
    *,
    project_root: str | Path | None = None,
) -> bool:
    """Return true only for same-path, precise, overlapping source ranges.

    Missing end lines or either column makes the result false.  Invalid or
    unscoped absolute paths are also treated as non-overlapping rather than
    raising from a display projection.
    """

    left_bounds = _precise_bounds(left)
    right_bounds = _precise_bounds(right)
    if left_bounds is None or right_bounds is None:
        return False
    try:
        left_path = canonical_project_path(left.path, project_root)
        right_path = canonical_project_path(right.path, project_root)
    except (TypeError, ValueError):
        return False
    if left_path != right_path:
        return False
    left_start, left_end = left_bounds
    right_start, right_end = right_bounds
    return left_start <= right_end and right_start <= left_end


def can_merge_python_identities(
    left: PythonRuleIdentity,
    right: PythonRuleIdentity,
    left_location: SourceLocation,
    right_location: SourceLocation,
    *,
    project_root: str | Path | None = None,
) -> bool:
    """Apply the rule-family and strict-location merge contract."""

    if not left.mergeable or not right.mergeable:
        return False
    if left.merge_group != right.merge_group:
        return False
    return strict_location_overlap(left_location, right_location, project_root=project_root)


def can_merge_python_findings(
    left: Finding,
    right: Finding,
    *,
    left_engine_name: str = "",
    right_engine_name: str = "",
    left_semantic_context: str | None = None,
    right_semantic_context: str | None = None,
    project_root: str | Path | None = None,
) -> bool:
    """Return whether two source findings may be coalesced in a projection."""

    left_identity = python_rule_identity(
        left,
        engine_name=left_engine_name,
        semantic_context=left_semantic_context,
    )
    right_identity = python_rule_identity(
        right,
        engine_name=right_engine_name,
        semantic_context=right_semantic_context,
    )
    return can_merge_python_identities(
        left_identity,
        right_identity,
        left.primary_location,
        right.primary_location,
        project_root=project_root,
    )


__all__ = [
    "PYTHON_RULE_MAPPINGS",
    "PythonFindingProjection",
    "PythonRuleIdentity",
    "PythonRuleMapping",
    "PythonRuleProvenance",
    "can_merge_python_findings",
    "can_merge_python_identities",
    "canonical_python_rule_identity",
    "project_python_finding",
    "python_rule_identity",
    "strict_location_overlap",
]
