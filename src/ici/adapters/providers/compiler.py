"""Re-running the captured compiler as an analyzer, not as a builder.

#214 item 2 is the constraint this module exists for: the analyzer's
invocation is *derived from* the project's compile invocation, not replaced
by one of ours. The database's argv is taken apart into a small verified
drop-list — flags that produce artifacts or drive dependency/link machinery a
``-fsyntax-only`` read must not re-run — and everything else is kept verbatim.
An option this module does not recognize is kept, never dropped: dropping an
option that mattered while still claiming an accurate analysis is the failure
the issue names.

Two consequences of that honesty:

- A translation unit whose compiler is not a gcc/clang-family driver is
  *unsupported*, not silently skipped — the caller marks the planned check
  blocked and the run incomplete rather than passing on zero diagnostics.
- The task is never cached. What a TU's diagnostics read includes every
  header the preprocessor pulls in transitively, and no dep-file evidence is
  captured, so no honest identity exists — the cache records "not cacheable"
  instead of inventing one.

The project's compiler is the project's: the executable is the one the
database recorded (after ``ccache``/``distcc``-style launchers), resolved
through PATH like any other tool. The analyzer never touches the project's
own Qt/GCC installation — it only re-reads what the build already does.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.core.models import EngineStatus
from ici.domain.enums import TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.engines._cpp_diagnostics import parse_compiler_diagnostics
from ici.execution.process import ExitContract, TaskOutcome

__all__ = [
    "COMPILER_CONTRACT",
    "CompilerDiagnosticsProvider",
    "TransformedInvocation",
    "compiler_family",
    "transform_argv",
]

PROVIDER_NAME = "compiler"

#: gcc/clang exit codes: 0 is clean, 1 is diagnostics emitted — both are
#: answers. Anything else is a failure to run, which the executor reports.
COMPILER_CONTRACT = ExitContract(success=(0,), findings=(1,))

#: Basenames that take gcc-compatible diagnostic and flag spellings. A
#: compiler outside this set — cl.exe, icx, a cross-driver — does not get a
#: guessed invocation.
_FAMILY_RE = re.compile(r"^(?:c\+\+|g\+\+|gcc|clang\+\+|clang|cc|em\+\+|emcc)(?:-\d+.*)?$")

#: Flags dropped from the captured argv, each with the reason it cannot stay.
#: Anything not named here is kept verbatim — including options this module
#: has never heard of.
_DROP_WITH_OPERAND = {
    "-o": "output artifact — a syntax check writes nothing",
    "-MF": "dependency output file — nothing may be generated",
    "-MT": "dependency target — belongs to -M machinery",
    "-MQ": "dependency target — belongs to -M machinery",
    "-MJ": "JSON job record — a compilation-database writer, not a check",
    "-include-pch": "precompiled-header input — produced by a build step",
}
_DROP_EXACT = {
    "-c": "compile-only driver step — the analyzer checks syntax",
    "-S": "emit-assembly step — an artifact, not an analysis",
    "-E": "preprocess-only step — produces a different artifact",
    "-M": "dependency generation — writes make rules",
    "-MD": "dependency generation — writes make rules",
    "-MM": "dependency generation — writes make rules",
    "-MMD": "dependency generation — writes make rules",
    "-pipe": "inter-stage piping — irrelevant without codegen",
    "-shared": "link-mode flag — no link happens",
    "-pie": "link-mode flag — no link happens",
    "-pg": "profiling instrumentation — an artifact flag",
    "--coverage": "coverage instrumentation — writes .gcno",
    "-ftest-coverage": "coverage instrumentation — writes .gcno",
}
_DROP_PREFIXES = (
    ("-o", "output artifact — a syntax check writes nothing"),
    ("-MF", "dependency output file — nothing may be generated"),
    ("-MT", "dependency target — belongs to -M machinery"),
    ("-MQ", "dependency target — belongs to -M machinery"),
    ("-MJ", "JSON job record — a compilation-database writer, not a check"),
    ("-flto", "link-time optimization — a link-step flag"),
    ("-save-temps", "intermediate-artifact retention — writes files"),
    ("-fdump", "compiler dump — writes files"),
    ("-fprofile-", "profile instrumentation — writes .gcda/.gcno"),
    ("-Wa,", "assembler passthrough — no assembly is emitted"),
    ("-Wl,", "linker passthrough — no link happens"),
    ("-Wp,", "preprocessor passthrough — may name output files"),
    ("-l", "link input — no link happens"),
    ("-L", "link search path — no link happens"),
)


@dataclass(frozen=True)
class TransformedInvocation:
    """The analyzer's argv, and what the transform removed and why."""

    argv: tuple[str, ...]
    #: ``(flag, reason)`` pairs — the drop-list applied to this invocation,
    #: reported so a stripped flag is a record, not a silence.
    dropped: tuple[tuple[str, str], ...] = ()


def compiler_family(executable: str) -> str:
    """Which diagnostic dialect the driver speaks, or ``""`` when unknown."""

    name = PurePosixPath(executable).name.casefold()
    if _FAMILY_RE.match(name):
        return "gcc" if "clang" not in name else "clang"
    return ""


def transform_argv(argv: tuple[str, ...], source_token: str) -> TransformedInvocation | None:
    """Turn a build's compile invocation into a syntax-check invocation.

    ``argv`` is the database's own command — launchers already stripped by the
    TU model. ``source_token`` is the entry in ``argv`` that names the
    translation unit, spelled exactly as the database spelled it; it is
    re-positioned last so flag ordering around it stays the build's.

    ``None`` is returned for a non-gcc/clang driver: no guessed invocation,
    no silent skip, the caller reports it as unsupported.
    """

    if not argv or not compiler_family(argv[0]):
        return None
    kept: list[str] = [argv[0], "-fsyntax-only"]
    dropped: list[tuple[str, str]] = []
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in _DROP_WITH_OPERAND:
            dropped.append((arg, _DROP_WITH_OPERAND[arg]))
            skip_next = True
            continue
        if arg in _DROP_EXACT:
            dropped.append((arg, _DROP_EXACT[arg]))
            continue
        prefixed = next(
            (reason for prefix, reason in _DROP_PREFIXES if arg.startswith(prefix)),
            None,
        )
        if prefixed is not None:
            dropped.append((arg, prefixed))
            continue
        if arg == source_token:
            continue
        kept.append(arg)
    kept.append(source_token)
    return TransformedInvocation(argv=tuple(kept), dropped=tuple(dropped))


class CompilerDiagnosticsProvider:
    """One ``-fsyntax-only`` re-run per translation unit.

    Planning is per TU so each task's argv is *that unit's* real invocation —
    a merged argv would be a different compile than the build performed, and
    identical TUs shared across components deduplicate through ``share_key``
    the same way any identical work does.
    """

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        argv: tuple[str, ...],
        *,
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(executable, *argv[1:]),
            cwd=cwd,
            input_refs=input_refs,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=300,
            tool_digest=tool_digest,
            # No dep-file evidence is captured, so no honest cache key exists —
            # header edits would be invisible to one. The scheduler records
            # "not cacheable" rather than pretending.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=COMPILER_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Normalize the compiler's diagnostics through the shared parser."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        root = outcome.spec.cwd or Path.cwd()
        result = parse_compiler_diagnostics(root, root, outcome.stdout, outcome.stderr)
        if result.error:
            return ParsedOutput(failed_to_parse=result.error)
        findings: list[Finding] = []
        for diagnostic in result.diagnostics:
            target = diagnostic.target
            filename = _relative(target.file_path, root)
            if not filename or Path(filename).is_absolute():
                continue
            message = target.message or "(no message)"
            findings.append(
                Finding(
                    fingerprint=_fingerprint(
                        diagnostic.family,
                        diagnostic.tool_rule_id,
                        filename,
                        target.start_line,
                        target.start_column,
                        message,
                    ),
                    rule_id=f"compiler.{diagnostic.family}",
                    message=message,
                    severity="high" if target.status is EngineStatus.FAIL else "medium",
                    confidence="high",
                    primary_location=SourceSpan(
                        path=filename,
                        start_line=target.start_line,
                        start_column=target.start_column,
                    ),
                    provider=self.name,
                    native_rule_id=diagnostic.tool_rule_id,
                    task_id=outcome.spec.name,
                )
            )
        return ParsedOutput(findings=tuple(findings))


def _relative(path: str, root: Path) -> str:
    """A finding path made workspace-relative when it points inside it.

    Relative spellings are anchored at the task's working directory — not at
    wherever this process happens to be running from.
    """

    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        return str(candidate.resolve().relative_to(root))
    except (OSError, ValueError):
        return path


def _fingerprint(
    family: str,
    rule: str,
    filename: str,
    line: int,
    column: int | None,
    message: str,
) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            (PROVIDER_NAME, family, rule, filename, str(line), str(column), message)
        ).encode()
    ).hexdigest()
    return f"sha256:{digest}"
