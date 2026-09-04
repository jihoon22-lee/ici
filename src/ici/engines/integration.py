"""Shell-free Python/native integration contract engine."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from ici.core.context import ArtifactScope
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines._integration import (
    IntegrationCase,
    IntegrationConfigError,
    OutputArtifactAssertion,
    parse_integration_cases,
)
from ici.engines.base import BaseEngine

_PLACEHOLDER_RE = re.compile(r"^\{(python|artifact):([^{}]+)\}$")
_PYTHON_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _finding(case: IntegrationCase, message: str, rule: str) -> Finding:
    location = SourceLocation(path="ici.toml", start_line=1, label=case.name)
    severity = FindingSeverity.HIGH if case.required else FindingSeverity.MEDIUM
    rule_id = "ici.hybrid.process-contract"
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.CORRECTNESS,
        severity=severity,
        confidence=FindingConfidence.EXACT,
        fingerprint=finding_fingerprint(rule_id, location, symbol=case.name),
        primary_location=location,
        message=message,
        explanation="A declared cross-language process assertion was not satisfied.",
        remediation="Correct the producer/consumer contract or its declared expected result.",
        tool_rule_id=rule,
        tool_name="ici integration runner",
    )


class IntegrationEngine(BaseEngine):
    """Resolve typed placeholders from immutable inputs and run bounded cases."""

    CACHE_REUSE_SAFE = False
    CACHE_IMPLEMENTATION_MODULES = ("ici.engines._integration", "ici.engines.integration")

    def _artifacts(self) -> dict[str, Path]:
        if self.analysis_context is None:
            return {}
        result: dict[str, Path] = {}
        for manifest in self.analysis_context.manifests:
            manifest.validate()
            for record in manifest.artifacts:
                root = (
                    manifest.project_root
                    if record.scope is ArtifactScope.PROJECT
                    else manifest.shadow_root
                )
                if root is None:
                    raise IntegrationConfigError(f"artifact root is missing: {record.path}")
                value = root / record.path
                keys = {record.path, getattr(record, "artifact_id", "")}
                for key in keys - {""}:
                    if key in result and result[key] != value:
                        raise IntegrationConfigError(f"artifact placeholder is ambiguous: {key}")
                    result[key] = value
        return result

    def _python_targets(self, cfg: dict[str, Any]) -> dict[str, str]:
        raw = cfg.get("python_targets", {})
        result = {"current": str(Path(sys.executable).resolve())}
        if not isinstance(raw, dict) or len(raw) > 32:
            raise IntegrationConfigError(
                "engines.integration.python_targets must be a table with at most 32 entries"
            )
        for name, value in raw.items():
            if (
                not isinstance(name, str)
                or _PYTHON_TARGET_RE.fullmatch(name) is None
                or not isinstance(value, str)
                or not value
                or len(value) > 1024
                or any(ord(character) < 32 for character in value)
            ):
                raise IntegrationConfigError("integration Python targets must be named strings")
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as err:
                raise IntegrationConfigError(f"Python target is unavailable: {name}") from err
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                raise IntegrationConfigError(f"Python target is not executable: {name}")
            result[name] = str(resolved)
        return result

    @staticmethod
    def _resolve_argv(
        case: IntegrationCase,
        artifacts: dict[str, Path],
        python_targets: dict[str, str],
    ) -> list[str]:
        argv: list[str] = []
        for token in case.argv:
            match = _PLACEHOLDER_RE.fullmatch(token)
            if match is None:
                if "{" in token or "}" in token:
                    raise IntegrationConfigError(
                        f"case {case.name!r} uses a non-whole or unknown placeholder: {token!r}"
                    )
                argv.append(token)
                continue
            kind, identity = match.groups()
            values: dict[str, Any] = python_targets if kind == "python" else artifacts
            value = values.get(identity)
            if value is None:
                raise IntegrationConfigError(
                    f"case {case.name!r} references unknown {kind} placeholder {identity!r}"
                )
            argv.append(str(value))
        return argv

    def _output_path(self, assertion: OutputArtifactAssertion) -> Path:
        lexical = self.project_root / assertion.path
        try:
            resolved_parent = lexical.parent.resolve(strict=True)
            resolved_parent.relative_to(self.project_root)
        except (OSError, RuntimeError, ValueError) as err:
            raise IntegrationConfigError(
                f"output artifact parent is unavailable or outside project: {assertion.path}"
            ) from err
        if lexical.is_symlink():
            raise IntegrationConfigError(f"output artifact must not be a symlink: {assertion.path}")
        return lexical

    def _prepare_outputs(self, case: IntegrationCase) -> None:
        for assertion in case.output_artifacts:
            path = self._output_path(assertion)
            if not path.exists():
                continue
            if not path.is_file():
                raise IntegrationConfigError(
                    f"stale output artifact is not a regular file: {assertion.path}"
                )
            path.unlink()

    def _check_case(
        self,
        case: IntegrationCase,
        result: ProcessResult,
    ) -> tuple[list[str], dict[str, bool]]:
        failures: list[str] = []
        assertions: dict[str, bool] = {}
        checks = (
            ("exit_code", result.returncode == case.expected_exit),
            ("stdout_contains", all(value in result.stdout for value in case.stdout_contains)),
            ("stderr_contains", all(value in result.stderr for value in case.stderr_contains)),
            (
                "stdout_not_contains",
                all(value not in result.stdout for value in case.stdout_not_contains),
            ),
            (
                "stderr_not_contains",
                all(value not in result.stderr for value in case.stderr_not_contains),
            ),
        )
        for name, passed in checks:
            assertions[name] = passed
            if not passed:
                failures.append(name)
        outputs_ok = True
        for assertion in case.output_artifacts:
            path = self._output_path(assertion)
            try:
                passed = (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_size >= assertion.min_size
                )
            except OSError:
                passed = False
            outputs_ok = outputs_ok and passed
        assertions["output_artifacts"] = outputs_ok
        if not outputs_ok:
            failures.append("output_artifacts")
        return failures, assertions

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("integration")
        targets: list[InspectionTarget] = []
        findings: list[Finding] = []
        tool_evidence: list[ToolEvidence] = []
        case_payload: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            cases = parse_integration_cases(cfg)
            max_cases = cfg.get("max_cases", 32)
            if type(max_cases) is not int or not 1 <= max_cases <= 32:
                raise IntegrationConfigError(
                    "integration max_cases must be an integer from 1 to 32"
                )
            if len(cases) > max_cases:
                raise IntegrationConfigError(
                    f"integration case count exceeds configured maximum {max_cases}"
                )
            if not cases:
                if cfg.get("required", False):
                    raise IntegrationConfigError("required integration engine has no cases")
                return self.create_result(
                    name="integration",
                    status=EngineStatus.SKIP,
                    summary="Integration analysis not applicable: no cases configured",
                    duration=time.time() - started,
                    targets=[
                        InspectionTarget(
                            file_path="ici.toml",
                            start_line=1,
                            target_name="Integration:NotApplicable",
                            status=EngineStatus.SKIP,
                            message="No integration cases were configured",
                        )
                    ],
                    extra={"integration": {"state": "NOT_APPLICABLE", "cases": []}},
                    required=False,
                    evidence=EvidenceState.NOT_APPLICABLE,
                )
            artifacts = self._artifacts()
            python_targets = self._python_targets(cfg)
            max_output = cfg.get("max_output_bytes", 65536)
            if type(max_output) is not int or not 1024 <= max_output <= 8 * 1024 * 1024:
                raise IntegrationConfigError(
                    "integration max_output_bytes must be an integer from 1024 to 8388608"
                )
            planned = [
                (case, self._resolve_argv(case, artifacts, python_targets)) for case in cases
            ]
            for case, argv in planned:
                self._prepare_outputs(case)
                env = {name: os.environ[name] for name in case.inherit_env if name in os.environ}
                env.update(dict(case.env))
                result = run_process(
                    argv,
                    cwd=self.project_root,
                    env=env,
                    timeout=case.timeout_seconds,
                    max_output_chars=max_output,
                    replace_env=True,
                )
                tool_evidence.append(
                    ToolEvidence(
                        name=f"integration case: {case.name}",
                        path=argv[0],
                        argv=argv,
                        returncode=result.returncode,
                        timed_out=result.timed_out,
                        truncated=result.truncated,
                        error=result.stderr[:512]
                        if result.returncode != case.expected_exit
                        else "",
                    )
                )
                if result.timed_out or result.truncated or result.returncode < 0:
                    raise IntegrationConfigError(
                        f"case {case.name!r} did not produce complete process evidence"
                    )
                failures, assertions = self._check_case(case, result)
                if failures:
                    findings.append(
                        _finding(
                            case,
                            f"Integration case {case.name!r} failed assertions: {', '.join(failures)}",
                            "process." + failures[0].replace("_", "-"),
                        )
                    )
                targets.append(
                    InspectionTarget(
                        file_path="ici.toml",
                        start_line=1,
                        target_name=f"Integration:{case.name}",
                        status=(
                            EngineStatus.FAIL
                            if failures and case.required
                            else EngineStatus.WARN
                            if failures
                            else EngineStatus.PASS
                        ),
                        message=(
                            f"Failed assertions: {', '.join(failures)}"
                            if failures
                            else "All process and output artifact assertions passed"
                        ),
                    )
                )
                case_payload.append(
                    {
                        "name": case.name,
                        "status": (
                            "FAIL" if failures and case.required else "WARN" if failures else "PASS"
                        ),
                        "returncode": result.returncode,
                        "duration_seconds": result.duration,
                        "stdout_truncated": result.truncated,
                        "stderr_truncated": result.truncated,
                        "assertions": assertions,
                    }
                )
        except (IntegrationConfigError, OSError, RuntimeError, ValueError) as err:
            errors.append(str(err))

        if errors:
            status = EngineStatus.ERROR
            state = EvidenceState.NOT_RUN
            summary = f"Integration analysis incomplete: {errors[0]}"
            targets.append(
                InspectionTarget(
                    file_path="ici.toml",
                    start_line=1,
                    target_name="Integration:ConfigurationError",
                    status=EngineStatus.ERROR,
                    message=errors[0],
                )
            )
        else:
            has_fail = any(item.severity is FindingSeverity.HIGH for item in findings)
            has_warn = any(item.severity is FindingSeverity.MEDIUM for item in findings)
            status = self.evaluate_status(has_fail, has_warn, cfg.get("mode", "pass_warn_fail"))
            state = EvidenceState.MEASURED
            summary = (
                f"Integration contracts: {len(case_payload)} run, {len(findings)} violation(s)"
            )
        engine_result = self.create_result(
            name="integration",
            status=status,
            summary=summary,
            duration=time.time() - started,
            targets=targets,
            extra={"integration": {"state": state.value, "cases": case_payload}},
            required=bool(cfg.get("required", False)),
            evidence=state,
            tool_evidence=tool_evidence,
        )
        engine_result.findings = findings
        return engine_result
