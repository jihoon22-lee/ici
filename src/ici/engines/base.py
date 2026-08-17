"""BaseEngine abstract class for all verification engines in ici."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ici.config import get_engine_config
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

    def get_config(self, engine_name: str) -> dict[str, Any]:
        """Gets configuration for this specific engine."""
        return get_engine_config(self.config, engine_name)

    def evaluate_status(
        self, has_fail: bool, has_warn: bool, mode: str = "pass_warn_fail"
    ) -> EngineStatus:
        """Evaluates final engine status considering configured policy mode.

        mode options:
        - "pass_warn_fail" (default): FAIL on error, WARN on warning, else PASS
        - "pass_fail": FAIL on error, ignore warnings (PASS)
        - "pass_warn": Downgrade all FAILs and WARNs to WARN (never fails CI)
        """
        if mode == "pass_fail":
            return EngineStatus.FAIL if has_fail else EngineStatus.PASS
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
