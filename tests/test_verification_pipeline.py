"""End-to-end contracts for profile selection and orchestrator scheduling."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import BuildVariant
from ici.core.models import EngineResult, EngineStatus
from ici.core.pipeline import ENGINE_DESCRIPTORS, AnalysisProfile
from ici.engines import verify as verify_module
from ici.engines.verify import VerifyOrchestrator


def _project(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")


def _config(*enabled: str) -> dict[str, Any]:
    selected = set(enabled)
    return {
        "ici": {"profile": "standard"},
        "project": {"source_dirs": ["src"]},
        "engines": {
            descriptor.name: {
                "enabled": descriptor.name in selected,
                "mode": "pass_warn",
            }
            for descriptor in ENGINE_DESCRIPTORS
        },
    }


def _isolate_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verify_module,
        "collect_capability_inventory",
        lambda **_kwargs: CapabilityInventory(),
    )
    monkeypatch.setattr(verify_module, "print_suite_dashboard", lambda *args, **kwargs: None)


def _install_passing_engines(
    monkeypatch: pytest.MonkeyPatch,
    seen_configs: dict[str, list[dict[str, Any]]],
) -> None:
    def engine_type(name: str):
        class PassingEngine:
            def __init__(
                self,
                project_root: Path,
                config: dict[str, Any],
                analysis_context=None,
            ) -> None:
                del project_root, analysis_context
                seen_configs.setdefault(name, []).append(config)

            def run(self) -> EngineResult:
                return EngineResult(name, EngineStatus.PASS, "ok")

        return PassingEngine

    for descriptor in ENGINE_DESCRIPTORS:
        monkeypatch.setattr(verify_module, descriptor.factory_name, engine_type(descriptor.name))


def test_profiles_select_engines_without_changing_shared_rule_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _project(tmp_path)
    _isolate_orchestrator(monkeypatch)
    seen_configs: dict[str, list[dict[str, Any]]] = {}
    _install_passing_engines(monkeypatch, seen_configs)
    all_names = tuple(descriptor.name for descriptor in ENGINE_DESCRIPTORS)
    orchestrator = VerifyOrchestrator(tmp_path, _config(*all_names))

    fast = orchestrator.run_all(profile=AnalysisProfile.FAST)
    standard = orchestrator.run_all(profile=AnalysisProfile.STANDARD)
    deep = orchestrator.run_all(profile=AnalysisProfile.DEEP)

    assert [result.engine_name for result in fast.results] == [
        descriptor.name
        for descriptor in ENGINE_DESCRIPTORS
        if AnalysisProfile.FAST in descriptor.profiles
    ]
    assert [result.engine_name for result in standard.results] == [
        descriptor.name
        for descriptor in ENGINE_DESCRIPTORS
        if AnalysisProfile.STANDARD in descriptor.profiles
    ]
    assert [result.engine_name for result in deep.results] == list(all_names)
    assert fast.analysis_context is not None
    assert standard.analysis_context is not None
    assert deep.analysis_context is not None
    assert fast.analysis_context.profile == "fast"
    assert standard.analysis_context.profile == "standard"
    assert deep.analysis_context.profile == "deep"
    assert fast.analysis_context.requested_variants == ()
    assert standard.analysis_context.requested_variants == (
        BuildVariant.COVERAGE,
        BuildVariant.SANITIZE,
    )
    assert deep.analysis_context.requested_variants == (
        BuildVariant.COVERAGE,
        BuildVariant.SANITIZE,
    )
    line_policies = [config["engines"]["line"] for config in seen_configs["line"]]
    assert line_policies == [line_policies[0]] * 3


def test_orchestrator_parallelizes_only_read_only_engines_and_preserves_result_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _project(tmp_path)
    _isolate_orchestrator(monkeypatch)
    lock = threading.Lock()
    read_barrier = threading.Barrier(2)
    active: set[str] = set()
    read_overlap: set[str] = set()
    timeline: list[tuple[str, str]] = []

    def engine_type(name: str, *, build: bool):
        class ObservedEngine:
            def __init__(self, *args, **kwargs) -> None:
                del args, kwargs

            def run(self) -> EngineResult:
                with lock:
                    if build:
                        assert not active
                    active.add(name)
                    timeline.append(("start", name))
                if build:
                    time.sleep(0.01)
                else:
                    read_barrier.wait(timeout=2)
                    with lock:
                        read_overlap.update(active)
                with lock:
                    active.remove(name)
                    timeline.append(("end", name))
                return EngineResult(name, EngineStatus.PASS, "ok")

        return ObservedEngine

    selected = ("line", "lint", "test", "sanitize")
    for descriptor in ENGINE_DESCRIPTORS:
        if descriptor.name in selected:
            monkeypatch.setattr(
                verify_module,
                descriptor.factory_name,
                engine_type(name=descriptor.name, build=descriptor.build_variant is not None),
            )

    suite = VerifyOrchestrator(tmp_path, _config(*selected)).run_all()

    assert read_overlap == {"line", "lint"}
    assert [result.engine_name for result in suite.results] == list(selected)
    starts = [name for event, name in timeline if event == "start"]
    assert set(starts[:2]) == {"line", "lint"}
    assert starts[2:] == ["test", "sanitize"]
