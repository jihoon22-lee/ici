"""Exact Qt generated-code linkage and major-version evidence verification."""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.models import (
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)
from ici.core.project import _iter_project_files

_MAX_INPUTS = 4_096
_MAX_FILE_BYTES = 2 * 1_048_576
_Q_OBJECT_RE = re.compile(r"\bQ_OBJECT\b")
_INCLUDE_RE = re.compile(
    r"^\s*#\s*include\s*[<\"](?P<name>[^>\"\r\n]{1,1024})[>\"]",
    re.MULTILINE,
)
_QT_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"](?:Qt[56]?[/A-Za-z]|Q[A-Z])", re.MULTILINE)
_QT5_RE = re.compile(r"(?:^|[/\\_.-])qt5(?:$|[/\\_.-])|\bqt5(?:core|gui|widgets)\b", re.I)
_QT6_RE = re.compile(r"(?:^|[/\\_.-])qt6(?:$|[/\\_.-])|\bqt6(?:core|gui|widgets)\b", re.I)
_GENERATED_MARKERS = {
    "moc": ("Meta object code", "qt_meta_stringdata_", "qt_static_metacall"),
    "qrc": ("QT_RCC_MANGLE_NAMESPACE", "qInitResources_", "qt_resource_data"),
    "ui": ("setupUi(", "retranslateUi(", "Ui_"),
}


@dataclass(frozen=True)
class _QtInput:
    kind: str
    path: str
    line: int
    stem: str


@dataclass
class QtCodegenOutcome:
    """Qt build-stage observations added to the lint facade."""

    targets: list[InspectionTarget] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "not_applicable"
    inputs_checked: int = 0
    ui_checked: int = 0
    qrc_checked: int = 0
    moc_checked: int = 0
    qt5_units: int = 0
    qt6_units: int = 0


def _cpp_code(text: str) -> str:
    """Blank comments and literals while preserving code positions and lines."""

    output: list[str] = []
    index = 0
    state = "code"
    quote = ""
    raw_end = ""
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]
        if state == "line-comment":
            output.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
            continue
        if state == "block-comment":
            if pair == "*/":
                output.extend((" ", " "))
                index += 2
                state = "code"
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if state == "literal":
            if char == "\\" and index + 1 < len(text):
                output.extend((" ", "\n" if text[index + 1] == "\n" else " "))
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
                if char == quote or char == "\n":
                    state = "code"
            continue
        if state == "raw":
            if raw_end and text.startswith(raw_end, index):
                output.extend(" " for _ in raw_end)
                index += len(raw_end)
                raw_end = ""
                state = "code"
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if pair == "//":
            output.extend((" ", " "))
            index += 2
            state = "line-comment"
            continue
        if pair == "/*":
            output.extend((" ", " "))
            index += 2
            state = "block-comment"
            continue
        if char in {"'", '"'}:
            output.append(" ")
            index += 1
            quote = char
            state = "literal"
            continue
        if pair == 'R"':
            opening = text.find("(", index + 2, min(len(text), index + 19))
            delimiter = text[index + 2 : opening] if opening >= 0 else ""
            if opening >= 0 and not any(value.isspace() or value in "\\()" for value in delimiter):
                consumed = opening - index + 1
                output.extend(" " for _ in range(consumed))
                index = opening + 1
                raw_end = f'){delimiter}"'
                state = "raw"
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_contained(root: Path, path: Path) -> tuple[str | None, str]:
    try:
        if path.is_symlink():
            return None, "file is a symbolic link"
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except (OSError, RuntimeError) as err:
        return None, f"file could not be resolved: {type(err).__name__}"
    if not _inside(root, resolved) or not stat.S_ISREG(details.st_mode):
        return None, "file is not a contained regular file"
    if details.st_size > _MAX_FILE_BYTES:
        return None, "file exceeds the 2 MiB inspection limit"
    try:
        return resolved.read_text(encoding="utf-8", errors="replace"), ""
    except OSError as err:
        return None, f"file could not be read: {type(err).__name__}"


def _target(
    item: _QtInput,
    status: EngineStatus,
    target_name: str,
    message: str,
    *,
    metrics: dict[str, int | str] | None = None,
) -> InspectionTarget:
    return InspectionTarget(
        file_path=item.path,
        start_line=item.line,
        target_name=target_name,
        status=status,
        message=message,
        metrics=metrics or {},
    )


def _finding_for_target(target: InspectionTarget, rule_id: str) -> Finding | None:
    if target.status in {EngineStatus.PASS, EngineStatus.SKIP}:
        return None
    return Finding(
        rule_id=rule_id,
        category=(
            FindingCategory.COMPATIBILITY
            if rule_id.endswith("compatibility")
            else FindingCategory.BUILD
        ),
        severity=(
            FindingSeverity.HIGH
            if target.status in {EngineStatus.FAIL, EngineStatus.ERROR}
            else FindingSeverity.MEDIUM
        ),
        confidence=(
            FindingConfidence.EXACT
            if target.status == EngineStatus.FAIL
            else FindingConfidence.HIGH
        ),
        fingerprint="",
        primary_location=SourceLocation(
            path=target.file_path,
            start_line=target.start_line,
            end_line=target.end_line,
            start_column=target.start_column,
            end_column=target.end_column,
            label=target.target_name,
        ),
        message=target.message,
        explanation=(
            "Qt major compatibility could not be proved from a successful exact compile replay."
            if rule_id.endswith("compatibility")
            else "A declared Qt generated-code input is not linked by the exact compilation context."
        ),
    )


def _append(
    outcome: QtCodegenOutcome,
    target: InspectionTarget,
    rule_id: str,
    *,
    problem: str = "",
) -> None:
    outcome.targets.append(target)
    finding = _finding_for_target(target, rule_id)
    if finding is not None:
        outcome.findings.append(finding)
    if target.status == EngineStatus.ERROR:
        outcome.errors.append(problem or target.message)
    elif target.status == EngineStatus.WARN:
        outcome.warnings.append(problem or target.message)


def _discover_inputs(
    root: Path,
    source_dirs: list[Path],
    cpp_files: list[Path],
    cpp_headers: list[Path],
    outcome: QtCodegenOutcome,
) -> list[_QtInput]:
    inputs: list[_QtInput] = []
    resource_paths: dict[str, tuple[str, Path]] = {}
    for directory in source_dirs:
        for path in _iter_project_files(directory, root, (".ui", ".qrc")):
            relative = path.relative_to(root).as_posix()
            resource_paths[relative] = (path.suffix[1:], path)
            if len(resource_paths) > _MAX_INPUTS:
                message = "Qt generated-code input count exceeds the bounded limit"
                outcome.errors.append(message)
                outcome.targets.append(
                    InspectionTarget(
                        file_path=".",
                        start_line=1,
                        target_name="QtCodegenBudgetError",
                        status=EngineStatus.ERROR,
                        message=message,
                    )
                )
                return []
    inputs.extend(
        _QtInput(kind, relative, 1, path.stem)
        for relative, (kind, path) in sorted(resource_paths.items())
    )

    object_paths: dict[str, Path] = {}
    for lexical in [*cpp_files, *cpp_headers]:
        try:
            relative = lexical.resolve(strict=False).relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        object_paths.setdefault(relative, lexical)
    for relative, path in sorted(object_paths.items()):
        text, error = _read_contained(root, path)
        if error:
            item = _QtInput("moc", relative, 1, path.stem)
            _append(
                outcome,
                _target(item, EngineStatus.ERROR, "QtMocInspectionError", error),
                "ici.qt.codegen.moc",
            )
            continue
        assert text is not None
        for line, content in enumerate(_cpp_code(text).splitlines(), start=1):
            if _Q_OBJECT_RE.search(content):
                inputs.append(_QtInput("moc", relative, line, path.stem))
                break
    if len(inputs) > _MAX_INPUTS:
        message = "Qt generated-code input count exceeds the bounded limit"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="QtCodegenBudgetError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return []
    return inputs


def _unit_file(root: Path, unit: CompilationUnit) -> Path | None:
    try:
        path = (root / PurePosixPath(unit.source)).resolve(strict=True)
        details = path.stat()
    except (OSError, RuntimeError):
        return None
    if (
        not _inside(root, path)
        or not stat.S_ISREG(details.st_mode)
        or details.st_size > _MAX_FILE_BYTES
    ):
        return None
    return path


def _unit_text(root: Path, unit: CompilationUnit) -> str:
    path = _unit_file(root, unit)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _include_roots(root: Path, unit: CompilationUnit) -> list[Path]:
    roots: list[Path] = []
    try:
        source_parent = (root / PurePosixPath(unit.source)).resolve(strict=False).parent
        cwd = (root / PurePosixPath(unit.directory)).resolve(strict=False)
    except (OSError, RuntimeError):
        return []
    roots.extend((source_parent, cwd))
    for entry in unit.include_paths:
        if entry.scope != "project" or not entry.exists:
            continue
        try:
            candidate = (root / PurePosixPath(entry.path)).resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if _inside(root, candidate):
            roots.append(candidate)

    index = 1
    while index < len(unit.argv):
        token = unit.argv[index]
        value = ""
        if token in {"-I", "-iquote", "-isystem"} and index + 1 < len(unit.argv):
            value = unit.argv[index + 1]
            index += 2
        elif token.startswith("-I") and len(token) > 2:
            value = token[2:]
            index += 1
        else:
            index += 1
        if not value or "\x00" in value:
            continue
        try:
            lexical = Path(value)
            candidate = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if _inside(root, candidate):
            roots.append(candidate)
    return list(dict.fromkeys(roots))[:1_024]


def _resolved_include(root: Path, unit: CompilationUnit, include: str) -> Path | None:
    lexical = PurePosixPath(include)
    if lexical.is_absolute() or ".." in lexical.parts:
        return None
    for directory in _include_roots(root, unit):
        candidate = directory / lexical
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            details = resolved.stat()
        except (OSError, RuntimeError):
            continue
        if _inside(root, resolved) and stat.S_ISREG(details.st_mode):
            return resolved
    return None


def _is_generated(root: Path, path: Path, kind: str) -> bool:
    text, error = _read_contained(root, path)
    return (
        not error
        and text is not None
        and any(marker in text for marker in _GENERATED_MARKERS[kind])
    )


def _unit_includes(root: Path, unit: CompilationUnit) -> tuple[str, ...]:
    return tuple(match.group("name") for match in _INCLUDE_RE.finditer(_unit_text(root, unit)))


def _find_ui_link(
    root: Path,
    units: tuple[CompilationUnit, ...],
    expected: str,
) -> tuple[CompilationUnit, Path] | None:
    for unit in units:
        for include in _unit_includes(root, unit):
            if PurePosixPath(include).name != expected:
                continue
            generated = _resolved_include(root, unit, include)
            if generated is not None and _is_generated(root, generated, "ui"):
                return unit, generated
    return None


def _find_generated_unit(
    root: Path,
    units: tuple[CompilationUnit, ...],
    expected: str,
    kind: str,
) -> CompilationUnit | None:
    return next(
        (
            unit
            for unit in units
            if PurePosixPath(unit.source).name == expected
            and (path := _unit_file(root, unit)) is not None
            and _is_generated(root, path, kind)
        ),
        None,
    )


def _find_moc_link(
    root: Path,
    units: tuple[CompilationUnit, ...],
    stem: str,
) -> tuple[CompilationUnit, str] | None:
    generated_cpp = f"moc_{stem}.cpp"
    direct = _find_generated_unit(root, units, generated_cpp, "moc")
    if direct is not None:
        return direct, generated_cpp
    source_moc = f"{stem}.moc"
    for unit in units:
        for include in _unit_includes(root, unit):
            name = PurePosixPath(include).name
            if name == source_moc:
                generated = _resolved_include(root, unit, include)
                if generated is not None and _is_generated(root, generated, "moc"):
                    return unit, source_moc
    for unit in units:
        if PurePosixPath(unit.source).name != "mocs_compilation.cpp":
            continue
        for include in _unit_includes(root, unit):
            if PurePosixPath(include).name != generated_cpp:
                continue
            generated = _resolved_include(root, unit, include)
            if generated is not None and _is_generated(root, generated, "moc"):
                return unit, generated_cpp
    return None


def _qt_major(unit: CompilationUnit) -> set[int]:
    values = [*unit.argv]
    values.extend(path.path for path in unit.include_paths)
    values.extend(f"{definition.name}={definition.value or ''}" for definition in unit.defines)
    majors: set[int] = set()
    if any(_QT5_RE.search(value) or "QT_VERSION_MAJOR=5" in value for value in values):
        majors.add(5)
    if any(_QT6_RE.search(value) or "QT_VERSION_MAJOR=6" in value for value in values):
        majors.add(6)
    return majors


def _qt_relevant(root: Path, unit: CompilationUnit) -> bool:
    metadata = "\n".join(
        [
            *unit.argv,
            *(path.path for path in unit.include_paths),
            *(item.name for item in unit.defines),
        ]
    )
    return bool(
        _qt_major(unit)
        or re.search(r"\bQT_[A-Z0-9_]+_LIB\b", metadata)
        or _QT_INCLUDE_RE.search(_unit_text(root, unit))
    )


def _verify_major_evidence(
    root: Path,
    context: AnalysisContext,
    compiled_sources: set[str],
    outcome: QtCodegenOutcome,
) -> None:
    production = set(context.project.cpp_sources)
    relevant = [
        unit
        for unit in context.compilation.units
        if unit.source in production and _qt_relevant(root, unit)
    ]
    for unit in relevant:
        majors = _qt_major(unit)
        item = _QtInput("compatibility", unit.source, 1, Path(unit.source).stem)
        if len(majors) > 1:
            _append(
                outcome,
                _target(
                    item,
                    EngineStatus.FAIL,
                    "QtCompatibility:ConflictingMajor",
                    "The exact translation unit mixes Qt 5 and Qt 6 include or define evidence.",
                ),
                "ici.qt.compatibility",
            )
            continue
        if not majors:
            _append(
                outcome,
                _target(
                    item,
                    EngineStatus.WARN,
                    "QtCompatibility:UnknownMajor",
                    "Qt code is present, but the exact translation unit does not identify Qt 5 or Qt 6.",
                ),
                "ici.qt.compatibility",
            )
            continue
        major = next(iter(majors))
        if major == 5:
            outcome.qt5_units += 1
        else:
            outcome.qt6_units += 1
        replayed = unit.source in compiled_sources
        status = EngineStatus.PASS if replayed else EngineStatus.WARN
        message = (
            f"Qt {major} API compatibility passed an exact sanitized compile replay."
            if replayed
            else f"Qt {major} compilation context exists without successful replay evidence."
        )
        _append(
            outcome,
            _target(
                item,
                status,
                f"QtCompatibility:Qt{major}",
                message,
                metrics={"qt_major": major, "compile_replay": int(replayed)},
            ),
            "ici.qt.compatibility",
        )


def verify_qt_codegen(
    project_root: Path,
    source_dirs: list[Path],
    cpp_files: list[Path],
    cpp_headers: list[Path],
    context: AnalysisContext | None,
    *,
    compiled_sources: set[str] | None = None,
) -> QtCodegenOutcome:
    """Verify moc/uic/rcc outputs and Qt-major evidence without invoking a build."""

    outcome = QtCodegenOutcome()
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        message = f"Qt generated-code project root could not be resolved: {type(err).__name__}"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="QtCodegenContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        outcome.mode = "error"
        return outcome
    inputs = _discover_inputs(root, source_dirs, cpp_files, cpp_headers, outcome)
    if outcome.errors:
        outcome.mode = "error"
        return outcome
    if not inputs and context is None:
        return outcome
    if context is not None and context.project.root != root:
        message = "Qt generated-code analysis context belongs to another project root"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="QtCodegenContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        outcome.mode = "error"
        return outcome
    if not inputs and context is not None:
        if any(_qt_relevant(root, unit) for unit in context.compilation.units):
            _verify_major_evidence(root, context, compiled_sources or set(), outcome)
            outcome.mode = "exact" if not outcome.errors else "error"
        return outcome
    if context is None or context.compilation.database_path is None:
        for item in inputs:
            _append(
                outcome,
                _target(
                    item,
                    EngineStatus.WARN,
                    f"Qt{item.kind.title()}ContextMissing",
                    "Qt generated-code linkage requires an exact compilation database.",
                ),
                f"ici.qt.codegen.{item.kind}",
            )
        outcome.inputs_checked = len(inputs)
        outcome.mode = "unavailable"
        return outcome
    if any(entry.level == "error" for entry in context.compilation.diagnostics):
        for item in inputs:
            _append(
                outcome,
                _target(
                    item,
                    EngineStatus.ERROR,
                    f"Qt{item.kind.title()}ContextError",
                    "Qt generated-code linkage was not checked because compilation context has errors.",
                ),
                f"ici.qt.codegen.{item.kind}",
            )
        outcome.inputs_checked = len(inputs)
        outcome.mode = "error"
        return outcome

    units = context.compilation.units
    for item in inputs:
        outcome.inputs_checked += 1
        if item.kind == "ui":
            outcome.ui_checked += 1
            expected = f"ui_{item.stem}.h"
            link = _find_ui_link(root, units, expected)
            if link is None:
                target = _target(
                    item,
                    EngineStatus.FAIL,
                    "QtUicLinkage",
                    f"uic output {expected} is absent or not included by an exact translation unit.",
                )
            else:
                unit, generated = link
                target = _target(
                    item,
                    EngineStatus.PASS,
                    "QtUicLinkage",
                    f"uic output {generated.relative_to(root).as_posix()} is linked by {unit.source}.",
                )
            _append(outcome, target, "ici.qt.codegen.ui")
        elif item.kind == "qrc":
            outcome.qrc_checked += 1
            expected = f"qrc_{item.stem}.cpp"
            unit = _find_generated_unit(root, units, expected, "qrc")
            target = (
                _target(
                    item,
                    EngineStatus.PASS,
                    "QtRccLinkage",
                    f"rcc output {unit.source} is a generated compilation unit.",
                )
                if unit is not None
                else _target(
                    item,
                    EngineStatus.FAIL,
                    "QtRccLinkage",
                    f"rcc output {expected} is absent from the exact compilation database.",
                )
            )
            _append(outcome, target, "ici.qt.codegen.qrc")
        else:
            outcome.moc_checked += 1
            link = _find_moc_link(root, units, item.stem)
            target = (
                _target(
                    item,
                    EngineStatus.PASS,
                    "QtMocLinkage",
                    f"moc output {link[1]} is linked by {link[0].source}.",
                )
                if link is not None
                else _target(
                    item,
                    EngineStatus.FAIL,
                    "QtMocLinkage",
                    f"No linked moc output was found for Q_OBJECT in {item.path}.",
                )
            )
            _append(outcome, target, "ici.qt.codegen.moc")

    _verify_major_evidence(root, context, compiled_sources or set(), outcome)
    outcome.mode = "exact" if not outcome.errors else "error"
    return outcome
