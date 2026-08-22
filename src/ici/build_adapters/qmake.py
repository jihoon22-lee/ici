"""qmake/Make shadow-build adapter."""

from pathlib import Path

from ici.build_adapters.base import BuildAdapter, BuildOutcome, BuildRequest, step_from_result
from ici.core.runner import run_process


class QMakeAdapter(BuildAdapter):
    """Runs qmake into a shadow Makefile, then make -C build_dir."""

    name = "qmake"

    def __init__(self, tool_paths: dict[str, str], pro_file: Path):
        super().__init__(tool_paths)
        self.pro_file = pro_file

    def run(self, request: BuildRequest) -> BuildOutcome:
        qmake = self._require_tool("qmake")
        make = self._require_tool("make")
        request.build_dir.mkdir(parents=True, exist_ok=True)
        outcome = BuildOutcome(adapter=self.name, ok=False)

        configure = [qmake, "-o", "Makefile", str(self.pro_file)]
        result = run_process(configure, cwd=request.build_dir)
        outcome.steps.append(step_from_result("qmake", configure, request.build_dir, result))
        if result.returncode != 0:
            outcome.error = f"qmake exited {result.returncode}"
            return outcome
        if not (request.build_dir / "Makefile").is_file():
            outcome.error = "qmake succeeded but no Makefile was produced"
            return outcome

        build = [make, "-C", str(request.build_dir), "-j", str(request.jobs)]
        result = run_process(build, cwd=request.project_root)
        outcome.steps.append(step_from_result("make", build, request.project_root, result))
        if result.returncode != 0 or result.timed_out:
            outcome.error = f"make failed (rc={result.returncode})"
            return outcome

        outcome.ok = True
        return outcome
