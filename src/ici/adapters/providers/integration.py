"""The declared process-contract runner — the dynamic half of #220 item 6.

One task per declared ``[[components.integrations]]`` case: the argv the
plan resolved, the assertions the case declared, and the bounds it set.
Everything the runner needs to *judge* the run rides in the task's
environment, because a provider instance is shared across every task of its
name and the outcome is the only thing ``parse`` is handed.

Reading the outcome keeps the distinctions the issue names:

- a case that ran to completion and failed an assertion produces
  *findings* — measured contract violations;
- a case that timed out, was killed, cancelled or never started produces
  no findings at all — it is an execution/environment failure, reported as
  an incomplete check rather than as evidence the contract held or broke;
- a declared service/network requirement (``requires``) is echoed into the
  observation's limitations so the evidence states what it depended on —
  ici does not probe services it cannot know how to check.

Output-artifact assertions verify the file exists, is a regular file,
meets ``min_size`` and was written during the run — a ``not_before``
timestamp the plan stamped makes a stale leftover fail rather than pass.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["IntegrationCaseProvider"]

_PROVIDER = "integration"


class IntegrationCaseProvider:
    """Run one declared case and judge it against its declared contract."""

    name = _PROVIDER

    def plan(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        task_id: str,
        case_name: str,
        case_source: str,
        case_required: bool,
        expected_exit: int,
        stdout_contains: tuple[str, ...],
        stderr_contains: tuple[str, ...],
        stdout_not_contains: tuple[str, ...],
        stderr_not_contains: tuple[str, ...],
        outputs: tuple[tuple[str, int], ...],
        env: tuple[tuple[str, str], ...],
        requires: tuple[str, ...],
        timeout_seconds: float,
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        """The resolved case as one process task — never cached.

        A case's verdict depends on services, artifacts and state that
        change between runs; caching it would report a contract held on the
        strength of an old world (#220: 동적 검사의 cross-run cache 금지).
        """

        contract_env = (
            ("ICI_CASE", case_name),
            ("ICI_CASE_SOURCE", case_source),
            ("ICI_CASE_REQUIRED", "1" if case_required else "0"),
            ("ICI_EXPECTED_EXIT", str(expected_exit)),
            ("ICI_STDOUT_MUST", json.dumps(list(stdout_contains))),
            ("ICI_STDERR_MUST", json.dumps(list(stderr_contains))),
            ("ICI_STDOUT_NEVER", json.dumps(list(stdout_not_contains))),
            ("ICI_STDERR_NEVER", json.dumps(list(stderr_not_contains))),
            (
                "ICI_OUTPUT_ASSERTS",
                json.dumps([{"path": path, "min_size": size} for path, size in outputs]),
            ),
            ("ICI_OUT_NOT_BEFORE", repr(time.time())),
            ("ICI_REQUIRES", json.dumps(list(requires))),
        )
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.TEST,
            provider=self.name,
            argv=tuple(argv),
            cwd=cwd,
            env_overlay=(*contract_env, *env),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            requires=requires,
            output_specs=tuple(path for path, _ in outputs),
            timeout_seconds=timeout_seconds,
            cacheable=False,
        )
        # Every finished run is an answer: the case's exit code is data, so
        # any code that is not the expected one is a finding to evaluate,
        # never a process failure.
        contract = ExitContract(
            success=(expected_exit,),
            findings=tuple(code for code in range(256) if code != expected_exit),
        )
        return ProviderPlan(task=task, contract=contract)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        env = outcome.spec.environment
        case = env.get("ICI_CASE", outcome.spec.name)
        required = env.get("ICI_CASE_REQUIRED") == "1"
        expected = int(env.get("ICI_EXPECTED_EXIT", "0"))

        checks: list[tuple[str, bool, str]] = []
        checks.append(
            (
                "exit",
                outcome.exit_code == expected,
                f"expected exit {expected}, got {outcome.exit_code}",
            )
        )
        checks.extend(_stream_checks("stdout", outcome.stdout, env, want=True))
        checks.extend(_stream_checks("stderr", outcome.stderr, env, want=True))
        checks.extend(_stream_checks("stdout", outcome.stdout, env, want=False))
        checks.extend(_stream_checks("stderr", outcome.stderr, env, want=False))
        checks.extend(_output_checks(env))

        failed = [(aspect, detail) for aspect, passed, detail in checks if not passed]
        severity = "high" if required else "medium"
        findings = tuple(
            Finding(
                fingerprint=_fingerprint(outcome.spec.name, case, aspect, detail),
                rule_id=f"integration.{aspect.replace('_', '-')}",
                message=f"integration case {case!r}: {detail}",
                severity=severity,
                confidence="high",
                category="correctness",
                primary_location=SourceSpan(
                    _source_path(env.get("ICI_CASE_SOURCE", "")), 1, label=case
                ),
                provider=_PROVIDER,
                task_id=outcome.spec.name,
                evidence=EvidenceLevel.MEASURED,
            )
            for aspect, detail in failed
        )
        limitations: list[str] = []
        requires = _strings(env.get("ICI_REQUIRES"))
        if requires:
            limitations.append(
                f"case {case!r} declares external requirements: {', '.join(requires)}"
            )
        return ParsedOutput(
            findings=findings,
            measurements=(
                Measurement(
                    name="integration.assertions",
                    value=float(len(checks) - len(failed)),
                    unit="assertions",
                    numerator=len(checks) - len(failed),
                    denominator=len(checks) or None,
                ),
            ),
            limitations=tuple(limitations),
        )


def _stream_checks(
    stream: str, text: str, env: Mapping[str, str], *, want: bool
) -> list[tuple[str, bool, str]]:
    key = f"ICI_{stream.upper()}_{'MUST' if want else 'NEVER'}"
    verb = "missing" if want else "forbidden"
    return [
        (stream, (needle in text) is want, f"{verb} {needle!r} in {stream}")
        for needle in _strings(env.get(key))
    ]


def _output_checks(env: Mapping[str, str]) -> list[tuple[str, bool, str]]:
    """Declared output files: present, regular, sized and fresh."""

    try:
        not_before = float(env.get("ICI_OUT_NOT_BEFORE", "0"))
    except ValueError:
        not_before = 0.0
    checks: list[tuple[str, bool, str]] = []
    for item in _json_list(env.get("ICI_OUTPUT_ASSERTS")):
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path", "")))
        minimum = item.get("min_size", 1)
        minimum = minimum if isinstance(minimum, int) else 1
        try:
            stat = path.stat()
        except OSError:
            checks.append(("output", False, f"{path} was not produced"))
            continue
        if not path.is_file() or path.is_symlink():
            checks.append(("output", False, f"{path} is not a regular file"))
            continue
        if stat.st_size < minimum:
            checks.append(
                ("output", False, f"{path} is {stat.st_size}B, expected at least {minimum}B")
            )
            continue
        if stat.st_mtime < not_before:
            checks.append(
                ("output", False, f"{path} predates this run — a stale file is not proof")
            )
            continue
        checks.append(("output", True, ""))
    return checks


def _strings(raw: str | None) -> tuple[str, ...]:
    return tuple(str(item) for item in _json_list(raw))


def _json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _source_path(source: str) -> str:
    """The declaring file as a finding location — relative or ici.toml."""

    if not source:
        return "ici.toml"
    path = Path(source)
    if path.is_absolute() or ".." in path.parts:
        return "ici.toml"
    return source


def _fingerprint(task_id: str, case: str, aspect: str, detail: str) -> str:
    digest = hashlib.sha256("\x00".join((task_id, case, aspect, detail)).encode()).hexdigest()
    return f"sha256:{digest}"
