"""Integration contracts for the analysis context ownership boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    ProjectModel,
)
from ici.core.models import EngineResult, EngineStatus, SupportMatrix
from ici.core.support import ENGINE_NAMES
from ici.engines import verify as verify_module
from ici.engines.base import BaseEngine
from ici.engines.verify import VerifyOrchestrator

_IDENTITY = AnalysisIdentity(
    source_commit="a" * 40,
    config_digest="sha256:" + "b" * 64,
    toolchain_digest="sha256:" + "c" * 64,
)


def _enabled_config(*names: str) -> dict[str, Any]:
    enabled = set(names)
    return {"engines": {name: {"enabled": name in enabled} for name in ENGINE_NAMES}}


def _context(root: Path) -> tuple[AnalysisContext, ProjectModel, CapabilityInventory]:
    project = ProjectModel(
        root=root,
        name="context-project",
        version="9.9.9",
        project_type="python",
    )
    inventory = CapabilityInventory()
    context = AnalysisContext(
        project=project,
        capabilities=inventory,
        identity=_IDENTITY,
    )
    return context, project, inventory


def _install_wiring_spies(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    enabled: tuple[str, ...] = ("line", "lint"),
) -> tuple[AnalysisContext, ProjectModel, CapabilityInventory, list[str], list[Any]]:
    context, project, inventory = _context(root)
    events: list[str] = []
    support_projects: list[Any] = []

    def discover_project_model(project_root: Path, config: dict[str, Any]) -> ProjectModel:
        del config
        events.append("discover")
        assert project_root == root.resolve()
        return project

    def collect_capability_inventory(*args: Any, **kwargs: Any) -> CapabilityInventory:
        del args, kwargs
        events.append("capabilities")
        return inventory

    def create_analysis_context(*args: Any, **kwargs: Any) -> AnalysisContext:
        del args, kwargs
        events.append("context")
        return context

    def evaluate_support_matrix(*args: Any, **kwargs: Any) -> SupportMatrix:
        project_arg = kwargs.get("project")
        if project_arg is None:
            project_arg = next(
                (value for value in args if isinstance(value, ProjectModel)),
                None,
            )
        support_projects.append(project_arg)
        events.append("support")
        return SupportMatrix()

    monkeypatch.setattr(
        verify_module, "discover_project_model", discover_project_model, raising=False
    )
    monkeypatch.setattr(
        verify_module,
        "collect_capability_inventory",
        collect_capability_inventory,
    )
    monkeypatch.setattr(
        verify_module, "create_analysis_context", create_analysis_context, raising=False
    )
    monkeypatch.setattr(verify_module, "evaluate_support_matrix", evaluate_support_matrix)
    monkeypatch.setattr(verify_module, "print_suite_dashboard", lambda *args, **kwargs: None)
    return context, project, inventory, events, support_projects


def _fake_engine(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    seen: list[tuple[str, AnalysisContext | None]],
    *,
    crash: bool = False,
) -> None:
    class FakeEngine:
        def __init__(
            self,
            project_root: Path,
            config: dict[str, Any],
            *,
            analysis_context: AnalysisContext | None = None,
        ) -> None:
            del project_root, config
            self.analysis_context = analysis_context
            seen.append((name, analysis_context))

        def run(self) -> EngineResult:
            if crash:
                assert self.analysis_context is not None
                with pytest.raises(FrozenInstanceError):
                    self.analysis_context.project.name = "tampered"
                raise RuntimeError("boom")
            return EngineResult(name, EngineStatus.PASS, "ok")

    class_names = {
        "line": "LineCountEngine",
        "lint": "LintEngine",
    }
    monkeypatch.setattr(verify_module, class_names[name], FakeEngine)


class _ProbeEngine(BaseEngine):
    def run(self) -> EngineResult:
        return self.create_result("probe", EngineStatus.PASS, "ok")


def test_base_engine_retains_the_exact_analysis_context_object(tmp_path: Path):
    marker, _project, _inventory = _context(tmp_path)

    engine = _ProbeEngine(tmp_path, {}, analysis_context=marker)

    assert engine.analysis_context is marker


def test_orchestrator_discovers_once_and_shares_one_context_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    context, project, inventory, events, support_projects = _install_wiring_spies(
        monkeypatch,
        tmp_path,
    )
    seen: list[tuple[str, AnalysisContext | None]] = []
    _fake_engine(monkeypatch, "line", seen)
    _fake_engine(monkeypatch, "lint", seen)

    suite = VerifyOrchestrator(tmp_path, _enabled_config("line", "lint")).run_all()

    assert events.count("discover") == 1
    assert events.count("capabilities") == 1
    assert events.count("context") == 1
    assert events.index("capabilities") < events.index("context")
    assert len(seen) == 2
    assert [name for name, _context in seen] == ["line", "lint"]
    assert all(received is context for _name, received in seen)
    assert suite.analysis_context is context
    assert suite.capability_inventory is inventory
    assert context.capabilities is inventory
    assert support_projects and all(received is project for received in support_projects)

    assert context.identity.source_commit == "a" * 40
    assert context.identity.config_digest == "sha256:" + "b" * 64
    assert context.identity.toolchain_digest == "sha256:" + "c" * 64


def test_reporting_cannot_mutate_the_frozen_shared_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    context, _project, _inventory, _events, _support_projects = _install_wiring_spies(
        monkeypatch,
        tmp_path,
        enabled=("line",),
    )
    seen: list[tuple[str, AnalysisContext | None]] = []
    _fake_engine(monkeypatch, "line", seen)
    observed: list[AnalysisContext] = []

    def mutating_reporter(suite: Any, root: Path, **kwargs: Any) -> None:
        del root, kwargs
        assert suite.analysis_context is context
        observed.append(suite.analysis_context)
        with pytest.raises(FrozenInstanceError):
            suite.analysis_context.project.name = "reporter-tampered"

    monkeypatch.setattr(verify_module, "print_suite_dashboard", mutating_reporter)
    suite = VerifyOrchestrator(tmp_path, _enabled_config("line")).run_all()

    assert observed == [context]
    assert suite.analysis_context is context
    assert context.project.name == "context-project"


def test_engine_crash_leaves_the_shared_context_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    context, _project, _inventory, _events, _support_projects = _install_wiring_spies(
        monkeypatch,
        tmp_path,
        enabled=("line",),
    )
    seen: list[tuple[str, AnalysisContext | None]] = []
    _fake_engine(monkeypatch, "line", seen, crash=True)

    suite = VerifyOrchestrator(tmp_path, _enabled_config("line")).run_all()

    assert seen == [("line", context)]
    assert suite.analysis_context is context
    assert suite.results[0].status is EngineStatus.ERROR
    assert suite.results[0].summary == "Engine crashed: RuntimeError: boom"
    assert context.project.name == "context-project"
    assert context.identity == _IDENTITY


def test_html_and_publish_receive_the_context_project_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    context, project, _inventory, _events, _support_projects = _install_wiring_spies(
        monkeypatch,
        tmp_path,
        enabled=("line",),
    )
    seen: list[tuple[str, AnalysisContext | None]] = []
    _fake_engine(monkeypatch, "line", seen)
    monkeypatch.setattr(
        verify_module, "get_project_name", lambda root: "legacy-name", raising=False
    )
    html_calls: list[tuple[Any, Path, str, Path | None]] = []
    publisher_names: list[str] = []
    published_suites: list[Any] = []

    def generate_html(
        suite: Any,
        output_path: Path,
        project_name: str,
        base_dir: Path | None = None,
    ) -> None:
        html_calls.append((suite, output_path, project_name, base_dir))

    class FakePublisher:
        def __init__(self, *, project_name: str) -> None:
            publisher_names.append(project_name)

        def publish(self, html_path: Path, suite: Any) -> SimpleNamespace:
            del html_path
            published_suites.append(suite)
            return SimpleNamespace(message="published", comment_url=None)

    monkeypatch.setattr(verify_module, "generate_html_report", generate_html)
    monkeypatch.setattr(verify_module, "ReportPublisher", FakePublisher)

    suite = VerifyOrchestrator(tmp_path, _enabled_config("line")).run_all(
        report_html="report.html",
        publish=True,
    )

    assert html_calls and html_calls[0][2] == project.name
    assert html_calls[0][0].analysis_context is context
    assert publisher_names == [project.name]
    assert published_suites and published_suites[0].analysis_context is context
    assert suite.analysis_context is context
