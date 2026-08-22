"""Common build-adapter contracts and artifact manifest."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class BuildAdapterError(Exception):
    """Raised when a build system cannot be selected or executed."""


@dataclass
class BuildRequest:
    """Inputs for one shadow-build execution."""

    project_root: Path
    build_dir: Path
    jobs: int = 4
    run_ctest: bool = True


@dataclass
class BuildStep:
    """One executed argv command with its outcome evidence."""

    name: str
    argv: list[str]
    cwd: str
    returncode: int | None
    duration: float
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass
class BuildOutcome:
    """Aggregated result of an adapter run."""

    adapter: str
    ok: bool
    steps: list[BuildStep] = field(default_factory=list)
    compile_commands: Path | None = None
    error: str = ""


@dataclass
class ArtifactManifest:
    """Records what a shadow build produced, with project-boundary validation."""

    adapter: str
    build_dir: str
    steps: list[dict] = field(default_factory=list)
    compile_commands: str | None = None

    def validate(self, project_root: Path) -> None:
        """Reject any recorded path that resolves outside the project root."""
        allowed = project_root.resolve()
        candidates = [self.build_dir]
        if self.compile_commands:
            candidates.append(self.compile_commands)
        for value in candidates:
            resolved = (allowed / value).resolve()
            try:
                resolved.relative_to(allowed)
            except ValueError as err:
                raise BuildAdapterError(f"artifact path is outside project: {value}") from err

    def to_dict(self) -> dict:
        return {
            "adapter": self.adapter,
            "build_dir": self.build_dir,
            "steps": self.steps,
            "compile_commands": self.compile_commands,
        }


def step_from_result(name: str, argv: list[str], cwd: Path, result) -> BuildStep:
    """Convert a runner ProcessResult into a BuildStep record."""
    return BuildStep(
        name=name,
        argv=argv,
        cwd=str(cwd),
        returncode=result.returncode,
        duration=result.duration,
        stdout_tail=result.stdout[-500:],
        stderr_tail=result.stderr[-500:],
    )


class BuildAdapter(ABC):
    """A real build system driven through argv-only subprocesses."""

    name: str = "base"

    def __init__(self, tool_paths: dict[str, str]):
        self.tools = tool_paths

    @abstractmethod
    def run(self, request: BuildRequest) -> BuildOutcome:
        """Execute configure/build(/test) inside the shadow directory."""
        raise NotImplementedError

    def _require_tool(self, key: str) -> str:
        path = self.tools.get(key)
        if not path:
            raise BuildAdapterError(f"required tool '{key}' was not found on PATH")
        return path
