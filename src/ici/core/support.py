"""Declarative engine support and project-specific capability evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ici.config import get_engine_config
from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineSupport,
    EvidenceState,
    FindingConfidence,
    SupportLanguage,
    SupportMatrix,
)
from ici.core.project import (
    _iter_project_files,
    get_all_cpp_sources,
    get_all_python_sources,
    get_source_dirs,
)

ENGINE_NAMES = (
    "line",
    "lint",
    "test",
    "type",
    "cognitive",
    "resource",
    "security",
    "cycle",
    "complexity",
    "sanitize",
    "dead",
    "dup",
    "exception",
)


@dataclass(frozen=True)
class SupportDeclaration:
    """Static support contract for one engine/language pair."""

    engine_name: str
    language: SupportLanguage
    mode: AnalysisMode
    confidence: FindingConfidence
    frameworks: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    optional_tools: tuple[str, ...] = ()
    fallback_mode: AnalysisMode | None = None
    limitations: tuple[str, ...] = ()


_QT = ("qt",)
_DECLARATIONS = (
    SupportDeclaration(
        "line",
        SupportLanguage.PYTHON,
        AnalysisMode.EXACT,
        FindingConfidence.EXACT,
        limitations=("Counts physical source lines; it does not measure semantic size.",),
    ),
    SupportDeclaration(
        "line",
        SupportLanguage.CPP,
        AnalysisMode.EXACT,
        FindingConfidence.EXACT,
        frameworks=_QT,
        limitations=(
            "Counts physical source lines; generated files outside source scope are excluded.",
        ),
    ),
    SupportDeclaration(
        "lint",
        SupportLanguage.PYTHON,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        optional_tools=("ruff",),
        fallback_mode=AnalysisMode.HEURISTIC,
        limitations=("Without Ruff, fallback validates AST syntax only, not style or lint rules.",),
    ),
    SupportDeclaration(
        "lint",
        SupportLanguage.CPP,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        frameworks=_QT,
        required_tools=("g++",),
        optional_tools=("pkg-config",),
        limitations=(
            "Uses per-translation-unit g++ syntax diagnostics; generated build context may require configured pkg-config flags.",
        ),
    ),
    SupportDeclaration(
        "test",
        SupportLanguage.PYTHON,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        required_tools=("python3",),
        optional_tools=("coverage", "pytest"),
        fallback_mode=AnalysisMode.HEURISTIC,
        limitations=(
            "Falls back from pytest to unittest only when pytest is unavailable; coverage is estimated when optional evidence is absent.",
        ),
    ),
    SupportDeclaration(
        "test",
        SupportLanguage.CPP,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        frameworks=_QT,
        required_tools=("g++",),
        optional_tools=("cmake", "qmake", "make", "gcov", "pkg-config"),
        fallback_mode=AnalysisMode.HEURISTIC,
        limitations=(
            "Uses CMake/CTest or qmake/QtTest when declared, otherwise a generic g++ harness; coverage is estimated without valid gcov evidence.",
        ),
    ),
    SupportDeclaration(
        "type",
        SupportLanguage.PYTHON,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        optional_tools=("mypy",),
        fallback_mode=AnalysisMode.HEURISTIC,
        limitations=(
            "Without mypy, fallback checks annotations structurally and cannot prove type safety.",
        ),
    ),
    SupportDeclaration(
        "type",
        SupportLanguage.CPP,
        AnalysisMode.UNSUPPORTED,
        FindingConfidence.LOW,
        limitations=(
            "C++ semantic type analysis is not implemented; compiler syntax checks belong to lint.",
        ),
    ),
    SupportDeclaration(
        "cognitive",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=("AST-based cognitive score is an ici policy metric, not a compiler proof.",),
    ),
    SupportDeclaration(
        "cognitive",
        SupportLanguage.CPP,
        AnalysisMode.UNSUPPORTED,
        FindingConfidence.LOW,
        limitations=("C++ cognitive complexity is not implemented.",),
    ),
    SupportDeclaration(
        "resource",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=(
            "AST patterns cover known resource and mutable-default risks, not runtime ownership.",
        ),
    ),
    SupportDeclaration(
        "resource",
        SupportLanguage.CPP,
        AnalysisMode.UNSUPPORTED,
        FindingConfidence.LOW,
        limitations=(
            "C++ ownership/resource analysis is not implemented; runtime leaks belong to sanitize.",
        ),
    ),
    SupportDeclaration(
        "security",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=(
            "Offline source patterns cover selected secret and unsafe-API rules, not dependency CVEs.",
        ),
    ),
    SupportDeclaration(
        "security",
        SupportLanguage.CPP,
        AnalysisMode.UNSUPPORTED,
        FindingConfidence.LOW,
        limitations=("C++ security rule coverage is not implemented.",),
    ),
    SupportDeclaration(
        "cycle",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.HIGH,
        limitations=(
            "Import-to-module resolution is best effort for dynamic and ambiguous imports.",
        ),
    ),
    SupportDeclaration(
        "cycle",
        SupportLanguage.CPP,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        frameworks=_QT,
        limitations=(
            "Include graph resolution is textual and may not reproduce conditional preprocessor state.",
        ),
    ),
    SupportDeclaration(
        "complexity",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.HIGH,
        limitations=(
            "Cyclomatic and nesting metrics approximate maintainability rather than correctness.",
        ),
    ),
    SupportDeclaration(
        "complexity",
        SupportLanguage.CPP,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        frameworks=_QT,
        limitations=("Lightweight C++ parsing does not expand macros or fully model templates.",),
    ),
    SupportDeclaration(
        "sanitize",
        SupportLanguage.PYTHON,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        required_tools=("python3", "pytest"),
        limitations=(
            "Promotes ResourceWarning during pytest; it does not instrument Python memory safety.",
        ),
    ),
    SupportDeclaration(
        "sanitize",
        SupportLanguage.CPP,
        AnalysisMode.TOOL_BACKED,
        FindingConfidence.HIGH,
        frameworks=_QT,
        required_tools=("g++",),
        optional_tools=("cmake", "qmake", "make", "pkg-config"),
        limitations=("Runs ASan/UBSan tests; unexecuted code paths remain unobserved.",),
    ),
    SupportDeclaration(
        "dead",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=(
            "AST reachability and reference rules cannot resolve all dynamic Python uses.",
        ),
    ),
    SupportDeclaration(
        "dead",
        SupportLanguage.CPP,
        AnalysisMode.UNSUPPORTED,
        FindingConfidence.LOW,
        limitations=("C++ dead-code analysis is not implemented.",),
    ),
    SupportDeclaration(
        "dup",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=(
            "Token-window similarity can miss semantic clones and report intentional repetition.",
        ),
    ),
    SupportDeclaration(
        "dup",
        SupportLanguage.CPP,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        frameworks=_QT,
        limitations=(
            "Token-window similarity does not expand macros or identify semantic clones.",
        ),
    ),
    SupportDeclaration(
        "exception",
        SupportLanguage.PYTHON,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        limitations=(
            "AST patterns detect selected exception hazards but do not model runtime call graphs.",
        ),
    ),
    SupportDeclaration(
        "exception",
        SupportLanguage.CPP,
        AnalysisMode.HEURISTIC,
        FindingConfidence.MEDIUM,
        frameworks=_QT,
        limitations=(
            "Lightweight parsing checks selected exception-safety patterns without full semantic analysis.",
        ),
    ),
)


def support_declarations() -> tuple[SupportDeclaration, ...]:
    """Return the immutable, deterministically ordered support contract."""

    return _DECLARATIONS


def _project_languages(project_root: Path, config: dict[str, Any]) -> list[SupportLanguage]:
    languages: list[SupportLanguage] = []
    if get_all_python_sources(project_root, config):
        languages.append(SupportLanguage.PYTHON)
    has_cpp_headers = any(
        any(_iter_project_files(source_dir, project_root, (".h", ".hh", ".hpp", ".hxx")))
        for source_dir in get_source_dirs(project_root, config)
    )
    if get_all_cpp_sources(project_root, config) or has_cpp_headers:
        languages.append(SupportLanguage.CPP)

    project = config.get("project", {})
    configured_type = config.get("type")
    if configured_type is None and isinstance(project, dict):
        configured_type = project.get("type")
    if configured_type in ("python", "hybrid") and SupportLanguage.PYTHON not in languages:
        languages.append(SupportLanguage.PYTHON)
    if configured_type in ("cpp", "hybrid") and SupportLanguage.CPP not in languages:
        languages.append(SupportLanguage.CPP)

    # Header-only and not-yet-populated projects still have a declared build
    # identity even when no compilation unit exists under source_dirs.
    if not languages:
        if (project_root / "pyproject.toml").is_file():
            languages.append(SupportLanguage.PYTHON)
        if (project_root / "CMakeLists.txt").is_file() or any(project_root.glob("*.pro")):
            languages.append(SupportLanguage.CPP)
    return sorted(languages, key=lambda item: item.value)


def _project_frameworks(project_root: Path, config: dict[str, Any]) -> list[str]:
    project = config.get("project", {})
    packages = project.get("cpp_pkg_config", []) if isinstance(project, dict) else []
    if isinstance(packages, list) and any(
        isinstance(package, str) and package.casefold().startswith(("qt5", "qt6"))
        for package in packages
    ):
        return ["qt"]

    candidates = [project_root / "CMakeLists.txt", *sorted(project_root.glob("*.pro"))]
    qt_pattern = re.compile(r"(?:find_package\s*\(\s*Qt[56]\b|\bqt_add_|\bQT\s*\+=)")
    for path in candidates:
        try:
            if path.is_file() and qt_pattern.search(
                path.read_text(encoding="utf-8", errors="replace")
            ):
                return ["qt"]
        except OSError:
            continue
    return []


def _confidence_for_evidence(
    declared: FindingConfidence, evidence: EvidenceState
) -> FindingConfidence:
    if evidence in (EvidenceState.NOT_APPLICABLE, EvidenceState.NOT_RUN):
        return FindingConfidence.LOW
    if evidence == EvidenceState.ESTIMATED and declared in (
        FindingConfidence.EXACT,
        FindingConfidence.HIGH,
    ):
        return FindingConfidence.MEDIUM
    return declared


def _tool_policy(
    declaration: SupportDeclaration, config: dict[str, Any]
) -> tuple[list[str], list[str]]:
    required = list(declaration.required_tools)
    optional = list(declaration.optional_tools)
    engine_config = get_engine_config(config, declaration.engine_name)
    promoted = ""
    if declaration.engine_name == "lint" and declaration.language == SupportLanguage.PYTHON:
        promoted = "ruff" if engine_config.get("ruff_required", False) else ""
    elif declaration.engine_name == "type" and declaration.language == SupportLanguage.PYTHON:
        promoted = "mypy" if engine_config.get("mypy_required", False) else ""
    elif declaration.engine_name == "test" and engine_config.get("coverage_required", False):
        promoted = "coverage" if declaration.language == SupportLanguage.PYTHON else "gcov"
    if promoted and promoted in optional:
        optional.remove(promoted)
        required.append(promoted)
    return required, optional


def evaluate_support_matrix(
    project_root: Path,
    config: dict[str, Any],
    results: list[EngineResult] | None = None,
    *,
    engine_names: set[str] | None = None,
) -> SupportMatrix:
    """Evaluate declarations against discovered project scope and observed results."""

    root = project_root.resolve()
    languages = _project_languages(root, config)
    frameworks = _project_frameworks(root, config)
    by_engine = {result.engine_name: result for result in results or []}
    entries: list[EngineSupport] = []

    for declaration in _DECLARATIONS:
        if engine_names is not None and declaration.engine_name not in engine_names:
            continue
        enabled = bool(get_engine_config(config, declaration.engine_name).get("enabled", True))
        language_present = declaration.language in languages
        supported = declaration.mode != AnalysisMode.UNSUPPORTED
        applicable = language_present and supported
        result = by_engine.get(declaration.engine_name)

        if not language_present:
            evidence = EvidenceState.NOT_APPLICABLE
            reason = f"project has no discovered {declaration.language.value} source scope"
        elif not supported:
            evidence = EvidenceState.NOT_APPLICABLE
            reason = f"{declaration.engine_name} does not support {declaration.language.value}"
        elif not enabled:
            evidence = EvidenceState.NOT_RUN
            reason = "engine is disabled by effective policy"
        elif result is None:
            evidence = EvidenceState.NOT_RUN
            reason = "applicable engine has not been run"
        else:
            evidence = result.evidence
            reason = f"observed engine result reported {result.evidence.value} evidence"

        active_mode: AnalysisMode | None = None
        if applicable and enabled and evidence == EvidenceState.MEASURED:
            active_mode = declaration.mode
        elif applicable and enabled and evidence == EvidenceState.ESTIMATED:
            active_mode = declaration.fallback_mode or declaration.mode

        required_tools, optional_tools = _tool_policy(declaration, config)
        entries.append(
            EngineSupport(
                engine_name=declaration.engine_name,
                language=declaration.language,
                mode=declaration.mode,
                active_mode=active_mode,
                applicable=applicable,
                enabled=enabled,
                evidence=evidence,
                confidence=_confidence_for_evidence(declaration.confidence, evidence),
                frameworks=list(declaration.frameworks),
                required_tools=required_tools,
                optional_tools=optional_tools,
                fallback_mode=declaration.fallback_mode,
                limitations=list(declaration.limitations),
                reason=reason,
            )
        )

    return SupportMatrix(
        project_languages=languages,
        project_frameworks=frameworks,
        entries=entries,
    )


def render_support_markdown() -> str:
    """Render the documentation table from the executable declaration registry."""

    lines = [
        "| Engine | Python | C++ / Qt |",
        "|---|---|---|",
    ]
    declarations = {(item.engine_name, item.language): item for item in support_declarations()}
    for engine_name in ENGINE_NAMES:
        values: list[str] = []
        for language in (SupportLanguage.PYTHON, SupportLanguage.CPP):
            item = declarations[(engine_name, language)]
            mode = item.mode.value
            if language == SupportLanguage.CPP and "qt" in item.frameworks:
                mode += " (Qt)"
            if item.fallback_mode is not None:
                mode += f" → {item.fallback_mode.value} fallback"
            values.append(mode)
        lines.append(f"| `{engine_name}` | {values[0]} | {values[1]} |")
    return "\n".join(lines)
