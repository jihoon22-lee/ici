"""Declarative verification pipeline and deterministic bounded scheduler."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from ici.core.context import BuildVariant


class AnalysisProfile(str, Enum):
    """Cost profile selecting engines without changing their rule semantics."""

    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class EngineExecution(str, Enum):
    """Whether an engine only observes the project or owns mutable build state."""

    READ_ONLY = "read-only"
    BUILD = "build"


_CONTEXT_ARTIFACTS = frozenset(
    {
        "analysis-context",
        "capability-inventory",
        "project-model",
    }
)


@dataclass(frozen=True)
class EngineDescriptor:
    """Static scheduling and data-flow contract for one verification engine."""

    name: str
    factory_name: str
    dependencies: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ("analysis-context",)
    profiles: frozenset[AnalysisProfile] = frozenset(AnalysisProfile)
    execution: EngineExecution = EngineExecution.READ_ONLY
    build_variant: BuildVariant | None = None

    def __post_init__(self) -> None:
        for field_name in ("dependencies", "produces", "consumes"):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"{self.name or 'engine'} {field_name} must be non-empty names")
            if len(values) != len(set(values)):
                raise ValueError(f"{self.name or 'engine'} {field_name} contains duplicates")
            object.__setattr__(self, field_name, values)
        profiles = frozenset(
            item if isinstance(item, AnalysisProfile) else AnalysisProfile(item)
            for item in self.profiles
        )
        execution = (
            self.execution
            if isinstance(self.execution, EngineExecution)
            else EngineExecution(self.execution)
        )
        variant = (
            self.build_variant
            if self.build_variant is None or isinstance(self.build_variant, BuildVariant)
            else BuildVariant(self.build_variant)
        )
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "execution", execution)
        object.__setattr__(self, "build_variant", variant)
        if not self.name or not self.factory_name:
            raise ValueError("engine descriptor name and factory_name must be non-empty")
        if not profiles:
            raise ValueError(f"engine {self.name} must support at least one profile")
        if self.name in self.dependencies:
            raise ValueError(f"engine {self.name} cannot depend on itself")
        if execution is EngineExecution.BUILD and variant is None:
            raise ValueError(f"build engine {self.name} must declare a build variant")
        if execution is EngineExecution.READ_ONLY and variant is not None:
            raise ValueError(f"read-only engine {self.name} cannot declare a build variant")


_STANDARD_DEEP = frozenset({AnalysisProfile.STANDARD, AnalysisProfile.DEEP})


ENGINE_DESCRIPTORS = (
    EngineDescriptor("line", "LineCountEngine", produces=("findings:line",)),
    EngineDescriptor("lint", "LintEngine", produces=("findings:lint",)),
    EngineDescriptor(
        "compile_db",
        "CompileDatabaseEngine",
        produces=("findings:compile-db", "compilation-coverage"),
    ),
    EngineDescriptor(
        "test",
        "TestEngine",
        produces=("findings:test", "test-results", "coverage-report", "build:coverage"),
        consumes=("analysis-context", "capability-inventory"),
        profiles=_STANDARD_DEEP,
        execution=EngineExecution.BUILD,
        build_variant=BuildVariant.COVERAGE,
    ),
    EngineDescriptor("type", "TypeCheckEngine", produces=("findings:type",)),
    EngineDescriptor(
        "python_compat",
        "PythonCompatibilityEngine",
        produces=("findings:python-compat", "python-runtime-evidence"),
    ),
    EngineDescriptor(
        "cognitive",
        "CognitiveEngine",
        produces=("findings:cognitive",),
        profiles=frozenset({AnalysisProfile.DEEP}),
    ),
    EngineDescriptor("resource", "ResourceEngine", produces=("findings:resource",)),
    EngineDescriptor("security", "SecurityEngine", produces=("findings:security",)),
    EngineDescriptor("cycle", "CycleEngine", produces=("findings:cycle",)),
    EngineDescriptor("complexity", "ComplexityEngine", produces=("findings:complexity",)),
    EngineDescriptor(
        "sanitize",
        "SanitizeEngine",
        produces=("findings:sanitize", "sanitizer-results", "build:sanitize"),
        consumes=("analysis-context", "capability-inventory"),
        profiles=_STANDARD_DEEP,
        execution=EngineExecution.BUILD,
        build_variant=BuildVariant.SANITIZE,
    ),
    EngineDescriptor(
        "thread_sanitize",
        "ThreadSanitizeEngine",
        produces=(
            "findings:thread-sanitize",
            "thread-sanitizer-results",
            "build:thread-sanitize",
        ),
        consumes=("analysis-context", "capability-inventory"),
        profiles=frozenset({AnalysisProfile.DEEP}),
        execution=EngineExecution.BUILD,
        build_variant=BuildVariant.THREAD_SANITIZE,
    ),
    EngineDescriptor("dead", "DeadCodeEngine", produces=("findings:dead",)),
    EngineDescriptor("dup", "DuplicateEngine", produces=("findings:dup",)),
    EngineDescriptor("exception", "ExceptionSafetyEngine", produces=("findings:exception",)),
    EngineDescriptor(
        "build",
        "BuildEngine",
        produces=("findings:build", "artifact-manifests", "build:release"),
        consumes=("analysis-context", "capability-inventory"),
        profiles=frozenset({AnalysisProfile.DEEP}),
        execution=EngineExecution.BUILD,
        build_variant=BuildVariant.RELEASE,
    ),
    EngineDescriptor(
        "binary_compat",
        "BinaryCompatibilityEngine",
        dependencies=("build",),
        produces=("findings:binary-compat", "elf-facts"),
        consumes=("analysis-context", "capability-inventory", "artifact-manifests"),
        profiles=frozenset({AnalysisProfile.DEEP}),
    ),
    EngineDescriptor(
        "integration",
        "IntegrationEngine",
        dependencies=("build",),
        produces=("findings:integration", "integration-results"),
        consumes=("analysis-context", "artifact-manifests"),
        profiles=frozenset({AnalysisProfile.DEEP}),
        execution=EngineExecution.BUILD,
        build_variant=BuildVariant.RELEASE,
    ),
)


class PipelineDefinitionError(ValueError):
    """Raised when engine descriptors do not form a valid executable DAG."""


def validate_engine_descriptors(
    descriptors: Iterable[EngineDescriptor],
) -> tuple[EngineDescriptor, ...]:
    """Validate names, artifact ownership, profile closure, and acyclicity."""

    ordered = tuple(descriptors)
    if not ordered:
        raise PipelineDefinitionError("verification pipeline must contain at least one engine")
    by_name = {descriptor.name: descriptor for descriptor in ordered}
    if len(by_name) != len(ordered):
        raise PipelineDefinitionError("verification pipeline contains duplicate engine names")

    producers: dict[str, str] = {}
    for descriptor in ordered:
        for dependency in descriptor.dependencies:
            if dependency not in by_name:
                raise PipelineDefinitionError(
                    f"engine {descriptor.name} depends on unknown engine {dependency}"
                )
            missing_profiles = descriptor.profiles - by_name[dependency].profiles
            if missing_profiles:
                profiles = ", ".join(sorted(item.value for item in missing_profiles))
                raise PipelineDefinitionError(
                    f"engine {descriptor.name} dependency {dependency} is unavailable in {profiles}"
                )
        for artifact in descriptor.produces:
            previous = producers.get(artifact)
            if previous is not None:
                raise PipelineDefinitionError(
                    f"artifact {artifact} has multiple producers: {previous}, {descriptor.name}"
                )
            producers[artifact] = descriptor.name

    _topological_layers(ordered)
    dependencies = _transitive_dependencies(by_name)
    for descriptor in ordered:
        for artifact in descriptor.consumes:
            producer = producers.get(artifact)
            if artifact in _CONTEXT_ARTIFACTS:
                continue
            if producer is None:
                raise PipelineDefinitionError(
                    f"engine {descriptor.name} consumes unproduced artifact {artifact}"
                )
            if producer not in dependencies[descriptor.name]:
                raise PipelineDefinitionError(
                    f"engine {descriptor.name} consumes {artifact} without depending on {producer}"
                )
    return ordered


def _transitive_dependencies(
    by_name: dict[str, EngineDescriptor],
) -> dict[str, set[str]]:
    resolved: dict[str, set[str]] = {}

    def visit(name: str) -> set[str]:
        cached = resolved.get(name)
        if cached is not None:
            return cached
        found: set[str] = set()
        for dependency in by_name[name].dependencies:
            found.add(dependency)
            found.update(visit(dependency))
        resolved[name] = found
        return found

    for name in by_name:
        visit(name)
    return resolved


def _topological_layers(
    descriptors: tuple[EngineDescriptor, ...],
) -> tuple[tuple[EngineDescriptor, ...], ...]:
    by_name = {descriptor.name: descriptor for descriptor in descriptors}
    remaining = {descriptor.name: set(descriptor.dependencies) for descriptor in descriptors}
    layers: list[tuple[EngineDescriptor, ...]] = []
    completed: set[str] = set()
    while remaining:
        ready = tuple(
            descriptor
            for descriptor in descriptors
            if descriptor.name in remaining and remaining[descriptor.name] <= completed
        )
        if not ready:
            cycle_names = ", ".join(name for name in by_name if name in remaining)
            raise PipelineDefinitionError(f"verification pipeline contains a cycle: {cycle_names}")
        layers.append(ready)
        completed.update(descriptor.name for descriptor in ready)
        for descriptor in ready:
            del remaining[descriptor.name]
    return tuple(layers)


def descriptors_for_profile(
    descriptors: Iterable[EngineDescriptor],
    profile: AnalysisProfile | str,
    enabled: Callable[[str], bool],
) -> tuple[EngineDescriptor, ...]:
    """Select enabled descriptors for a profile and preserve registry order."""

    selected_profile = profile if isinstance(profile, AnalysisProfile) else AnalysisProfile(profile)
    validated = validate_engine_descriptors(descriptors)
    selected = tuple(
        descriptor
        for descriptor in validated
        if selected_profile in descriptor.profiles and enabled(descriptor.name)
    )
    selected_names = {descriptor.name for descriptor in selected}
    for descriptor in selected:
        missing = set(descriptor.dependencies) - selected_names
        if missing:
            names = ", ".join(sorted(missing))
            raise PipelineDefinitionError(
                f"engine {descriptor.name} has disabled dependencies in {selected_profile.value}: {names}"
            )
    return selected


ResultT = TypeVar("ResultT")


class PipelineExecutor(Generic[ResultT]):
    """Run read-only DAG nodes concurrently and build-owning nodes serially."""

    def __init__(
        self,
        descriptors: Iterable[EngineDescriptor],
        *,
        max_parallel: int = 4,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self.descriptors = validate_engine_descriptors(descriptors)
        self.max_parallel = max_parallel

    def run(self, execute: Callable[[EngineDescriptor], ResultT]) -> list[ResultT]:
        """Execute the DAG and return results in descriptor order."""

        completed: dict[str, ResultT] = {}
        for layer in _topological_layers(self.descriptors):
            read_only = tuple(
                descriptor
                for descriptor in layer
                if descriptor.execution is EngineExecution.READ_ONLY
            )
            if read_only:
                worker_count = min(self.max_parallel, len(read_only))
                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="ici-read",
                ) as pool:
                    futures = {
                        descriptor.name: pool.submit(execute, descriptor)
                        for descriptor in read_only
                    }
                    for descriptor in read_only:
                        completed[descriptor.name] = futures[descriptor.name].result()

            # Build owners never overlap with read-only work or each other.
            # This is deliberately stronger than a per-variant lock and keeps
            # project-wide observations deterministic while adapters write.
            for descriptor in layer:
                if descriptor.execution is EngineExecution.BUILD:
                    completed[descriptor.name] = execute(descriptor)

        return [completed[descriptor.name] for descriptor in self.descriptors]


# Import-time validation makes a malformed built-in graph fail before analysis.
validate_engine_descriptors(ENGINE_DESCRIPTORS)


def apply_analysis_profile(
    config: dict,
    profile: AnalysisProfile | str | None = None,
    *,
    descriptors: Iterable[EngineDescriptor] = ENGINE_DESCRIPTORS,
) -> tuple[dict, AnalysisProfile]:
    """Return an effective policy where profile-excluded engines are disabled."""

    configured = config.get("ici", {}).get("profile", AnalysisProfile.STANDARD.value)
    selected = profile if profile is not None else configured
    selected_profile = (
        selected if isinstance(selected, AnalysisProfile) else AnalysisProfile(selected)
    )
    validated = validate_engine_descriptors(descriptors)
    effective = deepcopy(config)
    ici_config = effective.setdefault("ici", {})
    ici_config["profile"] = selected_profile.value
    engines = effective.setdefault("engines", {})
    for descriptor in validated:
        engine_config = engines.setdefault(descriptor.name, {})
        if selected_profile not in descriptor.profiles:
            engine_config["enabled"] = False
    return effective, selected_profile
