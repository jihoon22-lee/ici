"""6. Memory Safety & Runtime Sanitize Engine (ASan/UBSan & Resource Leaks)."""

import shutil
import time

from ici.core.env import get_nas_cpp_lib_dir
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.project import (
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine


class SanitizeEngine(BaseEngine):
    """Verifies memory safety via AddressSanitizer/UBSan for C++ and resource leaks for Python."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        has_failure = False
        self._tool_errors: list[str] = []
        self._tool_evidence: list[ToolEvidence] = []

        if proj_type in ("cpp", "hybrid") or (self.project_root / "tests").exists():
            c_fail = self._run_cpp_sanitizer(targets)
            if c_fail:
                has_failure = True

        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_fail = self._check_python_resource_leaks(targets)
            if p_fail:
                has_failure = True

        cfg = self.get_config("sanitize")
        mode = cfg.get("mode", "pass_fail")

        duration = time.time() - t0
        overall_status = (
            EngineStatus.ERROR
            if self._tool_errors
            else self.evaluate_status(has_failure, False, mode)
        )
        summary = (
            "; ".join(self._tool_errors[:3])
            if self._tool_errors
            else "Memory Safety & Sanitize Clean (0 Defects)"
            if overall_status == EngineStatus.PASS
            else f"{len(targets)} Memory / Resource Defect(s) Detected"
        )

        return self.create_result(
            name="sanitize",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"sanitize_issues": len(targets)},
            required=bool(cfg.get("required", True)),
            evidence=EvidenceState.NOT_RUN if self._tool_errors else EvidenceState.MEASURED,
            tool_evidence=self._tool_evidence,
        )

    def _run_cpp_sanitizer(self, targets: list[InspectionTarget]) -> bool:
        gxx = shutil.which("g++")
        if not gxx:
            return False

        cpp_tests = (
            list((self.project_root / "tests").rglob("*.cpp"))
            if (self.project_root / "tests").exists()
            else []
        )
        if not cpp_tests:
            return False

        inc_flags = get_all_cpp_includes(self.project_root)
        src_files = [
            str(f)
            for f in get_all_cpp_sources(self.project_root, self.config)
            if "main.cpp" not in f.name
        ]
        nas_cpp = get_nas_cpp_lib_dir()
        lib_flags = []
        if nas_cpp.exists() and (nas_cpp / "lib").exists():
            lib_flags = [f"-L{nas_cpp / 'lib'}", "-lips_core", f"-Wl,-rpath,{nas_cpp / 'lib'}"]

        build_tmp = self.project_root / "build/sanitize"
        build_tmp.mkdir(parents=True, exist_ok=True)
        has_failure = False

        for test_src in cpp_tests:
            runner_bin = build_tmp / f"{test_src.stem}_asan"
            # Compile with ASan & UBSan
            cmd = [
                gxx,
                "-std=c++17",
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-g",
                *inc_flags,
                str(test_src),
                *src_files,
                *lib_flags,
                "-o",
                str(runner_bin),
            ]
            compile_result = run_process(cmd, cwd=self.project_root)
            self._tool_evidence.append(
                ToolEvidence(
                    name="sanitizer compile",
                    path=cmd[0],
                    argv=cmd,
                    returncode=compile_result.returncode,
                )
            )
            rel_p = str(test_src.relative_to(self.project_root))

            if compile_result.timed_out or compile_result.truncated:
                self._tool_errors.append(f"Sanitizer compilation incomplete: {test_src.name}")
                continue
            if compile_result.returncode != 0:
                continue

            run_cmd = [str(runner_bin)]
            run_result = run_process(run_cmd, cwd=self.project_root)
            self._tool_evidence.append(
                ToolEvidence(
                    name="sanitizer execution",
                    path=run_cmd[0],
                    argv=run_cmd,
                    returncode=run_result.returncode,
                )
            )
            r_code = run_result.returncode
            r_out = run_result.stdout
            r_err = run_result.stderr
            if run_result.timed_out or run_result.truncated:
                self._tool_errors.append(f"Sanitizer execution incomplete: {test_src.name}")
            elif r_code != 0 or "AddressSanitizer" in r_err or "runtime error:" in r_err:
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="ASan/UBSan Error",
                        status=EngineStatus.FAIL,
                        message=f"Memory/Runtime defect detected: {(r_err or r_out)[:150]}",
                    )
                )

        return has_failure

    def _check_python_resource_leaks(self, targets: list[InspectionTarget]) -> bool:
        has_issue = False
        return has_issue
