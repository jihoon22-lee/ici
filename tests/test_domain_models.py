"""Validation and conversion for the ici-next domain models (#200 PR A).

The tests are organized around what each model is supposed to make impossible,
because that is the reason these are frozen dataclasses with validators rather
than dicts. Where a rule comes from a spec or from a WP01 measurement, the test
says so — a constraint whose origin is forgotten is a constraint that gets
relaxed by the next person who finds it inconvenient.
"""

from __future__ import annotations

import dataclasses

import pytest

from ici.core.models import (
    EvidenceState,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SuppressionKind,
)
from ici.core.models import (
    Finding as LegacyFinding,
)
from ici.core.models import (
    SourceLocation as LegacySourceLocation,
)
from ici.domain import (
    AnalysisUnit,
    BuildUnit,
    Component,
    EvidenceLevel,
    ExecutionSummary,
    Finding,
    FindingSuppression,
    GateOutcome,
    GateVerdict,
    Measurement,
    Observation,
    PublicationOutcome,
    PublicationState,
    ResolvedTool,
    RunIdentity,
    RunResult,
    ScopeKind,
    ScopeSelection,
    SourceSnapshot,
    SourceSpan,
    TaskKind,
    TaskSpec,
    TaskState,
    ToolRole,
    ToolSource,
    Workspace,
)
from ici.domain.legacy import (
    LEGACY_UNMAPPED,
    evidence_from_legacy,
    evidence_to_legacy,
    finding_from_legacy,
    legacy_limitations,
    span_from_legacy,
    span_to_legacy,
)

DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32


def make_snapshot(**overrides: object) -> SourceSnapshot:
    return SourceSnapshot(**{"digest": DIGEST, "files": ("src/a.py",), **overrides})  # type: ignore[arg-type]


def make_span(**overrides: object) -> SourceSpan:
    return SourceSpan(**{"path": "src/a.py", "start_line": 3, **overrides})  # type: ignore[arg-type]


def make_finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "fingerprint": "fp-1",
        "rule_id": "ICI001",
        "message": "something to fix",
        "severity": "high",
        "confidence": "exact",
        "primary_location": make_span(),
        "provider": "ruff",
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def make_task(**overrides: object) -> TaskSpec:
    base: dict[str, object] = {
        "id": "analyze-gui",
        "kind": TaskKind.ANALYZE,
        "provider": "ruff",
        "argv": ("ruff", "check"),
        "cwd": ".",
    }
    base.update(overrides)
    return TaskSpec(**base)  # type: ignore[arg-type]


class TestImmutability:
    @pytest.mark.parametrize(
        ("value", "field_name"),
        [
            (make_snapshot(), "digest"),
            (make_span(), "path"),
            (make_finding(), "provider"),
            (make_task(), "cwd"),
        ],
        ids=["snapshot", "span", "finding", "task"],
    )
    def test_models_are_frozen(self, value: object, field_name: str):
        """Inputs are not modified during a run (ARCH section 4)."""

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field_name, "mutated")


class TestValidationPrimitives:
    def test_digest_must_be_complete(self):
        """SPEC-04's envelope shows ``sha256:...``; that is not a valid value."""

        with pytest.raises(ValueError, match="full sha256"):
            make_snapshot(digest="sha256:...")

    def test_paths_must_be_contained(self):
        with pytest.raises(ValueError, match="relative and contained"):
            make_span(path="../outside.py")

    def test_paths_must_be_canonical(self):
        """``src/./a.py`` and ``src/a.py`` must not both be accepted.

        One path has to be one string, or a digest over the model stops meaning
        anything.
        """

        with pytest.raises(ValueError, match="canonical"):
            make_span(path="src/./a.py")

    def test_absolute_paths_are_rejected(self):
        with pytest.raises(ValueError, match="relative and contained"):
            make_span(path="/etc/passwd")

    def test_a_bare_string_is_not_a_collection(self):
        """Accepting one would turn ``"gui"`` into four components."""

        with pytest.raises(ValueError, match="not a scalar"):
            Component(id="gui", root="apps/gui", languages="cpp")  # type: ignore[arg-type]

    def test_identifiers_reject_uppercase_and_spaces(self):
        for bad in ("GUI", "my component", "-leading"):
            with pytest.raises(ValueError, match="lowercase alphanumeric"):
                Component(id=bad, root="apps/gui", languages=("cpp",))

    def test_line_numbers_are_one_indexed(self):
        with pytest.raises(ValueError, match="1-indexed"):
            make_span(start_line=0)

    def test_end_line_may_not_precede_start(self):
        with pytest.raises(ValueError, match="must not precede"):
            make_span(start_line=10, end_line=4)

    def test_booleans_are_not_integers(self):
        with pytest.raises(ValueError, match="must be an integer"):
            make_span(start_line=True)


class TestWorkspaceComposition:
    def test_component_needs_a_language(self):
        with pytest.raises(ValueError, match="at least one language"):
            Component(id="core", root="libs/core", languages=())

    def test_dangling_build_reference_is_rejected(self):
        """SPEC-01 section 3 makes this a configuration error."""

        with pytest.raises(ValueError, match="undefined build units"):
            Workspace(
                id="product",
                name="product",
                components=(
                    Component(id="gui", root="apps/gui", languages=("cpp",), build_ids=("native",)),
                ),
            )

    def test_duplicate_component_ids_are_rejected(self):
        component = Component(id="gui", root="apps/gui", languages=("cpp",))
        with pytest.raises(ValueError, match="duplicate ids"):
            Workspace(id="product", name="product", components=(component, component))

    def test_required_component_must_be_registered(self):
        with pytest.raises(ValueError, match="not registered"):
            Workspace(id="product", name="product", required_component_ids=("ghost",))

    def test_lookup_helpers(self):
        build = BuildUnit(id="native", system="qmake", variant="default", directory="build/ici")
        component = Component(id="gui", root="apps/gui", languages=("cpp",), build_ids=("native",))
        workspace = Workspace(
            id="product", name="product", components=(component,), builds=(build,)
        )
        assert workspace.component("gui") is component
        assert workspace.build("native") is build
        with pytest.raises(KeyError):
            workspace.component("absent")

    def test_build_unit_knows_whether_it_mutates(self):
        """``prepare = explicit`` grants permission, not a command.

        A unit with no prepare argv has no approved way to configure itself, and
        that is a blocking condition rather than licence to guess.
        """

        quiet = BuildUnit(id="native", system="qmake", variant="default", directory="build/ici")
        assert not quiet.mutating
        loud = dataclasses.replace(quiet, prepare_argv=("qmake", "product.pro"))
        assert loud.mutating

    def test_analysis_unit_keeps_variants_apart(self):
        """The key is the whole tuple, never the source file (SPEC-01 section 1)."""

        coverage = AnalysisUnit(
            id="gui-cpp-coverage",
            component_id="gui",
            language="cpp",
            sources=("apps/gui/w.cpp",),
            variant="coverage",
        )
        sanitize = dataclasses.replace(coverage, id="gui-cpp-sanitize", variant="sanitize")
        assert coverage != sanitize
        assert coverage.sources == sanitize.sources


class TestResolvedTool:
    def test_launch_path_and_real_path_are_distinct_fields(self):
        """WP01 measured that substituting realpath changes ``sys.prefix``."""

        tool = ResolvedTool(
            name="python",
            role=ToolRole.TEST,
            source=ToolSource.CONFIGURED,
            launch_path="/proj/.venv/bin/python",
            real_path="/usr/bin/python3.10",
        )
        assert tool.relocated
        assert tool.launch_path != tool.real_path

    def test_a_non_symlinked_tool_is_not_relocated(self):
        tool = ResolvedTool(
            name="ruff",
            role=ToolRole.ANALYZER,
            source=ToolSource.BUNDLE,
            launch_path="/bundle/tools/ruff",
            real_path="/bundle/tools/ruff",
        )
        assert not tool.relocated

    def test_role_and_source_must_be_enums(self):
        with pytest.raises(ValueError, match="must be a ToolRole"):
            ResolvedTool(name="ruff", role="analyzer", source=ToolSource.BUNDLE, launch_path="/x")  # type: ignore[arg-type]


class TestTaskSpec:
    def test_argv_may_not_be_empty(self):
        with pytest.raises(ValueError, match="non-empty argv"):
            make_task(argv=())

    def test_a_task_cannot_depend_on_itself(self):
        with pytest.raises(ValueError, match="cannot depend on itself"):
            make_task(depends_on=("analyze-gui",))

    def test_only_prepare_mutates(self):
        assert not make_task().mutating
        assert make_task(id="prep", kind=TaskKind.PREPARE, cacheable=False).mutating

    def test_a_prepare_task_may_not_be_cacheable(self):
        with pytest.raises(ValueError, match="must not be marked cacheable"):
            make_task(id="prep", kind=TaskKind.PREPARE)

    def test_env_overlay_is_sorted_and_unique(self):
        task = make_task(env_overlay=(("ZED", "1"), ("ALPHA", "2")))
        assert task.env_overlay == (("ALPHA", "2"), ("ZED", "1"))
        with pytest.raises(ValueError, match="not set a variable twice"):
            make_task(env_overlay=(("A", "1"), ("A", "2")))

    def test_env_overlay_accepts_a_mapping(self):
        assert make_task(env_overlay={"B": "2", "A": "1"}).env_overlay == (("A", "1"), ("B", "2"))

    def test_share_key_ignores_identity_but_not_inputs(self):
        """Two units needing the identical command is the case worth sharing."""

        first = make_task(id="analyze-a", analysis_unit_ids=("unit-a",))
        second = make_task(id="analyze-b", analysis_unit_ids=("unit-b",))
        assert first.share_key == second.share_key

        different_env = make_task(id="analyze-c", env_overlay=(("CFLAGS", "-O2"),))
        assert different_env.share_key != first.share_key

        different_cwd = make_task(id="analyze-d", cwd="apps/gui")
        assert different_cwd.share_key != first.share_key

    def test_timeout_must_be_positive_and_finite(self):
        for bad in (0, -1, float("inf")):
            with pytest.raises(ValueError):
                make_task(timeout_seconds=bad)


class TestFindingSemantics:
    def test_a_suppressed_finding_must_say_why(self):
        with pytest.raises(ValueError, match="record why"):
            FindingSuppression(suppressed=True)

    def test_suppressed_findings_do_not_count_against_the_gate(self):
        finding = make_finding(
            suppression=FindingSuppression(suppressed=True, reason="accepted debt")
        )
        assert not finding.counts_against_gate

    def test_estimated_findings_do_not_count_against_the_gate(self):
        """SPEC-03 forbids promoting a heuristic to an exact gate."""

        assert not make_finding(evidence=EvidenceLevel.ESTIMATED).counts_against_gate
        assert make_finding().counts_against_gate

    def test_attribution_fields_accept_the_run_context(self):
        finding = make_finding(
            component_id="gui",
            analysis_unit_id="gui-cpp-coverage",
            variant="coverage",
            task_id="analyze-gui",
        )
        assert finding.variant == "coverage"


class TestObservation:
    def test_truncated_output_is_not_complete_evidence(self):
        """Zero findings from a cut-off parser is missing data, not a clean run."""

        clean = Observation(task_id="analyze-gui", provider="ruff", state=TaskState.SUCCEEDED)
        assert clean.evidence_is_complete
        assert not dataclasses.replace(clean, truncated=True).evidence_is_complete
        assert not dataclasses.replace(clean, timed_out=True).evidence_is_complete

    def test_a_failed_task_is_not_complete_evidence(self):
        failed = Observation(task_id="analyze-gui", provider="ruff", state=TaskState.FAILED)
        assert not failed.evidence_is_complete

    def test_measurement_keeps_raw_counts(self):
        """SPEC-04 forbids averaging percentages; raw counts have to survive."""

        measurement = Measurement(
            name="line-coverage", value=89.2, unit="percent", numerator=446, denominator=500
        )
        assert (measurement.numerator, measurement.denominator) == (446, 500)

    def test_measurement_rejects_nan(self):
        with pytest.raises(ValueError, match="must not be NaN"):
            Measurement(name="broken", value=float("nan"))


class TestRunResultAxes:
    def make_result(self, **overrides: object) -> RunResult:
        base: dict[str, object] = {
            "run_id": "run-1",
            "identity": RunIdentity(
                source=make_snapshot(), policy_digest=DIGEST, toolchain_digest=OTHER_DIGEST
            ),
            "scope": ScopeSelection(kind=ScopeKind.FULL, full_required_satisfied=True),
            "execution": ExecutionSummary(required_complete=True),
            "gate": GateOutcome(selected=GateVerdict.PASS),
        }
        base.update(overrides)
        return RunResult(**base)  # type: ignore[arg-type]

    def test_a_full_scope_must_be_satisfied(self):
        with pytest.raises(ValueError, match="must satisfy the required"):
            ScopeSelection(kind=ScopeKind.FULL)

    def test_a_partial_scope_reports_what_it_omitted(self):
        """R05: a partial pass is not a workspace pass."""

        scope = ScopeSelection(
            kind=ScopeKind.PARTIAL,
            selected_components=("tool-a",),
            required_components=("tool-a", "gui"),
        )
        assert scope.omitted
        assert not scope.full_required_satisfied

    def test_blocked_work_means_incomplete(self):
        with pytest.raises(ValueError, match="not complete"):
            ExecutionSummary(required_complete=True, blocked_task_ids=("prep",))

    def test_a_passing_gate_cannot_report_violations(self):
        with pytest.raises(ValueError, match="cannot also report violations"):
            GateOutcome(selected=GateVerdict.PASS, has_violations=True)

    def test_a_failing_gate_must_give_a_reason(self):
        with pytest.raises(ValueError, match="must say why"):
            GateOutcome(selected=GateVerdict.FAIL)

    def test_incomplete_and_violations_coexist(self):
        """The combination #200 names explicitly, and its exit code.

        A run that could not finish but did find real problems reports both.
        Exit 3 tells the caller the picture is partial; ``has_violations`` keeps
        the findings from being thrown away.
        """

        result = self.make_result(
            scope=ScopeSelection(
                kind=ScopeKind.PARTIAL,
                selected_components=("tool-a",),
                required_components=("tool-a", "gui"),
            ),
            execution=ExecutionSummary(required_complete=False, blocked_task_ids=("prep",)),
            gate=GateOutcome(
                selected=GateVerdict.INCOMPLETE,
                has_violations=True,
                reasons=("prepare for gui was blocked",),
            ),
            findings=(make_finding(),),
        )
        assert result.gate.exit_code == 3
        assert result.gate.has_violations
        assert result.blocking_findings

    def test_incomplete_outranks_fail_in_the_exit_code(self):
        assert GateOutcome(selected=GateVerdict.FAIL, reasons=("x",)).exit_code == 1
        assert GateOutcome(selected=GateVerdict.INCOMPLETE, reasons=("x",)).exit_code == 3
        assert GateOutcome(selected=GateVerdict.PASS).exit_code == 0
        assert GateOutcome(selected=GateVerdict.NOT_EVALUATED).exit_code == 0

    def test_an_incomplete_run_cannot_pass(self):
        with pytest.raises(ValueError, match="cannot report a passing gate"):
            self.make_result(execution=ExecutionSummary(required_complete=False))

    def test_claimed_violations_need_a_blocking_finding(self):
        with pytest.raises(ValueError, match="no finding counts against it"):
            self.make_result(
                gate=GateOutcome(selected=GateVerdict.FAIL, has_violations=True, reasons=("lint",))
            )

    def test_publication_does_not_change_the_verdict(self):
        """SPEC-04 section 2: a failed upload is retryable, not a quality result."""

        result = self.make_result(
            publication=PublicationOutcome(state=PublicationState.FAILED, detail="403")
        )
        assert result.gate.selected is GateVerdict.PASS
        assert result.gate.exit_code == 0
        assert result.publication.state is PublicationState.FAILED

    def test_schema_identity_is_pinned(self):
        result = self.make_result()
        assert (result.schema_id, result.schema_version) == ("ici.next.run", 1)
        with pytest.raises(ValueError, match="schema_id must be"):
            self.make_result(schema_id="ici-result-v3")


class TestLegacyBridge:
    def make_legacy(self) -> LegacyFinding:
        return LegacyFinding(
            rule_id="ICI001",
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.HIGH,
            confidence=FindingConfidence.EXACT,
            fingerprint="fp-1",
            primary_location=LegacySourceLocation(
                path="src/a.py", start_line=3, end_line=4, start_column=2, label="add"
            ),
            message="something to fix",
            tool_rule_id="E501",
            tool_version="0.15.8",
        )

    def test_evidence_mapping_is_total_both_ways(self):
        """A new value on either side should break a test, not pick a default."""

        for level in EvidenceLevel:
            assert evidence_from_legacy(evidence_to_legacy(level)) is level
        for state in EvidenceState:
            assert evidence_to_legacy(evidence_from_legacy(state)) is state

    def test_span_roundtrip_preserves_every_coordinate(self):
        original = LegacySourceLocation(
            path="src/a.py", start_line=3, end_line=9, start_column=2, end_column=7, label="add"
        )
        assert span_to_legacy(span_from_legacy(original)) == original

    def test_lifting_preserves_location_confidence_and_scope(self):
        """#200 acceptance: finding, location, confidence and scope survive."""

        legacy = self.make_legacy()
        lifted = finding_from_legacy(legacy, provider="ruff")
        assert lifted.fingerprint == legacy.fingerprint
        assert lifted.rule_id == legacy.rule_id
        assert lifted.message == legacy.message
        assert lifted.severity == legacy.severity.value
        assert lifted.confidence == legacy.confidence.value
        assert lifted.category == legacy.category.value
        assert span_to_legacy(lifted.primary_location) == legacy.primary_location
        assert lifted.native_rule_id == "E501"
        assert lifted.rule_version == "0.15.8"

    def test_provider_is_required_from_the_caller(self):
        """``tool_name`` names an executable, not the provider that chose it."""

        lifted = finding_from_legacy(self.make_legacy(), provider="ruff")
        assert lifted.provider == "ruff"

    def test_unmapped_fields_come_back_empty_not_invented(self):
        lifted = finding_from_legacy(self.make_legacy(), provider="ruff")
        for name in ("component_id", "analysis_unit_id", "variant", "task_id"):
            assert getattr(lifted, name) is None
        assert lifted.tags == ()

    def test_suppression_records_where_it_came_from(self):
        lifted = finding_from_legacy(self.make_legacy(), provider="ruff")
        assert lifted.suppression.origin == "legacy-v3"

    def test_limitations_name_only_real_losses(self):
        """A caller can attach this to a converted report and mean something."""

        plain = finding_from_legacy(self.make_legacy(), provider="ruff")
        assert legacy_limitations(plain) == ("provider",)

        attributed = dataclasses.replace(
            plain, component_id="gui", variant="coverage", tags=("security",)
        )
        losses = legacy_limitations(attributed)
        assert set(losses) == {"provider", "component_id", "variant", "tags"}

    def test_fields_with_a_v3_home_are_not_reported_as_losses(self):
        lifted = finding_from_legacy(self.make_legacy(), provider="ruff")
        assert "native_rule_id" not in legacy_limitations(lifted)
        assert "rule_version" not in legacy_limitations(lifted)

    def test_unmapped_list_matches_the_model(self):
        """Keeps ``LEGACY_UNMAPPED`` from drifting away from the dataclass."""

        names = {field.name for field in dataclasses.fields(Finding)}
        assert set(LEGACY_UNMAPPED) <= names

    def test_legacy_suppression_kind_roundtrips_through_its_value(self):
        legacy = self.make_legacy()
        legacy.suppression.suppressed = True
        legacy.suppression.kind = SuppressionKind.BASELINE
        legacy.suppression.reason = "accepted"
        lifted = finding_from_legacy(legacy, provider="ruff")
        assert lifted.suppression.kind == SuppressionKind.BASELINE.value
        assert lifted.suppression.suppressed
