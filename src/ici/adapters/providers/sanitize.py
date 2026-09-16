"""Running an instrumented suite under its sanitizer runtime — never building.

``cpp.sanitize`` and ``cpp.tsan`` (#220) are dynamic checks: they run the
binaries a ``variant = "sanitize"`` / ``variant = "thread-sanitize"`` build
produced, under the runtime options the sanitizer reads. ici does not
compile the instrumentation — the variant declaration is the project's own
claim, verified separately by the binary's embedded markers.

The run's answer mirrors the stable ``sanitize`` engine's contract:

- **diagnostics** — a report marker parsed into defects; each is a finding
  with the parser's normalized rule (``ici.sanitize.asan.heap-use-after-free``).
- **marker but nothing parseable** — the transcript claimed a sanitizer
  report and produced no complete diagnostic: ``failed_to_parse``, because a
  truncated or malformed report cannot stand in for a clean suite.
- **no marker, non-zero exit** — the suite failed on its own terms: one
  measured finding naming that, not a sanitizer defect.
- **no marker, exit zero** — the only clean answer.

Sanitizer transcripts arrive on stderr while the suite's own protocol is
stdout — the parse reads stderr first, matching the stable engine.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.core.models import SourceLocation
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines._sanitizer_diagnostics import (
    _ERROR_RE,
    _RUNTIME_RE,
    _SUMMARY_RE,
    _TSAN_WARNING_RE,
    SanitizerDiagnostic,
    SanitizerDiagnosticError,
    parse_sanitizer_diagnostics,
)
from ici.execution.process import ExitContract, TaskOutcome
from ici.execution.process import TaskSpec as ExecTaskSpec

__all__ = ["SanitizeProvider"]

#: Any finished exit is an answer — the parse decides whether it carried
#: sanitizer diagnostics, a suite failure, or neither.
_CONTRACT = ExitContract(success=(0,), findings=tuple(range(1, 256)))

#: The runtime options each variant needs, appended to whatever the project
#: environment already set — the stable engine's ``_append_option`` contract.
_OPTIONS = {
    "sanitize": (
        ("ASAN_OPTIONS", "detect_leaks=1"),
        ("UBSAN_OPTIONS", "halt_on_error=1"),
    ),
    "thread-sanitize": (("TSAN_OPTIONS", "halt_on_error=0"),),
}

#: Lines that mean "a sanitizer report exists in this transcript" — the same
#: markers the stable engine tests before deciding a run had anything to say.
_MARKERS = (_ERROR_RE, _TSAN_WARNING_RE, _SUMMARY_RE, _RUNTIME_RE)


def _env(variant: str) -> tuple[tuple[str, str], ...]:
    """The variant's options appended to the inherited ones, not replacing."""

    return tuple(
        (key, ":".join(part for part in (os.environ.get(key, ""), option) if part))
        for key, option in _OPTIONS[variant]
    )


class SanitizeProvider:
    """One suite run under the sanitizer runtime for its variant."""

    def __init__(self, variant: str, *, project_root: Path) -> None:
        if variant not in _OPTIONS:
            raise ValueError(f"unknown sanitizer variant {variant!r}")
        self.name = variant  # "sanitize" | "thread-sanitize"
        self._variant = variant
        self._root = project_root

    def plan(
        self,
        *,
        argv: tuple[str, ...],
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=argv,
            cwd=cwd,
            env_overlay=_env(self._variant),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            input_refs=input_refs,
            timeout_seconds=1800,
            # A suite's verdict depends on binaries nothing enumerates — a
            # cached verdict could describe stale instrumentation.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        # The suite's protocol is stdout; the sanitizer's transcript is
        # stderr — read stderr first, as the stable engine does.
        transcript = f"{outcome.stderr}\n{outcome.stdout}"
        limitations = (
            ("capture truncated — diagnostics past the limit were never read",)
            if outcome.truncated
            else ()
        )
        if any(marker.match(line) for line in transcript.splitlines() for marker in _MARKERS):
            try:
                diagnostics = parse_sanitizer_diagnostics(transcript, self._root)
            except SanitizerDiagnosticError as error:
                return ParsedOutput(
                    failed_to_parse=f"sanitizer report could not be normalized: {error}"
                )
            if not diagnostics:
                return ParsedOutput(
                    failed_to_parse=(
                        "transcript had a sanitizer report marker but no complete diagnostic"
                    )
                )
            return ParsedOutput(
                findings=tuple(
                    _diagnostic_finding(item, outcome.spec, self.name, self._anchor)
                    for item in diagnostics
                ),
                measurements=(
                    Measurement(
                        name=f"{self._variant}.diagnostics",
                        value=float(len(diagnostics)),
                        unit="diagnostics",
                    ),
                ),
                limitations=limitations,
            )
        if outcome.exit_code != 0:
            return ParsedOutput(
                findings=(_suite_failure_finding(outcome, self.name, self._anchor),),
                limitations=limitations,
            )
        return ParsedOutput(
            measurements=(
                Measurement(
                    name=f"{self._variant}.diagnostics",
                    value=0.0,
                    unit="diagnostics",
                ),
            ),
            limitations=limitations,
        )

    def _anchor(self, spec: ExecTaskSpec) -> str:
        """A project-relative path a location-less finding can point at.

        The run's own directory is the honest anchor; a workspace-root
        component has none, so the suite binary or build directory named in
        the argv is the next real path.
        """

        for candidate in (spec.cwd, *(Path(arg) for arg in spec.argv)):
            try:
                relative = candidate.relative_to(self._root).as_posix()
            except (OSError, ValueError):
                continue
            if relative != ".":
                return relative
        return Path(spec.argv[0]).name


def _span(location: SourceLocation) -> SourceSpan:
    return SourceSpan(
        path=location.path,
        start_line=location.start_line,
        end_line=location.end_line,
        start_column=location.start_column,
        end_column=location.end_column,
        label=location.label,
    )


def _diagnostic_finding(
    item: SanitizerDiagnostic,
    spec: ExecTaskSpec,
    provider: str,
    anchor: Callable[[ExecTaskSpec], str],
) -> Finding:
    primary = item.primary_location
    limitations: list[str] = []
    if primary is not None and primary.path == "[external]":
        limitations.append("primary location is outside the project and was redacted")
        primary = None
    if primary is None and item.primary_location is None:
        limitations.append("the diagnostic named no project location")
    return Finding(
        fingerprint=_fingerprint(spec.name, item.rule_id, item.message),
        rule_id=item.rule_id,
        message=item.message,
        severity="high",
        confidence="high",
        primary_location=(_span(primary) if primary is not None else SourceSpan(anchor(spec), 1)),
        native_rule_id=item.defect,
        related_locations=tuple(
            _span(loc) for loc in item.related_locations if loc.path != "[external]"
        ),
        provider=provider,
        task_id=spec.name,
        evidence=EvidenceLevel.MEASURED,
        limitations=tuple(limitations),
    )


def _suite_failure_finding(
    outcome: TaskOutcome, provider: str, anchor: Callable[[ExecTaskSpec], str]
) -> Finding:
    return Finding(
        fingerprint=_fingerprint(outcome.spec.name, "suite", f"exit-{outcome.exit_code}"),
        rule_id="ici.sanitize.suite-failure",
        message=(
            f"suite exited {outcome.exit_code} with no sanitizer diagnostic "
            "in its output — the run failed without the sanitizer reporting "
            "a defect"
        ),
        severity="high",
        confidence="high",
        primary_location=SourceSpan(anchor(outcome.spec), 1),
        provider=provider,
        task_id=outcome.spec.name,
        evidence=EvidenceLevel.MEASURED,
    )


def _fingerprint(task_id: str, rule: str, detail: str) -> str:
    digest = hashlib.sha256("\x00".join((task_id, rule, detail)).encode()).hexdigest()
    return f"sha256:{digest}"
