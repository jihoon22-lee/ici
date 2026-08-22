"""Unified Verification Suite Orchestrator for ici."""

import time
from pathlib import Path
from typing import Any

from ici.config import get_engine_config, load_config
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    VerificationSuiteResult,
    aggregate_suite_status,
)
from ici.core.project import get_project_name
from ici.engines.cmake_lint import CMakeLintEngine
from ici.engines.complexity import ComplexityEngine
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.file_hygiene import FileHygieneEngine
from ici.engines.line import LineCountEngine
from ici.engines.lint import LintEngine
from ici.engines.publish import ReportPublisher
from ici.engines.pyproject_lint import PyProjectLintEngine
from ici.engines.python_compat import PythonCompatEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine
from ici.engines.toolchain import ToolchainEngine
from ici.engines.type_check import TypeCheckEngine
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import save_json_report
from ici.reporters.markdown import (
    emit_github_actions_annotations,
    generate_markdown_report,
    write_github_step_summary,
)


class VerifyOrchestrator:
    """Orchestrates running the verification suite and delivers reports."""

    def __init__(self, project_root: Path | None = None, config: dict[str, Any] | None = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.config = config or load_config(self.project_root)

    def run_all(
        self,
        report_json: str | None = None,
        report_html: str | None = None,
        github_summary: bool = False,
        publish: bool = False,
        repo_url: str | None = None,
        commit_sha: str | None = None,
    ) -> VerificationSuiteResult:
        t0 = time.time()
        results: list[EngineResult] = []

        # Engine definitions mapping name to Engine class
        engine_defs = [
            ("line", LineCountEngine),
            ("cmake_lint", CMakeLintEngine),
            ("pyproject_lint", PyProjectLintEngine),
            ("file_hygiene", FileHygieneEngine),
            ("lint", LintEngine),
            ("test", TestEngine),
            ("type", TypeCheckEngine),
            ("complexity", ComplexityEngine),
            ("sanitize", SanitizeEngine),
            ("dead", DeadCodeEngine),
            ("dup", DuplicateEngine),
            ("exception", ExceptionSafetyEngine),
            ("toolchain", ToolchainEngine),
            ("python_compat", PythonCompatEngine),
        ]

        tem_score = None
        for name, engine_cls in engine_defs:
            eng_cfg = get_engine_config(self.config, name)
            if not eng_cfg.get("enabled", True):
                continue

            try:
                engine_instance = engine_cls(self.project_root, self.config)
                res = engine_instance.run()
            except Exception as exc:
                res = EngineResult(
                    engine_name=name,
                    status=EngineStatus.ERROR,
                    summary=f"Engine crashed: {type(exc).__name__}: {exc}",
                    required=bool(eng_cfg.get("required", True)),
                    evidence=EvidenceState.NOT_RUN,
                )
            results.append(res)

            if res.engine_name == "test" and res.score is not None:
                tem_score = res.score
        suite_status = aggregate_suite_status(results)
        duration = time.time() - t0

        suite = VerificationSuiteResult(
            suite_status=suite_status,
            results=results,
            duration=duration,
            tem_score=tem_score,
            max_tem_score=5.0,
        )

        # 1. Terminal Console Report
        print_suite_dashboard(suite, self.project_root)

        # 2. JSON Report if requested
        if report_json:
            save_json_report(suite, Path(report_json))

        # 3. HTML Report if requested
        if report_html:
            proj_name = get_project_name(self.project_root)
            generate_html_report(
                suite, Path(report_html), project_name=proj_name, base_dir=self.project_root
            )

        # 4. Markdown Report & GitHub Actions Summary
        md_content = generate_markdown_report(suite, repo_url=repo_url, commit_sha=commit_sha)
        if github_summary:
            write_github_step_summary(md_content)
            emit_github_actions_annotations(suite)

        # 5. Publish HTML report to GitHub (gh-pages / hub) with sticky PR comment
        if publish:
            html_target = Path(report_html) if report_html else Path("verify_report.html")
            proj_name = get_project_name(self.project_root)
            pub_result = ReportPublisher(project_name=proj_name).publish(html_target, suite)
            print(f"[publish] {pub_result.message}")
            if pub_result.comment_url:
                print(f"[publish] PR comment: {pub_result.comment_url}")

        return suite
