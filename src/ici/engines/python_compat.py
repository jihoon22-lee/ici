"""Configured Python runtime, syntax-floor, and import compatibility engine."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines._python_compatibility import (
    MAX_COMPAT_TOTAL_AST_NODES,
    PythonMetadataError,
    analyze_static_compatibility,
    inferred_target_version,
    load_python_metadata,
    parse_runtime_version,
    parse_target_version,
    requires_python_allows,
)
from ici.engines._python_packaging import (
    PackagingPolicy,
    PythonPackagingError,
    analyze_python_packaging,
)
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources
from ici.engines.base import BaseEngine

_IMPORT_SCRIPT = (
    "import importlib,json,sys;"
    "sys.path[:0]=json.loads(sys.argv[1]);"
    "[importlib.import_module(name) for name in json.loads(sys.argv[2])];"
    "print('ici-import-smoke-ok')"
)


def _tool_evidence(
    name: str,
    executable: str,
    argv: list[str],
    result: ProcessResult,
    *,
    version: str = "",
) -> ToolEvidence:
    return ToolEvidence(
        name=name,
        path=executable,
        version=version,
        argv=argv,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=result.stderr.strip() if result.returncode != 0 else "",
    )


def _runtime_target(
    name: str,
    status: EngineStatus,
    message: str,
    **metrics: str | int,
) -> InspectionTarget:
    return InspectionTarget(
        file_path=".",
        start_line=1,
        target_name=f"PythonRuntime:{name}",
        status=status,
        message=message,
        metrics=metrics,
    )


class PythonCompatibilityEngine(BaseEngine):
    """Run each configured interpreter without a shell and retain exact evidence."""

    # Configured interpreter paths can be replaced independently of project
    # source and the current-process capability inventory.
    CACHE_REUSE_SAFE = False

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.engines._python_compatibility",
        "ici.engines._python_packaging",
        "ici.engines._python_resource_scopes",
        "ici.engines._source_inputs",
        "ici.engines.python_compat",
    )

    def run(self) -> EngineResult:
        started = time.time()
        cfg = self.get_config("python_compat")
        selected = self.project_python_sources()
        if not selected:
            return self._not_applicable(started, cfg)

        targets: list[InspectionTarget] = []
        evidence: list[ToolEvidence] = []
        errors: list[str] = []
        failures = 0
        warnings = 0
        checked_files = 0
        ast_nodes = 0
        requires_python = ""
        discovered_imports: tuple[str, ...] = ()
        import_names: tuple[str, ...] = ()
        target_version: tuple[int, int] | None = None
        package_evidence: dict[str, Any] = {
            "state": "NOT_APPLICABLE",
            "policy": str(cfg.get("wheel_policy", "allow-native")),
            "requested": list(cfg.get("wheel_globs", [])),
            "checked": 0,
            "findings": [],
        }
        package_findings = []
        try:
            inventory = read_analysis_sources(self.project_root, selected)
            metadata = load_python_metadata(self.project_root, selected)
            requires_python = metadata.requires_python
            discovered_imports = metadata.import_names
            target_version = self._target_version(cfg, requires_python)
            configured_imports = cfg.get("imports", [])
            # Importing project code can have arbitrary top-level side effects.
            # Discovery is retained as metadata, but execution is explicit opt-in.
            import_names = tuple(configured_imports)
            for source in inventory.sources:
                if source.language != "python":
                    continue
                checked_files += 1
                try:
                    analysis = analyze_static_compatibility(
                        source.file_path,
                        source.text,
                        target_version,
                    )
                except SyntaxError as error:
                    line = max(1, error.lineno or 1)
                    targets.append(
                        InspectionTarget(
                            file_path=source.file_path,
                            start_line=line,
                            start_column=error.offset,
                            target_name="Compatibility:SyntaxUnavailable",
                            status=EngineStatus.ERROR,
                            message="Current ici runtime cannot parse this Python source",
                        )
                    )
                    errors.append(f"{source.file_path}:{line}: Python syntax could not be parsed")
                    continue
                if ast_nodes + analysis.ast_nodes > MAX_COMPAT_TOTAL_AST_NODES:
                    raise PythonMetadataError(
                        "Python compatibility aggregate AST exceeds the bounded limit "
                        f"({MAX_COMPAT_TOTAL_AST_NODES} nodes)"
                    )
                ast_nodes += analysis.ast_nodes
                targets.extend(analysis.targets)
                warnings += len(analysis.targets)
                targets.append(
                    InspectionTarget(
                        file_path=source.file_path,
                        start_line=1,
                        target_name="Compatibility:StaticScan",
                        status=EngineStatus.PASS,
                        message="Python syntax-floor and standard-library API scan completed",
                    )
                )
            if metadata.pyproject_present:
                package_analysis = analyze_python_packaging(
                    self.project_root,
                    selected,
                    PackagingPolicy(
                        wheel_globs=tuple(cfg.get("wheel_globs", [])),
                        wheel_required=bool(cfg.get("wheel_required", False)),
                        wheel_policy=str(cfg.get("wheel_policy", "allow-native")),
                        check_entrypoints=bool(cfg.get("check_entrypoints", True)),
                        check_package_files=bool(cfg.get("check_package_files", True)),
                        max_wheels=int(cfg.get("max_wheels", 32)),
                        max_wheel_members=int(cfg.get("max_wheel_members", 8192)),
                        max_wheel_uncompressed_bytes=int(
                            cfg.get("max_wheel_uncompressed_bytes", 64 * 1024 * 1024)
                        ),
                    ),
                )
                targets.extend(package_analysis.targets)
                package_findings.extend(package_analysis.findings)
                failures += package_analysis.failures
                warnings += package_analysis.warnings
                package_evidence = package_analysis.metadata
            if not errors:
                runtime_failures, runtime_warnings = self._check_runtimes(
                    cfg,
                    requires_python,
                    import_names,
                    selected,
                    targets,
                    evidence,
                )
                failures += runtime_failures
                warnings += runtime_warnings
        except (
            AnalysisSourceError,
            PythonMetadataError,
            PythonPackagingError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            file_path = (
                error.file_path if isinstance(error, AnalysisSourceError) else "pyproject.toml"
            )
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=1,
                    target_name="Compatibility:AnalysisUnavailable",
                    status=EngineStatus.ERROR,
                    message=str(error),
                )
            )
            errors.append(str(error))

        runtime_errors = [target for target in targets if target.status == EngineStatus.ERROR]
        if errors or runtime_errors:
            status = EngineStatus.ERROR
            summary = (
                f"Python compatibility analysis incomplete: {errors[0]}"
                if errors
                else runtime_errors[0].message
            )
            result_evidence = EvidenceState.NOT_RUN
        else:
            status = self.evaluate_status(
                failures > 0, warnings > 0, cfg.get("mode", "pass_warn_fail")
            )
            summary = (
                f"Python compatibility: {failures} failure(s), {warnings} warning(s)"
                if failures or warnings
                else "Python runtime and source compatibility verified"
            )
            result_evidence = EvidenceState.MEASURED
        result = self.create_result(
            name="python_compat",
            status=status,
            summary=summary,
            duration=time.time() - started,
            targets=targets,
            extra={
                "requires_python": requires_python,
                "target_version": (
                    f"{target_version[0]}.{target_version[1]}" if target_version else ""
                ),
                "files_checked": checked_files,
                "ast_nodes": ast_nodes,
                "runtime_count": sum(
                    target.target_name.endswith(":Verified") for target in targets
                ),
                "discovered_imports": list(discovered_imports),
                "configured_imports": list(import_names),
                "wheel": package_evidence,
                "limitations": [
                    "Import smoke executes project module top-level code in a contained subprocess, not a sandbox",
                    "Static API rules cover a documented standard-library compatibility inventory",
                    "C-extension ABI compatibility belongs to package and binary artifact analysis",
                ],
            },
            required=bool(cfg.get("required", False)),
            evidence=result_evidence,
            tool_evidence=evidence,
        )
        result.findings = package_findings
        return result

    def _not_applicable(self, started: float, cfg: dict[str, Any]) -> EngineResult:
        return self.create_result(
            name="python_compat",
            status=EngineStatus.SKIP,
            summary="Python compatibility analysis skipped: no Python source files",
            duration=time.time() - started,
            targets=[
                _runtime_target(
                    "NotApplicable",
                    EngineStatus.SKIP,
                    "No applicable Python source files were selected",
                )
            ],
            required=bool(cfg.get("required", False)),
            evidence=EvidenceState.NOT_APPLICABLE,
        )

    @staticmethod
    def _target_version(
        cfg: dict[str, Any],
        requires_python: str,
    ) -> tuple[int, int] | None:
        configured = cfg.get("target_version")
        if isinstance(configured, str) and configured:
            parsed = parse_target_version(configured)
            if parsed is None:
                raise PythonMetadataError(f"invalid target_version: {configured!r}")
            return parsed
        return inferred_target_version(requires_python)

    def _configured_runtimes(self, cfg: dict[str, Any]) -> list[tuple[str, bool]]:
        configured = cfg.get("interpreters", [])
        required = set(cfg.get("required_interpreters", []))
        if not configured:
            return [(sys.executable, True)]
        return [(str(value), str(value) in required) for value in configured]

    def _resolve_interpreter(self, configured: str) -> str | None:
        candidate = Path(configured)
        if (
            candidate.is_absolute()
            or os.sep in configured
            or (os.altsep and os.altsep in configured)
        ):
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                return None
            return str(resolved) if resolved.is_file() and os.access(resolved, os.X_OK) else None
        discovered = shutil.which(configured)
        return str(Path(discovered).resolve()) if discovered else None

    def _compile_targets(self, selected: list[Path]) -> list[str]:
        source_dirs = [path.resolve() for path in self.project_source_dirs() if path.is_dir()]
        targets: set[Path] = set()
        for source in selected:
            resolved = source.resolve()
            parent = next(
                (directory for directory in source_dirs if resolved.is_relative_to(directory)), None
            )
            targets.add(parent or resolved)
        if len(targets) > 128:
            raise PythonMetadataError("Python compileall target count exceeds 128")
        return [str(path) for path in sorted(targets)]

    def _check_runtimes(
        self,
        cfg: dict[str, Any],
        requires_python: str,
        import_names: tuple[str, ...],
        selected: list[Path],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
    ) -> tuple[int, int]:
        failures = 0
        warnings = 0
        for configured, required in self._configured_runtimes(cfg):
            executable = self._resolve_interpreter(configured)
            if executable is None:
                status = EngineStatus.ERROR if required else EngineStatus.WARN
                targets.append(
                    _runtime_target(
                        "Unavailable",
                        status,
                        f"Configured interpreter is unavailable: {configured}",
                        required=int(required),
                    )
                )
                if required:
                    failures += 1
                else:
                    warnings += 1
                continue
            runtime_failed, runtime_warned = self._check_runtime(
                configured,
                executable,
                required,
                requires_python,
                import_names,
                selected,
                targets,
                evidence,
            )
            failures += int(runtime_failed)
            warnings += int(runtime_warned)
        return failures, warnings

    def _check_runtime(
        self,
        configured: str,
        executable: str,
        required: bool,
        requires_python: str,
        import_names: tuple[str, ...],
        selected: list[Path],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
    ) -> tuple[bool, bool]:
        version_argv = [executable, "-VV"]
        version_result = run_process(
            version_argv, cwd=self.project_root, timeout=10, max_output_chars=8192
        )
        version = parse_runtime_version(f"{version_result.stdout}\n{version_result.stderr}")
        evidence.append(
            _tool_evidence(
                "python -VV",
                executable,
                version_argv,
                version_result,
                version=str(version) if version is not None else "",
            )
        )
        problem = self._process_problem(version_result) or (
            "unparseable version" if version is None else ""
        )
        if problem:
            status = EngineStatus.ERROR if required else EngineStatus.WARN
            targets.append(
                _runtime_target("VersionUnavailable", status, f"{configured}: {problem}")
            )
            return required, not required

        assert version is not None
        compatible = not requires_python or requires_python_allows(requires_python, version)
        compile_result, compile_evidence = self._run_compileall(executable, selected)
        evidence.append(compile_evidence)
        problems = []
        if not compatible:
            problems.append(f"does not satisfy requires-python {requires_python}")
        checks = [("compileall", compile_result)]
        if import_names:
            import_result, import_evidence = self._run_imports(executable, import_names)
            evidence.append(import_evidence)
            checks.append(("import smoke", import_result))
        for label, result in checks:
            process_problem = self._process_problem(result)
            if process_problem:
                problems.append(f"{label}: {process_problem}")
        if problems:
            status = EngineStatus.FAIL if required else EngineStatus.WARN
            targets.append(
                _runtime_target(
                    "Incompatible",
                    status,
                    f"{configured} ({version}): {'; '.join(problems)}",
                    required=int(required),
                )
            )
            return required, not required
        completed = (
            "version, compileall, and import smoke" if import_names else "version and compileall"
        )
        targets.append(
            _runtime_target(
                "Verified",
                EngineStatus.PASS,
                f"{configured} ({version}) passed {completed} checks",
                required=int(required),
                imports=len(import_names),
            )
        )
        return False, False

    def _run_compileall(
        self,
        executable: str,
        selected: list[Path],
    ) -> tuple[ProcessResult, ToolEvidence]:
        with tempfile.TemporaryDirectory(prefix="ici-python-compat-") as cache:
            argv = [
                executable,
                "-B",
                "-m",
                "compileall",
                "-q",
                "-f",
                *self._compile_targets(selected),
            ]
            result = run_process(
                argv,
                cwd=self.project_root,
                env={"PYTHONPYCACHEPREFIX": cache, "PYTHONHASHSEED": "0"},
                timeout=60,
                max_output_chars=128_000,
            )
        return result, _tool_evidence("python -m compileall", executable, argv, result)

    def _run_imports(
        self,
        executable: str,
        import_names: tuple[str, ...],
    ) -> tuple[ProcessResult, ToolEvidence]:
        source_paths = [str(self.project_root)]
        source_paths.extend(
            str(path.resolve()) for path in self.project_source_dirs() if path.is_dir()
        )
        argv = [
            executable,
            "-I",
            "-B",
            "-c",
            _IMPORT_SCRIPT,
            json.dumps(source_paths, separators=(",", ":")),
            json.dumps(import_names, separators=(",", ":")),
        ]
        result = run_process(
            argv,
            cwd=self.project_root,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"},
            timeout=30,
            max_output_chars=128_000,
        )
        return result, _tool_evidence("python import smoke", executable, argv, result)

    @staticmethod
    def _process_problem(result: ProcessResult) -> str:
        if result.timed_out:
            return "timed out"
        if result.truncated:
            return "output exceeded the bounded limit"
        if result.returncode != 0:
            return f"exit code {result.returncode}"
        return ""
