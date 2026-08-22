"""CMake shadow-build adapter (configure + build + optional CTest)."""

from ici.build_adapters.base import (
    BuildAdapter,
    BuildAdapterError,
    BuildOutcome,
    BuildRequest,
    step_from_result,
)
from ici.core.runner import run_process


class CMakeAdapter(BuildAdapter):
    """Runs cmake configure/build inside build/ici/cmake and optionally ctest."""

    name = "cmake"

    def run(self, request: BuildRequest) -> BuildOutcome:
        cmake = self._require_tool("cmake")
        request.build_dir.mkdir(parents=True, exist_ok=True)
        outcome = BuildOutcome(adapter=self.name, ok=False)

        configure = [
            cmake,
            "-S",
            str(request.project_root),
            "-B",
            str(request.build_dir),
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
        if not self._run_step("configure", configure, request, outcome):
            return outcome

        build = [cmake, "--build", str(request.build_dir), "--parallel", str(request.jobs)]
        if not self._run_step("build", build, request, outcome):
            return outcome

        compile_db = request.build_dir / "compile_commands.json"
        outcome.compile_commands = compile_db if compile_db.is_file() else None
        outcome.ok = True
        return outcome

    def maybe_test(self, request: BuildRequest, outcome: BuildOutcome) -> BuildOutcome:
        """Append a ctest step when tests are configured and available."""
        if not outcome.ok or not request.run_ctest:
            return outcome
        ctest = self.tools.get("ctest")
        if not ctest or not (request.build_dir / "CTestTestfile.cmake").is_file():
            return outcome
        argv = [ctest, "--test-dir", str(request.build_dir), "--output-on-failure"]
        result = run_process(argv, cwd=request.project_root)
        outcome.steps.append(step_from_result("ctest", argv, request.project_root, result))
        if result.returncode != 0:
            outcome.ok = False
            outcome.error = f"ctest exited {result.returncode}"
        return outcome

    def _run_step(
        self, name: str, argv: list[str], request: BuildRequest, outcome: BuildOutcome
    ) -> bool:
        result = run_process(argv, cwd=request.project_root)
        outcome.steps.append(step_from_result(name, argv, request.project_root, result))
        if result.returncode != 0 or result.timed_out:
            outcome.error = f"{name} failed (rc={result.returncode}, timed_out={result.timed_out})"
            return False
        return True


def require_build_tools(which) -> dict[str, str]:
    """Resolve cmake/ctest paths; missing cmake raises."""
    tools = {"cmake": which("cmake"), "ctest": which("ctest")}
    if not tools["cmake"]:
        raise BuildAdapterError("required tool 'cmake' was not found on PATH")
    return {k: v for k, v in tools.items() if v}
