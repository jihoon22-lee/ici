"""BaseEngine abstract class for all verification engines in ici."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ici.core.models import EngineResult, EngineStatus


class BaseEngine(ABC):
    """Abstract base class that all verification and build engines must inherit from."""

    def __init__(self, project_root: Path | None = None, config: dict[str, Any] | None = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.config = config or {}

    @abstractmethod
    def run(self) -> EngineResult:
        """Executes the verification logic and returns a structured EngineResult."""
        pass

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
        )
