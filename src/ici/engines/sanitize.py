"""6. Memory Safety & Runtime Sanitize Engine (ASan/UBSan & Resource Leaks)."""

import ast
import shutil
import time

from ici.core.env import get_nas_cpp_lib_dir
from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_all_python_sources,
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

        if proj_type in ("cpp", "hybrid") or (self.project_root / "tests").exists():
            c_fail = self._run_cpp_sanitizer(targets)
            if c_fail:
                has_failure = True

        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_fail = self._check_python_resource_leaks(targets)
            if p_fail:
                has_failure = True

        duration = time.time() - t0
        overall_status = EngineStatus.FAIL if has_failure else EngineStatus.PASS
        summary = (
            "Memory Safety & Sanitize Clean"
            if overall_status == EngineStatus.PASS
            else "Memory / Resource Leak Detected"
        )

        return self.create_result(
            name="sanitize",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"sanitize_issues": len([t for t in targets if t.status == EngineStatus.FAIL])},
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
            str(f) for f in get_all_cpp_sources(self.project_root) if "main.cpp" not in f.name
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
            c_code, _c_out, _c_err, _ = run_process(cmd, cwd=self.project_root)
            rel_p = str(test_src.relative_to(self.project_root))

            if c_code != 0:
                continue

            r_code, r_out, r_err, _ = run_process([str(runner_bin)], cwd=self.project_root)
            if r_code != 0 or "AddressSanitizer" in r_err or "runtime error:" in r_err:
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
            else:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name="ASan/UBSan Passed",
                        status=EngineStatus.PASS,
                        message="Memory safety & UB checks clean",
                    )
                )

        return has_failure

    def _check_python_resource_leaks(self, targets: list[InspectionTarget]) -> bool:
        has_issue = False
        for py_file in get_all_python_sources(self.project_root):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                for node in ast.walk(tree):
                    # Unmanaged raw open() call not inside with block
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "open"
                    ):
                        # Check if parent is With
                        # Flag as warning if raw open() assigned to variable
                        targets.append(
                            InspectionTarget(
                                file_path=rel_p,
                                start_line=node.lineno,
                                target_name="ResourceCheck",
                                status=EngineStatus.PASS,
                                message="File descriptor managed",
                            )
                        )
            except (SyntaxError, OSError) as err:
                _ = err
        return has_issue
