"""Interpreter resolution helpers for TestEngine."""

import os
import sys
from pathlib import Path

from ici.core.runner import ProcessResult


class TestInterpreterMixin:
    """Mixin providing interpreter resolution for pytest/coverage."""

    def _resolve_python(self) -> list[str]:
        """Resolve the interpreter used for every Python test-related module."""

        configured = self.get_config("test").get("python")  # type: ignore[attr-defined]
        if configured:
            return [str(configured)]

        candidates = (
            self.project_root / ".venv" / "bin" / "python",  # type: ignore[attr-defined]
            self.project_root / ".venv" / "Scripts" / "python.exe",  # type: ignore[attr-defined]
        )
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return [str(candidate)]
            except OSError:
                continue
        return [sys.executable]

    def _find_pytest_cmd(self) -> list[str]:
        return [*self._resolve_python(), "-m", "pytest"]

    def _build_python_test_env(self) -> dict[str, str]:
        env = os.environ.copy()
        source_paths = [str(path) for path in self.project_source_dirs()]  # type: ignore[attr-defined]
        if source_paths:
            env["PYTHONPATH"] = ":".join([*source_paths, env.get("PYTHONPATH", "")])
        if env.get("WSL_DISTRO_NAME") and Path("/tmp").is_dir():
            for key in ("TMPDIR", "TMP", "TEMP"):
                env[key] = "/tmp"
        return env

    def _find_coverage_cmd(self, python_cmd: list[str] | None) -> list[str] | None:
        """Find coverage.py through the exact interpreter used for pytest."""

        interpreter = self._interpreter_from_command(python_cmd)
        candidate = [*interpreter, "-m", "coverage"]
        probe = [*candidate, "--version"]
        result = self._run_test_process(  # type: ignore[attr-defined]
            probe,
            cwd=self.project_root,  # type: ignore[attr-defined]
        )
        self._record_tool("coverage --version", probe, result)  # type: ignore[attr-defined]
        if result.returncode == 0 and not result.timed_out and not result.truncated:
            return candidate
        if result.timed_out:
            self._record_tool_error("Coverage probe timed out")  # type: ignore[attr-defined]
        elif result.truncated:
            self._record_tool_error("Coverage probe output was truncated")  # type: ignore[attr-defined]
        elif result.returncode < 0:
            self._record_tool_error(  # type: ignore[attr-defined]
                "Coverage probe process terminated before reporting results"
            )
        elif not self._module_unavailable(result, "coverage"):  # type: ignore[attr-defined]
            self._record_tool_error(  # type: ignore[attr-defined]
                f"Coverage module probe failed with exit code {result.returncode}"
            )
        return None

    def _run_test_process(self, argv: list[str], *, cwd: Path) -> ProcessResult:
        """Execute a test helper through the owning engine's runner binding."""

        raise NotImplementedError

    def _interpreter_from_command(self, command: list[str] | None) -> list[str]:
        """Normalize legacy pytest argv into its interpreter prefix."""

        if not command:
            return self._resolve_python()
        if "-m" in command:
            module_index = command.index("-m")
            if module_index > 0:
                return command[:module_index]
        executable = command[0]
        if executable.endswith("pytest"):
            parent = Path(executable).parent
            for name in ("python", "python.exe"):
                candidate = parent / name
                if candidate.exists() or name == "python":
                    return [str(candidate)]
        return [executable]
