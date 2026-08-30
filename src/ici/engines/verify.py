"""Unified Verification Suite Orchestrator for ici."""

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ici.config import get_engine_config, load_config
from ici.core.baseline import BaselineError, build_analysis_metadata, compare_suite_to_baseline
from ici.core.capabilities import collect_capability_inventory, derive_tool_policy
from ici.core.context import BuildVariant, create_analysis_context, discover_project_model
from ici.core.models import (
    AnalysisMetadata,
    EngineResult,
    EngineStatus,
    EvidenceState,
    VerificationSuiteResult,
    aggregate_suite_status,
)
from ici.core.path_utils import resolve_project_path
from ici.core.redaction import redact_suite
from ici.core.support import ENGINE_NAMES, evaluate_support_matrix
from ici.engines.cognitive import CognitiveEngine
from ici.engines.complexity import ComplexityEngine
from ici.engines.cycle import CycleEngine
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.line import LineCountEngine
from ici.engines.lint import LintEngine
from ici.engines.publish import ReportPublisher
from ici.engines.resource import ResourceEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.security import SecurityEngine
from ici.engines.test import TestEngine
from ici.engines.type_check import TypeCheckEngine
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.issue_view import ConsoleOptions
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

    def _apply_baseline(
        self,
        suite: VerificationSuiteResult,
        metadata: AnalysisMetadata,
        baseline_path: str | Path | None,
        fail_on_new: bool,
    ) -> None:
        if baseline_path is None:
            return
        comparison = compare_suite_to_baseline(
            suite,
            baseline_path=Path(baseline_path),
            project_root=self.project_root,
            current_metadata=metadata,
            fail_on_new=fail_on_new,
        )
        suite.baseline_comparison = comparison
        if comparison.gate_failed and suite.suite_status not in (
            EngineStatus.FAIL,
            EngineStatus.ERROR,
        ):
            suite.suite_status = EngineStatus.FAIL

    def _write_baseline(
        self,
        suite: VerificationSuiteResult,
        output_path: str | Path | None,
        input_path: str | Path | None,
    ) -> None:
        if output_path is None:
            return
        try:
            baseline_output = resolve_project_path(self.project_root, str(output_path))
        except ValueError as err:
            raise BaselineError(f"unsafe baseline output path: {err}") from err
        if suite.baseline_comparison is not None and suite.baseline_comparison.gate_failed:
            baseline_input = (
                resolve_project_path(self.project_root, str(input_path))
                if input_path is not None
                else None
            )
            if baseline_output == baseline_input:
                raise BaselineError(
                    "refusing to overwrite a baseline that failed the fail-on-new gate"
                )
        baseline_suite = replace(
            suite,
            suite_status=aggregate_suite_status(suite.results),
            baseline_comparison=None,
        )
        try:
            save_json_report(
                baseline_suite,
                baseline_output,
                project_root=self.project_root,
            )
        except OSError as err:
            raise BaselineError(f"could not write baseline {baseline_output}: {err}") from err

    def run_all(
        self,
        report_json: str | None = None,
        report_html: str | None = None,
        github_summary: bool = False,
        publish: bool = False,
        repo_url: str | None = None,
        commit_sha: str | None = None,
        baseline_path: str | Path | None = None,
        fail_on_new: bool = False,
        write_baseline: str | Path | None = None,
        *,
        console_options: ConsoleOptions | None = None,
    ) -> VerificationSuiteResult:
        t0 = time.time()
        results: list[EngineResult] = []

        project = discover_project_model(self.project_root, self.config)
        declared_support = evaluate_support_matrix(
            self.project_root,
            self.config,
            project=project,
        )
        configured_required = {
            str(name) for name in self.config.get("doctor", {}).get("required_tools", []) or []
        }
        required_by, optional_by = derive_tool_policy(declared_support, configured_required)
        capability_inventory = collect_capability_inventory(
            cwd=self.project_root,
            required_by=required_by,
            optional_by=optional_by,
        )
        requested_variants: list[BuildVariant] = []
        if get_engine_config(self.config, "test").get("enabled", True):
            requested_variants.append(BuildVariant.COVERAGE)
        if get_engine_config(self.config, "sanitize").get("enabled", True):
            requested_variants.append(BuildVariant.SANITIZE)
        analysis_context = create_analysis_context(
            self.project_root,
            self.config,
            capability_inventory,
            requested_variants=tuple(requested_variants),
            project=project,
        )

        # Engine definitions mapping name to Engine class
        engine_defs = [
            ("line", LineCountEngine),
            ("lint", LintEngine),
            ("test", TestEngine),
            ("type", TypeCheckEngine),
            ("cognitive", CognitiveEngine),
            ("resource", ResourceEngine),
            ("security", SecurityEngine),
            ("cycle", CycleEngine),
            ("complexity", ComplexityEngine),
            ("sanitize", SanitizeEngine),
            ("dead", DeadCodeEngine),
            ("dup", DuplicateEngine),
            ("exception", ExceptionSafetyEngine),
        ]
        if tuple(name for name, _engine_cls in engine_defs) != ENGINE_NAMES:
            raise RuntimeError("verification engines and support declarations are out of sync")

        tem_score = None
        for name, engine_cls in engine_defs:
            eng_cfg = get_engine_config(self.config, name)
            if not eng_cfg.get("enabled", True):
                continue

            try:
                engine_instance = engine_cls(
                    self.project_root,
                    self.config,
                    analysis_context=analysis_context,
                )
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

        reporting_context = analysis_context
        for result in results:
            for manifest in result.artifact_manifests:
                reporting_context = reporting_context.with_manifest(manifest)

        support_matrix = evaluate_support_matrix(
            self.project_root,
            self.config,
            results,
            project=project,
        )
        metadata = build_analysis_metadata(self.config, support_matrix)
        suite = VerificationSuiteResult(
            suite_status=suite_status,
            results=results,
            duration=duration,
            tem_score=tem_score,
            max_tem_score=5.0,
            support_matrix=support_matrix,
            analysis_metadata=metadata,
            capability_inventory=capability_inventory,
            analysis_context=reporting_context,
        )
        self._apply_baseline(suite, metadata, baseline_path, fail_on_new)
        # All reporters share one sanitized suite. This prevents a secret in an
        # engine diagnostic from leaking through a non-JSON output path.
        suite = redact_suite(suite)

        self._write_baseline(suite, write_baseline, baseline_path)

        # 1. Terminal Console Report
        if console_options is None:
            print_suite_dashboard(suite, self.project_root)
        else:
            print_suite_dashboard(suite, self.project_root, options=console_options)

        # 2. JSON Report if requested
        if report_json:
            save_json_report(suite, Path(report_json), project_root=self.project_root)

        # 3. HTML Report if requested
        if report_html:
            generate_html_report(
                suite,
                Path(report_html),
                project_name=reporting_context.project.name,
                base_dir=self.project_root,
            )

        # 4. Markdown Report & GitHub Actions Summary
        md_content = generate_markdown_report(suite, repo_url=repo_url, commit_sha=commit_sha)
        if github_summary:
            write_github_step_summary(md_content)
            emit_github_actions_annotations(suite)

        # 5. Publish HTML report to GitHub (gh-pages / hub) with sticky PR comment
        if publish:
            html_target = Path(report_html) if report_html else Path("verify_report.html")
            pub_result = ReportPublisher(project_name=reporting_context.project.name).publish(
                html_target, suite
            )
            print(f"[publish] {pub_result.message}")
            if pub_result.comment_url:
                print(f"[publish] PR comment: {pub_result.comment_url}")

        return suite
