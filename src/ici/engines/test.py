"""3. Unit Test Execution, Coverage Measurement & TEM Scoring Engine."""

import ast
import contextlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ici.core.env import find_uv, get_nas_cpp_lib_dir
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
        self._tool_evidence: list[ToolEvidence] = []

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        has_failure = False
        self._tool_errors = []
        self._tool_evidence = []
        self._coverage_data = None
        self._coverage_files = []
        self._coverage_totals = None
        self._coverage_source = "estimated"
        self._function_rows = []
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []

        passed_tests = 0
        total_tests = 0

        # 1. Run Python Tests
        if proj_type in ("python", "hybrid") or (self.project_root / "tests").exists():
            py_tests = (
                list((self.project_root / "tests").rglob("test_*.py"))
                if (self.project_root / "tests").exists()
                else []
            )
            if py_tests:
                p_passed, p_total, p_fail = self._run_python_tests(targets)
                passed_tests += p_passed
                total_tests += p_total
                if p_fail:
                    has_failure = True

        # 2. Run C++ Tests
        if proj_type in ("cpp", "hybrid") or (self.project_root / "tests").exists():
            cpp_tests = (
                list((self.project_root / "tests").rglob("*.cpp"))
                if (self.project_root / "tests").exists()
                else []
            )
            if cpp_tests:
                c_passed, c_total, c_fail = self._run_cpp_tests(targets)
                passed_tests += c_passed
                total_tests += c_total
                if c_fail:
                    has_failure = True

        # 3. Calculate Coverage & TEM Score
        branch_cov, func_cov, missed_targets = self._measure_coverage(proj_type, has_failure)
        targets.extend(missed_targets)
        tem_info = self._calc_tem(branch_cov, func_cov, passed_tests, total_tests)
        tem_score = tem_info["tem_score"]

        cfg = self.get_config("test")
        mode = cfg.get("mode", "pass_fail")
        min_tem = cfg.get("min_tem_score", 4.0)
        min_branch = cfg.get("min_branch_cov", 80.0)
        min_func = cfg.get("min_func_cov", 90.0)

        has_warn = False
        if not has_failure and (
            tem_score < min_tem or branch_cov < min_branch or func_cov < min_func
        ):
            has_warn = True

        # Group into test_suites
        suite_map: dict[str, dict] = {}
        for t in targets:
            if t.target_name.startswith("Coverage:"):
                continue
            f_path = t.file_path
            if f_path not in suite_map:
                suite_map[f_path] = {
                    "file": f_path,
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                    "tests": [],
                }
            suite_map[f_path]["total"] += 1
            if t.status == EngineStatus.PASS:
                suite_map[f_path]["passed"] += 1
            else:
                suite_map[f_path]["failed"] += 1
            suite_map[f_path]["tests"].append(
                {
                    "name": t.target_name,
                    "status": t.status.value,
                    "message": t.message,
                }
            )
        test_suites = list(suite_map.values())

        duration = time.time() - t0
        overall_status = (
            EngineStatus.ERROR
            if self._tool_errors
            else self.evaluate_status(has_failure, has_warn, mode)
        )
        cov_label = tem_info["cov_label"]
        cov_shown = tem_info["cov_shown"]
        line_cov = tem_info["line_coverage"]
        pass_rate = tem_info["pass_rate"]
        summary = (
            "; ".join(self._tool_errors[:3])
            if self._tool_errors
            else (
                f"{passed_tests}/{total_tests} Tests Passed | "
                f"{cov_label}: {cov_shown:.1f}%{tem_info['cov_suffix']}, Func: {func_cov:.1f}% "
                f"-> TEM: {tem_score:.2f} / 5.0"
            )
        )

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
            evidence=EvidenceState.NOT_RUN if self._tool_errors else EvidenceState.MEASURED,
            tool_evidence=self._tool_evidence,
        )

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
        """Enterprise TEM 5.0 formula.

        Line 측정 가능: min(LineCov, 80) / 80 * FuncCov * PassRate * 5
        Branch만 측정 가능: min(BranchCov * 5/4, 80) / 80 * FuncCov * PassRate * 5
        """
        pass_rate = (passed_tests / total_tests) if total_tests > 0 else 0.0
        totals = self._coverage_totals
        line_cov = totals.get("cover") if totals else None
        real_branch = totals.get("branch_cover") if totals else None

        if line_cov is not None:
            cov_factor = min(80.0, line_cov) / 80.0
            cov_label = "Line"
            cov_shown = line_cov
        elif real_branch is not None:
            cov_factor = min(80.0, real_branch * 1.25) / 80.0
            cov_label = "Branch"
            cov_shown = real_branch
        else:
            cov_factor = min(80.0, branch_cov) / 80.0
            cov_label = "Line"
            cov_shown = branch_cov

        tem_score = round(cov_factor * (func_cov / 100.0) * pass_rate * 5.0, 2)
        return {
            "tem_score": max(0.0, min(5.0, tem_score)),
            "cov_label": cov_label,
            "cov_shown": cov_shown,
            "line_coverage": line_cov,
            "pass_rate": round(pass_rate, 4),
            "cov_suffix": " (est)" if line_cov is None and real_branch is None else "",
        }

    def _run_python_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        pytest_cmd: list[str] | None = None
        venv_pytest = self.project_root / ".venv/bin/pytest"
        which_pytest = shutil.which("pytest")
        if venv_pytest.exists():
            pytest_cmd = [str(venv_pytest)]
        elif which_pytest:
            pytest_cmd = [which_pytest]
        elif find_uv() and (self.project_root / "pyproject.toml").exists():
            pytest_cmd = ["uv", "run", "pytest"]
        else:
            pytest_cmd = [sys.executable, "-m", "pytest"]

        env = os.environ.copy()
        source_paths = [str(d) for d in get_source_dirs(self.project_root, self.config)]
        if source_paths:
            env["PYTHONPATH"] = ":".join([*source_paths, env.get("PYTHONPATH", "")])

        cov_cmd = self._find_coverage_cmd(pytest_cmd)
        if cov_cmd:
            cov_dir = self.project_root / "build" / "coverage"
            cov_dir.mkdir(parents=True, exist_ok=True)
            cov_env = dict(env)
            cov_env["COVERAGE_FILE"] = str(cov_dir / ".coverage")
            cov_run_cmd = [*cov_cmd, "run", "--branch"]
            rel_dirs = [
                str(d.relative_to(self.project_root))
                for d in get_source_dirs(self.project_root, self.config)
            ]
            if rel_dirs:
                cov_run_cmd.append(f"--source={','.join(rel_dirs)}")
            cov_run_cmd += ["-m", "pytest", "-o", "addopts=", "-v", "tests"]
            result = run_process(cov_run_cmd, cwd=self.project_root, env=cov_env)
            self._record_tool("coverage pytest", cov_run_cmd, result)
            if result.timed_out:
                self._record_tool_error("Coverage test run timed out")
                return 0, 0, False
            if result.truncated:
                self._record_tool_error("Coverage test output was truncated")
                return 0, 0, False
            out = result.stdout

            json_path = cov_dir / "coverage.json"
            with contextlib.suppress(OSError):
                json_path.unlink()
            coverage_json_cmd = [*cov_cmd, "json", "-o", str(json_path)]
            coverage_json_result = run_process(
                coverage_json_cmd, cwd=self.project_root, env=cov_env
            )
            self._record_tool("coverage json", coverage_json_cmd, coverage_json_result)
            self._coverage_data = None
            if coverage_json_result.timed_out:
                self._record_tool_error("Coverage JSON generation timed out")
            elif coverage_json_result.truncated:
                self._record_tool_error("Coverage JSON output was truncated")
            elif coverage_json_result.returncode != 0:
                self._record_tool_error(
                    f"Coverage JSON generation failed with exit code {coverage_json_result.returncode}"
                )
            else:
                self._coverage_data = self._parse_coverage_json(json_path)
                if self._coverage_data is None:
                    self._record_tool_error("Coverage JSON was missing or incomplete")

            passed, total, has_failure = self._parse_pytest_stdout(out, targets)
            if result.returncode not in (0, 1):
                self._record_tool_error(f"Pytest failed with exit code {result.returncode}")
            elif result.returncode == 1 and not has_failure:
                self._record_tool_error("Pytest returned failure without parseable diagnostics")
            elif total == 0:
                self._record_tool_error("Pytest produced no parseable test results")
            if self._tool_errors:
                return passed, total, has_failure
            if total > 0 or self._coverage_data:
                return passed, total, has_failure

        if pytest_cmd:
            result = run_process(
                [*pytest_cmd, "-o", "addopts=", "-v", "tests"], cwd=self.project_root, env=env
            )
            self._record_tool("pytest", [*pytest_cmd, "-o", "addopts=", "-v", "tests"], result)
            if result.timed_out:
                self._record_tool_error("Pytest timed out")
                return 0, 0, False
            if result.truncated:
                self._record_tool_error("Pytest output was truncated")
                return 0, 0, False
            out = result.stdout
            passed, total, has_failure = self._parse_pytest_stdout(out, targets)
            if result.returncode not in (0, 1):
                self._record_tool_error(f"Pytest failed with exit code {result.returncode}")
            elif result.returncode == 1 and not has_failure:
                self._record_tool_error("Pytest returned failure without parseable diagnostics")
            elif total == 0:
                self._record_tool_error("Pytest produced no parseable test results")
            if total > 0:
                return passed, total, has_failure

        # Unittest fallback
        passed = 0
        total = 0
        has_failure = False
        unittest_cmd = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
        result = run_process(
            unittest_cmd,
            cwd=self.project_root,
            env=env,
        )
        self._record_tool("unittest", unittest_cmd, result)
        if result.timed_out:
            self._record_tool_error("Unittest timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Unittest output was truncated")
            return 0, 0, False
        out = result.stdout
        err = result.stderr
        for line in (out + "\n" + err).splitlines():
            if " ... ok" in line:
                total += 1
                passed += 1
                tname = line.replace(" ... ok", "").strip()
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name=tname,
                        status=EngineStatus.PASS,
                        message="Unittest passed",
                    )
                )
            elif " ... FAIL" in line or " ... ERROR" in line:
                total += 1
                has_failure = True
                tname = line.split(" ...")[0].strip()
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name=tname,
                        status=EngineStatus.FAIL,
                        message="Unittest assertion failure",
                    )
                )

        if result.returncode not in (0, 1):
            self._record_tool_error(f"Unittest failed with exit code {result.returncode}")
        elif total == 0:
            self._record_tool_error("Unittest produced no parseable test results")

        return passed, total, has_failure

    def _find_coverage_cmd(self, pytest_cmd: list[str] | None) -> list[str] | None:
        """Finds a working coverage runner, preferring the same interpreter as pytest."""
        venv_cov = self.project_root / ".venv/bin/coverage"
        if venv_cov.exists():
            return [str(venv_cov)]
        which_cov = shutil.which("coverage")
        if which_cov:
            return [which_cov]

        candidates: list[list[str]] = []
        venv_python = self.project_root / ".venv/bin/python"
        if venv_python.exists():
            candidates.append([str(venv_python), "-m", "coverage"])
        if pytest_cmd and pytest_cmd[0].endswith("/pytest"):
            pytest_python = str(Path(pytest_cmd[0]).parent / "python")
            if pytest_python not in [c[0] for c in candidates]:
                candidates.append([pytest_python, "-m", "coverage"])
        if shutil.which("python3"):
            candidates.append(["python3", "-m", "coverage"])

        for cand in candidates:
            result = run_process([*cand, "--version"], cwd=self.project_root)
            if result.returncode == 0 and not result.timed_out and not result.truncated:
                return cand

        uv = find_uv()
        if uv and (self.project_root / "pyproject.toml").exists():
            try:
                content = (self.project_root / "pyproject.toml").read_text(encoding="utf-8")
            except OSError:
                content = ""
            if "coverage" in content:
                return ["uv", "run", "coverage"]
        return None

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
        return passed, total, has_failure

    def _parse_coverage_json(self, json_path) -> dict | None:
        """Parses coverage.json into per-file line/branch data (full line lists kept)."""
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        if not isinstance(data, dict):
            return None
        files = data.get("files")
        totals = data.get("totals")
        required_total_keys = (
            "covered_lines",
            "num_statements",
            "missing_lines",
            "num_branches",
            "covered_branches",
        )
        if (
            not isinstance(files, dict)
            or not files
            or not isinstance(totals, dict)
            or any(key not in totals for key in required_total_keys)
        ):
            return None

        def parse_counts(values: dict) -> dict[str, int] | None:
            parsed: dict[str, int] = {}
            for key in required_total_keys:
                value = values.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return None
                parsed[key] = value
            return parsed

        parsed_totals = parse_counts(totals)
        if parsed_totals is None:
            return None

        file_data: dict[str, dict] = {}
        for fname, finfo in files.items():
            if not isinstance(fname, str) or not isinstance(finfo, dict):
                return None
            executed_lines = finfo.get("executed_lines")
            missing_lines = finfo.get("missing_lines")
            summary = finfo.get("summary")
            if (
                not isinstance(executed_lines, list)
                or not isinstance(missing_lines, list)
                or not isinstance(summary, dict)
            ):
                return None
            parsed_summary = parse_counts(summary)
            if parsed_summary is None:
                return None
            try:
                parsed_executed = [int(x) for x in executed_lines]
                parsed_missing = [int(x) for x in missing_lines]
            except (TypeError, ValueError):
                return None
            rel = fname
            with contextlib.suppress(ValueError):
                rel = str(Path(fname).relative_to(self.project_root))
            file_data[rel] = {
                "executed_lines": parsed_executed,
                "missing_lines": parsed_missing,
                "summary": parsed_summary,
            }

        tnb = parsed_totals["num_branches"]
        tcb = parsed_totals["covered_branches"]
        tstmts = parsed_totals["num_statements"]
        tcovered = parsed_totals["covered_lines"]
        tmiss = parsed_totals["missing_lines"]
        tline = round(tcovered / tstmts * 100.0, 1) if tstmts else None

        return {
            "files": file_data,
            "branch_cov": round(tcb / tnb * 100.0, 1) if tnb else None,
            "line_cov": tline,
            "totals": {
                "stmts": tstmts,
                "miss": tmiss,
                "cover": tline,
                "branch_cover": round(tcb / tnb * 100.0, 1) if tnb else None,
            },
        }

    def _run_cpp_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        gxx = shutil.which("g++")
        if not gxx:
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
        use_coverage = gcov_bin is not None and bool(src_files)

        cpp_tests = list((self.project_root / "tests").rglob("*.cpp"))
        for test_src in cpp_tests:
            total += 1
            runner_bin = build_tmp / test_src.stem
            rel_p = str(test_src.relative_to(self.project_root))

            if use_coverage:
                ok, objs, c_err = self._compile_cpp_objects(
                    gxx, inc_flags, src_files, str(test_src), build_tmp
                )
                if not ok:
                    has_failure = True
                    targets.append(
                        InspectionTarget(
                            file_path=rel_p,
                            start_line=1,
                            target_name=f"[C++] {test_src.name}",
                            status=EngineStatus.FAIL,
                            message=f"Compilation Error: {c_err[:200]}",
                        )
                    )
                    continue
                compile_cmd = [
                    gxx,
                    "--coverage",
                    "-std=c++17",
                    *(str(o) for o in objs),
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

            c_code = compile_result.returncode
            c_err = compile_result.stderr

            if compile_result.timed_out or compile_result.truncated:
                self._record_tool_error(f"C++ test compilation incomplete: {test_src.name}")
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name=f"[C++] {test_src.name}",
                        status=EngineStatus.FAIL,
                        message="Compilation output was incomplete",
                    )
                )
                continue
            if c_code != 0:
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name=f"[C++] {test_src.name}",
                        status=EngineStatus.FAIL,
                        message=f"Compilation Error: {c_err[:200]}",
                    )
                )
                continue

            run_cwd = build_tmp if use_coverage else self.project_root
            run_cmd = [str(runner_bin)]
            run_result = run_process(run_cmd, cwd=run_cwd)
            self._record_tool("C++ test", run_cmd, run_result)
            r_code = run_result.returncode
            r_out = run_result.stdout
            r_err = run_result.stderr
            if run_result.timed_out or run_result.truncated:
                self._record_tool_error(f"C++ test execution incomplete: {test_src.name}")
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name=f"[C++] {test_src.name}",
                        status=EngineStatus.FAIL,
                        message="Execution output was incomplete",
                    )
                )
            elif r_code == 0:
                passed += 1
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name=f"[C++] {test_src.name}",
                        status=EngineStatus.PASS,
                        message="C++ Test Passed",
                    )
                )
            else:
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        target_name=f"[C++] {test_src.name}",
                        status=EngineStatus.FAIL,
                        message=f"Execution Failed: {r_out or r_err}",
                    )
                )

        if use_coverage and gcov_bin:
            gcno_files = sorted(str(p) for p in build_tmp.glob("*.gcno"))
            if gcno_files:
                gcov_cmd = [gcov_bin, "-b", "-p", "-o", ".", *gcno_files]
                gcov_result = run_process(gcov_cmd, cwd=build_tmp)
                self._record_tool("gcov", gcov_cmd, gcov_result)
                if gcov_result.timed_out or gcov_result.truncated:
                    self._record_tool_error("gcov output was incomplete")
                elif gcov_result.returncode != 0:
                    self._record_tool_error(f"gcov failed with exit code {gcov_result.returncode}")
            self._cpp_coverage_rows = self._parse_gcov_dir(build_tmp, src_rel_set)
            self._cpp_function_rows = self._parse_gcov_functions(build_tmp, src_rel_set)

        return passed, total, has_failure

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
            if c_code != 0:
                return False, objs, c_err
            objs.append(obj)
        return True, objs, ""

    def _parse_gcov_dir(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        """Parses gcov -b -p output files into per-module coverage rows."""
        rows: list[dict] = []
        for gcov_file in cov_dir.glob("*.gcov"):
            cand = gcov_file.name[:-5].replace("#", "/")
            rel: str | None = None
            if cand in source_files:
                rel = cand
            else:
                try:
                    p = Path(cand)
                    abs_p = p if p.is_absolute() else (self.project_root / p)
                    r = str(abs_p.resolve().relative_to(self.project_root))
                    if r in source_files:
                        rel = r
                except ValueError as err:
                    _ = err
            if rel is None:
                continue

            stmts = 0
            covered = 0
            miss = 0
            nb = 0
            cb = 0
            missing: list[int] = []
            try:
                content = gcov_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for line in content.splitlines():
                if line.startswith("branch"):
                    parts = line.split()
                    nb += 1
                    if "taken" in parts:
                        idx = parts.index("taken")
                        val = parts[idx + 1] if idx + 1 < len(parts) else "0"
                        try:
                            if int(val.rstrip("%")) > 0:
                                cb += 1
                        except ValueError as err:
                            _ = err
                    continue
                if ":" not in line:
                    continue
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                count, lno_str, _ = parts
                count = count.strip()
                lno_str = lno_str.strip()
                if count.startswith("-") or not lno_str.isdigit():
                    continue
                lno = int(lno_str)
                if count.startswith("#"):
                    miss += 1
                    missing.append(lno)
                else:
                    digits = count.rstrip("*")
                    if digits.isdigit() and int(digits) > 0:
                        covered += 1

            stmts = covered + miss
            rows.append(
                {
                    "file": rel,
                    "stmts": stmts,
                    "covered": covered,
                    "miss": miss,
                    "cover": round(covered / stmts * 100.0, 1) if stmts else 100.0,
                    "branch_cover": round(cb / nb * 100.0, 1) if nb else None,
                    "nb": nb,
                    "cb": cb,
                    "missing_lines": missing[:30],
                }
            )
        return rows

    def _compute_python_function_coverage(self, cov_data: dict) -> list[dict]:
        """Function coverage (gcov-style): covered when at least one body line executed."""
        rows: list[dict] = []
        file_map = cov_data.get("files", {})
        for py_file in get_all_python_sources(self.project_root, self.config):
            rel = str(py_file.relative_to(self.project_root))
            finfo = file_map.get(rel)
            if not finfo:
                continue
            executed = set(finfo.get("executed_lines") or [])
            missing = set(finfo.get("missing_lines") or [])
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (SyntaxError, OSError) as err:
                _ = err
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body_start = node.body[0].lineno if node.body else node.lineno
                body_end = node.end_lineno or body_start
                body_lines = set(range(body_start, body_end + 1))
                covered = bool(body_lines & executed)
                missing_body = sorted(body_lines & missing)
                rows.append(
                    {
                        "file": rel,
                        "name": node.name,
                        "start_line": node.lineno,
                        "end_line": body_end,
                        "covered": covered,
                        "missing_lines": missing_body[:30],
                    }
                )
        return rows

    def _parse_gcov_functions(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        """Parses gcov 'function ... called N' lines into function coverage rows."""
        rows: list[dict] = []
        for gcov_file in cov_dir.glob("*.gcov"):
            cand = gcov_file.name[:-5].replace("#", "/")
            rel: str | None = None
            if cand in source_files:
                rel = cand
            else:
                try:
                    p = Path(cand)
                    abs_p = p if p.is_absolute() else (self.project_root / p)
                    r = str(abs_p.resolve().relative_to(self.project_root))
                    if r in source_files:
                        rel = r
                except ValueError as err:
                    _ = err
            if rel is None:
                continue
            try:
                content = gcov_file.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                _ = err
                continue
            for line in content.splitlines():
                if not line.startswith("function "):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                name = parts[1]
                called = 0
                if "called" in parts:
                    idx = parts.index("called")
                    try:
                        called = int(parts[idx + 1])
                    except (ValueError, IndexError):
                        called = 0
                rows.append(
                    {
                        "file": rel,
                        "name": name,
                        "start_line": 1,
                        "end_line": 1,
                        "covered": called > 0,
                        "missing_lines": [],
                    }
                )
        return rows

    def _build_coverage_summary(self) -> None:
        """Merges coverage.py (Python) and gcov (C++) rows into one module table."""
        cov_data = self._coverage_data
        py_rows: list[dict] = []
        if cov_data:
            for rel, finfo in cov_data.get("files", {}).items():
                summary = finfo.get("summary") or {}
                stmts = int(summary.get("num_statements", 0))
                covered = int(summary.get("covered_lines", 0))
                miss = int(summary.get("missing_lines", 0))
                nb = int(summary.get("num_branches", 0))
                cb = int(summary.get("covered_branches", 0))
                py_rows.append(
                    {
                        "file": rel,
                        "stmts": stmts,
                        "covered": covered,
                        "miss": miss,
                        "cover": round(covered / stmts * 100.0, 1) if stmts else 100.0,
                        "branch_cover": round(cb / nb * 100.0, 1) if nb else None,
                        "nb": nb,
                        "cb": cb,
                        "missing_lines": finfo.get("missing_lines", [])[:30],
                    }
                )
        cpp_rows = getattr(self, "_cpp_coverage_rows", [])
        files = [*py_rows, *cpp_rows]
        files.sort(key=lambda r: (r["cover"], r["file"]))
        self._coverage_files = files

        if not files:
            self._coverage_totals = cov_data.get("totals") if cov_data else None
            self._coverage_source = "coverage.py" if cov_data else "estimated"
            return

        stmts = sum(r["stmts"] for r in files)
        covered = sum(r["covered"] for r in files)
        miss = sum(r["miss"] for r in files)
        nb = sum(r.get("nb", 0) for r in files)
        cb = sum(r.get("cb", 0) for r in files)
        self._coverage_totals = {
            "stmts": stmts,
            "miss": miss,
            "cover": round(covered / stmts * 100.0, 1) if stmts else None,
            "branch_cover": round(cb / nb * 100.0, 1) if nb else None,
        }
        sources = []
        if cov_data:
            sources.append("coverage.py")
        if cpp_rows:
            sources.append("gcov")
        self._coverage_source = "/".join(sources)

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
