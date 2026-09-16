"""Declared-runtime compatibility evidence — the dynamic half of #220 item 5.

``python.compat-runtime`` exists so the syntax scan's *estimate* and a real
interpreter's *evidence* stay separate. Two tasks run under the interpreter
the component declared (or its ``.venv``) — never ici's own:

- ``-VV``: the interpreter's version is measured and compared with the
  ``requires-python`` the plan resolved — an interpreter outside the
  declared range is a finding, not a footnote. The specifier rides in the
  task's environment (``ICI_REQUIRES_PYTHON``) so the verdict is judged
  against what the plan saw, not against whatever the file says later.
- ``compileall``: the component's sources compiled by that interpreter —
  what the declared runtime accepts, which no AST feature table can prove.

The stable engine's optional import smoke is not migrated here: importing
project code has arbitrary side effects and stable already gated it behind
explicit opt-in — the disposition records that boundary.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines._python_compatibility import (
    PythonMetadataError,
    parse_runtime_version,
    requires_python_allows,
)
from ici.execution.process import ExitContract, TaskOutcome
from ici.execution.process import TaskSpec as ExecTaskSpec

__all__ = ["CompileallProvider", "PythonVersionProvider"]

#: ``-VV`` always exits zero when the interpreter runs at all; a non-zero
#: exit means the interpreter itself is broken, which is still an answer
#: the parse reads — never a silent skip.
_CONTRACT = ExitContract(success=(0,), findings=tuple(range(1, 256)))

#: ``compileall -q`` reports each failed file as ``*** Error compiling
#: '<path>'...`` followed by the syntax error's own ``File``, ``line`` and
#: message lines.
_COMPILE_ERROR_RE = re.compile(r"Error compiling '(?P<path>[^']+)'")
_COMPILE_LOC_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)')
_COMPILE_MSG_RE = re.compile(r"^(?P<kind>\w+Error): (?P<msg>.+)$", re.MULTILINE)


class PythonVersionProvider:
    """``python -VV`` — the declared runtime's measured version."""

    name = "python-compat-version"

    def plan(
        self,
        interpreter: str,
        *,
        cwd: str,
        task_id: str,
        requires_python: str = "",
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(interpreter, "-VV"),
            cwd=cwd,
            env_overlay=(("ICI_REQUIRES_PYTHON", requires_python),),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=30,
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        if outcome.exit_code != 0:
            return ParsedOutput(
                failed_to_parse=f"the declared interpreter exited {outcome.exit_code} "
                "on -VV — it cannot answer"
            )
        version = parse_runtime_version(f"{outcome.stdout}\n{outcome.stderr}")
        if version is None:
            return ParsedOutput(failed_to_parse="interpreter version output was unparseable")
        requires_python = outcome.spec.environment.get("ICI_REQUIRES_PYTHON", "")
        limitations = [f"measured runtime: Python {version}"]
        findings: tuple[Finding, ...] = ()
        if not requires_python:
            limitations.append(
                "no requires-python floor is declared — the measured runtime "
                "was not judged against a floor"
            )
        else:
            try:
                allowed = requires_python_allows(requires_python, version)
            except PythonMetadataError as error:
                return ParsedOutput(failed_to_parse=str(error))
            if not allowed:
                findings = (
                    Finding(
                        fingerprint=_fingerprint(outcome.spec.name, "version", str(version)),
                        rule_id="python.compat.runtime-version",
                        message=(
                            f"declared interpreter reports Python {version}, outside "
                            f"requires-python {requires_python!r}"
                        ),
                        severity="high",
                        confidence="high",
                        category="compatibility",
                        primary_location=SourceSpan("pyproject.toml", 1),
                        provider=self.name,
                        task_id=outcome.spec.name,
                        evidence=EvidenceLevel.MEASURED,
                    ),
                )
            else:
                limitations.append(f"runtime satisfies requires-python {requires_python!r}")
        return ParsedOutput(findings=findings, limitations=tuple(limitations))


class CompileallProvider:
    """``python -m compileall`` — the declared runtime compiling the scope."""

    name = "python-compat-compileall"

    def plan(
        self,
        interpreter: str,
        *,
        files: tuple[str, ...],
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
        pycache_prefix: str,
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(interpreter, "-B", "-m", "compileall", "-q", "-f", *files),
            cwd=cwd,
            # Bytecode goes under .ici, not the project tree — a compat
            # check must not leave __pycache__ behind in the scope it read.
            env_overlay=(
                ("PYTHONPYCACHEPREFIX", pycache_prefix),
                ("PYTHONHASHSEED", "0"),
            ),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            input_refs=files,
            timeout_seconds=120,
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        if outcome.exit_code == 0:
            return ParsedOutput(
                measurements=(
                    Measurement(
                        name="compileall.checked",
                        value=float(len(outcome.spec.argv) - 6),
                        unit="files",
                    ),
                ),
            )
        transcript = f"{outcome.stderr}\n{outcome.stdout}"
        findings = _compile_findings(transcript, outcome.spec, self.name)
        if not findings:
            return ParsedOutput(
                failed_to_parse=(
                    f"compileall exited {outcome.exit_code} but named no failed "
                    "file — the failure cannot be attributed"
                )
            )
        return ParsedOutput(findings=findings)


def _compile_findings(transcript: str, spec: ExecTaskSpec, provider: str) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    lines = transcript.splitlines()
    for index, line in enumerate(lines):
        error = _COMPILE_ERROR_RE.search(line)
        if error is None:
            continue
        path = error.group("path")
        try:
            path = Path(path).relative_to(spec.cwd).as_posix()
        except (OSError, ValueError):
            path = Path(path).name
        start_line = 1
        message = "the declared interpreter could not compile this file"
        for follow in lines[index + 1 : index + 6]:
            located = _COMPILE_LOC_RE.search(follow)
            if located is not None:
                start_line = int(located.group("line"))
            matched = _COMPILE_MSG_RE.match(follow.strip())
            if matched is not None:
                message = f"{matched.group('kind')}: {matched.group('msg')}"
                break
        findings.append(
            Finding(
                fingerprint=_fingerprint(spec.name, path, message),
                rule_id="python.compat.compile-failure",
                message=message,
                severity="high",
                confidence="high",
                category="compatibility",
                primary_location=SourceSpan(path, start_line),
                provider=provider,
                task_id=spec.name,
                evidence=EvidenceLevel.MEASURED,
            )
        )
    return tuple(findings)


def _fingerprint(task_id: str, rule: str, detail: str) -> str:
    digest = hashlib.sha256("\x00".join((task_id, rule, detail)).encode()).hexdigest()
    return f"sha256:{digest}"
