"""Python runtime compatibility — compileall across configured target interpreters."""

import sys
import time
from pathlib import Path

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.project import get_all_python_sources, get_source_dirs
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine


def _resolve_targets(cfg) -> list[str]:
    """Configured targets first, then the running interpreter as fallback."""
    raw = cfg.get("targets") or []
    targets = [str(item) for item in raw if str(item).strip()]
    if targets:
        return targets
    return [sys.executable]


def _first_error_location(stderr: str) -> tuple[str, int] | None:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("File ") or ("SyntaxError" in line and ":" in line):
            return stripped[:200], 1
    return None


def _compile_target(
    rel_name: str,
    interpreter: str,
    compile_dirs: list[str],
    cwd,
) -> tuple[InspectionTarget, ToolEvidence, bool]:
    """Compile one target; returns (target, evidence, is_tool_error)."""
    argv = [interpreter, "-m", "compileall", "-q", *compile_dirs]
    result = run_process(argv, cwd=cwd)
    evidence = _to_evidence(interpreter, result)
    if result.returncode == 0 and not result.timed_out and not result.truncated:
        return (
            InspectionTarget(
                file_path="",
                start_line=1,
                target_name=f"PyCompat:{rel_name}",
                status=EngineStatus.PASS,
                message=f"{interpreter} compiled {len(compile_dirs)} source dir(s)",
            ),
            evidence,
            False,
        )
    detail = _first_error_location(result.stderr) or _first_error_location(result.stdout)
    message = f"{interpreter} compileall failed ({result.returncode})"
    snippet = ""
    tool_error = False
    if result.timed_out:
        message = f"{interpreter} compileall timed out"
        tool_error = True
    elif result.truncated:
        message = f"{interpreter} compileall output truncated"
        tool_error = True
    elif result.returncode < 0:
        message = f"{interpreter} compileall terminated abnormally"
        tool_error = True
    elif detail is None and "Failed to execute" in result.stderr:
        message = f"Interpreter could not be executed: {interpreter}"
        tool_error = True
    elif detail:
        message, _ = detail
        snippet = detail[0]
    return (
        InspectionTarget(
            file_path="",
            start_line=1,
            target_name=f"PyCompat:{rel_name}",
            status=EngineStatus.FAIL,
            message=message,
            snippet=snippet,
        ),
        evidence,
        tool_error,
    )


def _to_evidence(interpreter: str, result: ProcessResult) -> ToolEvidence:
    return ToolEvidence(
        name="python -m compileall",
        path=interpreter,
        argv=[interpreter, "-m", "compileall"],
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=result.stderr.strip()[:500],
    )


class PythonCompatEngine(BaseEngine):
    """Verifies every configured target Python can byte-compile the sources."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("python_compat")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))

        py_sources = get_all_python_sources(self.project_root, self.config)
        if not py_sources:
            return self.create_result(
                name="python_compat",
                status=EngineStatus.PASS,
                summary="No Python sources — compatibility check not applicable",
                duration=time.time() - t0,
                targets=[],
                required=required,
                evidence=EvidenceState.MEASURED,
            )

        source_dirs = [str(d) for d in get_source_dirs(self.project_root, self.config)]
        if not source_dirs:
            source_dirs = [str(self.project_root)]

        targets: list[InspectionTarget] = []
        evidence: list[ToolEvidence] = []
        has_fail = False
        has_error = False

        for interpreter in _resolve_targets(cfg):
            label = _label_for(interpreter)
            target, tool_ev, is_tool_error = _compile_target(
                label, interpreter, source_dirs, self.project_root
            )
            targets.append(target)
            evidence.append(tool_ev)
            has_fail = has_fail or target.status == EngineStatus.FAIL
            has_error = has_error or is_tool_error

        status = EngineStatus.ERROR if has_error else self.evaluate_status(has_fail, False, mode)
        summary = _build_summary(targets)
        return self.create_result(
            name="python_compat",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=targets,
            required=required,
            evidence=EvidenceState.MEASURED,
            tool_evidence=evidence,
        )


def _label_for(interpreter: str) -> str:
    return Path(interpreter).name or interpreter


def _build_summary(targets: list[InspectionTarget]) -> str:
    failed = sum(1 for t in targets if t.status != EngineStatus.PASS)
    total = len(targets)
    if failed:
        return f"Python compat: {failed}/{total} target(s) failed to compile"
    return f"Python compat OK across {total} target(s)"
