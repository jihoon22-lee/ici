"""SARIF `fixes`: the machine-usable half of a suggestion.

`remediation` renders a tool's fix-its for a person to read. A SARIF consumer
cannot act on that prose — it needs the region and the replacement text — so
the same suggestions travel structured on the finding and are mapped onto
SARIF's own fix model here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.core.findings import canonicalize_finding, finding_fingerprint
from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingFix,
    FindingFixReplacement,
    FindingSeverity,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.json_rep import serialize_suite_result
from ici.reporters.sarif import serialize_sarif


def _replacement(path: str = "src/main.cpp", text: str = "nullptr") -> FindingFixReplacement:
    return FindingFixReplacement(
        path=path,
        start_line=12,
        start_column=5,
        end_line=12,
        end_column=9,
        replacement=text,
    )


def _finding(fixes: list[FindingFix]) -> Finding:
    location = SourceLocation("src/main.cpp", 12, end_line=12, start_column=5)
    return canonicalize_finding(
        Finding(
            rule_id="ici.legacy.lint.target",
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.MEDIUM,
            confidence=FindingConfidence.EXACT,
            fingerprint=finding_fingerprint("ici.legacy.lint.target", location),
            primary_location=location,
            message="use nullptr",
            fixes=fixes,
        )
    )


def _sarif(fixes: list[FindingFix]) -> dict:
    result = EngineResult("lint", EngineStatus.WARN, "1 finding")
    result.findings = [_finding(fixes)]
    suite = VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[result])
    return serialize_sarif(suite, Path("."))


def _results(document: dict) -> list[dict]:
    return document["runs"][0]["results"]


def test_a_suggestion_becomes_a_sarif_fix_a_consumer_can_apply():
    document = _sarif([FindingFix("clang-tidy suggested fix", (_replacement(),))])

    fix = _results(document)[0]["fixes"][0]
    assert fix["description"]["text"] == "clang-tidy suggested fix"
    change = fix["artifactChanges"][0]
    assert change["artifactLocation"]["uri"] == "src/main.cpp"
    replacement = change["replacements"][0]
    assert replacement["deletedRegion"] == {
        "startLine": 12,
        "startColumn": 5,
        "endLine": 12,
        "endColumn": 9,
    }
    assert replacement["insertedContent"]["text"] == "nullptr"


def test_one_fix_spanning_two_files_stays_one_fix():
    """Applying half of a fix is not applying a smaller fix.

    SARIF nests replacements under the artifact they touch, so a two-file
    suggestion is one fix with two artifactChanges. Emitting two fixes would
    invite a consumer to offer them separately.
    """

    document = _sarif(
        [
            FindingFix(
                "cross-file fix",
                (_replacement("src/main.cpp"), _replacement("src/other.cpp", "0")),
            )
        ]
    )

    fixes = _results(document)[0]["fixes"]
    assert len(fixes) == 1
    changes = fixes[0]["artifactChanges"]
    assert [change["artifactLocation"]["uri"] for change in changes] == [
        "src/main.cpp",
        "src/other.cpp",
    ]


def test_a_deletion_is_a_replacement_with_empty_text():
    # SARIF has no separate deletion shape; empty insertedContent is how it is
    # spelled, so an empty replacement must not be dropped as "no suggestion".
    document = _sarif([FindingFix("remove the cast", (_replacement(text=""),))])

    replacement = _results(document)[0]["fixes"][0]["artifactChanges"][0]["replacements"][0]
    assert replacement["insertedContent"]["text"] == ""


def test_a_finding_without_suggestions_emits_no_fixes_key():
    # An empty `fixes` array would tell a consumer a fix exists and is empty.
    assert "fixes" not in _results(_sarif([]))[0]


def test_fixes_travel_in_the_v3_json_with_their_regions_intact():
    result = EngineResult("lint", EngineStatus.WARN, "1 finding")
    result.findings = [_finding([FindingFix("suggested", (_replacement(),))])]
    suite = VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[result])

    payload = serialize_suite_result(suite, project_root=Path("."))

    fixes = payload["results"][0]["findings"][0]["fixes"]
    assert fixes[0]["description"] == "suggested"
    assert fixes[0]["replacements"][0] == {
        "path": "src/main.cpp",
        "start_line": 12,
        "start_column": 5,
        "end_line": 12,
        "end_column": 9,
        "replacement": "nullptr",
    }


def test_the_v3_schema_accepts_a_document_written_before_fixes_existed():
    """`fixes` is additive: an older v3 document must stay valid.

    Making it required would reject reports that were valid v3 yesterday, which
    is the compatibility the stability policy promises within a major.
    """

    schema = json.loads(
        (
            Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
        ).read_text(encoding="utf-8")
    )
    finding = schema["$defs"]["finding"]

    assert "fixes" in finding["properties"]
    assert "fixes" not in finding["required"]
    assert finding["properties"]["fixes"]["items"]["$ref"] == "#/$defs/findingFix"
    replacement = schema["$defs"]["findingFixReplacement"]
    assert replacement["additionalProperties"] is False
    # Every bound is required: a region missing one end cannot be applied.
    assert set(replacement["required"]) == {
        "path",
        "start_line",
        "start_column",
        "end_line",
        "end_column",
        "replacement",
    }


def test_a_fix_region_outside_the_source_contract_is_rejected():
    location = SourceLocation("src/main.cpp", 12)
    with pytest.raises(ValueError, match="fix replacement"):
        canonicalize_finding(
            Finding(
                rule_id="ici.legacy.lint.target",
                category=FindingCategory.CORRECTNESS,
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.EXACT,
                fingerprint="",
                primary_location=location,
                message="bad region",
                fixes=[
                    FindingFix(
                        "reversed",
                        (
                            FindingFixReplacement(
                                path="src/main.cpp",
                                start_line=12,
                                start_column=9,
                                end_line=11,
                                end_column=5,
                                replacement="x",
                            ),
                        ),
                    )
                ],
            )
        )


def test_a_changed_suggestion_does_not_change_the_finding_identity():
    """Identity is where the problem is, not what a tool suggests about it.

    A tool upgrade that improves its fix-it must not read as a new finding in a
    baseline comparison.
    """

    without = _finding([])
    with_fix = _finding([FindingFix("suggested", (_replacement(),))])
    other_fix = _finding([FindingFix("different", (_replacement(text="0"),))])

    assert without.fingerprint == with_fix.fingerprint == other_fix.fingerprint


def test_lint_carries_compiler_fixits_through_to_the_finding():
    """The prose and the structured form must describe the same suggestion.

    `_cpp_remediation` and `_cpp_fixes` read the same fix-its. If only the prose
    were produced, a SARIF consumer would see a finding whose remediation text
    mentions an edit it cannot offer.
    """

    from ici.core.models import InspectionTarget
    from ici.engines._cpp_diagnostics import CppDiagnostic, CppFixIt
    from ici.engines.lint import LintEngine

    diagnostic = CppDiagnostic(
        target=InspectionTarget(
            file_path="src/main.cpp",
            start_line=9,
            status=EngineStatus.WARN,
            message="use nullptr",
        ),
        tool_rule_id="modernize-use-nullptr",
        family="clang-tidy",
        fixits=(
            CppFixIt(
                file_path="src/main.cpp",
                start_line=9,
                start_column=3,
                end_line=9,
                end_column=7,
                replacement="nullptr",
            ),
        ),
    )

    fixes = LintEngine._cpp_fixes(diagnostic)
    remediation = LintEngine._cpp_remediation(diagnostic)

    assert len(fixes) == 1
    assert fixes[0].description == "modernize-use-nullptr suggested fix"
    only = fixes[0].replacements[0]
    assert (only.path, only.start_line, only.start_column) == ("src/main.cpp", 9, 3)
    assert (only.end_line, only.end_column, only.replacement) == (9, 7, "nullptr")
    # Both surfaces name the same edit; neither is the source of the other.
    assert "nullptr" in remediation and "src/main.cpp:9:3-9:7" in remediation

    assert LintEngine._cpp_fixes(_without_fixits(diagnostic)) == []


def _without_fixits(diagnostic):
    from dataclasses import replace as dataclass_replace

    return dataclass_replace(diagnostic, fixits=())


def test_the_engine_attaches_the_fixes_to_the_finding_it_builds(tmp_path: Path):
    """Producing the fixes is not the same as putting them on the finding.

    Calling `_cpp_fixes` directly proves the mapping and nothing else: dropping
    the `fixes=` argument at the construction site left that assertion passing
    while every report lost its suggestions. This goes through the builder.
    """

    from ici.core.models import InspectionTarget
    from ici.engines._cpp_diagnostics import CppDiagnostic, CppFixIt
    from ici.engines.lint import LintEngine

    target = InspectionTarget(
        file_path="src/main.cpp",
        start_line=9,
        status=EngineStatus.WARN,
        target_name="use nullptr",
        message="use nullptr",
    )
    engine = LintEngine(tmp_path)
    engine._cpp_diagnostics = [
        CppDiagnostic(
            target=target,
            tool_rule_id="modernize-use-nullptr",
            family="clang-tidy",
            fixits=(
                CppFixIt(
                    file_path="src/main.cpp",
                    start_line=9,
                    start_column=3,
                    end_line=9,
                    end_column=7,
                    replacement="nullptr",
                ),
            ),
        )
    ]

    findings = engine._cpp_findings([target], [])

    assert len(findings) == 1
    assert len(findings[0].fixes) == 1
    replacement = findings[0].fixes[0].replacements[0]
    assert replacement.replacement == "nullptr"
    assert (replacement.start_line, replacement.end_column) == (9, 7)
