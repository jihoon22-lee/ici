"""BaseEngine abstract class for all verification engines in ici."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.config import get_engine_config
from ici.config_schema import validate_config_paths
from ici.core.models import EngineResult, EngineStatus, EvidenceState, ToolEvidence
from ici.core.project import (
    detect_project_type,
    get_all_cpp_headers,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_all_python_sources,
    get_compilable_cpp_sources,
    get_cpp_external_build_dirs,
    get_project_name,
    get_project_version,
    get_source_dirs,
)

if TYPE_CHECKING:
    from ici.core.context import AnalysisContext, ArtifactManifest


class BaseEngine(ABC):
    """Abstract base class that all verification and build engines must inherit from."""

    def __init__(
        self,
        project_root: Path | None = None,
        config: dict[str, Any] | None = None,
        analysis_context: AnalysisContext | None = None,
    ):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.config = config or {}
        validate_config_paths(self.config, self.project_root)
        if analysis_context is not None and analysis_context.project.root != self.project_root:
            raise ValueError("analysis context belongs to another project root")
        self.analysis_context = analysis_context

    def _context_paths(self, field_name: str) -> list[Path] | None:
        if self.analysis_context is None:
            return None
        values = getattr(self.analysis_context.project, field_name)
        return [self.project_root / value for value in values]

    def project_type(self) -> str:
        if self.analysis_context is not None:
            return self.analysis_context.project.project_type
        return detect_project_type(self.project_root)

    def project_name(self) -> str:
        if self.analysis_context is not None:
            return self.analysis_context.project.name
        return get_project_name(self.project_root)

    def project_version(self) -> str:
        if self.analysis_context is not None:
            return self.analysis_context.project.version
        return get_project_version(self.project_root)

    def project_source_dirs(self) -> list[Path]:
        paths = self._context_paths("source_dirs")
        return paths if paths is not None else get_source_dirs(self.project_root, self.config)

    def project_python_sources(self) -> list[Path]:
        paths = self._context_paths("python_sources")
        return (
            paths if paths is not None else get_all_python_sources(self.project_root, self.config)
        )

    def project_cpp_sources(self) -> list[Path]:
        paths = self._context_paths("cpp_sources")
        return paths if paths is not None else get_all_cpp_sources(self.project_root, self.config)

    def project_cpp_headers(self) -> list[Path] | None:
        """Return snapshotted or directly discovered C/C++ header inputs."""

        paths = self._context_paths("cpp_headers")
        return paths if paths is not None else get_all_cpp_headers(self.project_root, self.config)

    def project_compilable_cpp_sources(self) -> list[Path]:
        paths = self._context_paths("compilable_cpp_sources")
        return (
            paths
            if paths is not None
            else get_compilable_cpp_sources(self.project_root, self.config)
        )

    def project_external_cpp_dirs(self) -> list[Path]:
        paths = self._context_paths("external_cpp_dirs")
        return (
            paths
            if paths is not None
            else get_cpp_external_build_dirs(self.project_root, self.config)
        )

    def project_cpp_include_flags(self) -> list[str]:
        if self.analysis_context is not None:
            return list(self.analysis_context.project.cpp_include_flags)
        return get_all_cpp_includes(self.project_root, self.config)

    @abstractmethod
    def run(self) -> EngineResult:
        """Executes the verification logic and returns a structured EngineResult."""
        pass

    def get_config(self, engine_name: str) -> dict[str, Any]:
        """Gets configuration for this specific engine."""
        return get_engine_config(self.config, engine_name)

    def evaluate_status(
        self, has_fail: bool, has_warn: bool, mode: str = "pass_warn_fail"
    ) -> EngineStatus:
        """Evaluates final engine status considering configured policy mode.

        mode options:
        - "pass_warn_fail" (default): FAIL on error, WARN on warning, else PASS
        - "pass_fail": FAIL on error or warning
        - "pass_warn": Downgrade all FAILs and WARNs to WARN (never fails CI)
        """
        if mode == "pass_fail":
            return EngineStatus.FAIL if (has_fail or has_warn) else EngineStatus.PASS
        elif mode == "pass_warn":
            return EngineStatus.WARN if (has_fail or has_warn) else EngineStatus.PASS
        else:  # pass_warn_fail
            if has_fail:
                return EngineStatus.FAIL
            elif has_warn:
                return EngineStatus.WARN
            return EngineStatus.PASS

    def create_result(
        self,
        name: str,
        status: EngineStatus,
        summary: str,
        score: float | None = None,
        max_score: float | None = None,
        duration: float = 0.0,
        targets: list | None = None,
        raw_output: str = "",
        extra: dict[str, Any] | None = None,
        required: bool = True,
        evidence: EvidenceState = EvidenceState.MEASURED,
        tool_evidence: list[ToolEvidence] | None = None,
        artifact_manifests: list[ArtifactManifest] | tuple[ArtifactManifest, ...] | None = None,
    ) -> EngineResult:
        return EngineResult(
            engine_name=name,
            status=status,
            summary=summary,
            score=score,
            max_score=max_score,
            duration=duration,
            targets=targets or [],
            raw_output=raw_output,
            extra=extra or {},
            required=required,
            evidence=evidence,
            tool_evidence=tool_evidence or [],
            artifact_manifests=tuple(artifact_manifests or ()),
        )
