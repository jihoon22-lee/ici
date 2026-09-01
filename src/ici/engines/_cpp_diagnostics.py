"""Strict, bounded GCC/Clang diagnostic normalization for C++ analysis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ici.core.models import EngineStatus, InspectionTarget

MAX_DIAGNOSTICS = 10_000
MAX_DIAGNOSTIC_DEPTH = 32
MAX_DIAGNOSTIC_LINE = 2_147_483_647
MAX_MESSAGE_CHARS = 8_192
MAX_REPLACEMENT_CHARS = 8_192
MAX_DIAGNOSTIC_OUTPUT_CHARS = 1_000_000

_TEXT_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<kind>fatal error|error|warning|note|remark):\s*"
    r"(?P<message>\S.*?)(?:\s+\[(?P<rule>[-A-Za-z0-9_.]+)\])?$"
)
_TEXT_CONTEXT_RE = re.compile(r"^\s*(?:\d+\s*\|.*|\|.*|[\^~].*)$")
_TEXT_CONTEXT_HEADER_RE = re.compile(
    r"^.+:\s+(?:At global scope|In (?:function|member function|constructor|destructor|"
    r"lambda function|instantiation of)(?: .*)?):$"
)
_TEXT_REQUIRED_FROM_RE = re.compile(r"^.+:[1-9]\d*(?::[1-9]\d*)?:\s+required (?:from|by)\s+\S.*$")
_TEXT_TRAILER_RE = re.compile(
    r"^(?:[1-9]\d* (?:warning|warnings|error|errors) generated\.|compilation terminated\.)$"
)
_CLANG_TIDY_GENERATED_RE = re.compile(r"^(?P<count>[1-9]\d*) warnings? generated\.$")
_CLANG_TIDY_SUPPRESSED_RE = re.compile(
    r"^Suppressed (?P<count>[1-9]\d*) warnings? \([^\r\n]{1,512}\)\.$"
)
_CLANG_TIDY_HEADER_HINT_RE = re.compile(
    r"^Use -header-filter=.* to display errors from all non-system headers\.$"
)
_TEXT_FIXIT_RE = re.compile(
    r'^fix-it:"(?P<file>(?:[^"\\]|\\.)*)":\{'
    r"(?P<start_line>[1-9]\d*):(?P<start_column>[1-9]\d*)-"
    r"(?P<end_line>[1-9]\d*):(?P<end_column>[1-9]\d*)"
    r'\}:"(?P<replacement>(?:[^"\\]|\\.)*)"$'
)


@dataclass(frozen=True)
class CppFixIt:
    """One read-only replacement suggestion emitted by a compiler."""

    file_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    replacement: str


@dataclass(frozen=True)
class CppDiagnostic:
    """A normalized compiler or clang-tool diagnostic."""

    target: InspectionTarget
    tool_rule_id: str = ""
    family: str = "compiler"
    fixits: tuple[CppFixIt, ...] = ()


@dataclass(frozen=True)
class DiagnosticParseResult:
    """Atomic parse outcome; diagnostics are empty whenever ``error`` is set."""

    diagnostics: tuple[CppDiagnostic, ...] = ()
    format_name: str = "text"
    error: str = ""


@dataclass(frozen=True)
class _Region:
    file_path: str
    start_line: int
    start_column: int | None
    end_line: int | None
    end_column: int | None


def _bounded_text(value: Any, label: str, *, limit: int = MAX_MESSAGE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds the bounded size")
    return value.strip()


def _bounded_replacement(value: Any, label: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{label} must be a string")
    if len(value) > MAX_REPLACEMENT_CHARS:
        raise ValueError(f"{label} exceeds the bounded size")
    return value


def _number(value: Any, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not 0 < value <= MAX_DIAGNOSTIC_LINE:
        raise ValueError(f"{label} is outside the supported range")
    return value


def _text_number(value: str | None, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if value is None or len(value) > 10:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = int(value)
    except (OverflowError, ValueError) as err:
        raise ValueError(f"{label} is invalid") from err
    return _number(parsed, label, optional=optional)


def _diagnostic_path(project_root: Path, cwd: Path, value: Any) -> str:
    raw = _bounded_text(value, "diagnostic file", limit=4_096)
    try:
        lexical = Path(raw)
        path = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=False)
        return path.relative_to(project_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        # Tool diagnostics can legitimately originate in system headers. Keep
        # a location without publishing a machine-specific absolute path.
        return "[external]"


def _position(value: Any, label: str) -> tuple[str, int, int | None]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    file_value = value.get("file")
    line = _number(value.get("line"), f"{label}.line")
    column_value = value.get("column", value.get("display-column", value.get("byte-column")))
    column = _number(column_value, f"{label}.column", optional=True)
    assert line is not None
    return _bounded_text(file_value, f"{label}.file", limit=4_096), line, column


def _json_region(project_root: Path, cwd: Path, value: Any) -> _Region:
    if not isinstance(value, dict):
        raise ValueError("diagnostic location must be an object")
    if "caret" in value:
        caret = value["caret"]
        start = value.get("start", caret)
        finish = value.get("finish", caret)
    else:
        caret = value
        start = value
        finish = value
    caret_file, caret_line, caret_column = _position(caret, "diagnostic caret")
    start_file, start_line, start_column = _position(start, "diagnostic start")
    finish_file, finish_line, finish_column = _position(finish, "diagnostic finish")
    if start_file != caret_file or finish_file != caret_file:
        raise ValueError("diagnostic range crosses files")
    if (start_line, start_column or 0) > (caret_line, caret_column or 0):
        raise ValueError("diagnostic start follows the caret")
    if (finish_line, finish_column or 0) < (caret_line, caret_column or 0):
        raise ValueError("diagnostic finish precedes the caret")
    return _Region(
        file_path=_diagnostic_path(project_root, cwd, caret_file),
        start_line=caret_line,
        start_column=caret_column,
        end_line=finish_line,
        end_column=finish_column,
    )


def _kind(value: Any) -> tuple[str, EngineStatus]:
    kind = _bounded_text(value, "diagnostic kind", limit=32).casefold()
    if kind in {"fatal error", "error"}:
        return kind, EngineStatus.FAIL
    if kind in {"warning", "note", "remark"}:
        return kind, EngineStatus.WARN
    raise ValueError(f"unsupported diagnostic kind: {kind}")


def _json_fixit(project_root: Path, cwd: Path, value: Any) -> CppFixIt:
    if not isinstance(value, dict):
        raise ValueError("diagnostic fix-it must be an object")
    replacement_value = value.get("string", value.get("replacement"))
    replacement = _bounded_replacement(replacement_value, "diagnostic fix-it replacement")
    if "start" in value and ("next" in value or "end" in value):
        start = value["start"]
        end = value.get("next", value.get("end"))
    elif isinstance(value.get("range"), dict):
        range_value = value["range"]
        start = range_value.get("start")
        end = range_value.get("end")
    else:
        raise ValueError("diagnostic fix-it range is missing")
    start_file, start_line, start_column = _position(start, "fix-it start")
    end_file, end_line, end_column = _position(end, "fix-it end")
    if start_file != end_file or start_column is None or end_column is None:
        raise ValueError("diagnostic fix-it range is invalid")
    if (end_line, end_column) < (start_line, start_column):
        raise ValueError("diagnostic fix-it range is reversed")
    return CppFixIt(
        file_path=_diagnostic_path(project_root, cwd, start_file),
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        replacement=replacement,
    )


def _json_diagnostic(
    project_root: Path,
    cwd: Path,
    value: Any,
    output: list[CppDiagnostic],
    *,
    depth: int,
) -> None:
    if depth > MAX_DIAGNOSTIC_DEPTH:
        raise ValueError("diagnostic nesting exceeds the bounded depth")
    if len(output) >= MAX_DIAGNOSTICS:
        raise ValueError("diagnostic count exceeds the bounded limit")
    if not isinstance(value, dict):
        raise ValueError("diagnostic entry must be an object")
    kind, status = _kind(value.get("kind", value.get("level")))
    message = _bounded_text(value.get("message"), "diagnostic message")
    locations = value.get("locations")
    if locations is not None:
        if not isinstance(locations, list) or not 1 <= len(locations) <= 128:
            raise ValueError("diagnostic locations must be a bounded non-empty list")
        location_value = locations[0]
        for secondary in locations[1:]:
            _json_region(project_root, cwd, secondary)
    else:
        location_value = value.get("location")
    region = _json_region(project_root, cwd, location_value)
    rule_value = value.get("option", value.get("rule", value.get("check_name", "")))
    if rule_value:
        rule = _bounded_text(rule_value, "diagnostic rule", limit=256)
        if re.fullmatch(r"[-A-Za-z0-9_.]+", rule) is None:
            raise ValueError("diagnostic rule contains unsupported characters")
    else:
        rule = ""
    fixits_value = value.get("fixits", [])
    if not isinstance(fixits_value, list) or len(fixits_value) > 128:
        raise ValueError("diagnostic fix-its must be a bounded list")
    fixits = tuple(_json_fixit(project_root, cwd, item) for item in fixits_value)
    target_name = f"Compiler:{rule}" if rule else "C++Syntax"
    output.append(
        CppDiagnostic(
            target=InspectionTarget(
                file_path=region.file_path,
                start_line=region.start_line,
                end_line=region.end_line,
                start_column=region.start_column,
                end_column=region.end_column,
                target_name=target_name,
                status=status,
                message=f"{kind}: {message}",
            ),
            tool_rule_id=rule,
            fixits=fixits,
        )
    )
    children = value.get("children", [])
    if not isinstance(children, list):
        raise ValueError("diagnostic children must be a list")
    for child in children:
        _json_diagnostic(project_root, cwd, child, output, depth=depth + 1)


def _json_entries(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise ValueError("compiler JSON root must be an array or object")
    if "diagnostics" in value:
        diagnostics = value["diagnostics"]
        if not isinstance(diagnostics, list):
            raise ValueError("compiler JSON diagnostics must be an array")
        return diagnostics
    if "kind" in value or "level" in value:
        return [value]
    raise ValueError("compiler JSON object has no diagnostics")


def _parse_json(project_root: Path, cwd: Path, text: str) -> DiagnosticParseResult:
    try:
        value = json.loads(text)
        output: list[CppDiagnostic] = []
        for entry in _json_entries(value):
            _json_diagnostic(project_root, cwd, entry, output, depth=0)
        return DiagnosticParseResult(tuple(output), "json")
    except (json.JSONDecodeError, RecursionError, ValueError) as err:
        return DiagnosticParseResult(format_name="json", error=str(err))


def _decode_c_string(value: str, label: str, *, replacement: bool = False) -> str:
    try:
        decoded = json.loads(f'"{value}"')
    except json.JSONDecodeError as err:
        raise ValueError(f"{label} is not a supported quoted string") from err
    if replacement:
        return _bounded_replacement(decoded, label)
    return _bounded_text(decoded, label, limit=4_096)


def _text_fixit(project_root: Path, cwd: Path, match: re.Match[str]) -> CppFixIt:
    file_value = _decode_c_string(match.group("file"), "fix-it file")
    replacement = _decode_c_string(
        match.group("replacement"), "fix-it replacement", replacement=True
    )
    start_line = _text_number(match.group("start_line"), "fix-it start line")
    start_column = _text_number(match.group("start_column"), "fix-it start column")
    end_line = _text_number(match.group("end_line"), "fix-it end line")
    end_column = _text_number(match.group("end_column"), "fix-it end column")
    assert start_line is not None
    assert start_column is not None
    assert end_line is not None
    assert end_column is not None
    if (end_line, end_column) < (start_line, start_column):
        raise ValueError("fix-it range is reversed")
    return CppFixIt(
        file_path=_diagnostic_path(project_root, cwd, file_value),
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        replacement=replacement,
    )


def _parse_text(project_root: Path, cwd: Path, text: str) -> DiagnosticParseResult:
    output: list[CppDiagnostic] = []
    ancillary_output = False
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _TEXT_DIAGNOSTIC_RE.match(line)
            if match:
                if len(output) >= MAX_DIAGNOSTICS:
                    raise ValueError("diagnostic count exceeds the bounded limit")
                kind, status = _kind(match.group("kind"))
                file_path = _diagnostic_path(project_root, cwd, match.group("file"))
                line_number = _text_number(match.group("line"), "diagnostic line")
                column = _text_number(match.group("column"), "diagnostic column", optional=True)
                assert line_number is not None
                message = _bounded_text(match.group("message"), "diagnostic message")
                rule = match.group("rule") or ""
                output.append(
                    CppDiagnostic(
                        target=InspectionTarget(
                            file_path=file_path,
                            start_line=line_number,
                            start_column=column,
                            target_name=f"Compiler:{rule}" if rule else "C++Syntax",
                            status=status,
                            message=f"{kind}: {message}",
                        ),
                        tool_rule_id=rule,
                    )
                )
                continue
            fixit_match = _TEXT_FIXIT_RE.match(line)
            if fixit_match:
                if not output:
                    raise ValueError("fix-it appeared before a diagnostic")
                fixit = _text_fixit(project_root, cwd, fixit_match)
                output[-1] = replace(output[-1], fixits=(*output[-1].fixits, fixit))
                continue
            if line.startswith("In file included from") or line.startswith("from "):
                ancillary_output = True
                continue
            if output and _TEXT_CONTEXT_RE.fullmatch(line) is not None:
                continue
            if (
                _TEXT_CONTEXT_HEADER_RE.fullmatch(line)
                or _TEXT_REQUIRED_FROM_RE.fullmatch(line)
                or _TEXT_TRAILER_RE.fullmatch(line)
            ):
                ancillary_output = True
                continue
            raise ValueError(f"unrecognized compiler output line: {line!r}")
        if ancillary_output and not output:
            raise ValueError("compiler context output has no located diagnostic")
        return DiagnosticParseResult(tuple(output), "text")
    except ValueError as err:
        return DiagnosticParseResult(format_name="text", error=str(err))


def parse_compiler_diagnostics(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
) -> DiagnosticParseResult:
    """Parse compiler output atomically without accepting mixed/unknown formats."""

    root = project_root.resolve(strict=False)
    if len(stdout) + len(stderr) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return DiagnosticParseResult(error="compiler diagnostic output exceeds the bounded size")
    if "\x00" in stdout or "\x00" in stderr:
        return DiagnosticParseResult(error="compiler diagnostic output contains a null byte")
    non_empty = [stream.strip() for stream in (stdout, stderr) if stream.strip()]
    if not non_empty:
        return DiagnosticParseResult()
    text = non_empty[0] if len(non_empty) == 1 else "\n".join(non_empty)
    if text.lstrip().startswith(("[", "{")):
        return _parse_json(root, cwd, text)
    return _parse_text(root, cwd, text)


def parse_clang_tidy_diagnostics(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
) -> DiagnosticParseResult:
    """Parse clang-tidy text atomically and separate analyzer check families."""

    root = project_root.resolve(strict=False)
    if len(stdout) + len(stderr) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy diagnostic output exceeds the bounded size",
        )
    if "\x00" in stdout or "\x00" in stderr:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy diagnostic output contains a null byte",
        )
    retained: list[str] = []
    generated: int | None = None
    suppressed = 0
    header_hint = False
    for raw_line in (stdout + "\n" + stderr).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if match := _CLANG_TIDY_GENERATED_RE.fullmatch(line):
            if generated is not None:
                return DiagnosticParseResult(
                    format_name="clang-tidy-text",
                    error="clang-tidy emitted duplicate generated-warning summaries",
                )
            generated = int(match.group("count"))
            continue
        if match := _CLANG_TIDY_SUPPRESSED_RE.fullmatch(line):
            if suppressed:
                return DiagnosticParseResult(
                    format_name="clang-tidy-text",
                    error="clang-tidy emitted duplicate suppression summaries",
                )
            suppressed = int(match.group("count"))
            continue
        if _CLANG_TIDY_HEADER_HINT_RE.fullmatch(line):
            header_hint = True
            continue
        retained.append(raw_line)
    result = _parse_text(root, cwd, "\n".join(retained))
    if result.error:
        return replace(result, format_name="clang-tidy-text")
    normalized: list[CppDiagnostic] = []
    for diagnostic in result.diagnostics:
        rule = diagnostic.tool_rule_id
        if not rule:
            if not normalized or not diagnostic.target.message.startswith("note:"):
                return DiagnosticParseResult(
                    format_name="clang-tidy-text",
                    error="clang-tidy diagnostic has no check identifier",
                )
            parent = normalized[-1]
            note_prefix = (
                "ClangAnalyzerNote" if parent.family == "clang-analyzer" else "ClangTidyNote"
            )
            normalized.append(
                replace(
                    diagnostic,
                    target=replace(
                        diagnostic.target,
                        target_name=f"{note_prefix}:{parent.tool_rule_id}",
                    ),
                    tool_rule_id=parent.tool_rule_id,
                    family=parent.family,
                )
            )
            continue
        analyzer = rule.startswith("clang-analyzer-")
        family = "clang-analyzer" if analyzer else "clang-tidy"
        prefix = "ClangAnalyzer" if analyzer else "ClangTidy"
        normalized.append(
            replace(
                diagnostic,
                target=replace(diagnostic.target, target_name=f"{prefix}:{rule}"),
                family=family,
            )
        )
    if generated is not None and generated > len(normalized) + suppressed:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy warning summary exceeds parsed and suppressed diagnostics",
        )
    if header_hint and generated is None and not suppressed and not normalized:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy header-filter hint has no diagnostic summary",
        )
    return DiagnosticParseResult(tuple(normalized), "clang-tidy-text")
