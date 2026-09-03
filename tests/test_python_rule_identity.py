"""Focused contract tests for the read-only Python rule identity projection."""

from __future__ import annotations

from pathlib import Path

from ici.core.models import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
)
from ici.core.python_rules import (
    PYTHON_RULE_MAPPINGS,
    can_merge_python_findings,
    can_merge_python_identities,
    project_python_finding,
    python_rule_identity,
    strict_location_overlap,
)


def _finding(
    *,
    path: str = "src/app.py",
    start_line: int = 3,
    end_line: int | None = 3,
    start_column: int | None = 1,
    end_column: int | None = 8,
    rule_id: str = "ici.legacy.lint.target",
    tool_rule_id: str = "",
    tool_name: str = "",
    tool_version: str = "",
    label: str = "",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.EXACT,
        fingerprint="sha256:" + "a" * 64,
        primary_location=SourceLocation(
            path=path,
            start_line=start_line,
            end_line=end_line,
            start_column=start_column,
            end_column=end_column,
            label=label,
        ),
        message="diagnostic",
        tool_rule_id=tool_rule_id,
        tool_name=tool_name,
        tool_version=tool_version,
    )


def test_registry_contains_only_documented_python_overlap_families() -> None:
    canonical_ids = {mapping.canonical_rule_id for mapping in PYTHON_RULE_MAPPINGS}

    assert {
        "ici.python.exception.bare-except",
        "ici.python.correctness.mutable-default",
        "ici.python.resource.open-without-context",
        "ici.python.security.weak-md5",
        "ici.python.security.weak-sha1",
        "ici.python.security.weak-random",
        "ici.python.security.eval",
        "ici.python.security.pickle-load",
        "ici.python.security.shell-true",
        "ici.python.security.command-processor",
        "ici.python.security.hardcoded-secret",
        "ici.python.complexity.cyclomatic",
        "ici.python.type.undefined-name",
    } <= canonical_ids


def test_bare_except_and_mutable_default_have_lossless_provenance_and_merge() -> None:
    internal = _finding(
        rule_id="ici.legacy.exception.target",
        label="BareExcept",
        start_column=1,
        end_column=13,
    )
    ruff = _finding(
        rule_id="ici.legacy.lint.target",
        tool_rule_id="Ruff:E722",
        tool_name="ruff",
        tool_version="0.16.3",
        label="Ruff:E722",
        start_column=1,
        end_column=7,
    )

    internal_identity = python_rule_identity(internal, engine_name="exception")
    ruff_identity = python_rule_identity(ruff, engine_name="lint")

    assert internal_identity.canonical_rule_id == "ici.python.exception.bare-except"
    assert ruff_identity.canonical_rule_id == internal_identity.canonical_rule_id
    assert ruff_identity.provenance.tool_rule_id == "Ruff:E722"
    assert ruff_identity.provenance.tool_name == "ruff"
    assert ruff_identity.source_rule_id == "Ruff:E722"
    assert can_merge_python_findings(internal, ruff)

    mutable = _finding(
        rule_id="ici.legacy.resource.target",
        tool_rule_id="Correctness:MutableDefault",
        label="Correctness:MutableDefault",
        start_line=10,
        end_line=10,
        start_column=14,
        end_column=20,
    )
    mutable_ruff = _finding(
        tool_rule_id="Ruff:B006",
        tool_name="ruff",
        label="Ruff:B006",
        start_line=10,
        end_line=10,
        start_column=14,
        end_column=20,
    )
    assert python_rule_identity(mutable).canonical_rule_id == (
        "ici.python.correctness.mutable-default"
    )
    assert can_merge_python_findings(mutable, mutable_ruff)


def test_projection_keeps_the_original_finding_unchanged() -> None:
    finding = _finding(
        rule_id="ici.legacy.security.target",
        tool_rule_id="Security:HardcodedSecret",
        label="Security:HardcodedSecret",
        tool_name="ici Python AST",
    )
    before = finding.primary_location

    projection = project_python_finding(finding, engine_name="security")

    assert projection.finding is finding
    assert projection.finding.primary_location == before
    assert projection.identity.canonical_rule_id == "ici.python.security.hardcoded-secret"
    assert projection.identity.provenance.rule_id == "ici.legacy.security.target"
    assert projection.identity.provenance.tool_rule_id == "Security:HardcodedSecret"


def test_strict_location_overlap_rejects_line_only_and_ambiguous_occurrences() -> None:
    precise = SourceLocation("src/app.py", 4, 4, 5, 12)
    overlapping = SourceLocation("src/app.py", 4, 4, 10, 18)
    same_line_elsewhere = SourceLocation("src/app.py", 4, 4, 13, 20)
    adjacent = SourceLocation("src/app.py", 4, 4, 13, 21)

    assert strict_location_overlap(precise, overlapping)
    assert not strict_location_overlap(precise, same_line_elsewhere)
    assert not strict_location_overlap(precise, adjacent)
    assert not strict_location_overlap(SourceLocation("src/app.py", 4, None, 5, 12), precise)
    assert not strict_location_overlap(SourceLocation("src/app.py", 4, 4, None, 12), precise)
    assert not strict_location_overlap(SourceLocation("src/app.py", 4, 4, 5, None), precise)


def test_strict_location_overlap_normalizes_paths_but_never_crosses_roots() -> None:
    root = Path("/checkout")
    left = SourceLocation("/checkout/src/app.py", 4, 4, 1, 8)
    right = SourceLocation(r"src\app.py", 4, 4, 4, 9)
    outside = SourceLocation("/other/src/app.py", 4, 4, 4, 9)

    assert strict_location_overlap(left, right, project_root=root)
    assert not strict_location_overlap(left, outside, project_root=root)
    assert not strict_location_overlap(left, right)


def test_contextual_broad_aliases_require_semantic_proof() -> None:
    base = _finding(
        rule_id="ici.legacy.exception.target",
        label="BaseException",
        start_column=8,
        end_column=21,
    )
    blind = _finding(
        tool_rule_id="Ruff:BLE001",
        tool_name="ruff",
        label="Ruff:BLE001",
        start_column=8,
        end_column=21,
    )
    assert python_rule_identity(base).mergeable
    assert not python_rule_identity(blind).mergeable
    assert not can_merge_python_findings(base, blind)
    assert can_merge_python_findings(
        base,
        blind,
        right_semantic_context="baseexception",
    )

    resource = _finding(
        rule_id="ici.legacy.resource.target",
        tool_rule_id="Resource:OpenWithoutWith",
        label="Resource:OpenWithoutWith",
        start_column=5,
        end_column=12,
    )
    sim = _finding(
        tool_rule_id="Ruff:SIM115",
        tool_name="ruff",
        label="Ruff:SIM115",
        start_column=5,
        end_column=9,
    )
    assert can_merge_python_findings(resource, sim, right_semantic_context="confirmed-leak")
    assert not can_merge_python_findings(resource, sim)


def test_weak_hash_rule_needs_algorithm_context_and_then_merges_exactly() -> None:
    internal = _finding(
        rule_id="ici.legacy.security.target",
        tool_rule_id="Security:WeakCryptoMD5",
        label="Security:WeakCryptoMD5",
        start_column=1,
        end_column=12,
    )
    ruff = _finding(
        tool_rule_id="Ruff:S324",
        tool_name="ruff",
        label="Ruff:S324",
        start_column=1,
        end_column=12,
    )
    assert python_rule_identity(ruff).canonical_rule_id == "ici.python.security.weak-hash"
    assert not can_merge_python_findings(internal, ruff)
    assert can_merge_python_findings(ruff, internal, left_semantic_context="md5")
    assert can_merge_python_findings(internal, ruff, right_semantic_context="md5")
    assert not can_merge_python_findings(internal, ruff, right_semantic_context="sha1")


def test_mypy_name_defined_and_ruff_f821_merge_only_with_explicit_error_code() -> None:
    ruff = _finding(tool_rule_id="Ruff:F821", tool_name="ruff", label="Ruff:F821")
    mypy = _finding(tool_rule_id="Mypy:name-defined", tool_name="mypy", label="Mypy:name-defined")
    mypy_without_code = _finding(tool_rule_id="MypyError", tool_name="mypy", label="MypyError")

    assert python_rule_identity(ruff).canonical_rule_id == "ici.python.type.undefined-name"
    assert python_rule_identity(mypy).canonical_rule_id == "ici.python.type.undefined-name"
    assert can_merge_python_findings(ruff, mypy)
    assert not can_merge_python_findings(ruff, mypy_without_code)


def test_cognitive_cyclomatic_and_semantically_different_exception_rules_never_merge() -> None:
    cognitive = _finding(
        rule_id="ici.legacy.cognitive.target",
        label="calculate",
        start_line=20,
        end_line=30,
        start_column=1,
        end_column=2,
    )
    cyclomatic = _finding(
        rule_id="ici.legacy.complexity.target",
        label="calculate",
        start_line=20,
        end_line=30,
        start_column=1,
        end_column=2,
    )
    ruff_complexity = _finding(
        tool_rule_id="Ruff:C901",
        tool_name="ruff",
        label="Ruff:C901",
        start_line=20,
        end_line=20,
        start_column=5,
        end_column=14,
    )
    lost_traceback = _finding(
        rule_id="ici.legacy.exception.target",
        label="LostTraceback",
        start_line=40,
        end_line=40,
        start_column=5,
        end_column=13,
    )
    raise_without_from = _finding(
        tool_rule_id="Ruff:B904",
        tool_name="ruff",
        label="Ruff:B904",
        start_line=40,
        end_line=40,
        start_column=5,
        end_column=13,
    )

    cognitive_identity = python_rule_identity(cognitive, engine_name="cognitive")
    cyclomatic_identity = python_rule_identity(cyclomatic, engine_name="complexity")
    ruff_identity = python_rule_identity(
        ruff_complexity,
        engine_name="lint",
        semantic_context="cyclomatic",
    )
    assert cognitive_identity.canonical_rule_id == "ici.python.cognitive.complexity"
    assert cyclomatic_identity.canonical_rule_id == "ici.python.complexity.cyclomatic"
    assert can_merge_python_identities(
        cyclomatic_identity,
        ruff_identity,
        cyclomatic.primary_location,
        ruff_complexity.primary_location,
    )
    assert not can_merge_python_findings(
        cognitive,
        ruff_complexity,
        left_engine_name="cognitive",
        right_engine_name="lint",
        right_semantic_context="cyclomatic",
    )

    assert python_rule_identity(lost_traceback, engine_name="exception").canonical_rule_id == (
        "ici.python.exception.lost-traceback"
    )
    assert python_rule_identity(raise_without_from).canonical_rule_id == (
        "ici.python.exception.raise-without-from"
    )
    assert not can_merge_python_findings(lost_traceback, raise_without_from)


def test_dead_unreachable_does_not_alias_ruff_f401() -> None:
    unreachable = _finding(
        rule_id="ici.legacy.dead.target",
        label="UnreachableCode",
        start_line=50,
        end_line=50,
        start_column=5,
        end_column=15,
    )
    unused_import = _finding(
        tool_rule_id="Ruff:F401",
        tool_name="ruff",
        label="Ruff:F401",
        start_line=50,
        end_line=50,
        start_column=5,
        end_column=15,
    )

    assert python_rule_identity(unreachable, engine_name="dead").canonical_rule_id == (
        "ici.python.dead.unreachable-statement"
    )
    assert not can_merge_python_findings(unreachable, unused_import)


def test_unknown_or_line_only_rules_are_never_cross_merged() -> None:
    unknown_a = _finding(tool_rule_id="Ruff:XYZ999", tool_name="ruff")
    unknown_b = _finding(tool_rule_id="Mypy:unknown-code", tool_name="mypy")
    assert not python_rule_identity(unknown_a).mergeable
    assert not python_rule_identity(unknown_b).mergeable
    assert not can_merge_python_findings(unknown_a, unknown_b)

    line_only = _finding(tool_rule_id="Ruff:E722", start_column=None, end_column=None)
    precise = _finding(tool_rule_id="Ruff:E722")
    assert not can_merge_python_findings(line_only, precise)
