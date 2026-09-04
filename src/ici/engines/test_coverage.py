"""Coverage execution, aggregation, and policy helpers for ``TestEngine``."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ici.core.models import EngineStatus, InspectionTarget
from ici.engines.coverage_policy import (
    build_changed_line_status,
    evaluate_coverage_policy,
    parse_changed_lines,
)
from ici.engines.coverage_support import build_coverage_summary
from ici.engines.cpp_text import defines_main
from ici.engines.gcov_json import GcovJsonError


class TestCoverageMixin:
    """Provide coverage execution and measurement while preserving engine hooks."""

    def _run_coverage_tests(
        self,
        python_cmd: list[str],
        env: dict[str, str],
        targets: list[InspectionTarget],
    ) -> tuple[int, int, bool] | None:
        cov_cmd = self._find_coverage_cmd(python_cmd)  # type: ignore[attr-defined]
        if cov_cmd is None:
            return None
        cov_dir = self.project_root / "build" / "coverage"  # type: ignore[attr-defined]
        cov_dir.mkdir(parents=True, exist_ok=True)
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        with contextlib.suppress(OSError):
            (cov_dir / ".coverage").unlink()
        cov_env = dict(env)
        cov_env["COVERAGE_FILE"] = str(cov_dir / ".coverage")
        cov_run_cmd = self._build_coverage_run_cmd(cov_cmd)
        result = self._coverage_process(  # type: ignore[attr-defined]
            cov_run_cmd,
            cwd=self.project_root,  # type: ignore[attr-defined]
            env=cov_env,
        )
        self._record_tool("coverage pytest", cov_run_cmd, result)  # type: ignore[attr-defined]
        self._remember_pytest_output(result)  # type: ignore[attr-defined]
        if result.timed_out:
            self._record_tool_error("Coverage test run timed out")  # type: ignore[attr-defined]
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Coverage test output was truncated")  # type: ignore[attr-defined]
            return 0, 0, False
        if result.returncode < 0:
            self._record_tool_error(  # type: ignore[attr-defined]
                "Coverage test process terminated before reporting results"
            )
            return 0, 0, False
        if self._module_unavailable(result, "pytest"):  # type: ignore[attr-defined]
            return None
        parsed = self._parse_pytest_result(result, targets)  # type: ignore[attr-defined]
        skipped = sum(
            int(target.metrics.get("test_cases", 1))
            for target in targets
            if target.status == EngineStatus.SKIP
        )
        if skipped >= parsed[1] > 0:
            self._coverage_errors.append("Python tests were collected but not executed")  # type: ignore[attr-defined]
            return parsed
        if self._tool_errors or parsed[1] == 0 or result.returncode not in (0, 1):  # type: ignore[attr-defined]
            return parsed
        self._generate_coverage_json(cov_cmd, cov_dir, cov_env)
        return parsed

    def _build_coverage_run_cmd(self, cov_cmd: list[str]) -> list[str]:
        command = [*cov_cmd, "run", "--branch"]
        rel_dirs = [
            str(d.relative_to(self.project_root))  # type: ignore[attr-defined]
            for d in self.project_source_dirs()  # type: ignore[attr-defined]
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
            *self._pytest_duration_args(),  # type: ignore[attr-defined]
            "tests",
        ]

    def _generate_coverage_json(
        self, cov_cmd: list[str], cov_dir: Path, cov_env: dict[str, str]
    ) -> None:
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        command = [*cov_cmd, "json", "-o", str(json_path)]
        result = self._coverage_process(  # type: ignore[attr-defined]
            command,
            cwd=self.project_root,  # type: ignore[attr-defined]
            env=cov_env,
        )
        self._record_tool("coverage json", command, result)  # type: ignore[attr-defined]
        self._coverage_data = None  # type: ignore[attr-defined]
        self._coverage_measured = False  # type: ignore[attr-defined]
        if result.timed_out:
            self._record_tool_error("Coverage JSON generation timed out")  # type: ignore[attr-defined]
        elif result.truncated:
            self._record_tool_error("Coverage JSON output was truncated")  # type: ignore[attr-defined]
        elif result.returncode == -1:
            self._record_tool_error("Coverage JSON executable was unavailable")  # type: ignore[attr-defined]
        elif result.returncode != 0:
            self._record_tool_error(  # type: ignore[attr-defined]
                f"Coverage JSON generation failed with exit code {result.returncode}"
            )
        else:
            expected_files = {
                str(path.relative_to(self.project_root))  # type: ignore[attr-defined]
                for path in self.project_python_sources()  # type: ignore[attr-defined]
            }
            self._coverage_data = self._parse_coverage_json(  # type: ignore[attr-defined]
                json_path, expected_files
            )
            if self._coverage_data is None:  # type: ignore[attr-defined]
                self._coverage_errors.append(  # type: ignore[attr-defined]
                    "Python coverage JSON was missing or malformed"
                )
            else:
                self._coverage_measured = True  # type: ignore[attr-defined]
                self._coverage_provenance["python"] = {  # type: ignore[attr-defined]
                    "format": "coverage.py-json",
                    "expected_sources": len(expected_files),
                    "covered_sources": len(self._coverage_data.get("files", {})),  # type: ignore[attr-defined]
                    "function_geometry": "python-ast",
                    "source_mapping": "project-relative-exact",
                }

    def _collect_cpp_coverage(self, gcov_bin: str, build_tmp: Path, source_files: set[str]) -> None:
        gcno_files = sorted(str(p) for p in build_tmp.glob("*.gcno"))
        if gcno_files:
            probe_cmd = [gcov_bin, "--help"]
            probe = self._coverage_process(probe_cmd, cwd=build_tmp)  # type: ignore[attr-defined]
            self._record_tool("gcov capability", probe_cmd, probe)  # type: ignore[attr-defined]
            json_capability = self._gcov_json_capability(probe)  # type: ignore[attr-defined]
            if json_capability is None:
                self._record_tool_error(  # type: ignore[attr-defined]
                    "gcov JSON capability probe was incomplete"
                )
                self._coverage_errors.append(  # type: ignore[attr-defined]
                    "C++ gcov capability could not be determined"
                )
                return
            format_flags = ["--json-format"] if json_capability else []
            gcov_cmd = [gcov_bin, *format_flags, "-b", "-p", "-o", ".", *gcno_files]
            gcov_result = self._coverage_process(gcov_cmd, cwd=build_tmp)  # type: ignore[attr-defined]
            self._record_tool("gcov", gcov_cmd, gcov_result)  # type: ignore[attr-defined]
            if gcov_result.timed_out or gcov_result.truncated:
                self._record_tool_error("gcov output was incomplete")  # type: ignore[attr-defined]
            elif gcov_result.returncode != 0:
                self._record_tool_error(  # type: ignore[attr-defined]
                    f"gcov failed with exit code {gcov_result.returncode}"
                )
            else:
                self._consume_cpp_coverage(
                    build_tmp,
                    source_files,
                    "gcov-json" if json_capability else "gcov-text",
                )
        else:
            self._coverage_errors.append(  # type: ignore[attr-defined]
                "C++ gcov data files were unavailable"
            )
        if not self._cpp_coverage_rows:  # type: ignore[attr-defined]
            self._coverage_errors.append(  # type: ignore[attr-defined]
                "C++ gcov coverage output was missing or malformed"
            )
        else:
            self._coverage_measured = True  # type: ignore[attr-defined]

    def _consume_cpp_coverage(
        self,
        cov_dir: Path,
        source_files: set[str],
        coverage_format: str,
    ) -> None:
        """Consume exactly the format selected by the capability probe."""

        self._cpp_coverage_rows = []  # type: ignore[attr-defined]
        self._cpp_function_rows = []  # type: ignore[attr-defined]
        if not coverage_format:
            has_json = any(cov_dir.glob("*.gcov.json.gz"))
            has_text = any(cov_dir.glob("*.gcov"))
            if has_json != has_text:
                coverage_format = "gcov-json" if has_json else "gcov-text"
        if coverage_format == "gcov-json":
            try:
                rows, functions, provenance = self._parse_gcov_json_dir(  # type: ignore[attr-defined]
                    cov_dir, source_files
                )
            except GcovJsonError as exc:
                message = f"gcov JSON evidence rejected ({exc.code}): {exc}"
                self._coverage_errors.append(message)  # type: ignore[attr-defined]
                self._record_tool_error(message)  # type: ignore[attr-defined]
                return
            self._cpp_coverage_rows = rows  # type: ignore[attr-defined]
            self._cpp_function_rows = functions  # type: ignore[attr-defined]
            self._coverage_provenance["cpp"] = provenance  # type: ignore[attr-defined]
            return

        if coverage_format != "gcov-text":
            message = f"unknown gcov coverage format: {coverage_format or 'unreported'}"
            self._coverage_errors.append(message)  # type: ignore[attr-defined]
            self._record_tool_error(message)  # type: ignore[attr-defined]
            return
        self._cpp_coverage_rows = self._parse_gcov_dir(  # type: ignore[attr-defined]
            cov_dir, source_files
        )
        self._cpp_function_rows = self._parse_gcov_functions(  # type: ignore[attr-defined]
            cov_dir, source_files
        )
        observed = {row["file"] for row in self._cpp_coverage_rows}  # type: ignore[attr-defined]
        missing = sorted(source_files - observed)
        self._coverage_provenance["cpp"] = {  # type: ignore[attr-defined]
            "format": "gcov-text",
            "expected_sources": len(source_files),
            "covered_sources": len(observed),
            "function_geometry": "line-1-fallback",
            "source_mapping": "legacy-header-suffix",
            "throw_branches_excluded": True,
            "limitations": [
                "function columns and end lines are unavailable",
                "source relocation uses an exact known-suffix fallback",
            ],
        }
        if missing:
            preview = ", ".join(missing[:8])
            suffix = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            self._coverage_errors.append(  # type: ignore[attr-defined]
                f"legacy gcov text evidence is missing {len(missing)} source(s): {preview}{suffix}"
            )

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
            compile_result = self._coverage_process(  # type: ignore[attr-defined]
                compile_cmd,
                cwd=self.project_root,  # type: ignore[attr-defined]
            )
            self._record_tool(  # type: ignore[attr-defined]
                "g++ coverage compile", compile_cmd, compile_result
            )
            c_code = compile_result.returncode
            c_err = compile_result.stderr
            if compile_result.timed_out or compile_result.truncated:
                self._record_tool_error(  # type: ignore[attr-defined]
                    f"C++ coverage compilation incomplete: {src_abs}"
                )
                return False, objs, c_err
            if c_code < 0:
                self._record_tool_error(  # type: ignore[attr-defined]
                    f"C++ coverage compiler terminated before reporting results: {src_abs}"
                )
                return False, objs, c_err
            if c_code != 0:
                return False, objs, c_err
            objs.append(obj)
        return True, objs, ""

    def _build_coverage_summary(self) -> None:
        files, totals, source = build_coverage_summary(
            self._coverage_data,
            getattr(self, "_cpp_coverage_rows", []),  # type: ignore[attr-defined]
        )
        self._coverage_files = files  # type: ignore[attr-defined]
        self._coverage_totals = totals  # type: ignore[attr-defined]
        self._coverage_source = source  # type: ignore[attr-defined]
        missing_python = self._python_test_attempted and not self._coverage_data  # type: ignore[attr-defined]
        missing_cpp = self._cpp_test_attempted and not self._cpp_coverage_rows  # type: ignore[attr-defined]
        if source != "estimated" and (missing_python or missing_cpp):
            self._coverage_source = f"{source} (partial)"  # type: ignore[attr-defined]

    def _measure_coverage(
        self,
        proj_type: str,
        has_test_failures: bool,
    ) -> tuple[float, float, list[InspectionTarget]]:
        """Calculate branch/function coverage and per-module policy targets."""
        policy_targets: list[InspectionTarget] = []
        py_sources = self.project_python_sources()  # type: ignore[attr-defined]
        cpp_sources = self.project_cpp_sources()  # type: ignore[attr-defined]

        cov_data = self._coverage_data  # type: ignore[attr-defined]
        changed_line_status = build_changed_line_status(
            cov_data,
            self._cpp_coverage_rows,  # type: ignore[attr-defined]
        )
        self._build_coverage_summary()

        py_func_rows = (
            self._compute_python_function_coverage(cov_data)  # type: ignore[attr-defined]
            if cov_data
            else []
        )
        cpp_func_rows = getattr(self, "_cpp_function_rows", [])
        self._function_rows = [*py_func_rows, *cpp_func_rows]  # type: ignore[attr-defined]

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

        if self._function_rows:  # type: ignore[attr-defined]
            covered_funcs = sum(1 for r in self._function_rows if r["covered"])  # type: ignore[attr-defined]
            func_cov = covered_funcs / len(self._function_rows) * 100.0  # type: ignore[attr-defined]
        elif self._coverage_measured:  # type: ignore[attr-defined]
            func_cov = 100.0

        totals = self._coverage_totals  # type: ignore[attr-defined]
        if totals and totals.get("branch_cover") is not None:
            branch_cov = totals["branch_cover"]
        elif totals and self._coverage_measured:  # type: ignore[attr-defined]
            branch_cov = 100.0
        elif cov_data and cov_data.get("branch_cov") is not None:
            branch_cov = cov_data["branch_cov"]

        policy = self.get_config("test")  # type: ignore[attr-defined]
        coverage_complete = self._coverage_measured and not self._coverage_failure_messages()  # type: ignore[attr-defined]
        try:
            changed_lines = parse_changed_lines(
                self.project_root,  # type: ignore[attr-defined]
                policy.get("changed_lines", []),
            )
            if coverage_complete:
                policy_targets = evaluate_coverage_policy(
                    policy,
                    self._coverage_files,  # type: ignore[attr-defined]
                    self._function_rows,  # type: ignore[attr-defined]
                    changed_line_status,
                    changed_lines=changed_lines,
                )
        except (OSError, ValueError) as err:
            policy_targets = [
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="Coverage:Policy configuration",
                    status=EngineStatus.ERROR,
                    message=f"Coverage policy could not be evaluated: {err}",
                    metrics={"gated": True},
                )
            ]
        self._record_coverage_scope(py_sources, cpp_sources)
        self._coverage_policy = (  # type: ignore[attr-defined]
            self._coverage_policy_snapshot(policy_targets, branch_cov, policy)
            if coverage_complete
            else {}
        )
        return branch_cov, func_cov, policy_targets

    def _record_coverage_scope(self, py_sources: list[Path], cpp_sources: list[Path]) -> None:
        """Expose why production, test, generated, vendor, and entry sources differ."""

        entry_points = sorted(
            str(path.relative_to(self.project_root))  # type: ignore[attr-defined]
            for path in cpp_sources
            if defines_main(path)
        )
        self._coverage_provenance["scope"] = {  # type: ignore[attr-defined]
            "included_sources": sorted(row["file"] for row in self._coverage_files),  # type: ignore[attr-defined]
            "excluded_entry_points": entry_points,
            "exclusion_rules": [
                {
                    "kind": "tests",
                    "basis": "tests are execution inputs, not production coverage sources",
                },
                {
                    "kind": "generated-vendor-cache",
                    "basis": "bounded project source discovery excludes ignored directories",
                },
                {
                    "kind": "entry-point",
                    "basis": "C++ main definitions are excluded from the test-link coverage set",
                },
            ],
            "python_context": "aggregate-project-suite",
            "cpp_context": "aggregate-test-binaries",
            "discovered_python_sources": len(py_sources),
            "discovered_cpp_sources": len(cpp_sources),
        }

    @staticmethod
    def _coverage_policy_snapshot(
        targets: list[InspectionTarget],
        branch_cov: float,
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Return bounded numeric state suitable for a later v3 baseline comparison."""

        metrics: dict[str, float] = {"branch": round(branch_cov, 1)}
        files: dict[str, float] = {}
        changed_covered = 0
        changed_executable = 0
        for target in targets:
            actual = target.metrics.get("actual")
            if target.target_name == "Coverage:Overall line" and isinstance(actual, (int, float)):
                metrics["line"] = float(actual)
            elif target.target_name == "Coverage:Functions" and isinstance(actual, (int, float)):
                metrics["function"] = float(actual)
            elif target.target_name == "Coverage:File" and isinstance(actual, (int, float)):
                files[target.file_path] = float(actual)
            elif target.target_name == "Coverage:Changed lines":
                changed_covered += int(target.metrics.get("covered_lines", 0))
                changed_executable += int(target.metrics.get("executable_lines", 0))
        if changed_executable:
            metrics["changed_line"] = round(changed_covered / changed_executable * 100.0, 1)
        tolerance = cfg.get("max_coverage_regression")
        return {
            "metrics": metrics,
            "files": {path: files[path] for path in sorted(files)},
            "regression_tolerance": float(tolerance) if tolerance is not None else None,
        }
