"""Contract tests for the declarative verification pipeline scheduler."""

from __future__ import annotations

import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from ici.core.context import BuildVariant
from ici.core.pipeline import (
    ENGINE_DESCRIPTORS,
    AnalysisProfile,
    EngineDescriptor,
    EngineExecution,
    PipelineDefinitionError,
    PipelineExecutor,
    descriptors_for_profile,
    validate_engine_descriptors,
)


def _descriptor(
    name: str,
    *,
    dependencies: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    consumes: tuple[str, ...] = (),
    profiles: frozenset[AnalysisProfile] | None = None,
    execution: EngineExecution = EngineExecution.READ_ONLY,
    build_variant: BuildVariant | None = None,
) -> EngineDescriptor:
    return EngineDescriptor(
        name=name,
        factory_name=f"{name.title()}Engine",
        dependencies=dependencies,
        produces=produces,
        consumes=consumes,
        profiles=profiles if profiles is not None else frozenset(AnalysisProfile),
        execution=execution,
        build_variant=build_variant,
    )


def test_engine_descriptor_is_frozen_and_normalizes_collection_ownership():
    descriptor = EngineDescriptor(
        "lint",
        "LintEngine",
        dependencies=["context"],
        produces=["findings:lint"],
        consumes=["analysis-context"],
        profiles=[AnalysisProfile.FAST],
    )

    assert descriptor.dependencies == ("context",)
    assert descriptor.produces == ("findings:lint",)
    assert descriptor.profiles == frozenset({AnalysisProfile.FAST})
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "other"
    with pytest.raises(FrozenInstanceError):
        descriptor.dependencies += ("other",)


def test_unknown_dependency_is_rejected():
    with pytest.raises(PipelineDefinitionError, match="unknown engine missing"):
        validate_engine_descriptors((_descriptor("consumer", dependencies=("missing",)),))


def test_cyclic_dependencies_are_rejected():
    descriptors = (
        _descriptor("first", dependencies=("second",)),
        _descriptor("second", dependencies=("first",)),
    )

    with pytest.raises(PipelineDefinitionError, match="contains a cycle"):
        validate_engine_descriptors(descriptors)


def test_duplicate_engine_and_artifact_ownership_are_rejected():
    duplicate_names = (_descriptor("same"), _descriptor("same"))
    with pytest.raises(PipelineDefinitionError, match="duplicate engine names"):
        validate_engine_descriptors(duplicate_names)

    duplicate_artifacts = (
        _descriptor("first", produces=("shared-artifact",)),
        _descriptor("second", produces=("shared-artifact",)),
    )
    with pytest.raises(PipelineDefinitionError, match="multiple producers"):
        validate_engine_descriptors(duplicate_artifacts)


def test_consuming_an_unproduced_artifact_is_rejected():
    with pytest.raises(PipelineDefinitionError, match="consumes unproduced artifact report"):
        validate_engine_descriptors((_descriptor("consumer", consumes=("report",)),))


def test_consumed_artifact_requires_an_explicit_dependency_on_its_producer():
    descriptors = (
        _descriptor("producer", produces=("report",)),
        _descriptor("consumer", consumes=("report",)),
    )

    with pytest.raises(PipelineDefinitionError, match="without depending on producer"):
        validate_engine_descriptors(descriptors)


def test_dependency_profile_closure_is_validated():
    descriptors = (
        _descriptor(
            "producer",
            profiles=frozenset({AnalysisProfile.STANDARD, AnalysisProfile.DEEP}),
        ),
        _descriptor(
            "consumer",
            dependencies=("producer",),
            profiles=frozenset({AnalysisProfile.FAST}),
        ),
    )

    with pytest.raises(PipelineDefinitionError, match="unavailable in fast"):
        validate_engine_descriptors(descriptors)


def test_profile_selection_rejects_disabled_dependencies():
    descriptors = (
        _descriptor("producer"),
        _descriptor("consumer", dependencies=("producer",)),
    )

    with pytest.raises(PipelineDefinitionError, match="disabled dependencies in standard"):
        descriptors_for_profile(
            descriptors,
            AnalysisProfile.STANDARD,
            enabled=lambda name: name == "consumer",
        )


def test_builtin_fast_standard_and_deep_profiles_select_expected_registry_entries():
    def enabled(_name: str) -> bool:
        return True

    fast = descriptors_for_profile(ENGINE_DESCRIPTORS, AnalysisProfile.FAST, enabled)
    standard = descriptors_for_profile(ENGINE_DESCRIPTORS, AnalysisProfile.STANDARD, enabled)
    deep = descriptors_for_profile(ENGINE_DESCRIPTORS, AnalysisProfile.DEEP, enabled)

    assert [descriptor.name for descriptor in fast] == [
        "line",
        "lint",
        "compile_db",
        "type",
        "python_compat",
        "resource",
        "security",
        "cycle",
        "complexity",
        "dead",
        "dup",
        "exception",
    ]
    assert [descriptor.name for descriptor in standard] == [
        "line",
        "lint",
        "compile_db",
        "test",
        "type",
        "python_compat",
        "resource",
        "security",
        "cycle",
        "complexity",
        "sanitize",
        "dead",
        "dup",
        "exception",
    ]
    assert [descriptor.name for descriptor in deep] == [
        descriptor.name for descriptor in ENGINE_DESCRIPTORS
    ]


def test_read_only_nodes_are_parallel_but_bounded():
    descriptors = tuple(_descriptor(f"read-{index}") for index in range(6))
    lock = threading.Lock()
    active = 0
    max_active = 0

    def execute(descriptor: EngineDescriptor) -> str:
        nonlocal active, max_active
        assert descriptor.execution is EngineExecution.READ_ONLY
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return descriptor.name

    results = PipelineExecutor(descriptors, max_parallel=2).run(execute)

    assert max_active == 2
    assert set(results) == {descriptor.name for descriptor in descriptors}


def test_build_nodes_never_overlap_read_only_nodes_or_other_build_nodes():
    descriptors = (
        _descriptor("read-a"),
        _descriptor("read-b"),
        _descriptor(
            "build-a",
            execution=EngineExecution.BUILD,
            build_variant=BuildVariant.COVERAGE,
        ),
        _descriptor(
            "build-b",
            execution=EngineExecution.BUILD,
            build_variant=BuildVariant.SANITIZE,
        ),
    )
    lock = threading.Lock()
    active: set[str] = set()
    max_build_active = 0
    timeline: list[tuple[str, str]] = []

    def execute(descriptor: EngineDescriptor) -> str:
        nonlocal max_build_active
        with lock:
            assert not (descriptor.execution is EngineExecution.BUILD and active), (
                f"build overlapped with {sorted(active)}"
            )
            active.add(descriptor.name)
            timeline.append(("start", descriptor.name))
            if descriptor.execution is EngineExecution.BUILD:
                max_build_active = max(
                    max_build_active,
                    sum(1 for name in active if name.startswith("build-")),
                )
        time.sleep(0.01)
        with lock:
            active.remove(descriptor.name)
            timeline.append(("end", descriptor.name))
        return descriptor.name

    results = PipelineExecutor(descriptors, max_parallel=2).run(execute)

    assert max_build_active == 1
    assert results == [descriptor.name for descriptor in descriptors]
    starts = [name for event, name in timeline if event == "start"]
    assert starts[:2] == ["read-a", "read-b"] or set(starts[:2]) == {"read-a", "read-b"}
    assert starts[-2:] == ["build-a", "build-b"]


def test_executor_returns_results_in_registry_order_after_dag_scheduling():
    descriptors = (
        _descriptor("consumer", dependencies=("producer",)),
        _descriptor("independent"),
        _descriptor("producer"),
    )

    results = PipelineExecutor(descriptors, max_parallel=2).run(lambda item: item.name)

    assert results == ["consumer", "independent", "producer"]
