"""ELF/ABI evidence through readelf — the artifact contract's consumer.

``cpp.binary-compat`` (#220) inspects the binaries the linked builds'
``artifacts`` globs name — never a discovered or executed binary. Each ELF
artifact gets one bounded ``readelf`` task; the output is parsed by the
same ``ici.engines._elf`` reader the stable ``binary_compat`` engine uses,
and judged by the same ``_abi_violations`` policy function.

The policy applied here is the stable default set — absolute and
build-path loader entries are forbidden. Deployment floors (expected
class/machine, max glibc/glibcxx/cxxabi, allowed or forbidden ``NEEDED``)
are *not yet declarable* in ``ici.toml``'s check tables — when none are
declared the measured ABI facts are reported as a limitation per artifact
rather than silently judged against nothing (#220 item 5: 검사한 범위와
미확인 호환성을 구분한다).
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines._elf import ElfParseError, maximum_version, parse_readelf
from ici.engines.binary_compat import BinaryCompatibilityEngine
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["BinaryCompatProvider"]

#: readelf exits non-zero on a malformed or unreadable object — a finished
#: run is always an answer for the parse to read.
_CONTRACT = ExitContract(success=(0,), findings=tuple(range(1, 256)))

#: The stable engine's default policy — the parts that need no declaration.
_POLICY = {
    "forbid_absolute_rpath": True,
    "forbid_build_paths": True,
}


class BinaryCompatProvider:
    """One ``readelf`` read per ELF artifact the contract names."""

    name = "binary-compat"

    def __init__(self, *, project_root: Path, build_roots: tuple[Path, ...] = ()) -> None:
        # ``build_roots`` serve the parse-side policy (rpath entries must not
        # point inside a build tree); a plan-only instance needs none.
        self._root = project_root
        self._build_roots = build_roots

    def plan(
        self,
        executable: str,
        *,
        binary: Path,
        task_id: str,
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(
                executable,
                "--file-header",
                "--sections",
                "--dynamic",
                "--version-info",
                "--wide",
                str(binary),
            ),
            cwd=str(self._root),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            input_refs=(str(binary),),
            timeout_seconds=60,
            # The binary is not a declared input — a cached verdict could
            # describe a stale artifact.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        binary = Path(outcome.spec.argv[-1])
        try:
            relative = binary.relative_to(self._root).as_posix()
        except (OSError, ValueError):
            relative = binary.name
        if outcome.exit_code != 0:
            detail = outcome.stderr.strip().splitlines()
            return ParsedOutput(
                failed_to_parse=(
                    f"readelf exited {outcome.exit_code} for {relative}"
                    + (f": {detail[0][:200]}" if detail else "")
                )
            )
        try:
            facts = parse_readelf(outcome.stdout)
        except ElfParseError as error:
            return ParsedOutput(
                failed_to_parse=f"readelf output for {relative} was incomplete: {error}"
            )
        findings = tuple(
            _finding(item, relative, outcome.spec.name)
            for item in BinaryCompatibilityEngine._abi_violations(
                relative, facts, _POLICY, self._build_roots
            )
        )
        floors = ", ".join(
            f"{namespace}<={maximum_version(versions)}"
            for namespace, versions in (
                ("glibc", facts.glibc),
                ("glibcxx", facts.glibcxx),
                ("cxxabi", facts.cxxabi),
            )
            if maximum_version(versions)
        )
        return ParsedOutput(
            findings=findings,
            measurements=(Measurement(name="binary.checked", value=1.0, unit="artifacts"),),
            limitations=(
                f"{relative}: ELF {facts.elf_class} {facts.machine}"
                + (f"; measured {floors}" if floors else "")
                + " — no ABI floors are declared, so the measured ranges were "
                "not judged against a deployment contract",
            ),
        )


def _finding(item, artifact: str, task_id: str) -> Finding:
    """A stable-path ``ici.binary.*`` finding, normalized to the domain model."""

    location = item.primary_location
    return Finding(
        fingerprint=item.fingerprint,
        rule_id=item.rule_id,
        message=item.message,
        severity="high",
        confidence="high",
        primary_location=(
            SourceSpan(location.path, location.start_line)
            if location is not None
            else SourceSpan(artifact, 1)
        ),
        native_rule_id=getattr(item, "tool_rule_id", "") or item.rule_id,
        provider="binary-compat",
        task_id=task_id,
        evidence=EvidenceLevel.MEASURED,
    )
