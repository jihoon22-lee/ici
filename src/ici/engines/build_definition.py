"""Build definition engine — real shadow builds via adapters."""

import time

from ici.build_adapters.base import (
    ArtifactManifest,
    BuildAdapter,
    BuildAdapterError,
    BuildOutcome,
    BuildRequest,
)
from ici.build_adapters.cmake import CMakeAdapter
from ici.build_adapters.registry import select_build_adapter
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.engines.base import BaseEngine


def _step_to_evidence(step) -> ToolEvidence:
    name = step.argv[0] if step.argv else "build-step"
    return ToolEvidence(
        name=name,
        path=name,
        argv=step.argv,
        returncode=step.returncode,
        error=step.stderr_tail[:500],
    )


def _failed_target(location: str, message: str, status: EngineStatus) -> InspectionTarget:
    return InspectionTarget(
        file_path="",
        start_line=1,
        target_name=f"Build:{location}",
        status=status,
        message=message,
    )


def _is_tool_failure(outcome: BuildOutcome) -> bool:
    """A step that never reported a return code means the tool itself failed."""
    return any(step.returncode is None for step in outcome.steps)


class BuildDefinitionEngine(BaseEngine):
    """Configures and builds the project with its declared build system."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("build_definition")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        jobs = int(cfg.get("jobs", 4))
        run_ctest = bool(cfg.get("run_ctest", True))

        try:
            name, adapter = select_build_adapter(self.project_root, cfg.get("adapter", "auto"), {})
            if name == "none" or adapter is None:
                return self._not_a_build_project(t0)
            outcome = self._execute(adapter, name, jobs, run_ctest)
            return self._finish(name, outcome, mode, required, t0)
        except BuildAdapterError as err:
            return self._adapter_error(str(err), t0)

    def _not_a_build_project(self, t0: float) -> EngineResult:
        return self.create_result(
            name="build_definition",
            status=EngineStatus.PASS,
            summary="No CMakeLists.txt or .pro — nothing to build",
            duration=time.time() - t0,
            targets=[],
            required=bool(self.get_config("build_definition").get("required", False)),
            evidence=EvidenceState.MEASURED,
        )

    def _adapter_error(self, message: str, t0: float) -> EngineResult:
        return self.create_result(
            name="build_definition",
            status=EngineStatus.WARN,
            summary=f"Build definition error: {message}",
            duration=time.time() - t0,
            targets=[_failed_target("Adapter", message, EngineStatus.WARN)],
            evidence=EvidenceState.NOT_RUN,
        )

    def _execute(
        self, adapter: BuildAdapter, name: str, jobs: int, run_ctest: bool
    ) -> BuildOutcome:
        request = BuildRequest(
            project_root=self.project_root,
            build_dir=self.project_root / "build" / "ici" / name,
            jobs=jobs,
            run_ctest=run_ctest,
        )
        outcome = adapter.run(request)
        if isinstance(adapter, CMakeAdapter):
            outcome = adapter.maybe_test(request, outcome)
        return outcome

    def _finish(
        self, name: str, outcome: BuildOutcome, mode: str, required: bool, t0: float
    ) -> EngineResult:
        manifest = ArtifactManifest(
            adapter=name,
            build_dir=f"build/ici/{name}",
            steps=[
                {
                    "name": s.name,
                    "argv": s.argv,
                    "returncode": s.returncode,
                    "duration": s.duration,
                }
                for s in outcome.steps
            ],
        )
        manifest.validate(self.project_root)
        tool_evidence = [_step_to_evidence(s) for s in outcome.steps]
        duration = time.time() - t0
        extra = {"manifest": manifest.to_dict()}
        status = EngineStatus.PASS
        summary = f"{name} shadow build OK ({len(outcome.steps)} step(s))"
        targets: list[InspectionTarget] = []
        evidence = EvidenceState.MEASURED

        if not outcome.ok:
            if _is_tool_failure(outcome):
                status = EngineStatus.ERROR
                summary = f"{name} build tool failure: {outcome.error}"
            else:
                status = self.evaluate_status(True, False, mode)
                summary = f"{name} build failed: {outcome.error}"
            targets.append(_failed_target(name, outcome.error, status))
            return self.create_result(
                name="build_definition",
                status=status,
                summary=summary,
                duration=duration,
                targets=targets,
                extra=extra,
                required=required,
                evidence=evidence,
                tool_evidence=tool_evidence,
            )

        gate = self.evaluate_status(False, False, mode)
        return self.create_result(
            name="build_definition",
            status=gate if gate != EngineStatus.FAIL else status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra=extra,
            required=required,
            evidence=evidence,
            tool_evidence=tool_evidence,
        )
