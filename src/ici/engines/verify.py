"""Unified Verification Suite Orchestrator for ici."""

import time
from collections.abc import Collection
from dataclasses import replace
from pathlib import Path
from typing import Any

from ici.config import get_engine_config, load_config
from ici.core.backend import BACKEND_QMAKE
from ici.core.baseline import BaselineError, build_analysis_metadata, compare_suite_to_baseline
from ici.core.cache import (
    AnalysisCache,
    CacheEntryError,
    build_analysis_cache_key,
    project_source_digest,
)
from ici.core.capabilities import collect_capability_inventory, derive_tool_policy
from ici.core.cmake_context import prepare_cmake_compilation_context
from ici.core.context import (
    AnalysisContext,
    BuildVariant,
    CompilationContext,
    ProjectModel,
    create_analysis_context,
    discover_project_model,
)
from ici.core.models import (
    AnalysisMetadata,
    EngineResult,
    EngineStatus,
    EvidenceState,
    VerificationSuiteResult,
    aggregate_suite_status,
)
from ici.core.path_utils import resolve_project_path
from ici.core.pipeline import (
    ENGINE_DESCRIPTORS,
    AnalysisProfile,
    EngineDescriptor,
    PipelineExecutor,
    apply_analysis_profile,
    descriptors_for_profile,
)
from ici.core.qmake_context import prepare_qmake_compilation_context
from ici.core.support import ENGINE_NAMES, evaluate_support_matrix  # noqa: F401
from ici.core.toolchain import DEFAULT_TOOL_PROBES
from ici.engines.cognitive import CognitiveEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.compile_db import CompileDatabaseEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.complexity import ComplexityEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.cycle import CycleEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.dead import DeadCodeEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.dup import DuplicateEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.exception import ExceptionSafetyEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.line import LineCountEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.lint import LintEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.publish import ReportPublisher
from ici.engines.resource import ResourceEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.sanitize import SanitizeEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.security import SecurityEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.test import TestEngine  # noqa: F401 - dynamic descriptor factory
from ici.engines.type_check import TypeCheckEngine  # noqa: F401 - dynamic descriptor factory
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.issue_view import ConsoleOptions
from ici.reporters.json_rep import save_json_report
from ici.reporters.markdown import (
    emit_github_actions_annotations,
    generate_markdown_report,
    write_github_step_summary,
)


def _prepare_compilation_context(
    project_root: Path,
    config: dict[str, Any],
    project: ProjectModel,
) -> CompilationContext:
    if project.backend == BACKEND_QMAKE:
        return prepare_qmake_compilation_context(project_root, config, project)
    return prepare_cmake_compilation_context(project_root, config, project)


def prepare_analysis_context(
    project_root: Path,
    config: dict[str, Any],
    *,
    engine_names: Collection[str] | None = None,
    requested_variants: tuple[BuildVariant, ...] = (),
    profile: str = "standard",
    probe_all_tools: bool = True,
) -> tuple[ProjectModel, AnalysisContext]:
    """Prepare the immutable project/tool/compilation snapshot shared by engines."""

    project = discover_project_model(project_root, config)
    declared_support = evaluate_support_matrix(
        project_root,
        config,
        project=project,
        engine_names=set(engine_names) if engine_names is not None else None,
    )
    configured_required = {
        str(name) for name in config.get("doctor", {}).get("required_tools", []) or []
    }
    required_by, optional_by = derive_tool_policy(declared_support, configured_required)
    selected_tool_names = set(required_by) | set(optional_by)
    probes = (
        DEFAULT_TOOL_PROBES
        if probe_all_tools
        else tuple(probe for probe in DEFAULT_TOOL_PROBES if probe.name in selected_tool_names)
    )
    capability_inventory = collect_capability_inventory(
        cwd=project_root,
        probes=probes,
        required_by=required_by,
        optional_by=optional_by,
    )
    compilation = _prepare_compilation_context(project_root, config, project)
    analysis_context = create_analysis_context(
        project_root,
        config,
        capability_inventory,
        requested_variants=requested_variants,
        profile=profile,
        project=project,
        compilation=compilation,
    )
    return project, analysis_context


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
        profile: AnalysisProfile | str | None = None,
        use_cache: bool = True,
    ) -> VerificationSuiteResult:
        t0 = time.time()
        effective_config, selected_profile = apply_analysis_profile(self.config, profile)
        descriptors = descriptors_for_profile(
            ENGINE_DESCRIPTORS,
            selected_profile,
            lambda name: bool(get_engine_config(effective_config, name).get("enabled", True)),
        )

        requested_variants = tuple(
            descriptor.build_variant
            for descriptor in descriptors
            if descriptor.build_variant is not None
        )
        project, analysis_context = prepare_analysis_context(
            self.project_root,
            effective_config,
            engine_names={descriptor.name for descriptor in descriptors},
            requested_variants=requested_variants,
            profile=selected_profile.value,
        )
        capability_inventory = analysis_context.capabilities
        cache: AnalysisCache | None = AnalysisCache() if use_cache else None
        source_digest = ""
        if cache is not None:
            try:
                source_digest = project_source_digest(project)
            except CacheEntryError:
                cache = None

        prepared: dict[str, Any] = {}
        for descriptor in descriptors:
            eng_cfg = get_engine_config(effective_config, descriptor.name)
            try:
                engine_cls = globals()[descriptor.factory_name]
                engine_instance = engine_cls(
                    self.project_root,
                    effective_config,
                    analysis_context=analysis_context,
                )
            except Exception as exc:
                prepared[descriptor.name] = EngineResult(
                    engine_name=descriptor.name,
                    status=EngineStatus.ERROR,
                    summary=f"Engine crashed: {type(exc).__name__}: {exc}",
                    required=bool(eng_cfg.get("required", True)),
                    evidence=EvidenceState.NOT_RUN,
                )
            else:
                prepared[descriptor.name] = engine_instance

        def execute(descriptor: EngineDescriptor) -> EngineResult:
            candidate = prepared[descriptor.name]
            if isinstance(candidate, EngineResult):
                return candidate
            eng_cfg = get_engine_config(effective_config, descriptor.name)
            cache_key = None
            if cache is not None:
                cache_key = build_analysis_cache_key(
                    descriptor,
                    analysis_context,
                    source_digest,
                    implementation=candidate,
                )
                cached = cache.load(cache_key, self.project_root)
                if cached is not None:
                    return cached
            try:
                result = candidate.run()
            except Exception as exc:
                return EngineResult(
                    engine_name=descriptor.name,
                    status=EngineStatus.ERROR,
                    summary=f"Engine crashed: {type(exc).__name__}: {exc}",
                    required=bool(eng_cfg.get("required", True)),
                    evidence=EvidenceState.NOT_RUN,
                )
            if cache_key is not None and cache is not None:
                result = replace(result, cache_hit=False, cache_key=cache_key.digest)
                cache.store(cache_key, result, self.project_root)
            return result

        results = PipelineExecutor[EngineResult](descriptors).run(execute)

        tem_score = next(
            (
                result.score
                for result in results
                if result.engine_name == "test" and result.score is not None
            ),
            None,
        )
        suite_status = aggregate_suite_status(results)
        duration = time.time() - t0

        reporting_context = analysis_context
        for result in results:
            for manifest in result.artifact_manifests:
                reporting_context = reporting_context.with_manifest(manifest)

        support_matrix = evaluate_support_matrix(
            self.project_root,
            effective_config,
            results,
            project=project,
        )
        metadata = build_analysis_metadata(effective_config, support_matrix)
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
