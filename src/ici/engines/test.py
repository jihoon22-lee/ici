"""3. Unit Test Execution, Coverage Measurement & TEM Scoring Engine."""

import contextlib
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ici.core.env import (
    find_uv,  # noqa: F401 - retained for callers patching the legacy probe
    get_nas_cpp_lib_dir,
)
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
    get_all_python_sources,
    get_source_dirs,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine
from ici.engines.coverage_support import (
    build_coverage_summary,
    calculate_tem,
    compute_python_function_coverage,
    parse_coverage_json,
    parse_gcov_dir,
    parse_gcov_functions,
)


class TestEngine(BaseEngine):
    """Executes unit tests and calculates TEM score based on branch & function coverage."""

    __test__ = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._coverage_data: dict | None = None
        self._cpp_coverage_rows: list[dict] = []
        self._cpp_function_rows: list[dict] = []
        self._function_rows: list[dict] = []
        self._coverage_files: list[dict] = []
        self._coverage_totals: dict | None = None
        self._coverage_source = "estimated"
        self._tool_errors: list[str] = []
        self._coverage_errors: list[str] = []
        self._tool_evidence: list[ToolEvidence] = []
        self._coverage_measured = False
        self._python_test_attempted = False
        self._cpp_test_attempted = False
        self._has_run = False

    def _reset_run_state(self) -> None:
        """Discard every run-specific measurement before a new execution."""

        self._coverage_data = None
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []
        self._function_rows = []
        self._coverage_files = []
        self._coverage_totals = None
        self._coverage_source = "estimated"
        self._tool_errors = []
        self._coverage_errors = []
        self._tool_evidence = []
        self._coverage_measured = False
        self._python_test_attempted = False
        self._cpp_test_attempted = False

    def run(self) -> EngineResult:
        t0 = time.time()
        if self._has_run:
            self._reset_run_state()
        else:
            self._tool_errors = []
            self._coverage_errors = []
            self._tool_evidence = []
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        passed_tests, total_tests, has_failure = self._run_project_tests(proj_type, targets)
        cfg = self.get_config("test")
        required_coverage_missing, optional_coverage_warning = self._apply_coverage_policy(cfg)
        branch_cov, func_cov, missed_targets = self._measure_coverage(proj_type, has_failure)
        targets.extend(missed_targets)
        tem_info = self._calc_tem(branch_cov, func_cov, passed_tests, total_tests)
        tem_score = tem_info["tem_score"]
        has_warn = self._has_threshold_warning(
            cfg, optional_coverage_warning, has_failure, tem_score, branch_cov, func_cov
        )
        test_suites = self._build_test_suites(targets)
        overall_status = self._result_status(
            cfg, has_failure, has_warn, optional_coverage_warning, required_coverage_missing
        )
        summary = self._result_summary(
            passed_tests,
            total_tests,
            func_cov,
            tem_score,
            tem_info,
            optional_coverage_warning,
        )
        duration = time.time() - t0
        line_cov = tem_info["line_coverage"]
        pass_rate = tem_info["pass_rate"]
        cov_label = tem_info["cov_label"]
        cov_shown = tem_info["cov_shown"]
        self._has_run = True
        evidence = self._result_evidence(optional_coverage_warning, required_coverage_missing)
        return self.create_result(
            name="test",
            status=overall_status,
            summary=summary,
            score=tem_score,
            max_score=5.0,
            duration=duration,
            targets=targets,
            extra={
                "passed_tests": passed_tests,
                "total_tests": total_tests,
                "pass_rate": pass_rate,
                "branch_coverage": branch_cov,
                "function_coverage": func_cov,
                "line_coverage": line_cov,
                "tem_score": tem_score,
                "test_suites": test_suites,
                "coverage_files": self._coverage_files,
                "coverage_source": self._coverage_source,
                "coverage_totals": self._coverage_totals,
                "function_rows": self._function_rows,
                "metrics_summary": (
                    f"TEM: {tem_score:.2f}/5.0 ({cov_label}: {cov_shown:.0f}%, "
                    f"Func: {func_cov:.0f}%, PassRate: {pass_rate:.0%})"
                ),
            },
            required=bool(cfg.get("required", True)),
            evidence=evidence,
            tool_evidence=self._tool_evidence,
        )

    def _run_project_tests(
        self, proj_type: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        tests_root = self.project_root / "tests"
        py_tests = list(tests_root.rglob("test_*.py")) if tests_root.exists() else []
        cpp_tests = list(tests_root.rglob("*.cpp")) if tests_root.exists() else []
        py_sources = get_all_python_sources(self.project_root, self.config)
        cpp_sources = get_all_cpp_sources(self.project_root, self.config)
        totals = [0, 0]
        has_failure = False
        if py_tests or (proj_type in ("python", "hybrid") and py_sources):
            self._python_test_attempted = True
            result = (
                self._run_python_tests(targets)
                if py_tests or tests_root.exists()
                else self._mark_zero_tests("Python", targets)
            )
            totals[0] += result[0]
            totals[1] += result[1]
            has_failure = has_failure or result[2]
        if cpp_tests or (proj_type in ("cpp", "hybrid") and cpp_sources):
            self._cpp_test_attempted = True
            result = (
                self._run_cpp_tests(targets) if cpp_tests else self._mark_zero_tests("C++", targets)
            )
            totals[0] += result[0]
            totals[1] += result[1]
            has_failure = has_failure or result[2]
        return totals[0], totals[1], has_failure

    def _apply_coverage_policy(self, cfg: dict[str, Any]) -> tuple[bool, bool]:
        missing = self._coverage_failure_messages()
        required = bool(cfg.get("coverage_required", False)) and bool(missing)
        optional = bool(missing) and not required
        if required:
            for message in missing:
                self._record_tool_error(message)
        return required, optional

    @staticmethod
    def _has_threshold_warning(
        cfg: dict[str, Any],
        optional: bool,
        has_failure: bool,
        tem: float,
        branch: float,
        func: float,
    ) -> bool:
        if optional or has_failure:
            return optional
        return (
            tem < cfg.get("min_tem_score", 4.0)
            or branch < cfg.get("min_branch_cov", 80.0)
            or func < cfg.get("min_func_cov", 90.0)
        )

    @staticmethod
    def _build_test_suites(targets: list[InspectionTarget]) -> list[dict]:
        suite_map: dict[str, dict] = {}
        for target in targets:
            if target.target_name.startswith("Coverage:"):
                continue
            suite = suite_map.setdefault(
                target.file_path,
                {"file": target.file_path, "passed": 0, "failed": 0, "total": 0, "tests": []},
            )
            suite["total"] += 1
            key = "passed" if target.status == EngineStatus.PASS else "failed"
            suite[key] += 1
            suite["tests"].append(
                {
                    "name": target.target_name,
                    "status": target.status.value,
                    "message": target.message,
                }
            )
        return list(suite_map.values())

    def _result_status(
        self,
        cfg: dict[str, Any],
        has_failure: bool,
        has_warn: bool,
        optional: bool,
        required: bool,
    ) -> EngineStatus:
        if self._tool_errors or required:
            return EngineStatus.ERROR
        if optional and not has_failure:
            return EngineStatus.WARN
        return self.evaluate_status(has_failure, has_warn, cfg.get("mode", "pass_fail"))

    def _result_summary(
        self,
        passed_tests: int,
        total_tests: int,
        func_cov: float,
        tem_score: float,
        tem_info: dict[str, Any],
        optional: bool,
    ) -> str:
        if self._tool_errors:
            return "; ".join(self._tool_errors[:3])
        summary = (
            f"{passed_tests}/{total_tests} Tests Passed | "
            f"{tem_info['cov_label']}: {tem_info['cov_shown']:.1f}%{tem_info['cov_suffix']}, "
            f"Func: {func_cov:.1f}% -> TEM: {tem_score:.2f} / 5.0"
        )
        if optional:
            summary += "; Coverage evidence ESTIMATED (optional; not threshold evidence)"
        return summary

    def _result_evidence(self, optional: bool, required: bool) -> EvidenceState:
        if self._tool_errors or required:
            return EvidenceState.NOT_RUN
        return EvidenceState.ESTIMATED if optional else EvidenceState.MEASURED

    def _mark_zero_tests(
        self, language: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        targets.append(
            InspectionTarget(
                file_path="tests",
                start_line=1,
                target_name=f"[{language}] Tests",
                status=EngineStatus.FAIL,
                message="No tests collected",
            )
        )
        return 0, 0, True

    def _coverage_failure_messages(self) -> list[str]:
        failures = list(self._coverage_errors)
        if self._python_test_attempted and not self._coverage_data:
            failures.append("Python coverage evidence was unavailable or malformed")
        if self._cpp_test_attempted and not self._cpp_coverage_rows:
            failures.append("C++ gcov coverage evidence was unavailable or malformed")
        return list(dict.fromkeys(failures))

    def _record_tool(self, name: str, argv: list[str], result) -> None:
        self._tool_evidence.append(
            ToolEvidence(name=name, path=argv[0], argv=argv, returncode=result.returncode)
        )

    def _record_tool_error(self, message: str) -> None:
        if message not in self._tool_errors:
            self._tool_errors.append(message)

    def _calc_tem(
        self, branch_cov: float, func_cov: float, passed_tests: int, total_tests: int
    ) -> dict[str, Any]:
        return calculate_tem(branch_cov, func_cov, passed_tests, total_tests, self._coverage_totals)

    def _run_python_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        python_cmd = self._resolve_python()
        pytest_cmd = [*python_cmd, "-m", "pytest"]
        env = self._build_python_test_env()
        coverage_result = self._run_coverage_tests(python_cmd, env, targets)
        if coverage_result is not None:
            return coverage_result
        pytest_result = self._run_plain_pytest(pytest_cmd, env, targets)
        if pytest_result is not None:
            return pytest_result
        return self._run_unittest(env, targets, python_cmd)

    def _resolve_python(self) -> list[str]:
        """Resolve the interpreter used for every Python test-related module."""

        configured = self.get_config("test").get("python")
        if configured:
            return [str(configured)]

        candidates = (
            self.project_root / ".venv" / "bin" / "python",
            self.project_root / ".venv" / "Scripts" / "python.exe",
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
        source_paths = [str(d) for d in get_source_dirs(self.project_root, self.config)]
        if source_paths:
            env["PYTHONPATH"] = ":".join([*source_paths, env.get("PYTHONPATH", "")])
        if env.get("WSL_DISTRO_NAME") and Path("/tmp").is_dir():
            for key in ("TMPDIR", "TMP", "TEMP"):
                env[key] = "/tmp"
        return env

    def _run_coverage_tests(
        self,
        python_cmd: list[str],
        env: dict[str, str],
        targets: list[InspectionTarget],
    ) -> tuple[int, int, bool] | None:
        cov_cmd = self._find_coverage_cmd(python_cmd)
        if cov_cmd is None:
            return None
        cov_dir = self.project_root / "build" / "coverage"
        cov_dir.mkdir(parents=True, exist_ok=True)
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        with contextlib.suppress(OSError):
            (cov_dir / ".coverage").unlink()
        cov_env = dict(env)
        cov_env["COVERAGE_FILE"] = str(cov_dir / ".coverage")
        cov_run_cmd = self._build_coverage_run_cmd(cov_cmd)
        result = run_process(cov_run_cmd, cwd=self.project_root, env=cov_env)
        self._record_tool("coverage pytest", cov_run_cmd, result)
        if result.timed_out:
            self._record_tool_error("Coverage test run timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Coverage test output was truncated")
            return 0, 0, False
        if result.returncode < 0:
            self._record_tool_error("Coverage test process terminated before reporting results")
            return 0, 0, False
        if self._module_unavailable(result, "pytest"):
            return None
        parsed = self._parse_pytest_result(result, targets)
        if self._tool_errors or parsed[1] == 0 or result.returncode not in (0, 1):
            return parsed
        self._generate_coverage_json(cov_cmd, cov_dir, cov_env)
        return parsed

    def _build_coverage_run_cmd(self, cov_cmd: list[str]) -> list[str]:
        command = [*cov_cmd, "run", "--branch"]
        rel_dirs = [
            str(d.relative_to(self.project_root))
            for d in get_source_dirs(self.project_root, self.config)
        ]
        if rel_dirs:
            command.append(f"--source={','.join(rel_dirs)}")
        return [
            *command,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-v",
            "tests",
        ]

    def _generate_coverage_json(
        self, cov_cmd: list[str], cov_dir: Path, cov_env: dict[str, str]
    ) -> None:
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        command = [*cov_cmd, "json", "-o", str(json_path)]
        result = run_process(command, cwd=self.project_root, env=cov_env)
        self._record_tool("coverage json", command, result)
        self._coverage_data = None
        self._coverage_measured = False
        if result.timed_out:
            self._record_tool_error("Coverage JSON generation timed out")
        elif result.truncated:
            self._record_tool_error("Coverage JSON output was truncated")
        elif result.returncode == -1:
            self._record_tool_error("Coverage JSON executable was unavailable")
        elif result.returncode != 0:
            self._record_tool_error(
                f"Coverage JSON generation failed with exit code {result.returncode}"
            )
        else:
            expected_files = {
                str(path.relative_to(self.project_root))
                for path in get_all_python_sources(self.project_root, self.config)
            }
            self._coverage_data = self._parse_coverage_json(json_path, expected_files)
            if self._coverage_data is None:
                self._coverage_errors.append("Python coverage JSON was missing or malformed")
            else:
                self._coverage_measured = True

    def _run_plain_pytest(
        self,
        pytest_cmd: list[str],
        env: dict[str, str],
        targets: list[InspectionTarget],
    ) -> tuple[int, int, bool] | None:
        command = [
            *pytest_cmd,
            "-o",
            "addopts=",
            "-v",
            "tests",
        ]
        result = run_process(command, cwd=self.project_root, env=env)
        self._record_tool("pytest", command, result)
        if result.timed_out:
            self._record_tool_error("Pytest timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Pytest output was truncated")
            return 0, 0, False
        if result.returncode == -1:
            self._record_tool_error("Pytest executable was unavailable")
            return 0, 0, False
        if result.returncode < 0:
            self._record_tool_error("Pytest process terminated before reporting results")
            return 0, 0, False
        if self._module_unavailable(result, "pytest"):
            return None
        parsed = self._parse_pytest_result(result, targets)
        return parsed

    @staticmethod
    def _module_unavailable(result, module: str) -> bool:
        if result.returncode <= 0 or result.timed_out or result.truncated:
            return False
        lines = [
            line.strip()
            for line in f"{result.stdout}\n{result.stderr}".splitlines()
            if line.strip()
        ]
        if len(lines) != 1:
            return False
        missing = rf"No module named ['\"]?{re.escape(module)}['\"]?"
        interpreter_prefix = (
            rf"(?:python(?:3(?:\.\d+)?)?(?:\.exe)?|"
            rf"(?:[A-Za-z]:[\\/]|/)[^\n]*python(?:3(?:\.\d+)?)?(?:\.exe)?)"
            rf"\s*:\s*{missing}"
        )
        return bool(re.fullmatch(rf"(?:{missing}|{interpreter_prefix})", lines[0]))

    def _parse_pytest_result(
        self, result, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        parsed = self._parse_pytest_stdout(
            result.stdout + ("\n" + result.stderr if result.stderr else ""), targets
        )
        passed, total, has_failure = parsed
        output = result.stdout + "\n" + result.stderr
        collected = re.search(r"\bcollected\s+(\d+)\s+items?\b", output)
        if total == 0 and collected is not None:
            total = int(collected.group(1))
        if result.returncode == 5 or (total == 0 and result.returncode == 0):
            has_failure = True
            if not any(t.target_name == "[Python] Tests" for t in targets):
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name="[Python] Tests",
                        status=EngineStatus.FAIL,
                        message="No tests collected",
                    )
                )
        elif result.returncode not in (0, 1):
            self._record_tool_error(f"Pytest failed with exit code {result.returncode}")
        elif result.returncode == 1 and not has_failure:
            self._record_tool_error("Pytest returned failure without parseable diagnostics")
        elif total == 0:
            has_failure = True
        return passed, total, has_failure

    def _run_unittest(
        self,
        env: dict[str, str],
        targets: list[InspectionTarget],
        python_cmd: list[str] | None = None,
    ) -> tuple[int, int, bool]:
        interpreter = python_cmd or self._resolve_python()
        command = [*interpreter, "-m", "unittest", "discover", "-s", "tests", "-v"]
        result = run_process(command, cwd=self.project_root, env=env)
        self._record_tool("unittest", command, result)
        if result.timed_out:
            self._record_tool_error("Unittest timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Unittest output was truncated")
            return 0, 0, False
        if result.returncode == -1:
            self._record_tool_error("Unittest executable was unavailable")
            return 0, 0, False
        passed, total, has_failure = self._parse_unittest_stdout(result, targets)
        if result.returncode not in (0, 1):
            self._record_tool_error(f"Unittest failed with exit code {result.returncode}")
        elif total == 0:
            has_failure = True
        return passed, total, has_failure

    @staticmethod
    def _parse_unittest_stdout(result, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        passed = 0
        total = 0
        has_failure = False
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if " ... ok" in line:
                total += 1
                passed += 1
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name=line.replace(" ... ok", "").strip(),
                        status=EngineStatus.PASS,
                        message="Unittest passed",
                    )
                )
            elif " ... FAIL" in line or " ... ERROR" in line:
                total += 1
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name=line.split(" ...")[0].strip(),
                        status=EngineStatus.FAIL,
                        message="Unittest assertion failure",
                    )
                )
        return passed, total, has_failure

    def _find_coverage_cmd(self, python_cmd: list[str] | None) -> list[str] | None:
        """Find coverage.py through the exact interpreter used for pytest."""

        interpreter = self._interpreter_from_command(python_cmd)
        candidate = [*interpreter, "-m", "coverage"]
        probe = [*candidate, "--version"]
        result = run_process(probe, cwd=self.project_root)
        self._record_tool("coverage --version", probe, result)
        if result.returncode == 0 and not result.timed_out and not result.truncated:
            return candidate
        if result.timed_out:
            self._record_tool_error("Coverage probe timed out")
        elif result.truncated:
            self._record_tool_error("Coverage probe output was truncated")
        elif result.returncode < 0:
            self._record_tool_error("Coverage probe process terminated before reporting results")
        elif not self._module_unavailable(result, "coverage"):
            self._record_tool_error(
                f"Coverage module probe failed with exit code {result.returncode}"
            )
        return None

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

    def _parse_pytest_stdout(
        self, out: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        passed = 0
        total = 0
        has_failure = False
        for line in out.splitlines():
            if "::" in line and ("PASSED" in line or "FAILED" in line or "ERROR" in line):
                total += 1
                parts = line.split()
                tname = parts[0]
                test_file = tname.split("::")[0] if "::" in tname else "tests"
                if "PASSED" in line:
                    passed += 1
                    targets.append(
                        InspectionTarget(
                            file_path=test_file,
                            start_line=1,
                            target_name=tname,
                            status=EngineStatus.PASS,
                            message="Test passed successfully",
                        )
                    )
                else:
                    has_failure = True
                    targets.append(
                        InspectionTarget(
                            file_path=test_file,
                            start_line=1,
                            target_name=tname,
                            status=EngineStatus.FAIL,
                            message="Test assertion failed",
                        )
                    )
        if total == 0:
            passed_match = re.search(r"\b(\d+)\s+passed\b", out)
            failed_match = re.search(r"\b(\d+)\s+(?:failed|errors?)\b", out)
            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            total = passed + failed
            has_failure = failed > 0
        return passed, total, has_failure

    def _parse_coverage_json(
        self, json_path: Path, expected_files: set[str] | None = None
    ) -> dict | None:
        return parse_coverage_json(json_path, self.project_root, expected_files)

    def _run_cpp_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        gxx = shutil.which("g++")
        if not gxx:
            self._record_tool_error("g++ executable was unavailable")
            return 0, 0, False
        gcov_bin = shutil.which("gcov")

        passed = 0
        total = 0
        has_failure = False
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []

        inc_flags = get_all_cpp_includes(self.project_root, self.config)
        src_files = [
            str(f)
            for f in get_all_cpp_sources(self.project_root, self.config)
            if "main.cpp" not in f.name
        ]
        src_rel_set = {str(Path(f).relative_to(self.project_root)) for f in src_files}
        nas_cpp = get_nas_cpp_lib_dir()
        lib_flags = []
        if nas_cpp.exists() and (nas_cpp / "lib").exists():
            lib_flags = [f"-L{nas_cpp / 'lib'}", "-lips_core", f"-Wl,-rpath,{nas_cpp / 'lib'}"]

        build_tmp = self.project_root / "build/tests"
        build_tmp.mkdir(parents=True, exist_ok=True)
        for suffix in ("*.gcno", "*.gcda", "*.gcov"):
            for coverage_file in build_tmp.glob(suffix):
                with contextlib.suppress(OSError):
                    coverage_file.unlink()
        use_coverage = gcov_bin is not None and bool(src_files)
        if not gcov_bin:
            self._coverage_errors.append("gcov executable was unavailable")
        elif not src_files:
            self._coverage_errors.append("C++ source files for gcov were unavailable")

        cpp_tests = list((self.project_root / "tests").rglob("*.cpp"))
        for test_src in cpp_tests:
            total += 1
            case_passed, case_failed = self._run_cpp_test_case(
                gxx,
                inc_flags,
                src_files,
                lib_flags,
                build_tmp,
                test_src,
                use_coverage,
                targets,
            )
            passed += case_passed
            has_failure = has_failure or case_failed

        if use_coverage and gcov_bin:
            self._collect_cpp_coverage(gcov_bin, build_tmp, src_rel_set)

        return passed, total, has_failure

    def _run_cpp_test_case(
        self,
        gxx: str,
        inc_flags: list[str],
        src_files: list[str],
        lib_flags: list[str],
        build_tmp: Path,
        test_src: Path,
        use_coverage: bool,
        targets: list[InspectionTarget],
    ) -> tuple[int, bool]:
        runner_bin = build_tmp / test_src.stem
        relative = str(test_src.relative_to(self.project_root))
        target_name = f"[C++] {test_src.name}"
        if use_coverage:
            ok, objects, compile_error = self._compile_cpp_objects(
                gxx, inc_flags, src_files, str(test_src), build_tmp
            )
            if not ok:
                self._coverage_errors.append("C++ gcov coverage compilation failed")
                tool_error = bool(self._tool_errors)
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=target_name,
                        status=EngineStatus.ERROR if tool_error else EngineStatus.FAIL,
                        message=(
                            "Compiler process terminated before reporting results"
                            if tool_error
                            else f"Compilation Error: {compile_error[:200]}"
                        ),
                    )
                )
                return 0, not tool_error
            compile_cmd = [
                gxx,
                "--coverage",
                "-std=c++17",
                *(str(obj) for obj in objects),
                *lib_flags,
                "-o",
                str(runner_bin),
            ]
            compile_result = run_process(compile_cmd, cwd=build_tmp)
        else:
            compile_cmd = [
                gxx,
                "-std=c++17",
                *inc_flags,
                str(test_src),
                *src_files,
                *lib_flags,
                "-o",
                str(runner_bin),
            ]
            compile_result = run_process(compile_cmd, cwd=self.project_root)

        self._record_tool("g++ test compile", compile_cmd, compile_result)
        if compile_result.timed_out or compile_result.truncated:
            self._record_tool_error(f"C++ test compilation incomplete: {test_src.name}")
            self._append_cpp_tool_target(
                relative, target_name, targets, "Compilation output was incomplete"
            )
            return 0, False
        if compile_result.returncode < 0:
            self._record_tool_error(
                f"C++ test compilation terminated before reporting results: {test_src.name}"
            )
            self._append_cpp_tool_target(
                relative,
                target_name,
                targets,
                "Compiler process terminated before reporting results",
            )
            return 0, False
        if compile_result.returncode != 0:
            if use_coverage:
                self._coverage_errors.append("C++ gcov coverage compilation failed")
            targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name=target_name,
                    status=EngineStatus.FAIL,
                    message=f"Compilation Error: {compile_result.stderr[:200]}",
                )
            )
            return 0, True

        run_cmd = [str(runner_bin)]
        run_result = run_process(run_cmd, cwd=build_tmp if use_coverage else self.project_root)
        self._record_tool("C++ test", run_cmd, run_result)
        if run_result.timed_out or run_result.truncated:
            self._record_tool_error(f"C++ test execution incomplete: {test_src.name}")
            self._append_cpp_tool_target(
                relative, target_name, targets, "Execution output was incomplete"
            )
            return 0, False
        if run_result.returncode < 0:
            self._record_tool_error(
                f"C++ test process terminated before reporting results: {test_src.name}"
            )
            self._append_cpp_tool_target(
                relative, target_name, targets, "Test process terminated before reporting results"
            )
            return 0, False
        if run_result.returncode == 0:
            targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name=target_name,
                    status=EngineStatus.PASS,
                    message="C++ Test Passed",
                )
            )
            return 1, False
        targets.append(
            InspectionTarget(
                file_path=relative,
                start_line=1,
                target_name=target_name,
                status=EngineStatus.FAIL,
                message=f"Execution Failed: {run_result.stdout or run_result.stderr}",
            )
        )
        return 0, True

    @staticmethod
    def _append_cpp_tool_target(
        relative: str,
        target_name: str,
        targets: list[InspectionTarget],
        message: str,
    ) -> None:
        targets.append(
            InspectionTarget(
                file_path=relative,
                start_line=1,
                target_name=target_name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    def _collect_cpp_coverage(self, gcov_bin: str, build_tmp: Path, source_files: set[str]) -> None:
        gcno_files = sorted(str(p) for p in build_tmp.glob("*.gcno"))
        if gcno_files:
            gcov_cmd = [gcov_bin, "-b", "-p", "-o", ".", *gcno_files]
            gcov_result = run_process(gcov_cmd, cwd=build_tmp)
            self._record_tool("gcov", gcov_cmd, gcov_result)
            if gcov_result.timed_out or gcov_result.truncated:
                self._record_tool_error("gcov output was incomplete")
            elif gcov_result.returncode != 0:
                self._record_tool_error(f"gcov failed with exit code {gcov_result.returncode}")
        else:
            self._coverage_errors.append("C++ gcov data files were unavailable")
        self._cpp_coverage_rows = self._parse_gcov_dir(build_tmp, source_files)
        self._cpp_function_rows = self._parse_gcov_functions(build_tmp, source_files)
        if not self._cpp_coverage_rows:
            self._coverage_errors.append("C++ gcov coverage output was missing or malformed")
        else:
            self._coverage_measured = True

    def _compile_cpp_objects(
        self,
        gxx: str,
        inc_flags: list[str],
        src_files: list[str],
        test_src: str,
        build_tmp: Path,
    ) -> tuple[bool, list[Path], str]:
        objs: list[Path] = []
        for idx, src_abs in enumerate([*src_files, test_src]):
            obj = build_tmp / f"obj_{idx}.o"
            compile_cmd = [
                gxx,
                "--coverage",
                "-std=c++17",
                *inc_flags,
                "-c",
                src_abs,
                "-o",
                str(obj),
            ]
            compile_result = run_process(compile_cmd, cwd=self.project_root)
            self._record_tool("g++ coverage compile", compile_cmd, compile_result)
            c_code = compile_result.returncode
            c_err = compile_result.stderr
            if compile_result.timed_out or compile_result.truncated:
                self._record_tool_error(f"C++ coverage compilation incomplete: {src_abs}")
                return False, objs, c_err
            if c_code < 0:
                self._record_tool_error(
                    f"C++ coverage compiler terminated before reporting results: {src_abs}"
                )
                return False, objs, c_err
            if c_code != 0:
                return False, objs, c_err
            objs.append(obj)
        return True, objs, ""

    def _parse_gcov_dir(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        return parse_gcov_dir(cov_dir, source_files, self.project_root)

    def _compute_python_function_coverage(self, cov_data: dict) -> list[dict]:
        return compute_python_function_coverage(cov_data, self.project_root, self.config)

    def _parse_gcov_functions(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        return parse_gcov_functions(cov_dir, source_files, self.project_root)

    def _build_coverage_summary(self) -> None:
        files, totals, source = build_coverage_summary(
            self._coverage_data, getattr(self, "_cpp_coverage_rows", [])
        )
        self._coverage_files = files
        self._coverage_totals = totals
        self._coverage_source = source
        missing_python = self._python_test_attempted and not self._coverage_data
        missing_cpp = self._cpp_test_attempted and not self._cpp_coverage_rows
        if source != "estimated" and (missing_python or missing_cpp):
            self._coverage_source = f"{source} (partial)"

    def _measure_coverage(
        self, proj_type: str, has_test_failures: bool
    ) -> tuple[float, float, list[InspectionTarget]]:
        """Calculates branch coverage, function coverage, and per-module coverage rows."""
        missed_targets: list[InspectionTarget] = []
        py_sources = get_all_python_sources(self.project_root, self.config)
        cpp_sources = get_all_cpp_sources(self.project_root, self.config)

        cov_data = self._coverage_data
        self._build_coverage_summary()

        py_func_rows = self._compute_python_function_coverage(cov_data) if cov_data else []
        cpp_func_rows = getattr(self, "_cpp_function_rows", [])
        self._function_rows = [*py_func_rows, *cpp_func_rows]

        total_lines = 0
        for p in py_sources + cpp_sources:
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    total_lines += sum(
                        1
                        for line in f
                        if line.strip() and not line.strip().startswith(("#", "//", "/*"))
                    )
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        if total_lines == 0:
            return 100.0, 100.0, []

        if has_test_failures:
            branch_cov = 45.0
            func_cov = 50.0
        else:
            branch_cov = 85.0
            func_cov = 95.0

        if self._function_rows:
            covered_funcs = sum(1 for r in self._function_rows if r["covered"])
            func_cov = covered_funcs / len(self._function_rows) * 100.0

        totals = self._coverage_totals
        if totals and totals.get("branch_cover") is not None:
            branch_cov = totals["branch_cover"]
        elif cov_data and cov_data.get("branch_cov") is not None:
            branch_cov = cov_data["branch_cov"]

        for row in self._coverage_files:
            if row["stmts"] >= 5 and row["cover"] < 80.0:
                missed_targets.append(
                    InspectionTarget(
                        file_path=row["file"],
                        start_line=1,
                        target_name="Coverage:Module",
                        status=EngineStatus.WARN,
                        message=(
                            f"Module coverage {row['cover']:.1f}% — {row['miss']} missed statements"
                        ),
                        metrics={"coverage": row["cover"]},
                    )
                )

        return branch_cov, func_cov, missed_targets
