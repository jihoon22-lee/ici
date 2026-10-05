"""One explicit map from a descriptor's ``factory_name`` to its class.

``ici.core.pipeline`` stays class-agnostic — it is the lower layer and
cannot import engines without a cycle. The string-to-class hop used to be
``globals()[descriptor.factory_name]``, which let a typo sail past
descriptor validation and surface as a ``KeyError`` inside a pool worker.
This module is the single place a factory name means a class, and it checks
itself at import: a descriptor naming no registered class fails before any
analysis runs.
"""

from __future__ import annotations

from ici.core.pipeline import ENGINE_DESCRIPTORS, PipelineDefinitionError
from ici.engines.base import BaseEngine
from ici.engines.binary_compat import BinaryCompatibilityEngine
from ici.engines.build import BuildEngine
from ici.engines.cognitive import CognitiveEngine
from ici.engines.compile_db import CompileDatabaseEngine
from ici.engines.complexity import ComplexityEngine
from ici.engines.cycle import CycleEngine
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.integration import IntegrationEngine
from ici.engines.line import LineCountEngine
from ici.engines.lint import LintEngine
from ici.engines.python_compat import PythonCompatibilityEngine
from ici.engines.resource import ResourceEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.security import SecurityEngine
from ici.engines.test import TestEngine
from ici.engines.thread_sanitize import ThreadSanitizeEngine
from ici.engines.type_check import TypeCheckEngine

__all__ = ["ENGINE_FACTORIES", "resolve_engine_class"]

#: factory_name → engine class. Keys come from ``cls.__name__`` so the key
#: can never drift from the class it selects.
ENGINE_FACTORIES: dict[str, type[BaseEngine]] = {
    cls.__name__: cls
    for cls in (
        BinaryCompatibilityEngine,
        BuildEngine,
        CognitiveEngine,
        CompileDatabaseEngine,
        ComplexityEngine,
        CycleEngine,
        DeadCodeEngine,
        DuplicateEngine,
        ExceptionSafetyEngine,
        IntegrationEngine,
        LineCountEngine,
        LintEngine,
        PythonCompatibilityEngine,
        ResourceEngine,
        SanitizeEngine,
        SecurityEngine,
        TestEngine,
        ThreadSanitizeEngine,
        TypeCheckEngine,
    )
}


def resolve_engine_class(factory_name: str) -> type[BaseEngine]:
    """The class a descriptor or CLI command names, or a pipeline error."""

    found = ENGINE_FACTORIES.get(factory_name)
    if found is None:
        raise PipelineDefinitionError(f"unknown engine factory {factory_name!r}")
    return found


# Import-time proof that every built-in descriptor resolves — a malformed
# graph should not wait for a worker thread to notice.
for _descriptor in ENGINE_DESCRIPTORS:
    resolve_engine_class(_descriptor.factory_name)
